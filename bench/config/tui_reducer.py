#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""``schedule_after_keystrokes(schedule, keys, cwd) -> (Schedule, TuiState)``.

The pure reducer that turns a sequence of normalised keystrokes into an edited
schedule plus the final UI state. The curses loop in :mod:`bench.config.tui`
translates real input into keystroke tuples (``"up"``, ``"down"``,
``"enter"``, ``"escape"``, ``"space"``, ``"r"``, ``"q"``, ``"?"``, ``"s"``,
``"backspace"``, individual characters typed into the inline editor or the
picker path-entry mode) and calls this function. No curses, no I/O, no globals
touch this module: ``cwd`` is read only to seed the database-picker when it
opens, and the input schedule is treated as immutable.

The field layout per entry is fixed: ``DB PATH`` and ``START DELAY (minutes)``
followed by, for each suite in :data:`SUITE_ORDER`, four sub-fields
(``enabled``, ``samples``, ``amend``, ``skip_failed``). The renderer in
:mod:`bench.config.tui` displays each suite's ``enabled`` on a header row
alongside the suite name and the three other sub-fields indented beneath it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Literal, Optional

from bench.config.text_editor import edit_text
from bench.config import (
    SUITE_ORDER,
    Entry,
    Schedule,
    SuiteConfig,
)

if TYPE_CHECKING:
    from bench.config.options import OptionsState
    from bench.config.runs import RunsState
    from bench.config.summary import SuiteResultSummary


class ScheduleCancelled(Exception):
    """Raised by the reducer when the user quits without running."""


Outcome = Literal["running", "quitting", "editing"]
FieldKind = Literal["toggle", "numeric", "path"]
View = Literal["schedule", "options", "runs"]

DB_PATH_FIELD = 0
START_DELAY_FIELD = 1
# The schedule view shows one field per suite: its enabled toggle. The
# suite's other run-parameters (samples/amend/skip_failed) are edited from
# the options view instead — see options_reducer.RUN_PARAM_FIELDS, which
# carries the fuller per-suite field list for that view.
SUITE_SUB_FIELDS: dict[str, tuple[str, ...]] = {name: ("enabled",) for name in SUITE_ORDER}
SUITE_SUB_KINDS: dict[str, FieldKind] = {
    "enabled": "toggle",
}
FIELD_COUNT_PER_ENTRY = 2 + sum(
    len(SUITE_SUB_FIELDS[name]) for name in SUITE_ORDER
)


def _field_offset(field_index: int) -> tuple[int, int]:
    """Return ``(suite_index, position_in_suite)`` for a field past the top-level rows."""
    offset = field_index - 2
    for suite_index, name in enumerate(SUITE_ORDER):
        suite_size = len(SUITE_SUB_FIELDS[name])
        if offset < suite_size:
            return suite_index, offset
        offset -= suite_size
    last = len(SUITE_ORDER) - 1
    return last, len(SUITE_SUB_FIELDS[SUITE_ORDER[last]]) - 1


def suite_field(suite_name: str, sub_field: str) -> int:
    """Return the flat field index for the suite ``suite_name`` and ``sub_field``."""
    suite_index = SUITE_ORDER.index(suite_name)
    sub_index = SUITE_SUB_FIELDS[suite_name].index(sub_field)
    return 2 + sum(len(SUITE_SUB_FIELDS[n]) for n in SUITE_ORDER[:suite_index]) + sub_index


def field_kind(field_index: int) -> FieldKind:
    if field_index == DB_PATH_FIELD:
        return "path"
    if field_index == START_DELAY_FIELD:
        return "numeric"
    suite_index, sub_index = _field_offset(field_index)
    sub = SUITE_SUB_FIELDS[SUITE_ORDER[suite_index]][sub_index]
    return SUITE_SUB_KINDS[sub]


def _suite_name_at(field_index: int) -> str:
    suite_index, _ = _field_offset(field_index)
    return SUITE_ORDER[suite_index]


def _sub_field_at(field_index: int) -> str:
    suite_index, sub_index = _field_offset(field_index)
    return SUITE_SUB_FIELDS[SUITE_ORDER[suite_index]][sub_index]


@dataclass(frozen=True)
class TuiState:
    view: View = "schedule"
    outcome: Outcome = "editing"
    focused_entry: int = 0
    focused_field: int = 0
    editor_open: bool = False
    editor_buffer: str = ""
    editor_cursor: Optional[int] = None
    picker_open: bool = False
    picker_dir: Optional[Path] = None
    picker_cursor: int = 0
    picker_entries: tuple[Path, ...] = field(default_factory=tuple)
    picker_typing: bool = False
    confirm_run_open: bool = False
    run_name_buffer: str = "unnamed-1"
    run_name_cursor: Optional[int] = None
    run_name_error: str = ""
    existing_names: frozenset[str] = frozenset()
    resumable_names: frozenset[str] = frozenset()
    amend_run: bool = False
    rerun_run: bool = False
    run_name_choice_open: bool = False
    help_visible: bool = False
    status_message: str = ""
    options_state: Optional["OptionsState"] = None
    runs_state: Optional["RunsState"] = None
    # Intent flag set by the schedule view's toggle handler; the curses
    # layer drains it on its next iteration through :func:`_apply_one`,
    # writing the row via :meth:`~common.bench_db.BenchDB.set_schedule_config`
    # and folding the new :class:`SuiteConfig` back into the schedule.
    # ``(entry_index, suite_name, new_config)`` — the entry's other
    # run-parameters (``samples``/``amend``/``skip_failed``) are passed
    # through unchanged, so a toggle flip never silently rewrites them.
    pending_toggle_write: Optional[tuple[int, str, SuiteConfig]] = None
    # Per-suite result summary cache for the focused entry's database:
    # ``{(entry_index, suite_name): SuiteResultSummary}``. The curses
    # loop in :mod:`bench.config.tui` populates and refreshes it — the
    # reducer is pure and never touches the database. The renderer
    # reads from it to render both the per-row headline (result count
    # + last sample timestamp) and the side panel (full metadata
    # snapshot). Kept on the state, not the schedule, so it does not
    # leak into the runner or the persisted ``schedule_config`` row.
    summaries: dict[tuple[int, str], "SuiteResultSummary"] = field(default_factory=dict)
    # The DB path each cached summary was read against; ``None`` when
    # the entry has no path yet. The curses loop drops a slot whose
    # recorded path no longer matches the entry's current ``db`` (an
    # edit or a picker select can move it), then repopulates with the
    # new path. Keeping the path alongside the summary means a
    # previous summary never bleeds into the next render.
    summary_db_paths: dict[tuple[int, str], Optional[str]] = field(default_factory=dict)


PARENT_ENTRY = Path("..")


def _scan_db_picker_entries(directory: Path) -> tuple[Path, ...]:
    """Return the subdirectories and ``*.db`` files in ``directory``.

    ``..`` comes first if ``directory`` is not at the filesystem root.
    Subdirectories stay navigable (``enter`` descends); ``*.db`` files
    are the selectable leaves (``s`` selects one).
    """
    if not directory.exists() or not directory.is_dir():
        return ()
    found: list[Path] = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        try:
            if child.is_dir() or child.suffix == ".db":
                found.append(child)
        except OSError:
            continue
    if directory.parent != directory:
        return (PARENT_ENTRY, *found)
    return tuple(found)


def _open_picker_at(entry: Entry, state: TuiState, cwd: Path) -> TuiState:
    seed = entry.db.parent if entry.db is not None else cwd
    if not seed.exists() or not seed.is_dir():
        seed = cwd
    entries = _scan_db_picker_entries(seed)
    cursor = 0
    for index, candidate in enumerate(entries):
        if candidate == PARENT_ENTRY:
            continue
        if entry.db is not None and candidate == entry.db:
            cursor = index
            break
    return replace(
        state,
        picker_open=True,
        picker_dir=seed,
        picker_cursor=cursor,
        picker_entries=entries,
        picker_typing=False,
        editor_open=False,
    )


def _edit_buffer_for_field(entry: Entry, field_index: int) -> str:
    """Pre-fill buffer for the inline editor.

    Only ``DB_PATH_FIELD``/``START_DELAY_FIELD`` reach here — every
    suite field is a toggle (``enabled``), which flips immediately via
    :func:`_flip_toggle` and never opens the inline editor.
    """
    if field_index == DB_PATH_FIELD:
        return str(entry.db) if entry.db is not None else ""
    return str(entry.start_delay_minutes)


def _apply_edit(entry: Entry, field_index: int, buffer: str) -> Entry:
    if field_index == DB_PATH_FIELD:
        return entry.with_db(Path(buffer) if buffer else None)
    try:
        return entry.with_delay(int(buffer) if buffer else 0)
    except ValueError:
        return entry


def _flip_toggle(entry: Entry, field_index: int) -> Entry:
    name = _suite_name_at(field_index)
    config = entry.suite(name)
    return entry.with_suite(name, replace(config, enabled=not config.enabled))


def schedule_after_keystrokes(
    schedule: Schedule,
    keys: Iterable[str],
    cwd: Path,
    initial_state: Optional[TuiState] = None,
) -> tuple[Schedule, TuiState]:
    """Apply ``keys`` to ``schedule`` and return the new schedule and final state.

    ``q`` raises :class:`~bench.config.tui.ScheduleCancelled`; ``r`` followed
    by ``enter`` returns the schedule with ``state.outcome == "running"``.
    Everything else stays in ``"editing"``. The input ``schedule`` is not
    mutated. ``initial_state`` lets the curses layer resume from the
    previous iteration's state instead of resetting every call.
    """
    state = initial_state if initial_state is not None else TuiState()
    current = schedule
    for key in keys:
        if state.confirm_run_open:
            state = handle_run_name(state, key)
            if state.outcome == "running":
                return replace(current, name=state.run_name_buffer.strip(), amend=state.amend_run, rerun=state.rerun_run), state
            continue
        if state.editor_open:
            current, state = _handle_editor(current, state, key)
            continue
        if state.picker_open:
            current, state = _handle_picker(current, state, key, cwd)
            continue
        current, state = _handle_normal(current, state, key, cwd)
    return current, state


def _is_sub_field_focusable(entry: Entry, field_index: int) -> bool:
    """Every field is focusable — each suite has only its enabled toggle now."""
    return True


def _next_focusable_field(schedule: Schedule, field_index: int, direction: int) -> int:
    entry = schedule.entries[0]
    current = field_index
    while True:
        next_index = current + direction
        if next_index < 0:
            return 0
        if next_index >= FIELD_COUNT_PER_ENTRY:
            return FIELD_COUNT_PER_ENTRY - 1
        if _is_sub_field_focusable(entry, next_index):
            return next_index
        current = next_index


def _first_focusable_field(schedule: Schedule) -> int:
    entry = schedule.entries[0]
    for field_index in range(FIELD_COUNT_PER_ENTRY):
        if _is_sub_field_focusable(entry, field_index):
            return field_index
    return 0


def _last_focusable_field(schedule: Schedule) -> int:
    entry = schedule.entries[0]
    for field_index in range(FIELD_COUNT_PER_ENTRY - 1, -1, -1):
        if _is_sub_field_focusable(entry, field_index):
            return field_index
    return FIELD_COUNT_PER_ENTRY - 1


def _handle_normal(
    schedule: Schedule,
    state: TuiState,
    key: str,
    cwd: Path,
) -> tuple[Schedule, TuiState]:
    if key == "q":
        raise ScheduleCancelled()
    if key == "?":
        return schedule, replace(state, help_visible=not state.help_visible)
    if key == "r":
        return schedule, replace(state, confirm_run_open=True, run_name_cursor=None, run_name_error="")
    if key == "j" or key == "down":
        focused = _next_focusable_field(schedule, state.focused_field, 1)
        return schedule, replace(state, focused_field=focused)
    if key == "k" or key == "up":
        focused = _next_focusable_field(schedule, state.focused_field, -1)
        return schedule, replace(state, focused_field=focused)
    if key == "g":
        return schedule, replace(state, focused_field=_first_focusable_field(schedule))
    if key == "G":
        return schedule, replace(state, focused_field=_last_focusable_field(schedule))
    entry = schedule.entries[state.focused_entry]
    kind = field_kind(state.focused_field)
    if kind == "toggle":
        if key in ("space", "enter"):
            name = _suite_name_at(state.focused_field)
            config = entry.suite(name)
            new_config = replace(config, enabled=not config.enabled)
            new_entry = entry.with_suite(name, new_config)
            new_state = replace(
                state,
                pending_toggle_write=(state.focused_entry, name, new_config),
            )
            return schedule.with_entry(state.focused_entry, new_entry), new_state
        return schedule, state
    if key in ("space", "enter"):
        if state.focused_field == DB_PATH_FIELD:
            return schedule, _open_picker_at(entry, state, cwd)
        return schedule, replace(
            state,
            editor_open=True,
            editor_buffer=_edit_buffer_for_field(entry, state.focused_field),
            editor_cursor=None,
        )
    return schedule, state


def _handle_editor(
    schedule: Schedule,
    state: TuiState,
    key: str,
) -> tuple[Schedule, TuiState]:
    if key == "escape":
        if state.picker_open and state.picker_typing:
            return schedule, _close_picker(state)
        return schedule, _close_editor_and_picker(state)
    if key == "enter":
        if state.picker_open and state.picker_typing:
            buffer = state.editor_buffer
            target = Path(buffer) if buffer else None
            entry = schedule.entries[state.focused_entry].with_db(target)
            new_schedule = schedule.with_entry(state.focused_entry, entry)
            return new_schedule, _close_editor_and_picker(state)
        entry = schedule.entries[state.focused_entry]
        new_entry = _apply_edit(entry, state.focused_field, state.editor_buffer)
        new_schedule = schedule.with_entry(state.focused_entry, new_entry)
        return new_schedule, _close_editor_and_picker(state)
    buffer, cursor = edit_text(state.editor_buffer, state.editor_cursor, key)
    return schedule, replace(state, editor_buffer=buffer, editor_cursor=cursor)


def _close_editor_and_picker(state: TuiState) -> TuiState:
    return replace(
        state,
        editor_open=False,
        editor_buffer="",
        editor_cursor=None,
        picker_open=False,
        picker_dir=None,
        picker_cursor=0,
        picker_entries=(),
        picker_typing=False,
    )


def _handle_picker(
    schedule: Schedule,
    state: TuiState,
    key: str,
    cwd: Path,
) -> tuple[Schedule, TuiState]:
    if key == "escape":
        return schedule, _close_picker(state)
    if key == "/":
        return schedule, replace(
            state,
            editor_open=True,
            editor_buffer="",
            editor_cursor=None,
            picker_typing=True,
        )
    if key == "s":
        return _select_picker_entry(schedule, state)
    if key in ("j", "down"):
        if not state.picker_entries:
            return schedule, state
        cursor = (state.picker_cursor + 1) % len(state.picker_entries)
        return schedule, replace(state, picker_cursor=cursor)
    if key in ("k", "up"):
        if not state.picker_entries:
            return schedule, state
        cursor = (state.picker_cursor - 1) % len(state.picker_entries)
        return schedule, replace(state, picker_cursor=cursor)
    if key == "g":
        return schedule, replace(state, picker_cursor=0)
    if key == "G":
        if not state.picker_entries:
            return schedule, state
        return schedule, replace(state, picker_cursor=len(state.picker_entries) - 1)
    if key == "enter":
        if not state.picker_entries:
            return schedule, state
        picked = state.picker_entries[state.picker_cursor]
        if picked == PARENT_ENTRY:
            parent = state.picker_dir.parent if state.picker_dir is not None else cwd
            return schedule, _set_picker_dir(state, parent)
        if picked.is_dir():
            return schedule, _set_picker_dir(state, picked)
        return schedule, state
    return schedule, state


def _select_picker_entry(schedule: Schedule, state: TuiState) -> tuple[Schedule, TuiState]:
    if not state.picker_entries:
        return schedule, state
    picked = state.picker_entries[state.picker_cursor]
    if picked == PARENT_ENTRY or picked.is_dir():
        return schedule, state
    entry = schedule.entries[state.focused_entry].with_db(picked)
    new_schedule = schedule.with_entry(state.focused_entry, entry)
    return new_schedule, _close_picker(state)


def _set_picker_dir(state: TuiState, new_dir: Path) -> TuiState:
    seed = new_dir if new_dir.exists() and new_dir.is_dir() else (new_dir if new_dir.exists() else state.picker_dir)
    if seed is None or not seed.exists() or not seed.is_dir():
        return state
    entries = _scan_db_picker_entries(seed)
    return replace(
        state,
        picker_dir=seed,
        picker_entries=entries,
        picker_cursor=0,
    )


def _close_picker(state: TuiState) -> TuiState:
    return replace(
        state,
        picker_open=False,
        picker_dir=None,
        picker_cursor=0,
        picker_entries=(),
        picker_typing=False,
        editor_open=False,
        editor_buffer="",
        editor_cursor=None,
    )


def handle_run_name(state: TuiState, key: str) -> TuiState:
    if key == "escape":
        return replace(state, confirm_run_open=False, run_name_error="", amend_run=False,
                       rerun_run=False, run_name_choice_open=False)
    if state.run_name_choice_open:
        if key in ("a", "up"):
            if state.run_name_buffer.strip() not in state.resumable_names:
                return replace(state, amend_run=False, rerun_run=False,
                               run_name_error="No unfinished suites to amend; choose rerun.")
            return replace(state, amend_run=True, rerun_run=False, run_name_error="Name already exists; amend keeps saved samples.")
        if key in ("r", "down"):
            return replace(state, amend_run=False, rerun_run=True,
                           run_name_error="Resets enabled suites; disabled suites keep their data.")
        if key == "enter":
            if state.amend_run or state.rerun_run:
                return replace(state, confirm_run_open=False, run_name_choice_open=False, outcome="running")
            return replace(state, run_name_error="Choose rerun, or Esc to cancel.")
    if key == "enter":
        name = state.run_name_buffer.strip()
        if not name:
            return replace(state, run_name_error="Enter a benchmark name")
        if name in state.existing_names:
            return replace(state, run_name_choice_open=True, amend_run=name in state.resumable_names,
                           rerun_run=False, run_name_error="Name already exists; choose amend or rerun.")
        return replace(state, confirm_run_open=False, outcome="running", amend_run=False, rerun_run=False)
    buffer, cursor = edit_text(state.run_name_buffer, state.run_name_cursor, key)
    if buffer == state.run_name_buffer and cursor == state.run_name_cursor:
        return state
    return replace(
        state,
        run_name_buffer=buffer,
        run_name_cursor=cursor,
        run_name_error="",
        amend_run=False,
        rerun_run=False,
        run_name_choice_open=False,
    )
