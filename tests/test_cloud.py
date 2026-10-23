import base64
import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import Mock

from ghostdock.cloud import CloudNode
from ghostdock.config import AWS, Config, Workload
from ghostdock.demo import VirtualClock
from ghostdock.evidence import Receipt
from ghostdock.process import OperationError, Result


def live_config():
    return Config(
        workload=Workload(image="ghcr.io/example/demo@sha256:" + "a" * 64),
        aws=AWS(
            ami_id="ami-1234567890abcdef0",
            subnet_id="subnet-12345678",
            security_group_id="sg-12345678",
            instance_profile="ghostdock-profile",
        ),
    )


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = live_config()
        self.receipt = Receipt(self.temp.name, self.config)
        self.clock = VirtualClock()
        self.node = CloudNode(self.config, clock=self.clock.now, sleep=self.clock.sleep)

    def instance(self, incident=None, status="running"):
        return {
            "InstanceId": "i-0123456789abcdef0",
            "State": {"Name": status},
            "Tags": [
                {"Key": "ManagedBy", "Value": "GhostDock"},
                {"Key": "Workload", "Value": self.config.workload.name},
                {"Key": "IncidentId", "Value": incident or self.receipt.incident},
                {"Key": "ConfigSHA256", "Value": self.config.fingerprint},
            ],
        }

    def test_launch_is_single_instance_idempotent_and_hardened(self):
        self.receipt.record("PROVISIONING", cloud_expires_at="2026-10-23T09:00:00+00:00")
        request = self.node.launch_request(self.receipt)
        self.assertEqual((request["MinCount"], request["MaxCount"]), (1, 1))
        self.assertEqual(request["ClientToken"], self.receipt.incident)
        self.assertEqual(request["MetadataOptions"]["HttpTokens"], "required")
        self.assertEqual(request["InstanceInitiatedShutdownBehavior"], "terminate")
        self.assertEqual(request["CreditSpecification"]["CpuCredits"], "standard")
        self.assertTrue(request["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertTrue(request["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
        self.assertFalse(request["NetworkInterfaces"][0]["AssociatePublicIpAddress"])
        script = base64.b64decode(request["UserData"]).decode()
        self.assertNotIn("@@", script)
        self.assertIn("--user 10001:10001", script)
        self.assertLess(script.index("systemd-run"), script.index("dnf install"))

    def test_provision_persists_intent_before_request_and_captures_instance(self):
        self.node._owned = Mock(return_value=[])

        def fake_json(args):
            self.assertTrue(self.receipt.state["cloud_requested"])
            self.assertEqual(args[:2], ["ec2", "run-instances"])
            return {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}

        self.node._json = fake_json
        self.assertEqual(self.node.provision(self.receipt), "i-0123456789abcdef0")
        self.assertEqual(self.receipt.state["status"], "VERIFYING_CLOUD")

    def test_repeated_incident_reuses_instance_without_launching(self):
        self.node._owned = Mock(return_value=[self.instance()])
        self.node._json = Mock()
        self.assertEqual(self.node.provision(self.receipt), "i-0123456789abcdef0")
        self.node._json.assert_not_called()
        self.assertTrue(self.receipt.state["cloud_requested"])
        self.assertEqual(self.receipt.state["instance_id"], "i-0123456789abcdef0")

    def test_retry_after_uncertain_launch_preserves_all_request_parameters(self):
        self.node._owned = Mock(return_value=[])
        requests = []

        def uncertain_then_success(args):
            requests.append(json.loads(args[-1]))
            if len(requests) == 1:
                raise OperationError("request timed out; launch outcome unknown")
            return {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}

        self.node._json = uncertain_then_success
        with self.assertRaises(OperationError):
            self.node.provision(self.receipt)
        reopened = Receipt.reopen(self.receipt.path)
        self.node.provision(reopened)
        self.assertEqual(requests[0], requests[1])

    def test_existing_other_incident_blocks_new_launch(self):
        self.node._owned = Mock(return_value=[self.instance(incident="other")])
        with self.assertRaisesRegex(OperationError, "active cloud instance"):
            self.node.provision(self.receipt)
        self.assertFalse(self.receipt.state["cloud_requested"])

    def test_changed_configuration_blocks_instance_reuse(self):
        instance = self.instance()
        instance["Tags"][-1]["Value"] = "different"
        self.node._owned = Mock(return_value=[instance])
        with self.assertRaisesRegex(OperationError, "different configuration"):
            self.node.provision(self.receipt)

    def test_timeout_preserves_launch_intent_for_reconciliation(self):
        self.node._owned = Mock(return_value=[])
        self.node._json = Mock(side_effect=OperationError("timeout"))
        with self.assertRaises(OperationError):
            self.node.provision(self.receipt)
        self.assertTrue(self.receipt.state["cloud_requested"])

    def test_cleanup_targets_only_matching_incident(self):
        self.node._owned = Mock(return_value=[self.instance()])
        calls = []

        def fake_json(args):
            calls.append(args)
            return {"Reservations": [{"Instances": [self.instance(status="terminated")]}]}

        self.node._json = fake_json
        ids = self.node.cleanup(self.receipt.state)
        self.assertEqual(ids, ["i-0123456789abcdef0"])
        self.assertEqual(calls[0], ["ec2", "terminate-instances", "--instance-ids", *ids])
        self.node._owned.assert_called_once_with("ghostdock-demo", self.receipt.incident)

    def test_cleanup_refuses_foreign_tags(self):
        self.node._owned = Mock(return_value=[self.instance(incident="foreign")])
        self.node._json = Mock()
        with self.assertRaisesRegex(OperationError, "ownership"):
            self.node.cleanup(self.receipt.state)
        self.node._json.assert_not_called()

    def test_cleanup_does_not_treat_uncertain_launch_as_clean(self):
        self.receipt.state["cloud_requested"] = True
        self.node._owned = Mock(return_value=[])
        with self.assertRaisesRegex(OperationError, "outcome uncertain"):
            self.node.cleanup(self.receipt.state)

    def test_cleanup_rejects_simulation_and_region_mismatch(self):
        for changes in ({"simulation": True}, {"region": "us-east-1"}):
            with self.subTest(changes=changes), self.assertRaises(OperationError):
                self.node.cleanup(dict(self.receipt.state, **changes))

    def test_ssm_success_requires_exit_zero_and_application_marker(self):
        self.node._json = Mock(
            side_effect=[
                {"InstanceInformationList": [{"PingStatus": "Online"}]},
                {"Command": {"CommandId": "command-1"}},
            ]
        )
        self.node._raw = Mock(
            return_value=Result(
                0,
                json.dumps(
                    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "GHOSTDOCK_HEALTH_OK\n"}
                ),
            )
        )
        self.assertTrue(self.node.verify("i-0123456789abcdef0"))

    def test_ssm_success_without_marker_is_not_recovery(self):
        self.node._json = Mock(
            side_effect=[
                {"InstanceInformationList": [{"PingStatus": "Online"}]},
                {"Command": {"CommandId": "command-1"}},
            ]
        )
        self.node._raw = Mock(
            return_value=Result(
                0, json.dumps({"Status": "Success", "ResponseCode": 0, "StandardOutputContent": ""})
            )
        )
        self.assertFalse(self.node.verify("i-0123456789abcdef0"))

    def test_ssm_eventual_consistency_is_retried(self):
        self.node._json = Mock(
            side_effect=[
                {"InstanceInformationList": [{"PingStatus": "Online"}]},
                {"Command": {"CommandId": "command-1"}},
            ]
        )
        self.node._raw = Mock(
            side_effect=[
                Result(254, stderr="InvocationDoesNotExist"),
                Result(
                    0,
                    json.dumps(
                        {
                            "Status": "Success",
                            "ResponseCode": 0,
                            "StandardOutputContent": "GHOSTDOCK_HEALTH_OK",
                        }
                    ),
                ),
            ]
        )
        self.assertTrue(self.node.verify("i-0123456789abcdef0"))

    def test_ssm_offline_has_a_bounded_deadline(self):
        self.node._json = Mock(return_value={"InstanceInformationList": []})
        self.assertFalse(self.node.verify("i-0123456789abcdef0"))
        self.assertEqual(self.clock.now(), self.config.recovery.cloud_timeout_seconds)

    def test_aws_error_retains_nonzero_and_diagnostic(self):
        node = CloudNode(self.config, run=Mock(return_value=Result(254, stderr="UnauthorizedOperation")))
        with self.assertRaisesRegex(OperationError, "254.*UnauthorizedOperation"):
            node._json(["ec2", "run-instances"])

    def test_health_command_quotes_application_marker(self):
        cfg = replace(
            self.config, workload=replace(self.config.workload, expected_body="x'; touch /tmp/nope; '")
        )
        command = CloudNode(cfg).health_command()
        self.assertIn("grep -Fq -- 'x'", command)
        self.assertIn("curl --fail", command)


if __name__ == "__main__":
    unittest.main()
