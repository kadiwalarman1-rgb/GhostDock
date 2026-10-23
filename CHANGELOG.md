# Changelog

## 0.1.0 — 2026-10-23

- Replaced unconditional cloud-success messages with explicit application verification and terminal outcomes.
- Added validated TOML configuration, a local-first recovery policy, consecutive-failure detection, and explicit live mode.
- Implemented EC2/SSM recovery integration with incident tags, launch intent, client tokens, scoped cleanup, and expiry configuration.
- Added incident journals, integrity checks, standalone reports, and deterministic no-cloud scenarios.
- Added a non-root sample application, a network/IAM foundation, tests, CI, security analysis, and operational documentation.
- Retained the Bash exit-code exercise and the LocalNode/CloudNode OOP learning interface.

Live AWS acceptance evidence remains pending. This release does not implement traffic switching or stateful migration.
