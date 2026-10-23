# AWS lab runbook

This is an operator-executed acceptance procedure. Repository tests do not create AWS resources. Live execution can incur EC2, EBS, public IPv4, and transfer charges. Use a dedicated lab identity/account and keep the receipt path for cleanup.

## 1. Confirm the application locally

From the repository root, use Linux/WSL with Python 3.11+, Docker, and AWS CLI v2. Configure Docker for your current user; the controller does not invoke interactive `sudo`. Docker group membership carries host-level privileges.

```bash
docker info
docker build --platform linux/amd64 -t ghostdock-demo:local demo
python3 scripts/docker_integration.py
aws --version
```

The integration script creates a uniquely named test container, verifies it, kills it, confirms local recovery, and removes only that container.

## 2. Publish an image that EC2 can pull

The current adapter supports a **public** registry image. An image that exists only on your laptop cannot be pulled by EC2. Publish the sample under an account you control. For this repository's owner:

```bash
docker login ghcr.io -u kadiwalarman1-rgb
docker tag ghostdock-demo:local ghcr.io/kadiwalarman1-rgb/ghostdock-demo:0.1.0
docker push ghcr.io/kadiwalarman1-rgb/ghostdock-demo:0.1.0
docker image inspect ghcr.io/kadiwalarman1-rgb/ghostdock-demo:0.1.0 --format '{{index .RepoDigests 0}}'
```

Enter registry credentials only through the login prompt or an approved credential helper. Never put tokens in this repository. Make the package public in its GitHub package settings and verify anonymous pull access. Copy the returned `ghcr.io/...@sha256:...` reference into the AWS configuration. Other users should replace the account name with their own.

The demo base image tag is mutable. The deployed workload digest is the immutable identity used by GhostDock. Rebuild and rescan the image when base-image updates arrive.

## 3. Create the foundation

Use an authorized setup identity to review and deploy `infra/lab-foundation.yaml`:

```bash
aws cloudformation validate-template --template-body file://infra/lab-foundation.yaml --region ap-south-1
aws cloudformation deploy --template-file infra/lab-foundation.yaml --stack-name ghostdock-lab --capabilities CAPABILITY_IAM --region ap-south-1
aws cloudformation describe-stacks --stack-name ghostdock-lab --region ap-south-1 --query 'Stacks[0].Outputs' --output table
```

The template creates a VPC, public subnet and route, internet gateway, security group with **no inbound rules**, an SSM instance role/profile, and a controller IAM policy. It creates no EC2 standby instance or NAT gateway.

Have an administrator attach the exported `ControllerPolicyArn` to a dedicated controller identity/role. This is separate from the EC2 instance role. Prefer an existing SSO/temporary-credential workflow; the template does not automatically grant your terminal permission. Review the IAM policy for your account boundaries before attaching it.

The policy permits tagged launches in this network, the supplied AMI, narrow instance types, the specific EC2 role, SSM commands on the managed workload, and termination of that workload. Some read APIs require `Resource: "*"`; they are region-constrained. It does not grant general administrator access.

## 4. Fill configuration

```bash
cp config/aws.example.toml config/aws.toml
```

Edit the copy with the real image digest and stack outputs:

| Configuration field | Source |
|---|---|
| `workload.image` | Published, public `linux/amd64` image digest |
| `aws.ami_id` | `AmiId` output |
| `aws.subnet_id` | `SubnetId` output |
| `aws.security_group_id` | `SecurityGroupId` output |
| `aws.instance_profile` | `InstanceProfile` output |
| `aws.region` | Region where this stack was created |
| `aws.profile` | Optional existing AWS CLI profile for the controller |

For the example public subnet, keep `public_ip = true` so EC2 can reach package repositories, the registry, and SSM over outbound HTTPS. The attached security group still permits no inbound connections. Public IPv4 is billable. For `public_ip = false`, you must separately provide appropriate private egress/endpoints; the sample stack does not do that.

`config/aws.toml` is ignored by Git. It still must not contain access keys. The AMI must be official Amazon Linux 2023 x86_64; the public SSM parameter in the stack resolves a region-appropriate image when the stack is created/updated.

## 5. Read-only checks

```bash
python3 -m ghostdock plan --config config/aws.toml
python3 -m ghostdock check --config config/aws.toml --aws
```

These do not provision EC2. The AWS check verifies identity, AMI, network consistency, and no inbound security-group rules. It does not prove launch quota, all IAM conditions, instance capacity, registry availability, or eventual application health.

If you previously ran the local demo using the same container name with a different image/configuration, inspect and remove **only that demo container** before starting this run. GhostDock refuses to silently replace a differently configured or unmanaged container.

## 6. Arm recovery and inject one controlled failure

```bash
python3 -m ghostdock watch --config config/aws.toml --live --approve-cloud
```

Save the printed receipt path. Wait until `WATCHING` appears; this means the initial local app passed its probe. In a second terminal, confirm the expected local identity:

```bash
curl --fail http://127.0.0.1:8080/healthz
docker inspect ghostdock-demo --format '{{index .Config.Labels "io.ghostdock.managed"}}'
```

After confirming the label is `true`, remove **this test container** to exercise a case where a local restart cannot recover it:

```bash
docker rm --force ghostdock-demo
```

This intentionally interrupts the sample application. GhostDock should confirm consecutive failures, attempt local repair, launch one tagged instance, and perform SSM application verification. The expected terminal state is `RECOVERED_CLOUD`; the receipt must still say `traffic_switched: false`.

Do not use an invalid image as the cloud-recovery demo: the cloud would have the same missing image. For a local-only recovery exercise, use `docker kill` instead of removal.

## 7. Inspect evidence before claiming success

```bash
python3 -m ghostdock verify /path/to/incident
python3 -m ghostdock report /path/to/incident --output artifacts/live-recovery.html
```

Review the instance tags and boot log (`/var/log/cloud-init-output.log`) through an authorized SSM session if necessary. Confirm the SSM command result corresponds to the expected application. EC2 `running` alone is insufficient. No application port or SSH port is opened by this design.

Capture real evidence: the first failed probe, instance ID and ownership tags, verified application response/receipt, and termination confirmation. Redact account identifiers where appropriate; never publish credentials. Record measured recovery time as **first observed failed probe to application verification**, excluding user traffic cutover.

## 8. Explicit cleanup

Preview, then execute cleanup for the exact incident:

```bash
python3 -m ghostdock cleanup /path/to/incident --config config/aws.toml
python3 -m ghostdock cleanup /path/to/incident --config config/aws.toml --execute
```

The controller verifies ManagedBy, Workload, IncidentId, ConfigSHA256, and region before termination. It waits for termination confirmation. The configured root EBS volume is encrypted and marked delete-on-termination.

A boot-time systemd timer requests shutdown after the configured TTL; EC2 is configured to terminate on that shutdown. This is a fallback, not a guarantee: a boot or system failure can prevent the timer from running. Budget alerts also are not an instant spending cutoff.

If a launch or cleanup result was uncertain, retry scoped cleanup and inspect EC2 using the incident tags. Do not delete the receipt or start a fresh cloud run merely to bypass the visible uncertainty. Resources can appear after an eventually consistent query initially returned none.

After all incident instances are confirmed terminated, detach the exported controller policy from identities to which you attached it, then remove the foundation:

```bash
aws cloudformation delete-stack --stack-name ghostdock-lab --region ap-south-1
aws cloudformation wait stack-delete-complete --stack-name ghostdock-lab --region ap-south-1
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `BLOCKED` before watching | Docker access, existing container ownership/configuration, image availability, and local HTTP identity |
| AWS authorization failure | Controller policy attachment, `iam:PassRole`, requested region, request tags, and account permissions boundaries |
| AMI rejected | Official Amazon Linux 2023 x86_64 image in the same region |
| SSM never online | Instance role/profile, SSM agent, DNS, outbound HTTPS, public IP/private egress |
| Cloud health timeout | cloud-init log, public image visibility, image architecture, non-root startup, expected body and ports |
| `CLEANUP_REQUIRED` | Preserve receipt; reconcile incident-tagged resources and retry cleanup with the correct permissions |

Primary references: [SSM networking](https://docs.aws.amazon.com/systems-manager/latest/userguide/setup-create-vpc.html), [IAM temporary credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html), [EC2 billing states](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html).
