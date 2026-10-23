import json
import os
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from demo.app import Handler
from ghostdock.cloud import CloudNode
from ghostdock.config import Config
from ghostdock.local import LocalNode
from ghostdock.process import OperationError, Result
from tests.test_cloud import live_config


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://192.0.2.1/should-never-be-contacted")
        self.end_headers()

    def log_message(self, *_args):
        pass


class HealthTests(unittest.TestCase):
    def local_node(self, handler=QuietHandler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = replace(Config(), workload=replace(Config().workload, host_port=server.server_port))

        def docker(args, **_kwargs):
            if args[1] == "info":
                return Result(0, "test-version")
            return Result(
                0,
                json.dumps(
                    [
                        {
                            "State": {"Running": True},
                            "Config": {
                                "Image": config.workload.image,
                                "Labels": {
                                    "io.ghostdock.managed": "true",
                                    "io.ghostdock.config": config.fingerprint,
                                },
                            },
                        }
                    ]
                ),
            )

        return LocalNode(config, run=docker)

    def test_real_http_probe_matches_identity_and_clears_old_failure(self):
        node = self.local_node()
        node.failed_container = "old-image"
        self.assertTrue(node.observe().healthy)
        self.assertIsNone(node.failed_container)

    def test_real_http_503_is_unhealthy(self):
        node = self.local_node()
        with patch("demo.app.os.path.exists", return_value=True):
            self.assertEqual(node.observe().kind, "unhealthy")

    def test_local_probe_rejects_redirects(self):
        node = self.local_node(RedirectHandler)
        self.assertEqual(node.observe().kind, "unhealthy")

    def test_wrong_application_marker_is_not_healthy(self):
        node = self.local_node()
        node.config = replace(
            node.config, workload=replace(node.config.workload, expected_body="different-app")
        )
        # Keep the fake Docker ownership configuration in sync with this test's immutable spec.
        original = node.run

        def docker(args, **kwargs):
            result = original(args, **kwargs)
            if args[1] == "inspect":
                value = json.loads(result.stdout)
                value[0]["Config"]["Labels"]["io.ghostdock.config"] = node.config.fingerprint
                return Result(0, json.dumps(value))
            return result

        node.run = docker
        self.assertEqual(node.observe().kind, "unhealthy")

    def test_inspect_authorization_error_is_not_missing_container(self):
        node = LocalNode(Config(), run=Mock(return_value=Result(1, stderr="permission denied")))
        with self.assertRaisesRegex(OperationError, "permission denied"):
            node._inspect()

    def test_ssm_probe_script_rejects_redirect_status_and_wrong_body(self):
        node = CloudNode(live_config())
        with tempfile.TemporaryDirectory() as directory:
            fake_curl = Path(directory) / "curl"
            fake_curl.write_text('#!/usr/bin/env bash\nprintf "%s" "$CURL_RESULT"\n')
            fake_curl.chmod(0o755)
            for body, expected in [
                ('{"service": "ghostdock-demo"}\n200', 0),
                ('{"service": "ghostdock-demo"}\n302', 1),
                ('{"service": "other"}\n200', 1),
            ]:
                with self.subTest(body=body):
                    result = subprocess.run(
                        ["bash", "-c", node.health_command()],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=dict(
                            os.environ, PATH=directory + os.pathsep + os.environ["PATH"], CURL_RESULT=body
                        ),
                    )
                    self.assertEqual(result.returncode, expected)
                    self.assertEqual("GHOSTDOCK_HEALTH_OK" in result.stdout, expected == 0)

    def test_aws_preflight_rejects_public_ingress(self):
        node = CloudNode(live_config())
        node._json = Mock(
            side_effect=[
                {"Account": "123456789012"},
                {"Images": [{"Architecture": "x86_64", "Name": "al2023-ami-example"}]},
                {"Subnets": [{"VpcId": "vpc-test"}]},
                {"SecurityGroups": [{"VpcId": "vpc-test", "IpPermissions": [{"FromPort": 22}]}]},
            ]
        )
        with self.assertRaisesRegex(OperationError, "no inbound"):
            node.preflight()

    def test_aws_preflight_accepts_documented_topology(self):
        node = CloudNode(live_config())
        node._json = Mock(
            side_effect=[
                {"Account": "123456789012"},
                {"Images": [{"Architecture": "x86_64", "Name": "al2023-ami-example"}]},
                {"Subnets": [{"VpcId": "vpc-test"}]},
                {"SecurityGroups": [{"VpcId": "vpc-test", "IpPermissions": []}]},
            ]
        )
        self.assertEqual(node.preflight(), "123456789012")


if __name__ == "__main__":
    unittest.main()
