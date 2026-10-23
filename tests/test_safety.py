import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from ghostdock.cli import main
from ghostdock.config import Config, load_config
from ghostdock.evidence import Receipt, verify_receipt, workload_lock
from ghostdock.local import LocalNode
from ghostdock.process import OperationError, Result, command
from ghostdock.report import render_report
from tests.test_cloud import live_config

ROOT = Path(__file__).resolve().parents[1]


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_unknown_config_key_is_rejected(self):
        file = self.path / "bad.toml"
        file.write_text("[recovery]\nfailure_thresold = 1\n")
        with self.assertRaisesRegex(ValueError, "unknown recovery"):
            load_config(file)

    def test_live_requires_digest(self):
        with self.assertRaisesRegex(ValueError, "sha256"):
            Config().validate(live=True)

    def test_live_configuration_is_accepted(self):
        self.assertEqual(live_config().validate(live=True), live_config())

    def test_untrusted_image_syntax_is_rejected(self):
        for image in ("-v", "nginx;id", "x$(id)", "image with space"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                replace(Config(), workload=replace(Config().workload, image=image)).validate()

    def test_bad_ports_and_ttl_are_rejected(self):
        for value in (80, 70000, True, "8080"):
            with self.subTest(port=value), self.assertRaises(ValueError):
                replace(Config(), workload=replace(Config().workload, host_port=value)).validate()
        with self.assertRaisesRegex(ValueError, "TTL"):
            replace(Config(), recovery=replace(Config().recovery, ttl_minutes=10)).validate()

    def test_workload_lock_excludes_a_second_controller(self):
        with workload_lock(self.path, "demo"):
            with self.assertRaisesRegex(RuntimeError, "another controller"):
                with workload_lock(self.path, "demo"):
                    self.fail("a second lock must not be acquired")

    def test_receipt_detects_event_and_state_tampering(self):
        receipt = Receipt(self.path, Config(), simulation=True)
        receipt.record("BLOCKED", "configuration error")
        verify_receipt(receipt.path)
        file = receipt.path / "events.jsonl"
        original = file.read_text()
        file.write_text(original.replace("configuration error", "everything fine"))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_receipt(receipt.path)
        file.write_text(original)
        state_file = receipt.path / "state.json"
        state = json.loads(state_file.read_text())
        state["status"] = "RECOVERED_CLOUD"
        state_file.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "state and journal"):
            verify_receipt(receipt.path)

    def test_receipt_permissions_and_reopen(self):
        receipt = Receipt(self.path, Config())
        for name in ("state.json", "events.jsonl"):
            self.assertEqual((receipt.path / name).stat().st_mode & 0o777, 0o600)
        opened = Receipt.reopen(receipt.path)
        opened.record("CLEANED", "termination verified")
        state, events = verify_receipt(receipt.path)
        self.assertEqual(state["status"], "CLEANED")
        self.assertEqual(len(events), 2)

    def test_html_escapes_observations_and_marks_simulation(self):
        receipt = Receipt(self.path, Config(), simulation=True)
        receipt.record("BLOCKED", '<script>alert("x")</script>')
        output = render_report(receipt.path, self.path / "report.html")
        html = output.read_text()
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("SIMULATION", html)
        self.assertIn("Not switched", html)

    def test_live_requires_both_flags_before_docker_or_aws(self):
        with patch("ghostdock.cli.LocalNode") as local, patch("ghostdock.cli.CloudNode") as cloud:
            for flags in (["--live"], ["--approve-cloud"]):
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["watch", "--config", str(ROOT / "config/local.toml"), *flags]), 1)
            local.assert_not_called()
            cloud.assert_not_called()

    def test_simulation_cleanup_never_calls_aws(self):
        receipt = Receipt(self.path, Config(), simulation=True)
        with patch("ghostdock.cli.CloudNode") as cloud, redirect_stderr(io.StringIO()):
            self.assertEqual(main(["cleanup", str(receipt.path), "--execute"]), 1)
            cloud.assert_not_called()

    def test_cleanup_defaults_to_plan(self):
        receipt = Receipt(self.path, Config())
        output = io.StringIO()
        with patch("ghostdock.cli.CloudNode") as cloud, redirect_stdout(output):
            self.assertEqual(main(["cleanup", str(receipt.path)]), 0)
            cloud.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["mode"], "plan-only")

    def test_process_preserves_stderr_and_exit_status(self):
        result = command([sys.executable, "-c", "import sys; print('error', file=sys.stderr); sys.exit(125)"])
        self.assertEqual(result.code, 125)
        self.assertEqual(result.stderr.strip(), "error")

    def test_missing_executable_is_an_operation_error(self):
        with self.assertRaisesRegex(OperationError, "not found"):
            command([str(self.path / "does-not-exist")])

    def test_subprocess_timeout_is_explicit(self):
        with self.assertRaisesRegex(OperationError, "outcome may be unknown"):
            command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)

    def test_bash_runner_preserves_docker_exit_125(self):
        docker = self.path / "docker"
        docker.write_text('#!/usr/bin/env bash\necho "image missing" >&2\nexit 125\n')
        docker.chmod(0o755)
        result = subprocess.run(
            ["bash", str(ROOT / "runner.sh"), "missing-image"],
            env=dict(os.environ, PATH=str(self.path) + os.pathsep + os.environ["PATH"]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 125)
        self.assertIn("image missing", result.stderr)

    def test_bash_runner_rejects_missing_or_option_argument(self):
        for args in ([], ["--privileged"]):
            result = subprocess.run(
                ["bash", str(ROOT / "runner.sh"), *args], capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 2)

    def test_wrapper_works_outside_repository_directory(self):
        result = subprocess.run(
            ["bash", str(ROOT / "aws_runner.sh"), "--version"],
            cwd=self.temp.name,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_docker_ownership_is_checked(self):
        container = {"Config": {"Image": "ghostdock-demo:local", "Labels": {}}, "State": {"Running": True}}
        node = LocalNode(Config(), run=Mock(return_value=Result(0, json.dumps([container]))))
        with self.assertRaisesRegex(OperationError, "unmanaged"):
            node._inspect()

    def test_docker_daemon_error_is_recoverable_after_startup(self):
        node = LocalNode(Config(), run=Mock(return_value=Result(125, stderr="cannot connect")))
        observation = node.observe()
        self.assertEqual(observation.kind, "daemon_unavailable")
        self.assertTrue(observation.recoverable)

    def test_operator_wrapper_delegates_to_named_method(self):
        node = LocalNode(Config())
        cloud = Mock()
        cloud.provision.return_value = "i-example"
        receipt = object()
        self.assertEqual(node >> (cloud, receipt), "i-example")
        cloud.provision.assert_called_once_with(receipt)


if __name__ == "__main__":
    unittest.main()
