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
import html
import json
import os
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ALL_SERVERS = (
    "open62541", "o6-python", "asyncua", "ua-dotnet", "node-opcua", "node-opcua-fronts", "node-opcua-fronts-2", "milo", "s2opc", "gopcua"
)
# Flavours of one SDK: same package, so same version as the SDK they run.
FLAVOURS = {"node-opcua-fronts": "node-opcua", "node-opcua-fronts-2": "node-opcua"}
# The subscription suite needs a client in the SDK, which the server-only workers lack.
SUBSCRIPTION_SERVERS = ("open62541", "o6-python", "asyncua", "node-opcua", "ua-dotnet")

# Each profile: suite -> (option settings, sample command arguments).
# JIT servers (node-opcua, .NET, Java) need thousands of calls to reach a steady
# state: from cold, node-opcua 2.187 single thread plateaus after about 15,000
# synchronous Reads and with fronts after about 7,000 (Node.js client, so an upper
# bound). Every SDK gets the same warmup, in both profiles.
PROFILES: dict[str, dict[str, tuple[dict[str, list[str]], list[str]]]] = {
    "quick": {
        "throughput": (
            {
                "operation": ["read", "write"],
                "mode": ["sync", "async"],
                # 3 clients too: one connection uses one front, so only several show the fronts.
                "clients": ["1", "3"],
                "payload": ["scalar", "batch:100"],
                "security": ["None"],
                "warmup": ["20000"],
                "iterations": ["5000"],
                "max_values": ["300000"],
            },
            ["1"],
        ),
        # 2 s of warmup per probe; the budget pays for it (node-opcua needed more than 45 s at 0.5 s).
        "server_capacity": ({"warmup_ms": ["2000"], "budget_seconds": ["120"], "max_clients": ["8"]}, ["3"]),
    },
    "standard": {
        "throughput": (
            {
                "operation": ["read", "write"],
                "mode": ["sync", "async"],
                "clients": ["1", "3"],
                "payload": ["scalar", "batch:100", "batch:1000", "array:1000"],
                "security": ["None", "Basic256Sha256"],
                "warmup": ["20000"],
                "iterations": ["5000"],
                "max_values": ["2000000"],
            },
            ["1"],
        ),
        "server_capacity": ({"warmup_ms": ["2000"], "budget_seconds": ["120"], "max_clients": ["16"]}, ["3"]),
        "server_limits": (
            {"step_seconds": ["5"], "warmup_seconds": ["3"], "max_clients": ["32"], "max_outstanding": ["64"]},
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


def run(database: Path, profile: str, name: str, servers: list[str], only: list[str] | None = None) -> int:
    if database.exists():
        print(f"{database} already exists; CI runs start from an empty database", file=sys.stderr)
        return 1
    database.parent.mkdir(parents=True, exist_ok=True)
    suites = {suite: plan for suite, plan in PROFILES[profile].items() if not only or suite in only}
    if not suites:
        print(f"profile {profile} has none of the suites {only}", file=sys.stderr)
        return 2
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
        versions={
            **{sdk_name: sdk.pinned() for sdk_name, sdk in SDKS.items()},
            **{flavour: SDKS[sdk].pinned() for flavour, sdk in FLAVOURS.items()},
        },
        failures=failures,
        metrics=sorted(metrics, key=lambda m: (m["suite"], m["case"], m["server"])),
    )


def compare(entry: dict, history: list[dict], threshold: float = 0.10) -> str:
    """Markdown: each SDK's standing in this run against the latest published run of the same
    profile on the same runner: rank and share of the fastest per case, places gained or lost."""
    # Same profile and same machine (runner label): hosted runners change hardware between runs.
    label = entry["runner"].get("label")
    previous = next(
        (run for run in reversed(history) if run.get("profile") == entry["profile"] and run["runner"].get("label") == label),
        None,
    )
    lines = [f"### Benchmarks: {entry['profile']} profile, {len(entry['metrics'])} measurements, {entry['failures']} failures", ""]
    if previous is None:
        lines.append(f"No published run of this profile on {label} yet: nothing to compare with.")
    else:
        lines.append(f"Compared with [{previous['id']}]({previous.get('url') or ''}) on {previous['runner'].get('cpu')}.")
    lines += [
        "",
        "Standing within each run: every SDK of a run shares its machine, so ranks and shares of the fastest "
        "compare across runs even when the hardware changed; raw values do not.",
    ]

    def group(run):
        cases: dict[tuple[str, str], dict[str, dict]] = {}
        for metric in run["metrics"] if run else []:
            cases.setdefault((metric["suite"], metric["case"]), {})[metric["server"]] = metric
        return cases

    def standing(metrics: dict[str, dict], servers: list[str]) -> dict[str, tuple[int, float]]:
        higher = next(iter(metrics.values())).get("higher_is_better", True)
        ranked = sorted(servers, key=lambda server: metrics[server]["value"], reverse=higher)
        best = metrics[ranked[0]]["value"] if ranked else 0
        return {
            server: (rank, (metrics[server]["value"] / best if higher else best / metrics[server]["value"]) if best else 0.0)
            for rank, server in enumerate(ranked, 1)
        }

    now_cases, before_cases = group(entry), group(previous)
    moves, rows = [], []
    for (suite, case), metrics in sorted(now_cases.items()):
        old_metrics = before_cases.get((suite, case), {})
        # Rank only the servers both runs measured, so a newly added SDK moves nobody.
        common = [server for server in metrics if server in old_metrics] if old_metrics else list(metrics)
        now = standing(metrics, common)
        before = standing(old_metrics, common) if old_metrics else {}
        for server in sorted(common, key=lambda server: now[server][0]):
            rank, share = now[server]
            old_rank, old_share = before.get(server, (None, None))
            places = old_rank - rank if old_rank else 0
            move = f"▲ {places}" if places > 0 else f"▼ {-places}" if places < 0 else ("=" if old_rank else "")
            if places:
                moves.append(f"{server} {move} in {suite} {case}")
            flag = ""
            if old_share is not None and abs(share - old_share) > threshold:
                flag = " :green_circle:" if share > old_share else " :red_circle:"
            version = entry["versions"].get(server, "")
            old_version = (previous or {}).get("versions", {}).get(server)
            if old_version and old_version != version:
                version = f"{old_version} → **{version}**"
            rows.append(
                f"| {suite} | {case} | {server} | {version} | "
                + (f"#{old_rank} → " if old_rank else "")
                + f"**#{rank}** | {move} | "
                + (f"{old_share:.0%} → " if old_share is not None else "")
                + f"{share:.0%}{flag} (×{1 / share:.1f}) | {metrics[server]['value']:,.0f} |"
            )
    if previous is not None:
        lines += ["", "**Places changed:** " + ("; ".join(moves) if moves else "none.")]
    lines += ["", "| suite | case | server | version | rank | places | share of the fastest (×slower) | value |", "|" + " --- |" * 8]
    return "\n".join(lines + rows) + "\n"


def merge(parts: dict[str, dict], run_id: str, site: Path) -> dict:
    """One history entry from the per-suite jobs of a run, plus ``site/index.html``.

    Each suite ran on its own machine: ``suites`` records which, and ratios to
    open62541 stay meaningful because they never cross suites.
    """
    if not parts:
        raise ValueError("no suite summaries to merge")
    first = min(parts.values(), key=lambda part: part["date"])
    suites = {}
    for suite, part in sorted(parts.items()):
        reports = sorted(path.name for path in (site / suite).glob("*.html") if path.name != "index.html")
        suites[suite] = dict(
            runner=part["runner"],
            failures=part["failures"],
            report=f"{suite}/{reports[0]}" if reports else None,
        )
    entry = dict(
        first,
        id=run_id,
        report=f"runs/{run_id}/index.html",
        runner=first["runner"],
        suites=suites,
        failures=sum(part["failures"] for part in parts.values()),
        metrics=sorted(
            (metric for part in parts.values() for metric in part["metrics"]),
            key=lambda m: (m["suite"], m["case"], m["server"]),
        ),
    )
    rows = "".join(
        f"<tr><td>{html.escape(suite)}</td><td>"
        + (f'<a href="{html.escape(info["report"])}">report</a>' if info["report"] else "no report")
        + f"</td><td>{html.escape(str(info['runner'].get('cpu')))}</td><td>{info['failures']}</td></tr>"
        for suite, info in suites.items()
    )
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Benchmark run {html.escape(run_id)}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:auto;padding:24px 16px;background:#fff;color:#1d1d1b}}
table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:6px 10px;border-bottom:1px solid #ddd}}
@media (prefers-color-scheme: dark){{body{{background:#161615;color:#ecebe6}}a{{color:#7aa2f7}}td,th{{border-color:#333}}}}</style>
</head><body><h1>Benchmark run {html.escape(run_id)}</h1>
<p>{html.escape(entry['profile'])} profile, {html.escape(entry['trigger'])} on {html.escape(entry['ref'])}
(<a href="{html.escape(entry.get('url') or '#')}">log</a>, <a href="../../index.html">history</a>).
Each suite ran on its own machine: compare servers within a suite, not across suites.</p>
<table><tr><th>Suite</th><th>Report</th><th>Machine</th><th>Failures</th></tr>{rows}</table></body></html>
""",
        encoding="utf-8",
    )
    return entry


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
    run_parser.add_argument("--suite", action="append", help="run only this suite of the profile (repeatable)")
    plan_parser = commands.add_parser("plan", help="Print the profile's suites as a JSON list (the CI matrix)")
    plan_parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    merge_parser = commands.add_parser("merge", help="Combine per-suite summaries into one history entry")
    merge_parser.add_argument("summaries", type=Path, nargs="+")
    merge_parser.add_argument("--id", required=True, help="the run's id, as its folder under runs/")
    merge_parser.add_argument("--site", type=Path, required=True, help="the run's folder: gets an index of the suite reports")
    merge_parser.add_argument("--out", type=Path, required=True)
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
    if args.command == "plan":
        print(json.dumps(list(PROFILES[args.profile])))
        return 0
    if args.command == "merge":
        # Each summary sits in its suite's folder: <site>/<suite>/summary.json.
        parts = {path.parent.name: json.loads(path.read_text()) for path in args.summaries}
        entry = merge(parts, args.id, args.site)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(entry, indent=1) + "\n", encoding="utf-8")
        print(f"{len(entry['metrics'])} metrics from {len(args.summaries)} suite job(s) -> {args.out}")
        return 0
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
    return run(args.db, args.profile, args.name, servers, args.suite)


if __name__ == "__main__":
    raise SystemExit(main())
