"""CLI: `hands serve` (default), `hands doctor`, and `hands audit verify`."""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import anyio

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hands")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio")
    doctor_p = sub.add_parser("doctor",
                              help="print resolved config and driver status")
    doctor_p.add_argument("--metrics", action="store_true",
                          help="append the metrics snapshot")
    sub.add_parser("permissions", help="show TCC grant status")
    audit_p = sub.add_parser("audit", help="audit log utilities")
    audit_sub = audit_p.add_subparsers(dest="audit_cmd", required=True)
    verify_p = audit_sub.add_parser("verify", help="verify hash chain")
    verify_p.add_argument("--path", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "audit" and args.audit_cmd == "verify":
        from .audit import AuditLogger
        path = args.path or load_config().security.audit_path
        if not path.exists():
            print(f"audit log not found: {path}")
            return 1
        ok, bad = AuditLogger.verify_chain(path)
        if ok:
            n = sum(1 for line in path.open() if line.strip())
            print(f"audit chain OK ({n} lines)")
            return 0
        print(f"audit chain BROKEN at line {bad}")
        return 1

    config = load_config()

    if args.command == "doctor":
        from .container import Container
        c = Container.build(config)
        info = {
            "config": config.model_dump(mode="json"),
            "driver": type(c.driver).__name__,
            "displays": [dataclasses.asdict(d) for d in c.driver.displays()],
            "tools": sorted(s.name for s in c.registry.list_specs()),
        }
        print(json.dumps(info, indent=2, default=str))
        if args.metrics:
            print(json.dumps(c.metrics.snapshot(), indent=2))
        return 0

    if args.command == "permissions":
        from .container import Container
        container = Container.build(config)
        perms = container.driver.permissions()
        print(f"screen_recording: "
              f"{'granted' if perms.screen_recording else 'MISSING'}")
        print("  grant via: x-apple.systempreferences:"
              "com.apple.preference.security?Privacy_ScreenCapture")
        print(f"accessibility:    "
              f"{'granted' if perms.accessibility else 'MISSING'}")
        print("  grant via: x-apple.systempreferences:"
              "com.apple.preference.security?Privacy_Accessibility")
        return 0 if (perms.screen_recording
                     and perms.accessibility) else 1

    from .server import run_server
    anyio.run(run_server, config)
    return 0
