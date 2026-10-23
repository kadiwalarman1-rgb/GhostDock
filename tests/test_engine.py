import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from ghostdock.config import Config
from ghostdock.demo import SimulatedCloud, SimulatedLocal, VirtualClock, run_demo
from ghostdock.engine import Engine
from ghostdock.evidence import Receipt, verify_receipt
from ghostdock.local import Observation


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_cloud_recovery_is_health_verified_and_explicitly_simulated(self):
        code, receipt, cloud = run_demo(self.temp.name, "cloud-recovered")
        state, events = verify_receipt(receipt.path)
        self.assertEqual(code, 0)
        self.assertEqual(state["status"], "RECOVERED_CLOUD")
        self.assertEqual(cloud.launches, 1)
        self.assertEqual(cloud.cleanups, 0)
        self.assertTrue(state["simulation"])
        self.assertFalse(state["traffic_switched"])
        self.assertGreater(state["recovery_seconds"], 0)
        self.assertTrue(any(e["state"]["status"] == "VERIFYING_CLOUD" for e in events))

    def test_local_repair_prevents_cloud_launch(self):
        code, receipt, cloud = run_demo(self.temp.name, "local-recovered")
        self.assertEqual((code, receipt.state["status"], cloud.launches), (0, "RECOVERED_LOCAL", 0))

    def test_bad_configuration_never_launches_cloud(self):
        code, receipt, cloud = run_demo(self.temp.name, "blocked")
        self.assertEqual((code, receipt.state["status"], cloud.launches), (2, "BLOCKED", 0))

    def test_flapping_resets_consecutive_failure_counter(self):
        code, receipt, cloud = run_demo(self.temp.name, "flapping")
        self.assertEqual(cloud.launches, 0)
        self.assertEqual(code, 3)
        self.assertEqual(receipt.state["status"], "UNCONFIRMED_FAILURE")

    def test_unhealthy_cloud_is_rolled_back_without_success(self):
        code, receipt, cloud = run_demo(self.temp.name, "rollback")
        self.assertEqual(code, 1)
        self.assertEqual(cloud.cleanups, 1)
        self.assertEqual(receipt.state["status"], "ROLLED_BACK")
        _, events = verify_receipt(receipt.path)
        self.assertFalse(any(e["state"]["status"] == "RECOVERED_CLOUD" for e in events))

    def test_failed_rollback_is_explicit(self):
        code, receipt, _ = run_demo(self.temp.name, "cleanup-required")
        self.assertEqual(code, 1)
        self.assertEqual(receipt.state["status"], "CLEANUP_REQUIRED")

    def build(self, cloud=True):
        clock = VirtualClock()
        config = Config(recovery=replace(Config().recovery, poll_seconds=1))
        receipt = Receipt(Path(self.temp.name), config, simulation=True, clock=clock.now)
        local = SimulatedLocal("cloud-recovered", clock)
        node = SimulatedCloud("cloud-recovered", clock) if cloud else None
        return Engine(config, local, node, receipt, clock=clock.now, sleep=clock.sleep)

    def test_default_has_no_cloud_backend(self):
        engine = self.build(cloud=False)
        self.assertEqual(engine.watch(8), 3)
        self.assertEqual(engine.receipt.state["status"], "CLOUD_DISABLED")
        self.assertFalse(engine.receipt.state["cloud_requested"])

    def test_healthy_window_never_calls_cloud(self):
        engine = self.build()
        engine.local.observe = Mock(return_value=Observation("healthy", "ok"))
        self.assertEqual(engine.watch(3), 0)
        self.assertEqual(engine.cloud.launches, 0)

    def test_insufficient_observations_do_not_claim_health(self):
        engine = self.build()
        engine.local.observe = Mock(return_value=Observation("unhealthy", "HTTP 503"))
        self.assertEqual(engine.watch(1), 3)
        self.assertEqual(engine.receipt.state["status"], "UNCONFIRMED_FAILURE")

    def test_preflight_failure_never_launches_or_cleans_up(self):
        engine = self.build()
        engine.cloud.preflight = Mock(side_effect=RuntimeError("invalid AMI"))
        self.assertEqual(engine.watch(8), 1)
        self.assertEqual(engine.cloud.launches, 0)
        self.assertEqual(engine.cloud.cleanups, 0)

    def test_ctrl_c_after_launch_attempts_scoped_cleanup(self):
        engine = self.build()
        engine.cloud.verify = Mock(side_effect=KeyboardInterrupt())
        self.assertEqual(engine.watch(8), 130)
        self.assertEqual(engine.cloud.cleanups, 1)


if __name__ == "__main__":
    unittest.main()
