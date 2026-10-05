"""A small, self-contained report that keeps the measurement definition visible."""

from contextlib import closing
from html import escape
import json

from common.bench_db import BenchDB
from subscription.sample import SUITE
from bench.counter_report import workload_note


def build_page(database):
    with closing(BenchDB(database, SUITE)) as store:
        rows = list(store.results.values())
        failures = store.failures
        name = store.name
        complete = store.stored_metadata.get("complete", False)
        capacities = store.stored_metadata.get("capacities", [])
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Subscription counter</title>",
        "<style>body{font:16px system-ui;max-width:1300px;margin:40px auto;padding:0 20px}"
        "table{border-collapse:collapse;width:100%}td,th{text-align:right;padding:8px;border-bottom:1px solid #ddd}"
        "td:first-child,th:first-child{text-align:left}pre{white-space:pre-wrap}details{margin:12px 0}</style>",
        f"<h1>Subscription counter · {escape(name)}</h1>",
        f"<p>Run status: {'complete' if complete else 'partial'} · {len(rows)} observations.</p>",
        workload_note({row["observation"].get("server", {}).get("counter_source", "application_update") for row in rows}),
        "<p>One observer per measurement; client and server searches run separately. Scalar UInt64 counters, queue size 1, configurable publishing, "
        "one open62541 client. Server mode discards notifications without measuring them; client mode records no server measurements. "
        "Counts describe callback increments (application updates for asyncua and historical runs) or delivered counter differences.</p>",
        "<p>Measurements expect at least 1000 increments per item over whole publishing cycles. "
        "An additional 10% warm-up rounds up to whole publishing intervals; nothing is discarded at the end. "
        "Expected progress is the retained duration ÷ configured sampling period. "
        "A candidate passes when every item's progress reaches 99%. Counts are tested once: double/halve to find a bracket, "
        "then refine to 10% or one item. These are observed bounds, without repeated confirmation. "
        "Client progress uses the latest received counter at each window boundary; publishing phase and jitter can shift deltas. "
        "Historical rows retain their original duration and measurement semantics.</p>",
        "<p>Approximate 95% confidence intervals describe mean progress across items over time, not the worst item or the capacity bound. "
        "Adjacent time intervals are resampled in blocks. Short traces are marked limited; a flat trace cannot estimate unseen disturbances. "
        "Confidence does not change the fixed 99% worst-item pass rule.</p>",
    ]
    if capacities:
        parts.append(
            "<h2>Capacity estimates</h2><table><tr><th>SDK / observer</th><th>Sampling ms</th>"
            "<th>Publishing ms</th><th>Duration ms</th><th>Warm-up ms</th><th>Retained ms</th><th>Passing items</th><th>Failing items</th></tr>"
        )
        for result in capacities:
            case = result["case"]
            publishing = case.get("publishing_ms", 1000)
            retained = result.get("measured_duration_ms")
            if retained is None:
                # Earlier versions used configured durations and trimmed both ends.
                retained = case["duration_ms"] - 2000
                if "publishing_ms" in case:
                    retained = ((case["duration_ms"] - max(1000, publishing) - 1000) // publishing) * publishing
            warmup = result.get("warmup_ms", "—")
            label = escape(f"{case['implementation']} / {case['measurement']}")
            if "error" in result:
                parts.append(
                    f"<tr><td>{label}</td><td>{case['sampling_ms']}</td><td>{publishing}</td>"
                    f"<td>{case['duration_ms']}</td><td>{warmup}</td><td>{retained}</td>"
                    f"<td colspan='2'>Incomplete: {escape(result['error'])}</td></tr>"
                )
                continue
            lower = f"≥ {result['passing_items']}" if result["at_least"] else str(result["passing_items"])
            upper = str(result["failing_items"]) if result["failing_items"] is not None else "not reached"
            parts.append(
                f"<tr><td>{label}</td><td>{case['sampling_ms']}</td><td>{publishing}</td>"
                f"<td>{case['duration_ms']}</td><td>{warmup}</td><td>{retained}</td>"
                f"<td>{lower}</td><td>{upper}</td></tr>"
            )
        parts.append("</table>")
    parts.extend(
        [
            "<h2>Candidate observations</h2><table><thead><tr><th>SDK / observer</th><th>Items</th><th>Sampling ms</th>"
            "<th>Publishing ms</th><th>Duration ms</th><th>Warm-up ms</th><th>Retained ms</th><th>Expected/item</th>"
            "<th>Observed min–max</th><th>Worst progress</th><th>Mean progress</th><th>Mean progress: approx. 95% CI</th><th>Publish Δ / expected</th>"
            "<th>Full intervals/item min</th><th>Max full-window gap ms</th></tr></thead><tbody>",
        ]
    )
    details = []
    for row in rows:
        observation = row["observation"]
        case, summary = observation["case"], observation["summary"]
        delivery = observation["recording"].get("delivery", [])
        if isinstance(delivery, dict):
            has_intervals = delivery["intervals_total"] > 0
            delta = f"{delivery['min_delta']}–{delivery['max_delta']}" if has_intervals else "—"
            intervals = delivery["intervals_min"]
            gap = f"{delivery['max_gap_ms']:.1f}" if has_intervals else "—"
        else:
            complete_intervals = [item for item in delivery if item["intervals"]]
            delta = (
                f"{min(i['min_delta'] for i in complete_intervals)}–{max(i['max_delta'] for i in complete_intervals)}"
                if complete_intervals
                else "—"
            )
            intervals = min((i["intervals"] for i in delivery), default="—")
            gap = f"{max(i['max_gap_ms'] for i in complete_intervals):.1f}" if complete_intervals else "—"
        if case["measurement"] == "client":
            delta += f" / {summary['expected_per_publish']:g}"
        label = f"{case['implementation']} / {case['measurement']}"
        duration = case.get("duration_ms", case.get("duration", 0) * 1000)
        retained = summary.get("measured_duration_ms", duration)
        warmup = summary.get("warmup_ms", "—")
        confidence = summary.get("confidence", {})
        ci = escape(confidence.get("status", "not recorded"))
        if confidence.get("lower") is not None:
            ci = f"{confidence['lower']:.2%}–{confidence['upper']:.2%}<br><small>{ci}</small>"
        publishing = observation["setup"]["publishing_ms"]
        parts.append(
            f"<tr><td>{escape(label)}</td><td>{case['items']}</td><td>{case['sampling_ms']}</td>"
            f"<td>{publishing:g}</td><td>{duration}</td><td>{warmup}</td><td>{retained}</td><td>{summary['expected_per_item']:g}</td>"
            f"<td>{summary['observed_min']}–{summary['observed_max']}</td>"
            f"<td>{summary['progress_fraction_min']:.2%}</td><td>{summary['progress_fraction_mean']:.2%}</td>"
            f"<td>{ci}</td><td>{delta}</td><td>{intervals}</td><td>{gap}</td></tr>"
        )
        setup = observation["setup"]
        evidence = dict(
            case=case,
            summary=summary,
            server=observation["server"],
            affinity=observation["affinity"],
            fingerprint=observation["fingerprint"],
            publishing_ms=setup["publishing_ms"],
            revised_sampling_ms=(
                sorted(set(setup["sampling_ms"])) if isinstance(setup["sampling_ms"], list) else [setup["sampling_ms"]]
            ),
            queue_size=setup["queue_size"],
            interval_counts=observation["recording"].get("interval_counts"),
        )
        details.append(
            f"<details><summary>{escape(label)} · {case['items']} items · {case['sampling_ms']} ms sampling · {publishing:g} ms publishing · "
            f"{duration} ms duration</summary><pre>{escape(json.dumps(evidence, indent=2))}</pre></details>"
        )
    parts.append("</tbody></table>")
    parts.append(
        "<p>asyncua uses write-triggered notifications and revises the reported sampling interval to the publishing interval. "
        "Its application update period equals the configured sampling period. Revised intervals and CPU allocation appear below.</p>"
    )
    for failure in failures:
        parts.append(f"<p><strong>Failed:</strong> {escape(failure['configuration'])}: {escape(failure['message'])}</p>")
    parts.extend(details)
    return "\n".join(parts)
