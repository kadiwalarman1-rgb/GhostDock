# Security model

GhostDock controls Docker and may create/terminate EC2 instances. Treat its controller identity, configuration, runtime environment, and source checkout as trusted administrative inputs. This is a lab prototype with explicit operational limits.

## Threats and controls

| Failure or threat | Implemented control | Remaining boundary |
|---|---|---|
| Shell injection through image/config | Syntax validation, argv lists, `shell=False`, quoted generated shell values | Operator controls the environment and executable search path |
| Secret leakage into Git | Ignored local config/credential paths and repository secret-scan CI | `.gitignore` is not encryption; previously committed secrets require rotation |
| Misleading successful recovery | Application marker plus HTTP 200 and successful SSM execution | A compromised application can forge its health response |
| Health-probe redirect | Local probes reject redirects and ignore proxy settings; cloud probe does not follow redirects and checks HTTP 200 | Only the configured loopback endpoint is supported |
| Accidental cloud spending | Default cloud-disabled mode, two explicit live flags, one-instance launch count, instance-type/TTL bounds | No distributed hard budget cap; an operator can still run separate workloads |
| Duplicate/retried incidents | Local lock, active-workload check, persisted intent, client token, configuration tag | Single-controller contract; eventual consistency and independent controllers can race |
| Deleting unrelated resources | Region and four ownership-tag checks before scoped termination | Tags are ownership conventions, not unforgeable authorization tokens |
| Internet-facing cloud management | No inbound SG rules, no SSH key, SSM outbound channel | The example public subnet uses a billable public IP for egress |
| Privileged workload | User 10001, read-only root, all Linux capabilities dropped, no-new-privileges, resource limits | Docker daemon access is privileged; container isolation is not a VM boundary |
| EC2 metadata theft | IMDSv2 required and response hop limit 1 | Host-root workloads remain highly privileged |
| Evidence corruption | Hash-linked JSONL, private permissions, atomic/fsynced state, state/journal comparison | No signature or trusted external anchoring; a full rewrite can forge a receipt |
| Supply-chain drift | Live image digest requirement, pinned Actions commits, locked development dependencies, update checks | Sample Docker base tag is mutable until built; vulnerabilities evolve |

## CI security checks

Bandit scans controller and sample application code. ShellCheck inspects Bash. CloudFormation lint validates the template structure. Trivy scans repository files for secrets and gates the sample image on **fixable HIGH/CRITICAL** vulnerabilities. Unfixed vulnerabilities are outside that gate and require review; a green gate is not a claim that the image has no vulnerabilities. CI never obtains AWS credentials or provisions cloud resources.

GitHub Actions use read-only repository permissions and pinned action commits. No `pull_request_target` workflow executes contributor code with write credentials. The installed development tools are pinned in `requirements-dev.lock` for Python 3.12; the controller itself has no third-party Python runtime dependencies.

## Reviewed static-analysis exceptions

The Bandit exclusions in `pyproject.toml` document the intended trust boundary:

- B404/B603: subprocess imports and fixed argument-list execution are required for Docker/AWS integration. User-controlled values are validated; no `shell=True` is used.
- B607: `docker` and `aws` are installed through the operator's trusted environment. Use a controlled `PATH`; do not run the controller from an untrusted directory with a modified command path.
- B310: the only Python HTTP probe uses a constructed `127.0.0.1` URL with validated port/path and rejects redirects.
- B104: the sample application listens on all interfaces **inside its container**, while the host port is explicitly published on loopback.
- Targeted B108 annotations: a container tmpfs mount specification and an intentionally public fault-injection flag inside that isolated tmpfs. Neither creates a predictable host secret file.

Changing these boundaries requires revisiting the exceptions, not merely keeping the scanner green.

## Scope exclusions

No private-registry credential distribution, application secrets, persistent volume migration, multi-tenant isolation, signed evidence, automatic IAM creation by the controller, or production incident SLA. The foundation policy is a starting point for the documented lab topology and still requires account-specific review and live acceptance testing.
