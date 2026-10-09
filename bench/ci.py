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
import json
import os
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
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
        # The default 90 s budget: node-opcua needed more than 45 s on a hosted runner.
        "server_capacity": ({"budget_seconds": ["90"], "max_clients": ["8"]}, ["3"]),
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
    for index, (suite, (_, sample)) in enumerate(suites.items()):
        command = ["-m", f"bench.{suite}", "sample", *sample, str(database), "--name", name]
        if index:
            command.append("--amend")
        # A suite exits nonzero when one SDK fails or stays unconfirmed (common on a
        # shared runner). The report and the summary record which; the run goes on.
        if status := _call(command):
            print(f"::warning title={suite}::{suite} exited with {status}: see its report for the SDKs concerned")
    return 0


def _metric(suite: str, case: str, server: str, value, unit: str, higher_is_better: bool = True) -> dict | None:
    if value is None:
        return None
    return dict(suite=suite, case=case, server=server, value=float(value), unit=unit, higher_is_better=higher_is_better)


def _throughput(store) -> list[dict | None]:
    metrics = []
    for row in store.results.values():
        key, stats = row["config_key"], row.get("stats") or {}
        client, server = str(key["pair"]).split(":", 1)
        if client != "open62541":  # one series per server: the native client every profile uses
            continue
        case = f"{key['operation']} {key['mode']} {key['payload']} {key['clients']}c {key['security']}"
        metrics.append(_metric("throughput", case, server, stats.get("median_ops_per_second"), "calls/s"))
    return metrics


def _server_limits(store) -> list[dict | None]:
    peaks: dict[str, float] = {}
    for row in store.results.values():
        if (row.get("verdict") or {}).get("failed"):
            continue
        server = row["config_key"]["implementation"]
        ops = (row.get("stats") or {}).get("median_ops_per_second") or 0.0
        peaks[server] = max(peaks.get(server, 0.0), ops)
    return [_metric("server_limits", "peak before failure", server, ops, "calls/s") for server, ops in peaks.items()]


def _server_capacity(store) -> list[dict | None]:
    capacities = store.stored_metadata.get("capacities", {})
    return [
        _metric("server_capacity", "scalar Read capacity", server, result.get("capacity_requests_per_second"), "requests/s")
        for server, result in capacities.items()
    ]


def _subscription(store) -> list[dict | None]:
    metrics = []
    for result in store.stored_metadata.get("capacities", []):
        case = result["case"]
        if "error" in result:
            continue
        label = f"{case['measurement']} sampling {case['sampling_ms']} ms, publishing {case.get('publishing_ms', 1000)} ms"
        metrics.append(_metric("subscription", label, case["implementation"], result.get("passing_items"), "monitored items"))
    return metrics


SUMMARIES = {
    "throughput": _throughput,
    "server_limits": _server_limits,
    "server_capacity": _server_capacity,
    "subscription": _subscription,
}


def summary(database: Path, profile: str, name: str) -> dict:
    """One history entry: the headline number per suite, case and server, and where it came from."""
    from bench.sdk_versions import SDKS
    from common.bench_db import BenchDB, _cpu_model, _total_memory_bytes

    metrics: list[dict] = []
    failures = 0
    for suite, extract in SUMMARIES.items():
        with closing(BenchDB(database, suite, name=name)) as store:
            if not store.results and not store.stored_metadata:
                continue
            failures += len(store.failures)
            metrics.extend(metric for metric in extract(store) if metric is not None)
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    server_url, repository = os.environ.get("GITHUB_SERVER_URL", ""), os.environ.get("GITHUB_REPOSITORY", "")
    memory = _total_memory_bytes()
    return dict(
        id=f"{date[:10]}-{run_id}",
        date=date,
        profile=profile,
        trigger=os.environ.get("GITHUB_EVENT_NAME", "local"),
        ref=os.environ.get("GITHUB_REF_NAME", ""),
        commit=os.environ.get("GITHUB_SHA", ""),
        url=f"{server_url}/{repository}/actions/runs/{run_id}" if repository else None,
        runner=dict(
            label=os.environ.get("RUNNER_LABEL", os.environ.get("RUNNER_NAME", "")),
            cpu=_cpu_model(),
            cores=os.cpu_count(),
            memory_gb=round(memory / 2**30, 1) if memory else None,
        ),
        versions={sdk_name: sdk.pinned() for sdk_name, sdk in SDKS.items()},
        failures=failures,
        metrics=sorted(metrics, key=lambda m: (m["suite"], m["case"], m["server"])),
    )


def compare(entry: dict, history: list[dict], threshold: float = 0.10) -> str:
    """Markdown: this run against the latest published run of the same profile.

    Ratios to the open62541 server of the same run are shown too: on shared
    runners they move less than raw numbers when the machine changes.
    """
    previous = next((run for run in reversed(history) if run.get("profile") == entry["profile"]), None)
    lines = [f"### Benchmarks: {entry['profile']} profile, {len(entry['metrics'])} measurements, {entry['failures']} failures", ""]
    if previous is None:
        lines.append("No published run of this profile yet: nothing to compare with.")
    else:
        lines.append(f"Compared with [{previous['id']}]({previous.get('url') or ''}) on {previous['runner'].get('cpu')}.")
    lines += ["", "| suite | case | server | version | value | previous | change | vs open62541 | previous |", "|" + " --- |" * 9]

    def index(run):
        return {(m["suite"], m["case"], m["server"]): m["value"] for m in run["metrics"]} if run else {}

    now, before = index(entry), index(previous)
    for metric in entry["metrics"]:
        key = (metric["suite"], metric["case"], metric["server"])
        old = before.get(key)
        change = metric["value"] / old - 1 if old else None
        flag = ""
        if change is not None and abs(change) > threshold:
            better = (change > 0) == metric.get("higher_is_better", True)
            flag = " :green_circle:" if better else " :red_circle:"
        reference_key = (metric["suite"], metric["case"], "open62541")
        ratio = metric["value"] / now[reference_key] if now.get(reference_key) else None
        old_ratio = old / before[reference_key] if old and before.get(reference_key) else None
        version = entry["versions"].get(metric["server"], "")
        old_version = (previous or {}).get("versions", {}).get(metric["server"])
        if old_version and old_version != version:
            version = f"{old_version} → **{version}**"
        lines.append(
            f"| {metric['suite']} | {metric['case']} | {metric['server']} | {version} | {metric['value']:,.0f} | "
            + (f"{old:,.0f}" if old else "–")
            + f" | {f'{change:+.1%}' if change is not None else '–'}{flag} | "
            + (f"{ratio:.2f}" if ratio else "–")
            + f" | {f'{old_ratio:.2f}' if old_ratio else '–'} |"
        )
    return "\n".join(lines) + "\n"


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Configure and sample every suite of a profile")
    run_parser.add_argument("db", type=Path)
    run_parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    run_parser.add_argument("--name", default="ci")
    run_parser.add_argument(
        "--servers",
        default=os.environ.get("BENCH_SERVERS") or ",".join(ALL_SERVERS),
        help="comma-separated server SDKs to measure (default: all)",
    )
    summary_parser = commands.add_parser("summary", help="Write one run's history entry as JSON")
    summary_parser.add_argument("db", type=Path)
    summary_parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    summary_parser.add_argument("--name", default="ci")
    summary_parser.add_argument("--report", default="", help="link to this run's published report")
    summary_parser.add_argument("--out", type=Path, required=True)
    compare_parser = commands.add_parser("compare", help="Markdown comparison with the published history")
    compare_parser.add_argument("summary", type=Path)
    compare_parser.add_argument("history", type=Path, help="data/history.json; may be missing")
    append_parser = commands.add_parser("append", help="Add (or replace) a run's entry in data/history.json")
    append_parser.add_argument("history", type=Path)
    append_parser.add_argument("summary", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.command == "append":
        entry = json.loads(args.summary.read_text())
        history = json.loads(args.history.read_text()) if args.history.exists() else []
        history = [run for run in history if run["id"] != entry["id"]] + [entry]
        args.history.parent.mkdir(parents=True, exist_ok=True)
        args.history.write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")
        return 0
    if args.command == "compare":
        history = json.loads(args.history.read_text()) if args.history.exists() else []
        print(compare(json.loads(args.summary.read_text()), history), end="")
        return 0
    if args.command == "summary":
        entry = summary(args.db, args.profile, args.name)
        entry["report"] = args.report
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(entry, indent=1) + "\n", encoding="utf-8")
        print(f"{len(entry['metrics'])} metrics, {entry['failures']} failures -> {args.out}")
        return 0
    servers = [server.strip() for server in args.servers.split(",") if server.strip()]
    unknown = sorted(set(servers) - set(ALL_SERVERS))
    if not servers or unknown:
        print(f"unknown or no servers: {', '.join(unknown)}", file=sys.stderr)
        return 2
    return run(args.db, args.profile, args.name, servers)


if __name__ == "__main__":
    raise SystemExit(main())
