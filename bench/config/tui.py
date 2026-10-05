#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""The curses entry point for ``python -m bench.config``."""

from __future__ import annotations

import curses
import datetime
import json
import os
import sqlite3
import sys
import textwrap
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

# ncurses waits ESCDELAY milliseconds (1000 by default) after a lone ESC
# byte to see whether it is the start of a longer escape sequence before
# reporting a standalone Escape keypress. Must be set before curses.wrapper
# calls initscr(); ncurses only reads the env var at that point.
os.environ.setdefault("ESCDELAY", "25")

from common.bench_db import BenchDB
from common.suites import SUITE_OPTIONS

from bench.config import SUITE_ORDER, Schedule, SuiteConfig, db_ready
from bench.config.text_editor import visible_text
from bench.config.options import load_options_snapshot
from bench.config.options_reducer import options_after_keystrokes
from bench.config.runs import load_runs, render_runs, runs_after_key
from bench.config.summary import SuiteResultSummary, summarize_suite
from bench.config.tui_reducer import (
    DB_PATH_FIELD,
    FIELD_COUNT_PER_ENTRY,
    PARENT_ENTRY,
    START_DELAY_FIELD,
    ScheduleCancelled,
    TuiState,
    _suite_name_at as _reducer_suite_name_at,
    schedule_after_keystrokes,
)


HELP_LINE = "q: quit · arrows: navigate · space: flip · r: run · h: saved runs"
LABEL_COL_WIDTH = 22
CLEAR_SCREEN_ESCAPE = "\033[2J\033[H"

PICKER_POPUP_MIN_WIDTH = 30
PICKER_POPUP_MIN_HEIGHT = 7
PICKER_POPUP_MAX_WIDTH = 80
PICKER_POPUP_MAX_HEIGHT = 24

MIN_ROWS_FOR_FRAME = 7
MIN_COLS_FOR_FRAME = 30
MIN_COLS_FOR_SIDE_PANEL = 80
SIDE_PANEL_WIDTH = 48
SIDE_PANEL_HEADLINE_TS_FORMAT = "%Y-%m-%d %H:%M UTC"


@dataclass(frozen=True)
class RenderedLine:
    """One screen row, with both the plain text and the per-segment curses attrs.

    ``text`` is the concatenation of all segment texts; tests can keep
    asserting on strings without caring about colour. ``segments`` is
    the same content split into ``(text, attr)`` pairs where ``attr``
    is a curses attribute bitmask (``0`` for normal text,
    ``curses.A_DIM`` for grayed-out text).
    """

    text: str
    segments: tuple[tuple[str, int], ...]

    @classmethod
    def plain(cls, text: str) -> "RenderedLine":
        return cls(text=text, segments=((text, 0),))

    @classmethod
    def dim(cls, text: str) -> "RenderedLine":
        return cls(text=text, segments=((text, curses.A_DIM)))


def translate_key(raw: int) -> Optional[str]:
    """Map a curses ``getch()`` integer to the reducer's keystroke string.

    Returns ``None`` for keys that should be ignored (``curses.ERR`` and
    unknown special keys). Printable characters are returned unchanged so
    the inline editor and path-entry mode can accept arbitrary typing.
    ``\\x03`` (Ctrl-C) is mapped to ``"q"`` so the reducer's
    :class:`ScheduleCancelled` fires from anywhere in the loop.
    """
    if raw == curses.ERR:
        return None
    if raw == 3:
        return "q"
    if raw == curses.KEY_HOME or raw == 1:
        return "home"
    if raw == curses.KEY_END or raw == 5:
        return "end"
    if raw == curses.KEY_DC:
        return "delete"
    if raw == 21:
        return "clear"
    if raw == curses.KEY_PPAGE:
        return "pageup"
    if raw == curses.KEY_NPAGE:
        return "pagedown"
    if raw == curses.KEY_UP:
        return "up"
    if raw == curses.KEY_DOWN:
        return "down"
    if raw == curses.KEY_LEFT:
        return "left"
    if raw == curses.KEY_RIGHT:
        return "right"
    if raw == curses.KEY_ENTER or raw == 10 or raw == 13:
        return "enter"
    if raw == curses.KEY_BACKSPACE or raw == 127 or raw == 8:
        return "backspace"
    if raw == 27:
        return "escape"
    if raw == ord(" "):
        return "space"
    if raw < 0 or raw > 0x10FFFF:
        return None
    try:
        ch = chr(raw)
    except ValueError:
        return None
    return ch


def _on_off(value: bool) -> str:
    return "On" if value else "Off"


def _field_label(field_index: int) -> str:
    if field_index == DB_PATH_FIELD:
        return "DB PATH"
    return "START DELAY (minutes)"


def _field_value(entry, field_index: int) -> tuple[str, bool]:
    """Return ``(value_text, is_off)`` for ``DB_PATH_FIELD``/``START_DELAY_FIELD``.

    ``is_off`` chooses the dim attribute; neither top-level field has an
    inactive state, so it is always ``False`` here. Suite rows (each
    just an enabled toggle) are rendered directly in :func:`_field_line`.
    """
    if field_index == DB_PATH_FIELD:
        return (str(entry.db) if entry.db is not None else "", False)
    return (str(entry.start_delay_minutes), False)


def _missing_marker(entry) -> str:
    return "  [missing]" if not db_ready(entry.db) else ""


def _suite_name_at(field_index: int) -> str:
    return _reducer_suite_name_at(field_index)


def _format_timestamp(timestamp: Optional[str]) -> str:
    """Render an ISO-8601 ``timestamp_utc`` for the side panel headline.

    Falls back to the raw string when parsing fails (a malformed value
    in the database should still be shown to the user rather than
    silently replaced with ``"unknown"`` — the value is theirs, and a
    warning is more useful than an empty cell).
    """
    if not timestamp:
        return "—"
    try:
        parsed = datetime.datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    return parsed.strftime(SIDE_PANEL_HEADLINE_TS_FORMAT)


def _summary_headline(summary: SuiteResultSummary) -> str:
    """The one-line ``N results · YYYY-MM-DD HH:MM UTC`` tail shown on a suite row.

    Empty string when the summary has no rows yet — an empty tail is
    silent rather than printing ``0 results`` on every fresh database,
    which would add visual noise to a setup that has just been seeded.
    A partial run (rows plus failures) shows both counts so the user
    can see at a glance that the row count alone is not the whole story.
    """
    if summary.result_count == 0 and summary.failure_count == 0 and summary.skipped_count == 0:
        return ""
    parts: list[str] = []
    if summary.result_count:
        parts.append(f"{summary.result_count} result{'s' if summary.result_count != 1 else ''}")
    if summary.failure_count:
        parts.append(f"{summary.failure_count} failure{'s' if summary.failure_count != 1 else ''}")
    if summary.skipped_count:
        parts.append(f"{summary.skipped_count} skipped")
    if summary.last_timestamp:
        parts.append(f"last {_format_timestamp(summary.last_timestamp)}")
    return " · " + " · ".join(parts)


def _field_line(
    entry, field_index: int, cols: int, state: TuiState
) -> RenderedLine:
    if field_index >= 2:
        name = _suite_name_at(field_index)
        config = entry.suite(name)
        missing = _missing_marker(entry)
        on_attr = curses.A_DIM if not config.enabled else 0
        summary = state.summaries.get((state.focused_entry, name))
        headline = _summary_headline(summary) if summary is not None else ""
        tail_text = f"{headline}{missing}"
        line_plain = f"{name}".ljust(LABEL_COL_WIDTH) + f"[{_on_off(config.enabled)}]{tail_text}"
        segments = (
            (f"{name}".ljust(LABEL_COL_WIDTH), 0),
            (f"[{_on_off(config.enabled)}]", on_attr),
            (headline, 0),
            (missing, curses.A_DIM) if missing else ("", 0),
        )
    else:
        label = _field_label(field_index)
        value, is_off = _field_value(entry, field_index)
        editing = (
            state.editor_open
            and not state.picker_typing
            and state.focused_field == field_index
        )
        display_value = (
            visible_text(state.editor_buffer, state.editor_cursor, cols - LABEL_COL_WIDTH - 2)[0] if editing else value
        )
        dim_attr = curses.A_DIM if (is_off and not editing) else 0
        prefix = f"{label}".ljust(LABEL_COL_WIDTH)
        line_plain = f"{prefix}: {display_value}"
        segments = (
            (prefix, 0),
            (": ", 0),
            (display_value, dim_attr),
            ("", 0),
        )
    text = line_plain
    if len(text) > cols:
        text = text[:cols]
    return RenderedLine(text=text, segments=_trunc_segments(segments, cols))


def _trunc_segments(
    segments: tuple[tuple[str, int], ...], cols: int
) -> tuple[tuple[str, int], ...]:
    if cols <= 0:
        return ()
    remaining = cols
    out: list[tuple[str, int]] = []
    for text, attr in segments:
        if remaining <= 0:
            break
        if len(text) <= remaining:
            out.append((text, attr))
            remaining -= len(text)
        else:
            out.append((text[:remaining], attr))
            remaining = 0
    return tuple(out)


def _bottom_line(state: TuiState) -> str:
    if state.picker_open:
        if state.picker_typing:
            return "enter: commit · esc: cancel · Ctrl-U: clear"
        return "enter: descend · s: select · /: type path · esc: cancel"
    if state.confirm_run_open:
        return "enter: run · esc: cancel"
    if state.editor_open:
        return "enter: commit · esc: cancel · Ctrl-U: clear"
    if state.status_message:
        return state.status_message
    return HELP_LINE


def _focused_suite(state: TuiState) -> Optional[str]:
    """The suite name shown in the side panel, or ``None`` when the focus is elsewhere.

    Returns the suite name for any field past the top two (which map to
    the suites in :data:`SUITE_ORDER`), and ``None`` for
    ``DB_PATH_FIELD``/``START_DELAY_FIELD`` — those don't have a
    per-suite summary to display, so the panel renders an empty state
    on a wide screen rather than stretching the focused field's value
    into a misleading summary.
    """
    if state.focused_field < 2:
        return None
    return _suite_name_at(state.focused_field)


def _format_config_value(value: object) -> str:
    """Render a stored uniform/varying value as the side panel wants it.

    Mirrors :func:`bench.config.options_tui._scalar_to_text` so the two
    views render the same value identically — a number stays bare, a
    string drops its quotes, a list joins with ``, ``. The summary's
    intent is "what was this suite measured under?", and ``json.dumps``
    would muddle that by quoting strings and wrapping scalars.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(_format_config_value(item) for item in value)
    return json.dumps(value)


def right_panel_lines(state: TuiState, schedule: Schedule, width: int) -> list[str]:
    """Wrapped info-panel text for the focused suite, mirroring the options view.

    Pure and independent of curses — the same ``(TuiState, Schedule)``
    plus a target width yields the same panel text, so the curses
    loop's overlay and the unit tests both rely on one definition.
    The body is built top to bottom: the suite name as a header, a
    blank line, the headline counts (``Results:``, ``Last sample:``,
    ``Failures:``/``Skipped:`` only when non-zero), then a section
    each for stored uniform and stored varying options. A focused
    non-suite field renders a single hint line so the panel does not
    vanish on a narrow focus.
    """
    if width <= 0 or not schedule.entries:
        return []
    focused_entry = schedule.entries[min(state.focused_entry, len(schedule.entries) - 1)]
    summary: Optional[SuiteResultSummary] = None
    suite_name = _focused_suite(state)
    if suite_name is not None:
        summary = state.summaries.get((state.focused_entry, suite_name))
    if suite_name is None:
        return ["[focus a suite to see results]", ""]
    if summary is None:
        return [
            f"suite: {suite_name}",
            "",
            "loading results…",
        ]
    if not focused_entry or not db_ready(focused_entry.db):
        return [
            f"suite: {suite_name}",
            "",
            "no database — running this suite",
            "against this entry would scaffold it.",
        ]
    lines: list[str] = [f"suite: {suite_name}", ""]
    if summary.has_results:
        lines.append(f"Results: {summary.result_count}")
        lines.append(f"Last sample: {_format_timestamp(summary.last_timestamp)}")
    else:
        lines.append("Results: none yet")
    if summary.failure_count:
        lines.append(f"Failures: {summary.failure_count}")
    if summary.skipped_count:
        lines.append(f"Skipped: {summary.skipped_count}")
    declared = SUITE_OPTIONS.get(suite_name) or {}
    uniform_order = [name for name, opt in declared.items() if opt.kind == "uniform"]
    varying_order = [name for name, opt in declared.items() if opt.kind == "varying"]
    uniform_set = [(name, summary.stored_uniform.get(name)) for name in uniform_order if name in summary.stored_uniform]
    varying_set = [(name, summary.stored_varying.get(name)) for name in varying_order if name in summary.stored_varying]
    if uniform_set:
        lines.append("")
        lines.append("Stored uniform:")
        for name, value in uniform_set:
            lines.append(f"  {name} = {_format_config_value(value)}")
    if varying_set:
        lines.append("")
        lines.append("Stored varying:")
        for name, value in varying_set:
            value_text = _format_config_value(value)
            lines.append(f"  {name} = {value_text}")
            for extra in textwrap.wrap(value_text, width - 4)[:0:-1]:
                lines.append(f"    {extra}")
    metadata_lines = _metadata_panel_lines(summary.metadata)
    if metadata_lines:
        lines.append("")
        lines.extend(metadata_lines)
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width) or [""])
    return wrapped


_METADATA_PANEL_FIELDS: tuple[str, ...] = (
    "hostname",
    "platform",
    "python_version",
    "o6_version",
    "partial",
)


def _metadata_panel_lines(metadata: dict) -> list[str]:
    """Render the highlights of ``metadata`` for the side panel.

    Skips fields with empty / ``None`` values and the volatile
    ``timestamp_utc`` (already shown in the headline). The
    ``partial`` flag is rendered as a plain ``true``/``false`` rather
    than :func:`_format_config_value` so the panel never quietly
    surfaces a partial run without flagging it.
    """
    rows: list[str] = []
    visible = {
        name: metadata.get(name)
        for name in _METADATA_PANEL_FIELDS
        if metadata.get(name) not in (None, "", False)
    }
    if not visible:
        return rows
    rows.append("Environment:")
    for name, value in visible.items():
        rows.append(f"  {name} = {value}")
    return rows


def _column_split(content_w: int) -> tuple[int, int]:
    """``(left_w, right_w)`` for the suite list and the info panel.

    Below :data:`MIN_COLS_FOR_SIDE_PANEL` the panel disappears and the
    list gets the full width; a panel needs real room to be worth
    showing, and a cramped terminal is more useful with the headline
    alone than with both halves truncated.
    """
    if content_w < MIN_COLS_FOR_SIDE_PANEL:
        return content_w, 0
    right_w = SIDE_PANEL_WIDTH
    left_w = content_w - right_w - 3
    return left_w, right_w


def info_panel_origin(rows: int, cols: int) -> Optional[tuple[int, int, int, int]]:
    """``(top, left, height, width)`` of the side panel, or ``None``.

    Returns ``None`` when the screen is too small/narrow for a frame or
    too narrow for the panel to be worth showing (see
    :data:`MIN_COLS_FOR_SIDE_PANEL`). Kept as a separate seam so the
    curses loop can overlay the panel after drawing the main list —
    the focused row's reverse-video highlight (applied to the *whole*
    row by the curses loop) must not bleed into the panel text.
    """
    framed = rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME
    if not framed:
        return None
    content_w = max(0, (cols - 2) - 2 * FRAME_H_PAD)
    left_w, right_w = _column_split(content_w)
    if right_w <= 0:
        return None
    top = 3
    height = rows - 5
    left = 1 + FRAME_H_PAD + left_w + 3
    return top, left, height, right_w


def render(
    state: TuiState,
    schedule: Schedule,
    rows: int,
    cols: int,
) -> list[RenderedLine]:
    """Render the TUI screen as a list of :class:`RenderedLine` rows.

    The first row is the title; the last row is the bottom line (help,
    editor, picker, or confirmation). When the screen is large enough
    (see :data:`MIN_ROWS_FOR_FRAME` and :data:`MIN_COLS_FOR_FRAME`),
    the pane is wrapped in a frame with horizontal separators under
    the title and above the status line. On a wide enough screen the
    rightmost columns are reserved for the info panel via
    :func:`_column_split`; the body rows leave that space blank and
    the curses loop overlays :func:`right_panel_lines` at the
    coordinates :func:`info_panel_origin` returns. The caller applies
    reverse-video highlighting based on :func:`focus_positions` and
    overlays the picker popup based on :func:`picker_box`.
    """
    if rows < 3 or cols < 20:
        return [RenderedLine.plain("") for _ in range(max(rows, 0))]

    title = "bench.config"
    title_line = RenderedLine.plain(_fit(title, cols))

    focused_entry_index = state.focused_entry if schedule.entries else 0
    focused_entry = schedule.entries[focused_entry_index] if schedule.entries else None

    framed = rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME
    field_width = _column_split(cols - 2 - 2 * FRAME_H_PAD)[0] if framed else cols
    field_lines: list[RenderedLine] = []
    if focused_entry is not None:
        for field_index in range(FIELD_COUNT_PER_ENTRY):
            field_lines.append(_field_line(focused_entry, field_index, field_width, state))
    else:
        field_lines.append(RenderedLine.plain("(no entries)"))

    bottom_line = RenderedLine.plain(_fit(_bottom_line(state), cols))

    if rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME:
        content_w = max(0, (cols - 2) - 2 * FRAME_H_PAD)
        left_w, _right_w = _column_split(content_w)
        has_panel = _right_w > 0
        return _frame_layout(title_line, field_lines, bottom_line, rows, cols, left_w, has_panel)
    return _minimal_layout(title_line, field_lines, bottom_line, rows, cols)


def _minimal_layout(
    title: RenderedLine,
    field_lines: list[RenderedLine],
    bottom: RenderedLine,
    rows: int,
    cols: int,
) -> list[RenderedLine]:
    body_rows = rows - 2
    out: list[RenderedLine] = [title]
    for i in range(body_rows):
        if i < len(field_lines):
            line = field_lines[i]
        else:
            line = RenderedLine.plain("")
        if len(line.text) > cols:
            line = RenderedLine(text=line.text[:cols], segments=_trunc_segments(line.segments, cols))
        out.append(line)
    out.append(bottom)
    return out


def _frame_layout(
    title: RenderedLine,
    field_lines: list[RenderedLine],
    bottom: RenderedLine,
    rows: int,
    cols: int,
    left_w: int,
    has_panel: bool,
) -> list[RenderedLine]:
    inner_w = cols - 2
    sep = "─" * inner_w
    sep_line = RenderedLine.plain("├" + sep + "┤")
    frame_top = RenderedLine.plain("┌" + sep + "┐")
    title_wrapped = _wrap_inside(_with_divider(title, left_w, has_panel), inner_w)
    bottom_wrapped = _wrap_inside(_with_divider(bottom, left_w, has_panel), inner_w)
    body_rows = rows - 5
    body: list[RenderedLine] = []
    for i in range(body_rows):
        if i < len(field_lines):
            body.append(_wrap_inside(_with_divider(field_lines[i], left_w, has_panel), inner_w))
        else:
            blank_line = RenderedLine.plain("") if has_panel else RenderedLine.plain("")
            body.append(_wrap_inside(_with_divider(blank_line, left_w, has_panel), inner_w))
    return [frame_top, title_wrapped, sep_line, *body, sep_line, bottom_wrapped]


def _with_divider(line: RenderedLine, left_w: int, has_panel: bool) -> RenderedLine:
    """Pad ``line`` to ``left_w`` and append the vertical rule the info panel sits past.

    Applied to every body row, blanks included, so the rule runs
    unbroken down the pane rather than only appearing next to option
    rows. Mirrors :func:`bench.config.options_tui._with_divider`.
    """
    if not has_panel:
        return line
    text = line.text.ljust(left_w)[:left_w] + " │"
    attr = line.segments[0][1] if line.segments else 0
    return RenderedLine(text=text, segments=((text, attr),))


FRAME_H_PAD = 2


def _wrap_inside(line: RenderedLine, inner_w: int) -> RenderedLine:
    content_w = max(0, inner_w - 2 * FRAME_H_PAD)
    text = "│" + " " * FRAME_H_PAD + line.text.ljust(content_w)[:content_w] + " " * FRAME_H_PAD + "│"
    pad_segments = ((" " * FRAME_H_PAD, 0),)
    text_content = line.text.ljust(content_w)[:content_w]
    text_content_len = sum(len(s[0]) for s in _trunc_segments(line.segments, content_w))
    trailing_pad_w = content_w - text_content_len
    trailing_pad = (" " * trailing_pad_w, 0) if trailing_pad_w > 0 else ("", 0)
    segments = (
        ("│", 0),
    ) + pad_segments + _trunc_segments(line.segments, content_w) + (trailing_pad,) + pad_segments + (
        ("│", 0),
    )
    return RenderedLine(text=text, segments=segments)


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:width]


def _picker_box(rows: int, cols: int) -> tuple[int, int, int, int]:
    """Return ``(top, left, height, width)`` for the picker popup.

    The box is centred on the screen; height and width are clamped so
    the box always fits between the title row and the bottom line.
    """
    max_width = min(PICKER_POPUP_MAX_WIDTH, cols - 2)
    max_height = min(PICKER_POPUP_MAX_HEIGHT, rows - 2)
    width = max(PICKER_POPUP_MIN_WIDTH, max_width) if max_width >= PICKER_POPUP_MIN_WIDTH else max_width
    height = max(PICKER_POPUP_MIN_HEIGHT, max_height) if max_height >= PICKER_POPUP_MIN_HEIGHT else max_height
    if width <= 0 or height <= 0 or rows < 3 or cols < 3:
        return 1, 1, max(0, rows - 2), max(0, cols - 2)
    left = max(0, (cols - width) // 2)
    top = max(1, (rows - height) // 2)
    if top + height >= rows:
        top = max(1, rows - height - 1)
    return top, left, height, width


def focus_positions(
    state: TuiState,
    schedule: Schedule,
    rows: int,
) -> Optional[int]:
    """Return the body row to highlight (1-indexed from the title), or ``None``.

    Row 0 is the title, the last row is the bottom line, so the focused
    row lives between ``1`` and ``rows - 2``.
    """
    if rows < 3 or not schedule.entries:
        return None
    if state.picker_open:
        return None
    pane_rows = _pane_row_count(schedule, state)
    body_top = 3 if rows >= MIN_ROWS_FOR_FRAME else 1
    body_rows = (rows - 5) if rows >= MIN_ROWS_FOR_FRAME else (rows - 2)
    target = _focused_field_to_row(schedule, state)
    if 0 <= target < min(pane_rows, body_rows):
        return target + body_top
    return None


def _pane_row_count(schedule: Schedule, state: TuiState) -> int:
    if not schedule.entries:
        return 1
    return FIELD_COUNT_PER_ENTRY


def _focused_field_to_row(schedule: Schedule, state: TuiState) -> int:
    return state.focused_field


def _editor_row(state: TuiState, schedule: Schedule, rows: int) -> Optional[int]:
    """Return the screen row the inline editor's caret should sit on, or ``None``."""
    return focus_positions(state, schedule, rows)


def _apply_one(
    schedule: Schedule,
    state: TuiState,
    key: str,
    cwd: Path,
    screen_size: tuple[int, int] = (24, 80),
) -> tuple[Schedule, TuiState]:
    if state.view == "runs":
        browser = state.runs_state
        assert browser is not None
        if key == "q":
            raise ScheduleCancelled()
        if key in ("escape", "left") and not browser.details_open:
            return schedule, replace(state, view=browser.return_view, runs_state=None)
        return schedule, replace(state, runs_state=runs_after_key(browser, key, *screen_size))
    if key == "h" and not (
        state.confirm_run_open or state.picker_open or state.editor_open
        or (state.options_state is not None and state.options_state.editor_open)
    ):
        db = schedule.entries[state.focused_entry].db if schedule.entries else None
        browser = replace(load_runs(db), return_view=state.view)
        return schedule, replace(state, view="runs", runs_state=browser)
    if key == "r" and not (
        state.confirm_run_open or state.picker_open or state.editor_open
        or (state.options_state is not None and state.options_state.editor_open)
    ):
        from bench.config.names import benchmark_names, default_name, resumable_names

        try:
            names = benchmark_names(schedule)
            resumable = resumable_names(schedule, names)
        except (OSError, sqlite3.Error) as error:
            return schedule, replace(state, status_message=str(error))
        state = replace(
            state,
            existing_names=frozenset(names),
            resumable_names=resumable,
            run_name_buffer=default_name(names),
            run_name_cursor=None,
            amend_run=False,
            rerun_run=False,
            run_name_choice_open=False,
        )
    if state.view == "options":
        new_state = options_after_keystrokes(state, [key])
        new_schedule, new_state = _apply_options_db_writes(schedule, new_state)
        if new_state.outcome == "running":
            new_schedule = replace(new_schedule, name=new_state.run_name_buffer.strip(), amend=new_state.amend_run,
                                   rerun=new_state.rerun_run)
        if state.status_message:
            new_state = replace(new_state, status_message="")
        return new_schedule, new_state
    if (
        key == "enter"
        and not state.confirm_run_open
        and not state.picker_open
        and not state.editor_open
        and state.focused_field >= 2
        and state.focused_entry < len(schedule.entries)
    ):
        entry = schedule.entries[state.focused_entry]
        if entry.db is None or not db_ready(entry.db):
            return schedule, replace(
                state,
                status_message=(
                    f"DB missing — run 'python -m bench {entry.db}' to scaffold it."
                ),
            )
        suite_name = _suite_name_at(state.focused_field)
        snapshot = load_options_snapshot(
            entry.db, suite_name, state.focused_entry, run_params=entry.suite(suite_name)
        )
        return schedule, replace(
            state,
            view="options",
            options_state=snapshot,
            status_message="",
        )
    new_schedule, new_state = schedule_after_keystrokes(schedule, [key], cwd, initial_state=state)
    if state.status_message:
        new_state = replace(new_state, status_message="")
    if new_state.pending_toggle_write is not None:
        new_schedule, new_state = _drain_pending_toggle_write(new_schedule, new_state)
    return new_schedule, new_state


def _drain_pending_toggle_write(schedule: Schedule, state: TuiState) -> tuple[Schedule, TuiState]:
    """Persist the schedule view's toggle flip and clear the intent flag.

    The reducer (``schedule_after_keystrokes``) flips the in-memory
    :class:`~bench.config.SuiteConfig` for the focused suite and records
    :attr:`TuiState.pending_toggle_write` carrying the new config. This
    function writes the full :class:`~bench.config.SuiteConfig` back to
    ``bench.db``'s ``schedule_config`` table so the on/off toggle
    survives across TUI sessions — the options view's run-parameters
    block edits the same row, so both views must agree on it. Any
    failure (missing DB path, SQLite error, locked file) leaves the
    intent flag set with the reason in :attr:`TuiState.status_message`,
    so the user can see what went wrong and the in-memory flip survives
    long enough to retry.
    """
    entry_index, suite_name, new_config = state.pending_toggle_write
    if entry_index >= len(schedule.entries):
        return schedule, replace(state, pending_toggle_write=None)
    entry = schedule.entries[entry_index]
    db_path = entry.db
    if db_path is None:
        return schedule, replace(
            state,
            status_message=f"no DB path for suite {suite_name!r}; toggle not persisted.",
        )
    try:
        store = BenchDB(db_path, suite=suite_name)
        store.set_schedule_config(
            suite_name,
            enabled=new_config.enabled,
            samples=new_config.samples,
            amend=new_config.amend,
            skip_failed=new_config.skip_failed,
        )
    except (OSError, sqlite3.Error) as error:
        return schedule, replace(
            state,
            pending_toggle_write=(entry_index, suite_name, new_config),
            status_message=f"{suite_name} toggle: {error}",
        )
    new_schedule = schedule.with_entry(entry_index, entry.with_suite(suite_name, new_config))
    return new_schedule, replace(state, pending_toggle_write=None, status_message="")


def _apply_options_db_writes(schedule: Schedule, state: TuiState) -> tuple[Schedule, TuiState]:
    """Drain the options view's pending-write intent flags.

    The reducer is pure: it sets an intent flag on a successful
    pre-validation and leaves the actual write to this layer.
    ``pending_commit``/``pending_default_write`` go through
    :meth:`~common.bench_db.BenchDB.set_config` and only touch
    ``state`` (the in-memory snapshot). ``pending_run_param_write``
    additionally goes through
    :meth:`~common.bench_db.BenchDB.set_schedule_config` and folds
    back into ``schedule`` — the run-parameters block edits the same
    conceptual field the schedule view's suite header does, so both
    views must agree on its value. Any failure (rejected ``coerce``,
    missing DB path, SQLite error, locked file) leaves the relevant
    state untouched and surfaces the reason in :attr:`OptionsState.error`.
    """
    options = state.options_state
    if options is None:
        return schedule, state
    if options.pending_commit is not None:
        return schedule, _drain_pending_commit(state)
    if options.pending_default_write is not None:
        return schedule, _drain_pending_default_write(state)
    if options.pending_run_param_write is not None:
        return _drain_pending_run_param_write(schedule, state)
    return schedule, state


def _drain_pending_commit(state: TuiState) -> TuiState:
    options = state.options_state
    assert options is not None and options.pending_commit is not None
    name, encoded = options.pending_commit
    return _write_option(state, name, encoded)


def _drain_pending_default_write(state: TuiState) -> TuiState:
    options = state.options_state
    assert options is not None and options.pending_default_write is not None
    name = options.pending_default_write
    option = SUITE_OPTIONS[options.suite_name][name]
    if option.default is None:
        return state
    encoded = json.dumps(option.default)
    return _write_option(state, name, encoded)


def _drain_pending_run_param_write(schedule: Schedule, state: TuiState) -> tuple[Schedule, TuiState]:
    options = state.options_state
    assert options is not None and options.pending_run_param_write is not None
    new_params = options.pending_run_param_write
    db_path = options.db_path
    if db_path is None:
        return schedule, replace(
            state,
            options_state=replace(
                options,
                pending_run_param_write=None,
                error=f"no DB path for suite {options.suite_name!r}; cannot write run parameters.",
            ),
        )
    try:
        store = BenchDB(db_path, suite=options.suite_name)
        store.set_schedule_config(
            options.suite_name,
            enabled=new_params.enabled,
            samples=new_params.samples,
            amend=new_params.amend,
            skip_failed=new_params.skip_failed,
        )
    except (OSError, sqlite3.Error) as error:
        return schedule, replace(
            state,
            options_state=replace(
                options,
                pending_run_param_write=None,
                error=f"run parameters ({options.suite_name}): {error}",
            ),
        )
    entry = schedule.entries[options.entry_index]
    new_schedule = schedule.with_entry(options.entry_index, entry.with_suite(options.suite_name, new_params))
    new_state = replace(state, options_state=replace(options, pending_run_param_write=None, error=""))
    return new_schedule, new_state


def _write_option(
    state: TuiState,
    name: str,
    encoded: str,
) -> TuiState:
    options = state.options_state
    assert options is not None
    db_path = options.db_path
    if db_path is None:
        return replace(
            state,
            options_state=replace(
                options,
                pending_commit=None,
                pending_default_write=None,
                error=f"no DB path for suite {options.suite_name!r}; cannot write {name}.",
            ),
        )
    try:
        store = BenchDB(db_path, suite=options.suite_name)
        written = store.set_config(options.suite_name, name, encoded)
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        return replace(
            state,
            options_state=replace(
                options,
                pending_commit=None,
                pending_default_write=None,
                error=f"{name} ({encoded!r}): {error}",
            ),
        )
    new_snapshot = tuple(
        replace(row, current=written, is_set=True) if row.name == name else row
        for row in options.snapshot
    )
    return replace(
        state,
        options_state=replace(
            options,
            snapshot=new_snapshot,
            pending_commit=None,
            pending_default_write=None,
            error="",
        ),
    )


def _draw_picker(
    stdscr,
    state: TuiState,
    rows: int,
    cols: int,
) -> None:
    top, left, height, width = _picker_box(rows, cols)
    inner_w = max(0, width - 2)
    inner_h = max(0, height - 2)

    if state.picker_typing:
        value, cursor = visible_text(state.editor_buffer, state.editor_cursor, inner_w)
        lines = [
            "┌" + "─" * inner_w + "┐",
            "│" + "Database path".ljust(inner_w)[:inner_w] + "│",
            "│" + value.ljust(inner_w) + "│",
            "│" + "enter: commit · esc: cancel · Ctrl-U: clear".ljust(inner_w)[:inner_w] + "│",
            "└" + "─" * inner_w + "┘",
        ]
        for offset, line in enumerate(lines):
            try:
                stdscr.addnstr(top + offset, left, line, cols - left)
            except curses.error:
                pass
        try:
            stdscr.move(top + 2, left + 1 + cursor)
        except curses.error:
            pass
        return

    title = f" Pick database: {state.picker_dir} "
    title = title[:inner_w] if inner_w else ""
    top_border = "┌" + "─" * (width - 2) + "┐"
    title_row = "│" + title.ljust(inner_w)[:inner_w] + "│"
    bottom_hint = "enter: descend · s: select · /: type path · esc: cancel"
    bottom_hint = bottom_hint[:inner_w] if inner_w else ""

    visible_rows = inner_h - 2
    list_lines: list[str] = []
    for entry in state.picker_entries:
        if entry == PARENT_ENTRY:
            label = ".."
        elif entry.is_dir():
            label = entry.name + "/"
        else:
            label = entry.name
        list_lines.append(_fit(label, inner_w))

    cursor = state.picker_cursor if state.picker_entries else -1
    if visible_rows > 0:
        start = max(0, cursor - visible_rows // 2)
        start = min(start, max(0, len(list_lines) - visible_rows))
        shown = list_lines[start:start + visible_rows]
        pad_top = (visible_rows - len(shown)) // 2
        pad_bottom = visible_rows - len(shown) - pad_top

        box: list[str] = []
        box.append(top_border)
        box.append(title_row)
        box.extend(["│" + " " * inner_w + "│"] * pad_top)
        for offset, line in enumerate(shown):
            row_index = start + offset
            marker = "> " if row_index == cursor else "  "
            content = (marker + line).ljust(inner_w)[:inner_w]
            box.append("│" + content + "│")
        box.extend(["│" + " " * inner_w + "│"] * pad_bottom)
        bottom_row = "│" + bottom_hint.ljust(inner_w)[:inner_w] + "│"
        box.append(bottom_row)
        box.append("└" + "─" * (width - 2) + "┘")

        highlight_row = None
        if 0 <= cursor < len(list_lines):
            highlight_row = top + 2 + pad_top + (cursor - start)

        for offset, line in enumerate(box):
            y = top + offset
            if y >= rows:
                break
            try:
                stdscr.addnstr(y, left, line, cols - left)
            except curses.error:
                pass
        if highlight_row is not None and 0 <= highlight_row < rows:
            try:
                content = ("> " + list_lines[cursor]).ljust(inner_w)[:inner_w]
                stdscr.addnstr(highlight_row, left + 1, content, inner_w, curses.A_REVERSE)
            except curses.error:
                pass


def _draw_run_name(stdscr, state: TuiState, rows: int, cols: int) -> None:
    width = min(64, cols)
    if width < 4 or rows < 7:
        return
    inner = width - 4
    value, cursor = visible_text(state.run_name_buffer, state.run_name_cursor, inner)
    content = [
        "Benchmark name",
        value,
        state.run_name_error or "Edit name · Ctrl-U: clear",
        "enter: run · esc: cancel",
    ]
    if state.run_name_choice_open:
        amend = "keep samples; add pending work" if state.run_name_buffer.strip() in state.resumable_names else "no unfinished work"
        content = ["Existing benchmark name", value, state.run_name_error,
                   f"{'>' if state.amend_run else ' '} a: Amend — {amend}",
                   f"{'>' if state.rerun_run else ' '} r: Rerun — clear enabled suites only; current settings",
                   "a/r or arrows: select · Enter: confirm · Esc: cancel"]
    top, left = max(0, (rows - len(content) - 2) // 2), (cols - width) // 2
    lines = ["┌" + "─" * (width - 2) + "┐"]
    lines.extend("│ " + line[:inner].ljust(inner) + " │" for line in content)
    lines.append("└" + "─" * (width - 2) + "┘")
    for offset, line in enumerate(lines):
        try:
            stdscr.addnstr(top + offset, left, line, width)
        except curses.error:
            pass
    try:
        stdscr.move(top + 2, left + 2 + cursor)
    except curses.error:
        pass


def _draw(
    stdscr,
    state: TuiState,
    schedule: Schedule,
    rows: int,
    cols: int,
) -> None:
    if state.view == "runs":
        assert state.runs_state is not None
        texts, highlight = render_runs(state.runs_state, rows, cols)
        lines = [RenderedLine.plain(text) for text in texts]
        panel_origin = None
        panel_lines = []
    elif state.view == "options":
        from bench.config.options_tui import focus_positions as options_focus_positions
        from bench.config.options_tui import info_panel_origin as options_info_panel_origin
        from bench.config.options_tui import render_options
        from bench.config.options_tui import right_panel_lines as options_right_panel_lines

        lines = render_options(state, rows, cols)
        highlight = options_focus_positions(state, rows)
        panel_origin = options_info_panel_origin(rows, cols)
        panel_lines = options_right_panel_lines(state, panel_origin[3]) if panel_origin is not None else []
    else:
        lines = render(state, schedule, rows, cols)
        highlight = focus_positions(state, schedule, rows)
        panel_origin = info_panel_origin(rows, cols)
        panel_lines = right_panel_lines(state, schedule, panel_origin[3]) if panel_origin is not None else []
    try:
        stdscr.erase()
        for y, line in enumerate(lines):
            if y >= rows:
                break
            x = 0
            for text, attr in line.segments:
                if not text:
                    continue
                try:
                    if attr:
                        stdscr.addnstr(y, x, text, cols - x, attr)
                    else:
                        stdscr.addnstr(y, x, text, cols - x)
                except curses.error:
                    pass
                x += len(text)
        if highlight is not None and highlight < rows:
            line = lines[highlight]
            try:
                stdscr.addnstr(highlight, 0, line.text, cols, curses.A_REVERSE)
            except curses.error:
                pass
        if state.view == "options" and state.options_state is not None:
            if panel_origin is not None:
                top, left, height, width = panel_origin
                for i, text in enumerate(panel_lines):
                    if i >= height:
                        break
                    try:
                        stdscr.addnstr(top + i, left, text, width)
                    except curses.error:
                        pass
        elif state.view == "schedule":
            if panel_origin is not None:
                top, left, height, width = panel_origin
                for i, text in enumerate(panel_lines):
                    if i >= height:
                        break
                    try:
                        stdscr.addnstr(top + i, left, text, width)
                    except curses.error:
                        pass
        if state.confirm_run_open:
            _draw_run_name(stdscr, state, rows, cols)
        if state.view == "schedule" and state.picker_open:
            _draw_picker(stdscr, state, rows, cols)
        if state.view == "schedule" and state.editor_open and not state.picker_typing:
            editor_row = _editor_row(state, schedule, rows)
            if editor_row is not None and editor_row < rows:
                try:
                    cursor_x = _inline_editor_cursor_x(state, schedule, cols, rows)
                    stdscr.move(editor_row, cursor_x)
                except curses.error:
                    pass
        elif state.view == "options" and state.options_state is not None and state.options_state.editor_open:
            editor_row = highlight
            if editor_row is not None and editor_row < rows:
                try:
                    cursor_x = _options_editor_cursor_x(state, cols, rows)
                    stdscr.move(editor_row, cursor_x)
                except curses.error:
                    pass
        stdscr.refresh()
    except curses.error:
        pass


def _inline_editor_cursor_x(state: TuiState, schedule: Schedule, cols: int, rows: int = 24) -> int:
    if not schedule.entries:
        return 0
    framed = rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME
    inset = 1 + FRAME_H_PAD if framed else 0
    left_w, _ = _column_split(cols - 2 * inset) if framed else (cols, 0)
    prefix = LABEL_COL_WIDTH + 2
    _, cursor = visible_text(state.editor_buffer, state.editor_cursor, left_w - prefix)
    return min(cols - 1, inset + prefix + cursor)


def _options_editor_cursor_x(state: TuiState, cols: int, rows: int = 24) -> int:
    from bench.config.options_tui import editor_cursor_x

    if state.options_state is None:
        return 0
    return editor_cursor_x(state.options_state, rows, cols)


def tui_loop(stdscr, schedule: Schedule, cwd: Path) -> Schedule:
    """Drive the curses window: render, read a key, feed the reducer, repeat.

    Returns the edited schedule when the user confirms the run prompt
    (``r`` then ``enter``); raises :class:`ScheduleCancelled` when the user
    presses ``q`` (or sends Ctrl-C). The outer :func:`curses.wrapper`
    restores the terminal on every exit, so this loop never calls
    :func:`curses.endwin` itself.

    Summary cache: each iteration refreshes the
    :class:`~bench.config.summary.SuiteResultSummary` for every suite
    whose entry has a ``db`` path on disk and no cached summary yet.
    The cache is keyed by ``(entry_index, suite_name)`` and is dropped
    whenever the entry's DB path changes (the picker or the inline
    editor can both move it), so a stale summary from a previous path
    never bleeds into the next render.
    """
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    state = TuiState()
    current = schedule
    try:
        while True:
            state = _refresh_summaries(current, state)
            rows, cols = stdscr.getmaxyx()
            _draw(stdscr, state, current, rows, cols)
            cursor_visible = bool(
                state.confirm_run_open
                or state.editor_open
                or (state.view == "options" and state.options_state is not None and state.options_state.editor_open)
            )
            try:
                curses.curs_set(1 if cursor_visible else 0)
            except curses.error:
                pass
            raw = stdscr.getch()
            if raw == 3:
                raise ScheduleCancelled()
            keystroke = translate_key(raw)
            if keystroke is None:
                continue
            current, state = _apply_one(current, state, keystroke, cwd, screen_size=(rows, cols))
            state = _drop_stale_summaries(current, state)
            if state.outcome == "running":
                sys.stdout.write(CLEAR_SCREEN_ESCAPE)
                sys.stdout.flush()
                return current
            if state.outcome == "quitting":
                raise ScheduleCancelled()
    except ScheduleCancelled:
        raise
    except KeyboardInterrupt:
        raise ScheduleCancelled() from None


def _refresh_summaries(schedule: Schedule, state: TuiState) -> TuiState:
    """Populate any missing summary cache slots for ``schedule``.

    Read-only over the database (no writes): a missing DB just yields
    a zero summary, never an exception. Errors from ``BenchDB`` are
    likewise swallowed into an empty summary so a corrupt file
    doesn't take the TUI down with it. Returns a new :class:`TuiState`
    with the cache populated — ``TuiState`` is frozen, so the loop
    reassigns the local ``state`` from the return value.
    """
    summaries = dict(state.summaries)
    paths = dict(state.summary_db_paths)
    changed = False
    for entry_index, entry in enumerate(schedule.entries):
        db_path = entry.db
        path_str = str(db_path) if db_path is not None else None
        for suite_name in SUITE_ORDER:
            key = (entry_index, suite_name)
            if key in summaries and paths.get(key) == path_str:
                continue
            summaries[key] = summarize_suite(db_path, suite_name)
            paths[key] = path_str
            changed = True
    if not changed:
        return state
    return replace(state, summaries=summaries, summary_db_paths=paths)


def _drop_stale_summaries(schedule: Schedule, state: TuiState) -> TuiState:
    """Drop cache entries whose entry index no longer corresponds to a real entry.

    :func:`_refresh_summaries` already handles the "DB path moved"
    case — it overwrites the slot with a freshly read summary and
    records the new path. This function only exists to drop entries
    that reference an ``entry_index`` past the schedule's length
    (the schedule view does not currently allow removing entries, so
    this is a defensive cleanup for a future picker feature, not an
    exercised path).
    """
    valid = {entry_index for entry_index in range(len(schedule.entries))}
    summaries = {key: value for key, value in state.summaries.items() if key[0] in valid}
    paths = {key: value for key, value in state.summary_db_paths.items() if key[0] in valid}
    if summaries == state.summaries and paths == state.summary_db_paths:
        return state
    return replace(state, summaries=summaries, summary_db_paths=paths)


def open_tui(schedule: Schedule) -> Schedule:
    """Open the TUI on ``schedule``; return the edited schedule.

    Wraps :func:`tui_loop` in :func:`curses.wrapper` so the terminal is
    restored on any exception. Raises :class:`ScheduleCancelled` if the
    user quits without running.
    """
    return curses.wrapper(tui_loop, schedule, Path.cwd())
