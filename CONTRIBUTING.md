# Contributing

Keep GhostDock small enough to understand and honest about what it verifies.

1. Describe a concrete failure or operational need before adding a feature.
2. Add a regression test for changed recovery/ownership behavior. Unit tests must not create AWS resources.
3. Run `python3 -m unittest discover -s tests -v`; use the Python 3.12 tool environment for `make lint security`.
4. For Docker changes, run `python3 scripts/docker_integration.py` after building the demo image.
5. Update the runbook, threat model, and validation scope when behavior changes.

Use a dedicated branch and explain the resulting behavior in the pull request. Never commit local credentials, real account configuration, or unredacted live receipts. Simulation artifacts must be clearly labeled.

The project uses AI-assisted development. Contributors should review, understand, and test generated code; include the commands actually executed and their results in the PR.
