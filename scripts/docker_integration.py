"""Real Docker recovery check, used by CI. Requires an already built demo image."""

import json
import subprocess
import sys
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ghostdock.config import Config  # noqa: E402
from ghostdock.engine import Engine  # noqa: E402
from ghostdock.evidence import Receipt, verify_receipt  # noqa: E402
from ghostdock.local import LocalNode  # noqa: E402


def main():
    name = "gd-ci-" + uuid.uuid4().hex[:12]
    config = Config(
        workload=replace(Config().workload, name=name, host_port=18080),
        recovery=replace(Config().recovery, failure_threshold=1, poll_seconds=1),
    )
    try:
        node = LocalNode(config)
        node.deploy()
        assert node.observe().healthy, "initial HTTP health failed"
        subprocess.run(["docker", "kill", name], check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Receipt(tmp, config)
            code = Engine(config, node, None, receipt).watch(max_checks=3)
            state, _ = verify_receipt(receipt.path)
            assert code == 0 and state["status"] == "RECOVERED_LOCAL", state
            assert node.observe().healthy, "post-restart HTTP health failed"
            print(
                json.dumps(
                    {
                        "result": "PASS",
                        "test": "real Docker kill → local restart → HTTP verification",
                        "cloud_requested": state["cloud_requested"],
                    },
                    indent=2,
                )
            )
    finally:
        # Random per-test name; this script removes only its own test container.
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True)


if __name__ == "__main__":
    main()
