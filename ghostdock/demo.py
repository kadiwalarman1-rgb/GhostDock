"""Deterministic fault scenarios: the real engine, simulated infrastructure."""

from dataclasses import replace

from .config import Config
from .engine import Engine
from .evidence import Receipt
from .local import Observation
from .process import OperationError

SCENARIOS = ("cloud-recovered", "local-recovered", "blocked", "rollback", "cleanup-required", "flapping")


class VirtualClock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class SimulatedLocal:
    def __init__(self, scenario, clock):
        self.scenario, self.clock, self.checks = scenario, clock, 0

    def observe(self):
        self.checks += 1
        if self.scenario == "blocked":
            return Observation(
                "configuration_error", "SIMULATION: invalid image/command; cloud launch blocked"
            )
        if self.checks == 1 or (self.scenario == "flapping" and self.checks % 3 == 0):
            return Observation("healthy", "SIMULATION: expected HTTP response")
        return Observation("unhealthy", "SIMULATION: application health probe failed")

    def repair(self):
        self.clock.sleep(2)
        return self.scenario == "local-recovered"

    def failover_to(self, cloud, receipt):
        return cloud.provision(receipt)


class SimulatedCloud:
    def __init__(self, scenario, clock):
        self.scenario, self.clock, self.launches, self.cleanups = scenario, clock, 0, 0

    def preflight(self):
        return "SIMULATED_ACCOUNT"

    def provision(self, receipt):
        self.launches += 1
        receipt.record("PROVISIONING", "SIMULATION: no AWS API call", cloud_requested=True)
        self.clock.sleep(12)
        receipt.record(
            "VERIFYING_CLOUD", "SIMULATION: virtual instance allocated", instance_id="simulated-ec2"
        )
        return "simulated-ec2"

    def verify(self, instance_id):
        self.clock.sleep(5)
        return self.scenario not in {"rollback", "cleanup-required"}

    def cleanup(self, state):
        self.cleanups += 1
        if self.scenario == "cleanup-required":
            raise OperationError("SIMULATION: termination denied; operator cleanup required")
        return ["simulated-ec2"]


def run_demo(output, scenario):
    if scenario not in SCENARIOS:
        raise ValueError("unknown demo scenario")
    clock = VirtualClock()
    config = Config(recovery=replace(Config().recovery, poll_seconds=1))
    receipt = Receipt(output, config, simulation=True, clock=clock.now)
    local, cloud = SimulatedLocal(scenario, clock), SimulatedCloud(scenario, clock)
    result = Engine(config, local, cloud, receipt, sleep=clock.sleep, clock=clock.now).watch(max_checks=8)
    return result, receipt, cloud
