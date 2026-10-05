#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""The options-view state model and the snapshot loader.

One :class:`OptionsState` represents a single descent into the second
view: which suite is being edited, which entry's database the editor is
looking at, the in-memory snapshot of declared options read from that
database on descent, the focused option index, the editor buffer (when
open), and a status/footer message field. The pure helper
:func:`load_options_snapshot` folds
``common.suites.SUITE_OPTIONS[suite_name]`` with one
:meth:`~common.bench_db.BenchDB.get_config` call per declared option.

The seam is intentionally narrow. No curses, no I/O beyond the database
read; the snapshot loader opens a fresh :class:`~common.bench_db.BenchDB`
against ``db_path`` and never touches the schedule model. The reducer
consumes :class:`OptionsState` only via its public fields, and the
renderer does the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from bench.config import SuiteConfig
from common.bench_db import BenchDB
from common.suites import SUITE_OPTIONS


@dataclass(frozen=True)
class OptionSnapshotRow:
    """One declared option's value in the in-memory snapshot.

    ``current`` is the value stored in ``bench.db`` when ``is_set`` is
    true. When ``is_set`` is false the row is missing in the database;
    ``current`` is then ``None``, distinguishable from a stored value
    of ``None`` by ``is_set`` alone.
    """

    name: str
    current: Any
    is_set: bool
    label: str = ""


@dataclass(frozen=True)
class OptionsState:
    """The in-memory state for one descent into the options editor.

    Lives as a sibling of the schedule view's :class:`~bench.config.tui_reducer.TuiState`,
    not a deep merge. The parent TUI state's :attr:`view` field
    selects between the two: ``"schedule"`` keeps today's behaviour,
    ``"options"`` flips this dataclass into scope.

    ``pending_commit`` and ``pending_default_write`` are intent flags
    the curses layer reads once and clears. ``pending_commit`` carries
    the option name and the JSON-encoded value the curses layer will
    hand to :meth:`~common.bench_db.BenchDB.set_config`.

    ``run_params`` is the focused entry's :class:`~bench.config.SuiteConfig`
    for this suite — the run-parameters block (``enabled``, ``samples``,
    ``amend``, ``skip_failed``) rendered above the declared options.
    ``focused_option_index`` is a single flat index across that block
    (only the fields :data:`~bench.config.options_reducer.RUN_PARAM_FIELDS`
    declares for this suite are navigable) followed by :attr:`snapshot`;
    :func:`~bench.config.options_reducer.run_param_field_count` converts
    between the flat index and which section it falls in.
    ``pending_run_param_write`` carries the edited :class:`SuiteConfig`
    the curses layer will persist via
    :meth:`~common.bench_db.BenchDB.set_schedule_config` and fold back
    into the in-memory schedule.
    """

    suite_name: str
    entry_index: int
    snapshot: tuple[OptionSnapshotRow, ...]
    db_path: Optional[Path] = None
    run_params: SuiteConfig = field(default_factory=SuiteConfig)
    focused_option_index: int = 0
    editor_open: bool = False
    editor_buffer: str = ""
    editor_cursor: Optional[int] = None
    error: str = ""
    pending_commit: Optional[tuple[str, str]] = None
    pending_default_write: Optional[str] = None
    pending_run_param_write: Optional[SuiteConfig] = None


def load_options_snapshot(
    db_path: Path,
    suite_name: str,
    entry_index: int,
    run_params: SuiteConfig = SuiteConfig(),
) -> OptionsState:
    """Return a fresh :class:`OptionsState` for ``suite_name`` at ``entry_index``.

    Opens a :class:`~common.bench_db.BenchDB` against ``db_path`` and
    folds the suite's declared :class:`~common.suites.ConfigOption`s
    from :data:`common.suites.SUITE_OPTIONS` with the values read via
    :meth:`~common.bench_db.BenchDB.get_config`. The snapshot is in
    declaration order (the order of ``OPTIONS``); an option's
    :attr:`~OptionSnapshotRow.is_set` is false when the row is missing
    in the database, true otherwise.

    ``run_params`` seeds the run-parameters block from the schedule
    view's in-memory :class:`~bench.config.Entry` for this suite — not
    re-read from the database, so the options view reflects whatever
    the schedule view currently shows (including edits made earlier in
    the same session) rather than a second, possibly stale, source.
    """
    declared = SUITE_OPTIONS.get(suite_name) or {}
    store = BenchDB(db_path, suite=suite_name)
    rows: list[OptionSnapshotRow] = []
    for name in declared:
        try:
            value = store.get_config(suite_name, name)
            rows.append(OptionSnapshotRow(name=name, current=value, is_set=True))
        except KeyError:
            rows.append(OptionSnapshotRow(name=name, current=None, is_set=False))
    store.close()
    return OptionsState(
        suite_name=suite_name,
        entry_index=entry_index,
        snapshot=tuple(rows),
        db_path=db_path,
        run_params=run_params,
    )
