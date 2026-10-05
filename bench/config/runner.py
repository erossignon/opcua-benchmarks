#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""``run_schedule(schedule) -> int`` — the in-process runner.

Walks the expanded schedule, prints banner lines, sleeps the per-entry
delay once, and invokes each suite's own ``cmd_sample`` in-process with
the right ``Namespace``. Failures are recorded by the suite, not the
runner; a non-zero exit does not stop the schedule. The banner emitter
and sleep function are injectable so tests can capture one and stub
the other.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, replace
import time
import traceback
from typing import Callable, Optional

from bench.config.expand import expand_schedule
from bench.config.names import benchmark_names, default_name, resume_schedule
from common.bench_db import BenchDB, benchmark_scope


def run_schedule(
    schedule,
    banner: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    error_banner: Optional[Callable[[str], None]] = None,
) -> int:
    if error_banner is None:
        def error_banner(msg: str) -> None:
            print(msg, file=sys.stderr)
    if schedule.amend and schedule.rerun:
        error_banner("Choose either amend or rerun")
        return 1
    if schedule.rerun:
        schedule = replace(schedule, entries=tuple(
            replace(entry, suites=tuple((name, replace(config, amend=False, skip_failed=False))
                                        for name, config in entry.suites)) for entry in schedule.entries
        ))
    if schedule.amend:
        try:
            schedule = resume_schedule(schedule, (schedule.name or "").strip())
        except (OSError, ValueError, sqlite3.Error) as error:
            error_banner(str(error))
            return 1
    plans = expand_schedule(schedule)
    if not any(plan.invocations for plan in plans):
        return 0
    try:
        existing = benchmark_names(schedule)
        name = schedule.name.strip() if schedule.name is not None else default_name(existing)
        if not name:
            raise ValueError("Benchmark name must not be empty")
        if name in existing and not (schedule.amend or schedule.rerun):
            raise ValueError(f"Benchmark name {name!r} already exists")
        for db in dict.fromkeys(plan.db.resolve() for plan in plans if plan.invocations):
            assert db is not None
            with closing(BenchDB(db, suite="")) as store:
                if schedule.amend:
                    continue
                entries = [
                    {"start_delay_minutes": entry.start_delay_minutes,
                     "suites": {suite: asdict(config) for suite, config in entry.suites}}
                    for entry in schedule.entries if entry.db is not None and entry.db.resolve() == db
                ]
                if schedule.rerun and name in store.benchmark_names():
                    suites = {
                        invocation.suite_name for plan in plans
                        if plan.db is not None and plan.db.resolve() == db for invocation in plan.invocations
                    }
                    store.restart_suites(name, suites, {"entries": entries})
                else:
                    store.create_benchmark(name, {"entries": entries})
    except (OSError, ValueError, sqlite3.Error) as error:
        error_banner(str(error))
        return 1
    total = len(plans)
    for index, plan in enumerate(plans, start=1):
        banner(f">>> entry {index}/{total}: {plan.db} [{name}]")
        if plan.start_delay_minutes:
            sleep(plan.start_delay_minutes * 60)
        for pre in plan.pre_banners:
            banner(pre)
        for invocation in plan.invocations:
            target = invocation.namespace.num_samples
            flag_parts = [f"sample {target}" if target is not None else "sample (saved per-case targets)"]
            if invocation.namespace.amend:
                flag_parts.append("--amend")
            if invocation.namespace.skip_failed:
                flag_parts.append("--skip-failed")
            suffix = " " + " ".join(flag_parts) if flag_parts else ""
            banner(f">>>   {invocation.suite_name}{suffix}")
            try:
                with benchmark_scope(name):
                    code = invocation.run()
                if code:
                    error_banner(f"!! {invocation.suite_name} failed with exit status {code}; continuing")
            except KeyboardInterrupt:
                continue
            except Exception as error:
                error_banner(
                    f"!! {invocation.suite_name} raised {type(error).__name__}: {error}; continuing"
                )
                error_banner(traceback.format_exc())
    return 0
