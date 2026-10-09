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

"""A compact per-suite result summary, read straight from ``bench.db``.

The schedule view shows one such summary per suite: whether the focused
entry's database has any rows for that suite yet, when the most recent
sample landed, and what uniform/varying settings it was measured under.
The renderer in :mod:`bench.config.tui` consumes the dataclass as a
pure projection; the curses loop is the only caller of
:func:`summarize_suite`, which is the one place that opens a
:class:`~common.bench_db.BenchDB`.

The summary is intentionally small: the *content* of a result row
(which pair, which payload, which ops/sec) is the show path's job, not
the TUI's; this module only says "there are N rows, last touched at
T, measured under uniform U and varying V" — exactly the headline
information the user wants at a glance before deciding to re-run.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from common.bench_db import BenchDB
from common.suites import SUITE_OPTIONS


@dataclass(frozen=True)
class SuiteResultSummary:
    """A snapshot of ``suite``'s slice of ``bench.db`` for the schedule view.

    All four counters come straight from the corresponding tables; ``last_timestamp``
    is the ``timestamp_utc`` recorded on the suite's metadata row, ``None`` when
    the row has never been written (no sample has ever landed for this suite).
    ``stored_uniform`` and ``stored_varying`` mirror what
    :meth:`~common.bench_db.BenchDB.get_config` returns for the suite
    (``{name: value}``), restricted to declared options that actually have
    a row — anything else would be a phantom value rendered alongside real
    ones.
    """

    suite: str
    result_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    last_timestamp: Optional[str] = None
    stored_uniform: dict[str, Any] = field(default_factory=dict)
    stored_varying: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_results(self) -> bool:
        return self.result_count > 0


def summarize_suite(db_path: Optional[Path | str], suite_name: str) -> SuiteResultSummary:
    """Return a :class:`SuiteResultSummary` for ``suite_name`` in ``db_path``.

    ``None`` for ``db_path`` (the entry has no database yet) and a missing
    file both return an all-zeros summary rather than raising — the
    renderer needs to be able to ask "anything here?" without a
    try/except at every call site. Any other SQLite/OS error raises:
    those are genuine problems, not "no data" states.

    ``stored_uniform``/``stored_varying`` are restricted to options
    declared in :data:`common.suites.SUITE_OPTIONS`, so a stale row in
    the table that no longer corresponds to a declared option is
    dropped — the same rule :meth:`~common.bench_db.BenchDB.get_config`
    applies, kept here so the schedule view's headline cannot disagree
    with the options view's body.
    """
    if db_path is None:
        return SuiteResultSummary(suite=suite_name)
    db_path_obj = db_path if isinstance(db_path, Path) else Path(db_path)
    if not db_path_obj.exists():
        return SuiteResultSummary(suite=suite_name)
    try:
        store = BenchDB(db_path, suite=suite_name)
    except (OSError, sqlite3.Error):
        return SuiteResultSummary(suite=suite_name)
    try:
        # Startup only needs counts; measurement payloads can span gigabytes.
        result_count, failure_count, skipped_count = (
            store._connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE name = ? AND suite = ?",
                (store.name, suite_name),
            ).fetchone()[0]
            for table in ("results", "failures", "skipped")
        )
        metadata = dict(store.stored_metadata)
        declared = SUITE_OPTIONS.get(suite_name) or {}
        stored_uniform: dict[str, Any] = {}
        stored_varying: dict[str, Any] = {}
        if declared:
            try:
                uniform, varying = store.get_config(suite_name)
            except (OSError, sqlite3.Error):
                uniform, varying = {}, {}
            stored_uniform = {name: uniform[name] for name in declared if name in uniform}
            stored_varying = {name: varying[name] for name in declared if name in varying}
    finally:
        store.close()
    return SuiteResultSummary(
        suite=suite_name,
        result_count=result_count,
        failure_count=failure_count,
        skipped_count=skipped_count,
        last_timestamp=metadata.get("timestamp_utc"),
        stored_uniform=stored_uniform,
        stored_varying=stored_varying,
        metadata=metadata,
    )
