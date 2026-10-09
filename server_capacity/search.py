# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
#    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

"""Explore a bounded geometric grid, then repeat its top-decile configurations."""

import math
import statistics

METHOD = "ranked_candidates_v1"


def levels(start, maximum, factor):
    values = [start]
    while values[-1] < maximum:
        values.append(min(maximum, values[-1] * factor))
    return values


def discovery_plan(settings, samples):
    """Cover both load axes while reserving probe slots for top-decile repeats."""
    grid = [
        (c, d)
        for c in levels(1, settings["max_clients"], 2)
        for d in levels(settings["start_outstanding"], settings["max_outstanding"], 4)
    ]
    discovery_seconds = (settings["probe_ms"] + settings["warmup_ms"]) / 1000 + 0.1
    confirmation_seconds = (settings["confirm_ms"] + settings["warmup_ms"]) / 1000 + 0.1
    available_seconds = settings["budget_seconds"] - 5 - settings["grace_ms"] / 1000
    count = max(
        (
            n
            for n in range(1, len(grid) + 1)
            if n + math.ceil(n / 10) * samples <= settings["max_probes"]
            and n * discovery_seconds + math.ceil(n / 10) * samples * confirmation_seconds <= available_seconds
        ),
        default=1,
    )
    if count == 1:
        return [grid[0]]
    # If the budget cannot cover the grid, sample across its entire extent;
    # do not spend all discovery slots on the smallest client counts.
    return [grid[round(i * (len(grid) - 1) / (count - 1))] for i in range(count)]


def summarize(runs, *, client_cpus):
    successful = [r for r in runs if r["healthy"]]
    rates = [r["requests_per_second"] for r in successful]
    variance = statistics.variance(rates) if len(rates) > 1 else None
    mean = statistics.mean(rates) if rates else None
    return dict(
        healthy=bool(runs) and len(successful) == len(runs),
        count=len(runs),
        successful_count=len(successful),
        rate=mean,
        mean=mean,
        maximum=max(rates) if rates else None,
        minimum=min(rates) if rates else None,
        variance=variance,
        stddev=math.sqrt(variance) if variance is not None else None,
        spread=(max(rates) - min(rates)) / mean if mean else None,
        generator_headroom=bool(successful)
        and client_cpus > 0
        and all(
            r["client_cpu_max_fraction"] < 0.85
            and r.get("client_cpu_total_fraction", r["client_cpu_max_fraction"]) < 0.85 * client_cpus
            for r in successful
        ),
    )


def search(
    settings,
    samples,
    probe,
    *,
    client_cpus,
    selection=None,
    save_selection=lambda selection: None,
    can_discover=lambda c, d, reserve: True,
):
    """Freeze discovery ranking before confirmation, preserving it across resume.

    Selection uses only short discovery measurements. Each selected load receives
    exactly `samples` independent confirmation attempts in rotated order, including
    when repeats are slow or fail. No reranking or optional retries censor them.
    CPU headroom remains diagnostic; a repeatable rate is not proof of saturation.
    """
    if selection is None:
        plan = discovery_plan(settings, samples)
        reserve = math.ceil(len(plan) / 10) * samples
        discoveries = []
        rejected = {}
        stop = (
            "grid_complete"
            if len(plan)
            == len(levels(1, settings["max_clients"], 2))
            * len(levels(settings["start_outstanding"], settings["max_outstanding"], 4))
            else "discovery_budget"
        )
        for c, d in plan:
            # A deeper pipeline at this same client count already failed.
            # Other client counts still get their own discovery probes.
            if c in rejected and d > rejected[c]:
                continue
            if not can_discover(c, d, reserve):
                stop = "time_reserved_for_confirmation"
                break
            value = probe(c, d, "discovery", 0)
            discoveries.append(
                dict(clients=c, outstanding=d, healthy=value["healthy"], requests_per_second=value["requests_per_second"])
            )
            if not value["healthy"]:
                rejected[c] = d
        ranked = sorted(
            (r for r in discoveries if r["healthy"]),
            key=lambda r: (-r["requests_per_second"], r["clients"] * r["outstanding"], r["clients"], r["outstanding"]),
        )
        candidates = ranked[: math.ceil(len(ranked) / 10)]
        selection = dict(percentile=90, discoveries=discoveries, candidates=candidates, discovery_stop=stop)
        if discoveries:
            save_selection(selection)

    candidates = selection["candidates"]
    best = max((r["requests_per_second"] for r in selection["discoveries"] if r["healthy"]), default=0)
    result = dict(
        highest_observed_requests_per_second=best,
        selection=selection,
        confirmation=[],
        capacity_requests_per_second=None,
        saturation_proven=False,
    )
    if not candidates:
        return {
            **result,
            "status": "budget_exhausted" if not selection["discoveries"] else "unmeasured",
            "reason": "Discovery found no successful candidate to repeat",
        }

    groups = [[] for _ in candidates]
    for repetition in range(samples):
        for offset in range(len(candidates)):
            index = (repetition + offset) % len(candidates)
            candidate = candidates[index]
            value = probe(candidate["clients"], candidate["outstanding"], "confirmation", repetition)
            groups[index].append(value)
            if value["healthy"]:
                result["highest_observed_requests_per_second"] = max(
                    result["highest_observed_requests_per_second"], value["requests_per_second"]
                )

    result["confirmation"] = [
        dict(clients=c["clients"], outstanding=c["outstanding"], **summarize(group, client_cpus=client_cpus))
        for c, group in zip(candidates, groups)
    ]
    eligible = [r for r in result["confirmation"] if r["healthy"]]
    if not eligible:
        return {
            **result,
            "status": "confirmation_failed",
            "reason": "Every selected load failed at least one confirmation attempt",
        }
    winner = max(eligible, key=lambda r: (r["maximum"], r["mean"]))
    result.update(
        status="variable" if winner["spread"] > 0.15 else "confirmed",
        reason=f"Top-decile candidate completed {samples} successful confirmation measurements; saturation is not proven",
        capacity_requests_per_second=winner["maximum"],
        winning_configuration=dict(clients=winner["clients"], outstanding=winner["outstanding"]),
        observed_range_requests_per_second=[winner["minimum"], winner["maximum"]],
    )
    return result
