#!/usr/bin/env python3
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

"""Benchmark schedule model and configuration UI entry point."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

SUITE_ORDER: tuple[str, ...] = (
    "throughput",
    "server_limits",
    "server_capacity",
    "subscription",
)


@dataclass(frozen=True)
class SuiteConfig:
    samples: int = 3
    amend: bool = False
    skip_failed: bool = False
    enabled: bool = False


_DISABLED_SUITES: tuple[tuple[str, SuiteConfig], ...] = tuple(
    (name, SuiteConfig(samples=1 if name == "subscription" else 3)) for name in SUITE_ORDER
)


@dataclass(frozen=True)
class Entry:
    db: Optional[Path] = None
    start_delay_minutes: int = 0
    suites: tuple[tuple[str, SuiteConfig], ...] = _DISABLED_SUITES

    def __post_init__(self) -> None:
        if self.db is not None and not isinstance(self.db, Path):
            object.__setattr__(self, "db", Path(self.db))
        for name, config in self.suites:
            if name not in SUITE_ORDER:
                raise ValueError(f"unknown suite {name!r}; expected one of {SUITE_ORDER}")
            if not isinstance(config, SuiteConfig):
                raise TypeError(f"suite {name!r} config must be a SuiteConfig, got {type(config).__name__}")

    def with_suite(self, name: str, config: SuiteConfig) -> "Entry":
        return replace(self, suites=tuple(
            (n, config if n == name else c) for n, c in self.suites
        ))

    def with_db(self, db: Optional[Path]) -> "Entry":
        return replace(self, db=Path(db) if db is not None else None)

    def with_delay(self, minutes: int) -> "Entry":
        return replace(self, start_delay_minutes=int(minutes))

    def suite(self, name: str) -> SuiteConfig:
        for n, config in self.suites:
            if n == name:
                return config
        raise KeyError(name)


@dataclass(frozen=True)
class Schedule:
    entries: tuple[Entry, ...] = field(default_factory=tuple)
    name: str | None = None
    amend: bool = False
    rerun: bool = False

    def with_entry(self, index: int, entry: Entry) -> "Schedule":
        return replace(self, entries=tuple(
            entry if i == index else existing for i, existing in enumerate(self.entries)
        ))


def db_ready(db: Optional[Path]) -> bool:
    """Whether ``db`` exists — the single definition of "ready to sample"."""
    return db is not None and db.exists()


def _suite_config_for_seed(db: Path, name: str, db_exists: bool) -> SuiteConfig:
    """Seed one suite's ``SuiteConfig``, preferring a persisted run-parameters row.

    Absent a persisted row (the suite has never had its run-parameters
    written from the options view, or the row predates that feature),
    every field falls back to :class:`SuiteConfig`'s own defaults — so a
    suite starts disabled until explicitly turned on, even against a
    ``bench.db`` that already exists. Opening a :class:`~common.bench_db.BenchDB`
    is only safe once ``db_exists`` is true — the connection would
    otherwise create the file just to check.
    """
    if db_exists:
        from common.bench_db import BenchDB

        store = BenchDB(db, suite=name)
        persisted = store.get_schedule_config(name)
        if persisted is not None:
            return SuiteConfig(**persisted)
    return SuiteConfig(samples=1 if name == "subscription" else 3)


def _seeded_entry(db: Path) -> Entry:
    db_exists = db.exists()
    suites = tuple((name, _suite_config_for_seed(db, name, db_exists)) for name in SUITE_ORDER)
    return Entry(db=db, start_delay_minutes=0, suites=suites)


def default_schedule(db: Path = Path("bench.db")) -> Schedule:
    """Return a one-entry schedule pointing at ``db``.

    Each suite's run-parameters (``enabled``, ``samples``, ``amend``,
    ``skip_failed``) come from the database's persisted
    ``schedule_config`` row when one was written from the options view
    on a previous visit. Absent that, every suite starts disabled with
    :class:`SuiteConfig`'s own defaults — a suite is never auto-enabled
    just because ``bench.db`` happens to already exist.
    """
    return Schedule(entries=(_seeded_entry(Path(db)),))


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bench.config",
        description=(
            "Open a curses UI for scheduling benchmark runs across one or more "
            "shared bench.db files. Suites (throughput, "
            "server_limits, server_capacity, subscription) are toggled and run in-process in the "
            "fixed order the top-level `python -m bench <db>` dispatcher seeds; "
            "the schedule lives in memory only and is discarded when the TUI exits."
        ),
    )
    parser.add_argument(
        "db",
        nargs="?",
        type=Path,
        default=Path("bench.db"),
        metavar="<db>",
        help="Path to the bench.db the schedule is seeded with (default: ./bench.db).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """The CLI seam: open the TUI on no arguments, ``--help`` on help, error on bad args.

    On a confirmed run, hands the edited schedule to
    :func:`~bench.config.runner.run_schedule`. Quitting without running
    (``q``/Ctrl-C) returns 0 without invoking the runner.
    """
    from bench.config.runner import run_schedule
    from bench.config.tui import ScheduleCancelled, open_tui
    args = parse_arguments(argv)
    try:
        schedule = default_schedule(args.db)
    except (OSError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    try:
        edited = open_tui(schedule)
    except ScheduleCancelled:
        return 0
    return run_schedule(edited)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
