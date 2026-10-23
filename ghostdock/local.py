"""Docker adapter for one managed, stateless HTTP workload."""

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from .process import OperationError, command, require_success


@dataclass(frozen=True)
class Observation:
    kind: str
    detail: str

    @property
    def healthy(self):
        return self.kind == "healthy"

    @property
    def recoverable(self):
        return self.kind in {"stopped", "unhealthy", "daemon_unavailable"}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "health redirects are not allowed", headers, fp)


class LocalNode:
    def __init__(self, config, run=command, clock=time.monotonic, sleep=time.sleep):
        self.config, self.run, self.clock, self.sleep = config, run, clock, sleep
        self.failed_container = None
        self.opener = build_opener(ProxyHandler({}), NoRedirects())

    def _inspect(self):
        result = self.run(["docker", "inspect", self.config.workload.name])
        if result.code:
            if "No such object" in result.stderr or "No such container" in result.stderr:
                return None
            require_success(result, "Docker inspect")
        try:
            container = json.loads(result.stdout)[0]
            labels = container["Config"].get("Labels") or {}
            if labels.get("io.ghostdock.managed") != "true":
                raise OperationError(
                    "container name belongs to an unmanaged container; refusing to change it"
                )
            if container["Config"]["Image"] != self.config.workload.image:
                raise OperationError(
                    "managed container uses a different image; remove it explicitly before changing config"
                )
            if labels.get("io.ghostdock.config") != self.config.fingerprint:
                raise OperationError(
                    "managed container configuration changed; inspect/remove it explicitly first"
                )
            return container
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise OperationError("unexpected Docker inspect response") from exc

    def deploy(self):
        w = self.config.workload
        require_success(self.run(["docker", "info", "--format", "{{.ServerVersion}}"]), "Docker preflight")
        existing = self._inspect()
        if existing is None:
            args = [
                "docker",
                "run",
                "--detach",
                "--name",
                w.name,
                "--label",
                "io.ghostdock.managed=true",
                "--label",
                f"io.ghostdock.config={self.config.fingerprint}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "10001:10001",
                "--pids-limit",
                "128",
                "--memory",
                "256m",
                # Container tmpfs specification, not a host temporary file.
                "--cpus",
                "0.5",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",  # nosec B108
                "--publish",
                f"127.0.0.1:{w.host_port}:{w.container_port}",
                w.image,
            ]
            result = self.run(args, timeout=120)
            require_success(result, "initial deployment (image/config errors do not trigger AWS)")
        elif not existing["State"]["Running"]:
            require_success(self.run(["docker", "start", w.name]), "start managed container")
        if not self._wait_healthy():
            raise OperationError(
                "initial application health check failed; fix the app before arming recovery"
            )
        self.failed_container = None

    def observe(self):
        try:
            daemon = self.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
            if daemon.code:
                return Observation("daemon_unavailable", "Docker daemon could not be reached")
            container = self._inspect()
            if container is None:
                return Observation("stopped", "managed container is absent")
            if not container["State"]["Running"]:
                code = container["State"].get("ExitCode", 0)
                if code in {125, 126, 127}:
                    return Observation(
                        "configuration_error", f"container exit {code}; operator investigation required"
                    )
                return Observation("stopped", f"HTTP service exited with code {code}")
            w = self.config.workload
            url = f"http://127.0.0.1:{w.host_port}{w.health_path}"
            with self.opener.open(url, timeout=3) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                if response.status == 200 and w.expected_body in body:
                    self.failed_container = None
                    return Observation("healthy", "HTTP 200 and expected application marker verified")
            return Observation("unhealthy", "HTTP response did not match the expected application")
        except (URLError, TimeoutError, OSError):
            return Observation("unhealthy", "application health endpoint unavailable")
        except OperationError as exc:
            return Observation("configuration_error", str(exc))

    def _wait_healthy(self):
        deadline = self.clock() + self.config.recovery.startup_seconds
        while self.clock() < deadline:
            observation = self.observe()
            if observation.healthy:
                return True
            if not observation.recoverable:
                return False
            self.sleep(1)
        return False

    def repair(self):
        self.failed_container = self.config.workload.image
        try:
            existing = self._inspect()
            if existing is None:
                # There is nothing to restart; the engine applies the configured escalation policy.
                return False
            result = self.run(["docker", "restart", self.config.workload.name], timeout=45)
            return result.code == 0 and self._wait_healthy()
        except OperationError:
            return False

    def failover_to(self, cloud_node, receipt):
        return cloud_node.provision(receipt)

    def __rshift__(self, cloud_request):
        """Educational compatibility wrapper: local >> (cloud, receipt)."""
        cloud, receipt = cloud_request
        return self.failover_to(cloud, receipt)
