"""CLI with explicit live mode; the default never provisions AWS resources."""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .cloud import CloudNode
from .config import load_config
from .demo import SCENARIOS, run_demo
from .engine import Engine
from .evidence import Receipt, verify_receipt, workload_lock
from .local import LocalNode
from .report import render_report


def parser():
    root = argparse.ArgumentParser(description="GhostDock — evidence-driven container recovery")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="run deterministic simulations without Docker/AWS")
    demo.add_argument("--scenario", choices=SCENARIOS, default="cloud-recovered")
    demo.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    for name in ("watch", "plan", "check"):
        entry = sub.add_parser(name)
        entry.add_argument("--config", type=Path, default=Path("config/local.toml"))
        if name == "watch":
            entry.add_argument("--live", action="store_true", help="enable real EC2/SSM recovery")
            entry.add_argument(
                "--approve-cloud", action="store_true", help="acknowledge billable cloud resources"
            )
            entry.add_argument(
                "--max-checks", type=int, default=0, help="0 watches until an incident or Ctrl+C"
            )
            entry.add_argument("--state-dir", type=Path, default=Path(".ghostdock"))
        elif name == "check":
            entry.add_argument("--aws", action="store_true", help="also check AWS prerequisites (read-only)")
    for name in ("report", "verify", "cleanup"):
        entry = sub.add_parser(name)
        entry.add_argument("receipt", type=Path)
        if name == "report":
            entry.add_argument("--output", type=Path, default=Path("artifacts/recovery-report.html"))
        if name == "cleanup":
            entry.add_argument("--config", type=Path, default=Path("config/aws.toml"))
            entry.add_argument(
                "--execute", action="store_true", help="terminate only instances owned by this incident"
            )
            entry.add_argument("--state-dir", type=Path, default=Path(".ghostdock"))
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "demo":
            code, receipt, _ = run_demo(args.output, args.scenario)
            report = render_report(receipt.path, receipt.path / "report.html")
            print(f"SIMULATION complete. Receipt: {receipt.path}\nReport: {report}")
            return code
        if args.command == "report":
            print(render_report(args.receipt, args.output))
            return 0
        if args.command == "verify":
            state, events = verify_receipt(args.receipt)
            print(json.dumps({"integrity": "consistent", "event_count": len(events), **state}, indent=2))
            return 0
        if args.command == "cleanup":
            state, _ = verify_receipt(args.receipt)
            if not args.execute:
                print(
                    json.dumps(
                        {
                            "mode": "plan-only",
                            "incident": state["incident_id"],
                            "instance": state["instance_id"],
                            "region": state["region"],
                            "next_step": "repeat with --execute to terminate owned resources",
                        },
                        indent=2,
                    )
                )
                return 0
            if state["simulation"]:
                raise ValueError("simulated receipts have no AWS resources to clean up")
            config = load_config(args.config)
            if config.workload.name != state["workload"]:
                raise ValueError("configuration workload does not match receipt")
            with workload_lock(args.state_dir, state["workload"]):
                receipt = Receipt.reopen(args.receipt)
                ids = CloudNode(config).cleanup(state)
                receipt.record(
                    "CLEANED", "owned cloud instance termination verified", terminated_instances=ids
                )
                render_report(receipt.path, receipt.path / "report.html")
            return 0
        config = load_config(args.config)
        if args.command == "plan":
            print(
                json.dumps(
                    {
                        "workload": config.workload.name,
                        "image": config.workload.image,
                        "region": config.aws.region,
                        "config_sha256": config.fingerprint,
                        "policy": "consecutive failures → one local repair → opt-in EC2 recovery",
                        "cloud_default": "disabled",
                        "traffic_switching": False,
                        "ttl_minutes": config.recovery.ttl_minutes,
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "check":
            if args.aws:
                account = CloudNode(config).preflight()
                print(
                    f"AWS read-only checks passed for account {account}; launch permissions/quotas are not guaranteed."
                )
            else:
                print("Configuration is valid. Docker/AWS connectivity has not been checked.")
            return 0
        if args.live != args.approve_cloud:
            raise ValueError("real cloud recovery requires both --live and --approve-cloud")
        if args.max_checks < 0:
            raise ValueError("--max-checks must be zero or positive")
        config.validate(live=args.live)
        with workload_lock(args.state_dir, config.workload.name):
            receipt = Receipt(args.state_dir / config.workload.name, config)
            print(f"Receipt: {receipt.path}", flush=True)
            local = LocalNode(config)
            try:
                local.deploy()
            except Exception as exc:
                receipt.record("BLOCKED", f"initial deployment did not verify: {exc}")
                render_report(receipt.path, receipt.path / "report.html")
                return 2
            cloud = CloudNode(config) if args.live else None
            result = Engine(config, local, cloud, receipt).watch(args.max_checks)
            render_report(receipt.path, receipt.path / "report.html")
            return result
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
