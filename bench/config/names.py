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

"""Database checks for the schedule's benchmark name prompt."""

from contextlib import closing
from dataclasses import replace
import json

from bench.config import Entry, Schedule, SuiteConfig, SUITE_ORDER, db_ready
from common.bench_db import BenchDB


def benchmark_names(schedule: Schedule) -> set[str]:
    names: set[str] = set()
    for path in {entry.db for entry in schedule.entries if entry.db is not None and db_ready(entry.db)}:
        with closing(BenchDB(path, suite="")) as store:
            names.update(store.benchmark_names())
    return names


def default_name(names: set[str]) -> str:
    number = 1
    while f"unnamed-{number}" in names:
        number += 1
    return f"unnamed-{number}"


def resume_schedule(schedule: Schedule, name: str) -> Schedule:
    """Amend selected suites using saved targets; an empty selection resumes all."""
    entries = []
    for path in dict.fromkeys(entry.db for entry in schedule.entries if entry.db is not None and db_ready(entry.db)):
        with closing(BenchDB(path, suite="", name=name)) as store:
            row = store._connection.execute("SELECT schedule FROM benchmarks WHERE name = ?", (name,)).fetchone()
        if row is None:
            continue
        saved = json.loads(row[0]) if row[0] else None
        selected_entries = [entry for entry in schedule.entries if entry.db == path]
        selected = {suite for entry in selected_entries for suite, config in entry.suites if config.enabled}
        if saved and saved.get("entries"):
            candidates = [
                Entry(db=path, suites=tuple((suite, SuiteConfig(**entry["suites"].get(suite, {}))) for suite in SUITE_ORDER))
                for entry in saved["entries"]
            ]
            saved_suites = {suite for entry in candidates for suite, config in entry.suites if config.enabled}
            candidates.extend(
                replace(
                    entry,
                    suites=tuple(
                        (suite, replace(config, enabled=config.enabled and suite not in saved_suites))
                        for suite, config in entry.suites
                    ),
                )
                for entry in selected_entries
            )
        else:
            candidates = selected_entries
        for entry in candidates:
            suites = []
            for suite, config in entry.suites:
                if not config.enabled or (selected and suite not in selected):
                    suites.append((suite, replace(config, enabled=False)))
                    continue
                with closing(BenchDB(path, suite=suite, name=name)) as store:
                    metadata = store.stored_metadata
                    unfinished = bool(store.failures) or metadata.get("complete") is False or metadata.get("partial") is True
                    if suite == "subscription" and not unfinished:
                        from subscription.amend import amended_plan, saved_cases

                        planned_uniform, planned_cases = amended_plan(path, store)
                        unfinished = planned_uniform != store.get_config(suite)[0] or planned_cases != saved_cases(store)
                    uniform, _ = store.get_config(suite)
                    stored_samples = uniform.get("samples")
                    if stored_samples is not None and stored_samples < config.samples:
                        unfinished = True
                    has_results = store._connection.execute(
                        "SELECT 1 FROM results WHERE name = ? AND suite = ? LIMIT 1", (name, suite)
                    ).fetchone()
                    if not has_results and metadata.get("complete") is not True and metadata.get("partial") is not False:
                        unfinished = True
                    samples = max(config.samples, metadata.get("requested_samples", config.samples))
                suites.append((suite, replace(config, enabled=config.enabled and unfinished, amend=True, samples=samples)))
            if any(config.enabled for _, config in suites):
                entries.append(replace(entry, suites=tuple(suites), start_delay_minutes=0))
    if not entries:
        raise ValueError(f"Benchmark name {name!r} already exists but has no unfinished suites to amend")
    return Schedule(entries=tuple(entries), name=name, amend=True)


def resumable_names(schedule: Schedule, names: set[str]) -> frozenset[str]:
    resumable = set()
    for name in names:
        try:
            resume_schedule(schedule, name)
        except ValueError:
            continue
        resumable.add(name)
    return frozenset(resumable)
