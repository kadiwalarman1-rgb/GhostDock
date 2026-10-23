"""Validated configuration; no credentials belong in configuration files."""

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Workload:
    name: str = "ghostdock-demo"
    image: str = "ghostdock-demo:local"
    container_port: int = 8080
    host_port: int = 8080
    health_path: str = "/healthz"
    expected_body: str = '"service": "ghostdock-demo"'


@dataclass(frozen=True)
class Recovery:
    failure_threshold: int = 3
    poll_seconds: int = 5
    startup_seconds: int = 30
    cloud_timeout_seconds: int = 600
    ttl_minutes: int = 30


@dataclass(frozen=True)
class AWS:
    region: str = "ap-south-1"
    ami_id: str = ""
    subnet_id: str = ""
    security_group_id: str = ""
    instance_profile: str = ""
    instance_type: str = "t3.micro"
    public_ip: bool = False
    profile: str = ""


@dataclass(frozen=True)
class Config:
    workload: Workload = Workload()
    recovery: Recovery = Recovery()
    aws: AWS = AWS()

    @property
    def fingerprint(self):
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def validate(self, live=False):
        w, r, a = self.workload, self.recovery, self.aws
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,39}", w.name):
            raise ValueError("workload.name must be 3–40 lowercase letters, digits or hyphens")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,299}", w.image):
            raise ValueError("image must be an image reference without whitespace or shell syntax")
        for value in (w.host_port, w.container_port):
            if type(value) is not int or not 1024 <= value <= 65535:
                raise ValueError("ports must be integers between 1024 and 65535")
        if not re.fullmatch(r"/[a-zA-Z0-9/_-]*", w.health_path):
            raise ValueError("health_path must be a simple absolute HTTP path")
        if not isinstance(w.expected_body, str) or not 1 <= len(w.expected_body) <= 200:
            raise ValueError("expected_body must contain 1–200 characters")
        bounds = {
            "failure_threshold": (1, 10),
            "poll_seconds": (1, 60),
            "startup_seconds": (5, 120),
            "cloud_timeout_seconds": (60, 1800),
            "ttl_minutes": (10, 120),
        }
        for key, (low, high) in bounds.items():
            value = getattr(r, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"recovery.{key} must be between {low} and {high}")
        if r.ttl_minutes * 60 <= r.cloud_timeout_seconds + 120:
            raise ValueError("TTL must exceed cloud timeout by more than 120 seconds")
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", a.region):
            raise ValueError("invalid AWS region")
        if type(a.public_ip) is not bool:
            raise ValueError("aws.public_ip must be true or false")
        if a.instance_type not in {"t3.micro", "t3.small", "t2.micro"}:
            raise ValueError("instance_type must be t3.micro, t3.small or t2.micro")
        if a.profile and not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", a.profile):
            raise ValueError("invalid AWS CLI profile name")
        if live:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]+@sha256:[a-f0-9]{64}", w.image):
                raise ValueError("live recovery requires a public registry image pinned by sha256 digest")
            for key, prefix in (("ami_id", "ami"), ("subnet_id", "subnet"), ("security_group_id", "sg")):
                if not re.fullmatch(prefix + r"-[a-f0-9]{8,17}", getattr(a, key)):
                    raise ValueError(f"set a valid aws.{key} before live recovery")
            if not re.fullmatch(r"[a-zA-Z0-9+=,.@_-]{1,128}", a.instance_profile):
                raise ValueError("set aws.instance_profile to an EC2 instance profile name")
        return self


def load_config(path):
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    unknown = set(data) - {"workload", "recovery", "aws"}
    if unknown:
        raise ValueError(f"unknown configuration sections: {sorted(unknown)}")
    parts = []
    for name, cls in (("workload", Workload), ("recovery", Recovery), ("aws", AWS)):
        values = data.get(name, {})
        if not isinstance(values, dict):
            raise ValueError(f"{name} must be a TOML table")
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown {name} settings: {sorted(unknown)}")
        parts.append(cls(**values))
    return Config(*parts).validate()
