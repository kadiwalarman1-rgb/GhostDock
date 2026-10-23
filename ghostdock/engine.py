"""One incident, one bounded local repair, then optional verified cold recovery."""

import time


class Engine:
    def __init__(self, config, local, cloud, receipt, sleep=time.sleep, clock=time.monotonic):
        self.config, self.local, self.cloud, self.receipt = config, local, cloud, receipt
        self.sleep, self.clock = sleep, clock

    def watch(self, max_checks=0):
        failures, checks, failure_started = 0, 0, None
        r, receipt = self.config.recovery, self.receipt
        receipt.record("WATCHING", "watching an already verified local application")
        try:
            while not max_checks or checks < max_checks:
                observed = self.local.observe()
                checks += 1
                if observed.healthy:
                    failures, failure_started = 0, None
                elif not observed.recoverable:
                    receipt.record("BLOCKED", observed.detail)
                    return 2
                else:
                    if failure_started is None:
                        failure_started = self.clock()
                    failures += 1
                    receipt.record("SUSPECT", observed.detail, consecutive_failures=failures)
                    if failures >= r.failure_threshold:
                        receipt.record("REPAIRING_LOCAL", "trying one bounded local restart")
                        if self.local.repair():
                            receipt.record(
                                "RECOVERED_LOCAL",
                                "local application health verified",
                                recovery_seconds=round(self.clock() - failure_started, 6),
                            )
                            return 0
                        if self.cloud is None:
                            receipt.record(
                                "CLOUD_DISABLED", "local repair failed; no AWS resources were requested"
                            )
                            return 3
                        receipt.record("CLOUD_PREFLIGHT", "checking cloud prerequisites")
                        self.cloud.preflight()
                        instance_id = self.local.failover_to(self.cloud, receipt)
                        if not self.cloud.verify(instance_id):
                            raise RuntimeError("cloud application health did not verify before the deadline")
                        receipt.record(
                            "RECOVERED_CLOUD",
                            "application verified through SSM; traffic switching is out of scope",
                            instance_id=instance_id,
                            recovery_seconds=round(self.clock() - failure_started, 6),
                        )
                        return 0
                if not max_checks or checks < max_checks:
                    self.sleep(r.poll_seconds)
            if failures:
                receipt.record(
                    "UNCONFIRMED_FAILURE", "window ended before the failure threshold; health is not verified"
                )
                return 3
            receipt.record("OBSERVATION_COMPLETE", "observation window ended with local health verified")
            return 0
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            detail = "operator interrupted recovery" if interrupted else str(exc)
            receipt.record("FAILED", detail)
            if receipt.state["cloud_requested"] and self.cloud is not None:
                try:
                    ids = self.cloud.cleanup(receipt.state)
                    receipt.record("ROLLED_BACK", "cloud termination verified", terminated_instances=ids)
                except Exception as cleanup_error:
                    receipt.record("CLEANUP_REQUIRED", str(cleanup_error))
            return 130 if interrupted else 1
