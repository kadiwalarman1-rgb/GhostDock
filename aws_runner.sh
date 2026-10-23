#!/usr/bin/env bash
# Bash entry point; live provisioning still requires the explicit CLI flags.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/main.py" "$@"
