# Validation and evidence

This file distinguishes what was executed from what is implemented. Actual GitHub job results are available in [CI](https://github.com/kadiwalarman1-rgb/GhostDock/actions/workflows/ci.yml).

| Layer | Validation method | Scope |
|---|---|---|
| Python recovery engine | Isolated tests and deterministic scenarios | State transitions, failure thresholds, local-first policy, rollback, exit statuses |
| AWS adapter | Mocked AWS CLI responses | Request construction, launch intent, duplicate ownership, SSM verification, cleanup behavior |
| Bash integration | Fake Docker executable and real subprocess invocation | Exact failure-code and stderr propagation |
| Receipt/report | Real filesystem tests | Hash consistency, altered evidence detection, permissions, escaped HTML |
| Real Docker | `scripts/docker_integration.py` in CI | Actual container kill, restart, and HTTP verification |
| AWS foundation | CloudFormation lint | Template structure; not proof that IAM/networking works in an account |
| Live AWS | Operator acceptance runbook | **Pending; no live AWS recovery receipt is claimed by this release** |

## Recorded local checks

On 2026-10-23, Python 3.12.14 completed **57 tests successfully**, with **79% branch-enabled aggregate coverage**. Black, isort, Flake8, ShellCheck, CloudFormation lint, and the configured Bandit scan passed. The package installed and its CLI ran outside the repository. Docker integration runs separately in GitHub Actions; live AWS acceptance is pending.

A real output of the simulated engine is included in [the example receipt](examples/simulated-recovery). It contains the journal, state, and self-contained HTML report. Recheck it with:

```bash
python3 -m ghostdock verify docs/examples/simulated-recovery
```

This example uses simulated resources and a virtual clock. Its times are not AWS benchmarks.

## Scenario matrix

| Demo scenario | Expected result | Exit code | Cloud behavior |
|---|---|---|---|
| `cloud-recovered` | `RECOVERED_CLOUD` | 0 | One simulated launch; simulated app verified |
| `local-recovered` | `RECOVERED_LOCAL` | 0 | No cloud launch |
| `blocked` | `BLOCKED` | 2 | Configuration error never escalates |
| `rollback` | `ROLLED_BACK` | 1 | Simulated unhealthy cloud instance terminated |
| `cleanup-required` | `CLEANUP_REQUIRED` | 1 | Simulated termination failure remains visible |
| `flapping` | `UNCONFIRMED_FAILURE` | 3 | No launch; observation window ends before confirmation |

## Reproduce locally

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
python3 -m ghostdock demo --scenario cloud-recovered
```

Development checks use Python 3.12 and the locked tool environment:

```bash
python -m coverage run -m unittest discover -s tests
python -m coverage report
make lint security
```

Coverage includes branch measurement and a minimum threshold. It is a regression signal, not a correctness guarantee. Simulated tests do not establish AWS availability, launch permissions, live response latency, or cloud costs.

## Acceptance evidence for the next live run

Complete [the AWS runbook](aws-runbook.md), then add a redacted evidence directory with:

1. Exact source commit, configuration fingerprint, image digest, AWS region, and test date.
2. A verified healthy local application before fault injection.
3. The deliberately injected failure and the first observed failed probe.
4. EC2 incident/ownership tags and SSM application-health verification.
5. The generated live incident journal, state snapshot, and report.
6. Explicit instance termination and root-volume cleanup confirmation.

Report application recovery latency using the metric definition in [architecture.md](architecture.md). Do not label it zero downtime or end-user recovery time: traffic switching is not implemented.

## Honest portfolio wording

> Built a Python/Bash container-recovery prototype with health-based escalation, conditional EC2/SSM integration, scoped cleanup, and verifiable incident receipts; validated recovery logic through isolated fault scenarios and a Docker integration workflow.

Only add a claim of successful live AWS recovery after the acceptance evidence exists. Do not present simulation timestamps, planned features, or scaffolded cloud code as measured production results.
