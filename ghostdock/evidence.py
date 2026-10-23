"""Private, durable incident receipts with a locally verifiable event hash chain."""

import fcntl
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def workload_lock(root, workload):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / f"{workload}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another controller already owns this workload lock") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Receipt:
    def __init__(self, root, config, simulation=False, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.incident = str(uuid.uuid4())
        self.path = Path(root) / self.incident
        self.path.mkdir(parents=True, mode=0o700)
        self.sequence = 0
        self.head = "0" * 64
        self.state = {
            "schema_version": 1,
            "incident_id": self.incident,
            "workload": config.workload.name,
            "config_sha256": config.fingerprint,
            "region": config.aws.region,
            "simulation": simulation,
            "status": "CREATED",
            "cloud_requested": False,
            "instance_id": None,
            "traffic_switched": False,
            "recovery_seconds": None,
        }
        self.record("CREATED", "incident receipt initialized")

    def record(self, status, detail="", **changes):
        self.state.update(changes)
        self.state["status"] = status
        event = {
            "sequence": self.sequence,
            "at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(self.clock() - self.started, 6),
            "detail": detail,
            "state": dict(self.state),
            "previous_hash": self.head,
        }
        digest = hashlib.sha256(canonical(event).encode()).hexdigest()
        event["hash"] = digest
        fd = os.open(self.path / "events.jsonl", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(canonical(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.head = digest
        self.sequence += 1
        atomic_json(self.path / "state.json", dict(self.state, last_event_hash=self.head))
        logging.getLogger("ghostdock").info("%s | %s", status, detail)

    @classmethod
    def reopen(cls, path):
        state, events = verify_receipt(path)
        receipt = cls.__new__(cls)
        receipt.clock = time.monotonic
        receipt.started = receipt.clock() - events[-1]["elapsed_seconds"]
        receipt.incident = state["incident_id"]
        receipt.path = Path(path)
        receipt.sequence = len(events)
        receipt.head = events[-1]["hash"]
        receipt.state = state
        return receipt


def verify_receipt(path):
    path = Path(path)
    previous = "0" * 64
    events = []
    with (path / "events.jsonl").open() as handle:
        for sequence, line in enumerate(handle):
            event = json.loads(line)
            digest = event.pop("hash")
            if event["sequence"] != sequence or event["previous_hash"] != previous:
                raise ValueError("receipt sequence or previous hash mismatch")
            if hashlib.sha256(canonical(event).encode()).hexdigest() != digest:
                raise ValueError("receipt event hash mismatch")
            previous = digest
            events.append(dict(event, hash=digest))
    if not events:
        raise ValueError("empty incident receipt")
    state = json.loads((path / "state.json").read_text())
    if state.pop("last_event_hash") != previous or state != events[-1]["state"]:
        raise ValueError("state and journal differ; incident may have been interrupted or modified")
    return state, events
