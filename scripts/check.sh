#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python3 -m unittest discover -s tests -v
bash -n runner.sh aws_runner.sh ghostdock/bootstrap.sh
python3 -m ghostdock check --config config/local.toml
