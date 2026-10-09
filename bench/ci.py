#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Unattended benchmark run for CI: one named run across every suite.

``python -m bench.ci run results/ci/bench.db --profile quick --name ci``
configures every selected suite first, then samples them under one name.
A named run snapshots every suite's settings when it is created, so the
first suite creates it and the others join with ``--amend``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ALL_SERVERS = ("open62541", "o6-python", "asyncua", "ua-dotnet", "node-opcua", "milo", "s2opc", "gopcua")
# The subscription suite needs a client in the SDK, which the server-only workers lack.
SUBSCRIPTION_SERVERS = ("open62541", "o6-python", "asyncua", "node-opcua", "ua-dotnet")

# Each profile: suite -> (option settings, sample command arguments).
# JIT servers (node-opcua, .NET, Java) need thousands of calls to reach a
# steady state, so warmup stays high even in the quick profile.
PROFILES: dict[str, dict[str, tuple[dict[str, list[str]], list[str]]]] = {
    "quick": {
        "throughput": (
            {
                "operation": ["read", "write"],
                "mode": ["sync", "async"],
                "clients": ["1"],
                "payload": ["scalar", "batch:100"],
                "security": ["None"],
                "warmup": ["3000"],
                "iterations": ["5000"],
                "max_values": ["300000"],
            },
            ["1"],
        ),
        "server_capacity": ({"budget_seconds": ["45"], "max_clients": ["8"]}, ["3"]),
    },
    "standard": {
        "throughput": (
            {
                "operation": ["read", "write"],
                "mode": ["sync", "async"],
                "clients": ["1", "3"],
                "payload": ["scalar", "batch:100", "batch:1000", "array:1000"],
                "security": ["None", "Basic256Sha256"],
                "warmup": ["3000"],
                "iterations": ["5000"],
                "max_values": ["2000000"],
            },
            ["1"],
        ),
        "server_capacity": ({"budget_seconds": ["90"], "max_clients": ["16"]}, ["3"]),
        "server_limits": (
            {"step_seconds": ["5"], "max_clients": ["32"], "max_outstanding": ["64"]},
            ["7"],
        ),
        "subscription": (
            {"measurement": ["server"], "sampling_ms": ["100"], "publishing_ms": ["1000"]},
            ["1"],
        ),
    },
}


def _servers_for(suite: str, servers: list[str]) -> dict[str, list[str]]:
    if suite == "throughput":
        return {"pair": [f"open62541:{server}" for server in servers]}
    if suite == "subscription":
        return {"implementation": [server for server in servers if server in SUBSCRIPTION_SERVERS]}
    return {"implementation": servers}


def _call(arguments: list[str]) -> int:
    print("+ " + " ".join(arguments), flush=True)
    return subprocess.call([sys.executable, *arguments])


def run(database: Path, profile: str, name: str, servers: list[str]) -> int:
    if database.exists():
        print(f"{database} already exists; CI runs start from an empty database", file=sys.stderr)
        return 1
    database.parent.mkdir(parents=True, exist_ok=True)
    suites = PROFILES[profile]
    for suite, (settings, _) in suites.items():
        module = f"bench.{suite}"
        if _call(["-m", module, "new", str(database)]):
            return 1
        for option, values in {**settings, **_servers_for(suite, servers)}.items():
            if values and _call(["-m", module, "config", str(database), option, *values]):
                return 1
    status = 0
    for index, (suite, (_, sample)) in enumerate(suites.items()):
        command = ["-m", f"bench.{suite}", "sample", *sample, str(database), "--name", name]
        if index:
            command.append("--amend")
        # A failing SDK must not stop the others: the report and the summary record it.
        status = _call(command) or status
    return status


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Configure and sample every suite of a profile")
    run_parser.add_argument("db", type=Path)
    run_parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    run_parser.add_argument("--name", default="ci")
    run_parser.add_argument(
        "--servers",
        default=os.environ.get("BENCH_SERVERS", ",".join(ALL_SERVERS)),
        help="comma-separated server SDKs to measure (default: all)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    servers = [server.strip() for server in args.servers.split(",") if server.strip()]
    unknown = sorted(set(servers) - set(ALL_SERVERS))
    if unknown:
        print(f"unknown servers: {', '.join(unknown)}", file=sys.stderr)
        return 2
    return run(args.db, args.profile, args.name, servers)


if __name__ == "__main__":
    raise SystemExit(main())
