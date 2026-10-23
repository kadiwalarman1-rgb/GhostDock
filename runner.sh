#!/usr/bin/env bash
# Original learning milestone: preserve the Docker CLI exit status exactly.
set -uo pipefail
if [ "$#" -ne 1 ] || [[ "$1" == -* ]]; then
    echo "Usage: bash runner.sh IMAGE_REFERENCE" >&2
    exit 2
fi
docker run --rm "$1"
exit_code=$?
if [ "$exit_code" -ne 0 ]; then
    echo "Docker finished with exit code $exit_code. No cloud action was taken." >&2
fi
exit "$exit_code"
