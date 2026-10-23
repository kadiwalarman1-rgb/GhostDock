# Architecture and engineering decisions

GhostDock is a single-controller, single-workload recovery prototype for a stateless HTTP service. The controller must remain alive and retain access to AWS. The cloud adapter is implemented; live cloud validation is tracked separately in [validation.md](validation.md).

## Boundaries

| Boundary | Responsibility | Evidence |
|---|---|---|
| Configuration | Reject invalid inputs, unsafe image syntax, unbounded settings, and unpinned live images | Validation error before provisioning |
| Local adapter | Verify ownership, deploy, probe HTTP, attempt one restart | Docker process status and application response |
| Engine | Count consecutive failures and choose a bounded recovery action | State transitions |
| Cloud adapter | Check prerequisites, launch EC2, run SSM health probes, terminate owned resources | Instance ID, command status, application marker |
| Receipt | Record incident state and chronology | Private JSONL journal, state snapshot, HTML report |

`process.command()` is the subprocess boundary. Commands use argument lists and `shell=False`; AWS and Docker diagnostics retain their nonzero status. Long-running container output is not captured into an unbounded Python buffer: Docker runs detached, and monitoring uses bounded health probes.

## What happens during an incident

1. The initial application must become healthy before monitoring is armed. A bad image, invalid configuration, inaccessible Docker daemon at startup, or failed initial deployment blocks the run.
2. A healthy probe resets the consecutive-failure count. A known configuration/ownership error blocks cloud escalation.
3. Repeated runtime unhealthiness, a stopped/removed managed container, or a subsequently unreachable Docker daemon confirms an incident.
4. GhostDock attempts one local restart and waits within `startup_seconds` for application health. A removed container cannot be restarted; it proceeds to the next configured recovery action.
5. With cloud disabled, the incident ends visibly as `CLOUD_DISABLED`. Live mode requires two explicit flags and a digest-pinned workload image.
6. Cloud prerequisites are checked. GhostDock refuses a new launch if another active incident already owns the same workload.
7. Launch intent is persisted before `RunInstances`. The incident UUID is the client token. EC2 instances and volumes receive ownership tags; the instance ID is saved after the response.
8. The new host arms a best-effort expiry timer, installs Docker, and runs the same stateless image with a restricted container configuration.
9. SSM executes a loopback HTTP probe. Both HTTP 200 and the configured application marker are required. EC2 allocation or SSM registration alone is insufficient.
10. Failed cloud verification attempts tagged cleanup. A denied, timed-out, or ambiguous cleanup ends as `CLEANUP_REQUIRED`, preserving the incident for an operator.

## Recovery measurement

`recovery_seconds` measures from the first failed observed probe in the confirmed failure sequence to successful local/cloud application verification. It includes the confirmation window and repair attempts. It does not measure from the actual instant the service failed, and it does not include user-traffic cutover.

Simulation uses a virtual clock. Its elapsed times describe the scenario, not hardware or AWS performance. No real-world RTO is claimed without a live receipt and independent acceptance checks.

## Ownership and idempotency

A Linux advisory lock excludes a second controller using the same workload and state directory. The cloud adapter also looks for active instances tagged with the workload. A persisted incident token identifies one launch request; configuration tags prevent reusing an instance with a different specification.

These mechanisms are **not a distributed lease**. Controllers on different machines, different state roots, or simultaneous checks against eventually consistent EC2 results can race. Run one controller per workload. Distributed coordination is a separate milestone.

A timeout can occur after AWS accepted a request but before its response reached Python. GhostDock keeps launch intent and attempts discovery by ownership tags. If the result cannot be reconciled, it preserves an explicit cleanup-required outcome. It does not call that incident clean simply because a query returned no instance yet.

## Deployment contract

- Linux/WSL controller, Python 3.11+, Docker CLI/daemon, and AWS CLI v2 for live mode.
- One stateless, non-root HTTP image; port at least 1024; expected response marker.
- Public registry image accessible to a clean EC2 host, pinned by digest for live use.
- Official Amazon Linux 2023 x86_64 cloud AMI with SSM agent, outbound HTTPS, and an instance profile.
- The same image runs locally and in the cloud. Environment secrets, private registry login, persistent volumes, arbitrary commands, and GPU workloads are outside this version's contract.

## Deliberate trade-offs

| Decision | Benefit | Limitation |
|---|---|---|
| Cold EC2 recovery | No continuously running replacement instance | Boot/install/pull time causes a recovery delay |
| SSM-only verification | No inbound SSH/application ports | Does not test public client reachability |
| Standard-library controller | Small runtime dependency surface; easy reviewer setup | CLI adapters need explicit response/error handling |
| One incident per invocation | Clear terminal outcome and bounded action | Operator must restart monitoring after an incident |
| Local receipt storage | Simple, inspectable evidence | A user able to rewrite all files can forge the hash chain |
| Stateless workload only | Avoids pretending a container image contains runtime data | Databases and sessions need an independent recovery design |

Docker restart policies already solve many local restart cases. GhostDock explores the additional decisions around conditional cloud capacity, evidence, ownership, and cleanup. It is not a replacement for Kubernetes, a load balancer, or a production disaster-recovery platform.

## What remains outside this release

No zero-downtime guarantee, traffic switching, data replication, failback, automatic continuous re-arming, independent host-failure controller, distributed fencing, or production SLA. An application recovered on EC2 is not yet equivalent to users being served by that instance.

## Primary references

- [Docker exit statuses](https://docs.docker.com/engine/containers/run/#exit-status)
- [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
- [EC2 RunInstances](https://docs.aws.amazon.com/cli/latest/reference/ec2/run-instances.html)
- [EC2 idempotency](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-idempotency.html)
- [EC2 user data](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/user-data.html)
- [SSM Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/walkthrough-cli.html)
