"""AWS CLI adapter: EC2 cold start, SSM application verification, scoped cleanup."""

import base64
import json
import shlex
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .process import OperationError, command, require_success


class CloudNode:
    def __init__(self, config, run=command, clock=time.monotonic, sleep=time.sleep):
        self.config, self.run, self.clock, self.sleep = config, run, clock, sleep

    def _raw(self, args):
        prefix = [
            "aws",
            "--region",
            self.config.aws.region,
            "--no-cli-pager",
            "--output",
            "json",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "15",
        ]
        if self.config.aws.profile:
            prefix.extend(["--profile", self.config.aws.profile])
        return self.run(prefix + args, timeout=30)

    def _json(self, args):
        output = require_success(self._raw(args), "AWS " + " ".join(args[:2]))
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise OperationError("AWS returned invalid JSON") from exc

    def preflight(self):
        self.config.validate(live=True)
        identity = self._json(["sts", "get-caller-identity"])
        a = self.config.aws
        images = self._json(["ec2", "describe-images", "--image-ids", a.ami_id, "--owners", "amazon"])[
            "Images"
        ]
        if (
            not images
            or images[0].get("Architecture") != "x86_64"
            or not images[0].get("Name", "").startswith("al2023-ami-")
        ):
            raise OperationError("use an official Amazon Linux 2023 x86_64 AMI in this region")
        subnet = self._json(["ec2", "describe-subnets", "--subnet-ids", a.subnet_id])["Subnets"][0]
        group = self._json(["ec2", "describe-security-groups", "--group-ids", a.security_group_id])[
            "SecurityGroups"
        ][0]
        if subnet["VpcId"] != group["VpcId"]:
            raise OperationError("subnet and security group belong to different VPCs")
        if group.get("IpPermissions"):
            raise OperationError("this SSM-only demo requires a security group with no inbound rules")
        return identity["Account"]

    def _owned(self, workload, incident=None):
        filters = [
            {"Name": "tag:ManagedBy", "Values": ["GhostDock"]},
            {"Name": "tag:Workload", "Values": [workload]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped", "shutting-down"],
            },
        ]
        if incident:
            filters.append({"Name": "tag:IncidentId", "Values": [incident]})
        data = self._json(["ec2", "describe-instances", "--filters", json.dumps(filters)])
        return [instance for reservation in data["Reservations"] for instance in reservation["Instances"]]

    def bootstrap(self, incident):
        script = Path(__file__).with_name("bootstrap.sh").read_text()
        w, r = self.config.workload, self.config.recovery
        values = {
            "IMAGE": shlex.quote(w.image),
            "NAME": shlex.quote(w.name),
            "INCIDENT": shlex.quote(incident),
            "TTL_SECONDS": str(r.ttl_minutes * 60),
            "HOST_PORT": str(w.host_port),
            "CONTAINER_PORT": str(w.container_port),
        }
        for key, value in values.items():
            script = script.replace(f"@@{key}@@", value)
        return script

    def launch_request(self, receipt):
        a = self.config.aws
        tags = [
            {"Key": "ManagedBy", "Value": "GhostDock"},
            {"Key": "Workload", "Value": self.config.workload.name},
            {"Key": "IncidentId", "Value": receipt.incident},
            {"Key": "ConfigSHA256", "Value": self.config.fingerprint},
            {
                "Key": "ExpiresAt",
                "Value": receipt.state["cloud_expires_at"],
            },
            {"Key": "Name", "Value": f"ghostdock-{self.config.workload.name}"},
        ]
        return {
            "ImageId": a.ami_id,
            "InstanceType": a.instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "CreditSpecification": {"CpuCredits": "standard"},
            "ClientToken": receipt.incident,
            "IamInstanceProfile": {"Name": a.instance_profile},
            "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 1},
            "InstanceInitiatedShutdownBehavior": "terminate",
            "NetworkInterfaces": [
                {
                    "DeviceIndex": 0,
                    "SubnetId": a.subnet_id,
                    "Groups": [a.security_group_id],
                    "AssociatePublicIpAddress": a.public_ip,
                    "DeleteOnTermination": True,
                }
            ],
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": 12,
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": tags},
                {"ResourceType": "volume", "Tags": tags},
            ],
            "UserData": base64.b64encode(self.bootstrap(receipt.incident).encode()).decode(),
        }

    def provision(self, receipt):
        active = self._owned(self.config.workload.name)
        for instance in active:
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            if tags.get("IncidentId") != receipt.incident:
                raise OperationError(
                    "an active cloud instance already owns this workload; inspect/clean it first"
                )
            if tags.get("ConfigSHA256") != self.config.fingerprint:
                raise OperationError("incident exists with a different configuration")
        if len(active) > 1:
            raise OperationError("multiple instances found; refusing to choose or launch another")
        if active:
            instance_id = active[0]["InstanceId"]
            receipt.record(
                "VERIFYING_CLOUD",
                "reusing the instance owned by this incident; health still needs verification",
                cloud_requested=True,
                instance_id=instance_id,
            )
            return instance_id
        # Keep every request parameter stable when retrying the same ClientToken,
        # including tags; otherwise EC2 can reject a retry as a parameter mismatch.
        expires_at = (
            receipt.state.get("cloud_expires_at")
            or (datetime.now(timezone.utc) + timedelta(minutes=self.config.recovery.ttl_minutes)).isoformat()
        )
        receipt.record(
            "PROVISIONING",
            "launch intent persisted before AWS call",
            cloud_requested=True,
            cloud_expires_at=expires_at,
        )
        data = self._json(
            ["ec2", "run-instances", "--cli-input-json", json.dumps(self.launch_request(receipt))]
        )
        instances = data.get("Instances", [])
        if len(instances) != 1 or not instances[0].get("InstanceId"):
            raise OperationError("launch response did not identify exactly one instance; outcome unknown")
        instance_id = instances[0]["InstanceId"]
        receipt.record(
            "VERIFYING_CLOUD",
            "EC2 allocated; application health is not verified yet",
            instance_id=instance_id,
        )
        return instance_id

    def health_command(self):
        w = self.config.workload
        url = f"http://127.0.0.1:{w.host_port}{w.health_path}"
        return (
            "set -eu\n"
            f"body=$(curl --fail --silent --show-error --max-time 5 --write-out '\\n%{{http_code}}' {shlex.quote(url)})\n"
            "status=$(printf '%s' \"$body\" | tail -n 1)\n"
            '[ "$status" = 200 ]\n'
            f"printf '%s' \"$body\" | sed '$d' | grep -Fq -- {shlex.quote(w.expected_body)}\n"
            "printf 'GHOSTDOCK_HEALTH_OK\\n'\n"
        )

    def verify(self, instance_id):
        deadline = self.clock() + self.config.recovery.cloud_timeout_seconds
        command_id = None
        while self.clock() < deadline:
            if command_id is None:
                data = self._json(
                    [
                        "ssm",
                        "describe-instance-information",
                        "--filters",
                        json.dumps([{"Key": "InstanceIds", "Values": [instance_id]}]),
                    ]
                )
                if any(i.get("PingStatus") == "Online" for i in data["InstanceInformationList"]):
                    response = self._json(
                        [
                            "ssm",
                            "send-command",
                            "--instance-ids",
                            instance_id,
                            "--document-name",
                            "AWS-RunShellScript",
                            "--timeout-seconds",
                            "30",
                            "--parameters",
                            json.dumps({"commands": [self.health_command()], "executionTimeout": ["15"]}),
                        ]
                    )
                    command_id = response["Command"]["CommandId"]
            else:
                response = self._raw(
                    [
                        "ssm",
                        "get-command-invocation",
                        "--command-id",
                        command_id,
                        "--instance-id",
                        instance_id,
                    ]
                )
                if response.code and "InvocationDoesNotExist" in response.stderr:
                    self.sleep(3)
                    continue
                data = json.loads(require_success(response, "SSM health verification"))
                status = data.get("Status")
                if status == "Success" and data.get("ResponseCode") == 0:
                    return "GHOSTDOCK_HEALTH_OK" in data.get("StandardOutputContent", "")
                if status in {"Failed", "TimedOut", "Cancelled", "Cancelling"}:
                    # cloud-init may still be installing Docker; probe again until the bounded deadline.
                    command_id = None
            self.sleep(3)
        return False

    def cleanup(self, state):
        if state["simulation"]:
            raise OperationError("refusing AWS cleanup for a simulated receipt")
        if state["region"] != self.config.aws.region:
            raise OperationError("receipt region differs from configuration")
        instances = self._owned(state["workload"], state["incident_id"])
        if not instances:
            if state.get("instance_id"):
                data = self._json(["ec2", "describe-instances", "--instance-ids", state["instance_id"]])
                flat = [i for r in data["Reservations"] for i in r["Instances"]]
                if flat and flat[0]["State"]["Name"] == "terminated":
                    return []
            if state["cloud_requested"]:
                raise OperationError(
                    "no instance visible after launch intent; outcome uncertain; retry cleanup and inspect incident tags"
                )
            return []
        ids = []
        for instance in instances:
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            expected = {
                "ManagedBy": "GhostDock",
                "Workload": state["workload"],
                "IncidentId": state["incident_id"],
                "ConfigSHA256": state["config_sha256"],
            }
            if any(tags.get(key) != value for key, value in expected.items()):
                raise OperationError("cloud ownership verification failed; refusing termination")
            ids.append(instance["InstanceId"])
        self._json(["ec2", "terminate-instances", "--instance-ids", *ids])
        deadline = self.clock() + 120
        while self.clock() < deadline:
            data = self._json(["ec2", "describe-instances", "--instance-ids", *ids])
            found = [i for r in data["Reservations"] for i in r["Instances"]]
            if len(found) == len(ids) and all(i["State"]["Name"] == "terminated" for i in found):
                return ids
            self.sleep(3)
        raise OperationError("termination requested but not yet confirmed; run cleanup again")
