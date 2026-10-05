#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""The pure renderer for the second view of ``python -m bench.config``."""

from __future__ import annotations

import curses
import json
import textwrap
from typing import Optional

from bench.config.text_editor import visible_text
from bench.config.options import OptionsState
from bench.config.options_reducer import run_param_fields, _option, ordered_option_rows
from bench.config.tui import MIN_COLS_FOR_FRAME, MIN_ROWS_FOR_FRAME, RenderedLine
from bench.config.tui_reducer import TuiState


HELP_LINE = "q: quit  h: runs  r: run  enter: edit  space: flip  d: default  esc: back"
LABEL_COL_WIDTH = 22
KIND_LABEL_WIDTH = 10
VALUE_COL_WIDTH = 26
VARYING_TRUNCATE_AT = 8
VARYING_TRUNCATED_KEEP = 2
HEADER_DB_KEY = "db:"
FRAME_H_PAD = 2
MIN_COLS_FOR_SIDE_PANEL = 100
SIDE_PANEL_WIDTH = 32


def _on_off(flag: bool) -> str:
    return "ON" if flag else "OFF"


def _format_value(snapshot_row, option, max_len: int = 0) -> str:
    """Render the current value cell of one option row.

    Strings lose their JSON quotes (``opc.tcp://...`` not
    ``"opc.tcp://..."``); numbers stay bare (``2000``); a missing row
    (``is_set`` false) renders as ``<unset>``, distinguishable from a
    stored value of ``None``. A ``varying`` list with 8 or more
    elements, or whose joined text would push the row past
    ``max_len`` chars, is truncated to a head-plus-ellipsis so the
    row fits a standard terminal width.
    """
    if not snapshot_row.is_set:
        return "<unset>"
    value = snapshot_row.current
    if value is None:
        return "null"
    if option.kind == "varying":
        if not isinstance(value, list):
            return json.dumps(value)
        return _truncate_varying_text(value, max_len or VALUE_COL_WIDTH)
    text = _scalar_to_text(value)
    if max_len and len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _format_varying_value(value: list) -> str:
    parts = [_scalar_to_text(item) for item in value]
    return ", ".join(parts)


def _scalar_to_text(item) -> str:
    if item is None:
        return "null"
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, str):
        return item
    return json.dumps(item)


def _truncate_varying_text(value: list, max_len: int) -> str:
    """Return a comma-separated rendering of ``value`` for the row.

    Lists with 8 or more elements are cut to the first
    ``VARYING_TRUNCATED_KEEP`` items with an ellipsis so the row
    stays on one line. Shorter lists whose joined text would push
    the row past ``max_len`` chars get the head trimmed to fit;
    the truncation keeps as many items as it can while leaving
    room for the ellipsis. The full default remains available in
    the info panel when the option is focused.
    """
    if max_len <= 0:
        return _format_varying_value(value)
    text = _format_varying_value(value)
    if len(value) >= VARYING_TRUNCATE_AT:
        head = _head_fitting(value, VARYING_TRUNCATED_KEEP, max_len - 2)
        return head + ", …"
    if len(text) > max_len:
        head = _head_fitting(value, len(value), max_len - 1)
        return head + "…"
    return text


def _head_fitting(value: list, max_items: int, max_chars: int) -> str:
    """Return the longest head of ``value`` whose join fits in ``max_chars`` chars.

    Items are added one at a time while they fit; the result is
    guaranteed to be at most ``max_chars`` chars long or to be a
    single ellipsis.
    """
    if max_chars <= 0:
        return ""
    if max_items < 1:
        max_items = 1
    keep = min(len(value), max_items)
    best = ""
    for k in range(1, keep + 1):
        head = ", ".join(_scalar_to_text(item) for item in value[:k])
        if len(head) > max_chars:
            break
        best = head
    if best:
        return best
    return "…"


def _full_default_text(option) -> str:
    """Render an option's declared default in full, for the info panel.

    Unlike the row (which used to show a truncated default in trailing
    parens), the panel has nothing else competing for its width, so
    this never truncates — a long ``varying`` default just wraps
    across the panel's lines via :func:`textwrap.wrap`.
    """
    if option.default is None:
        return "<runner writes>"
    if option.kind == "varying":
        if not isinstance(option.default, list):
            return json.dumps(option.default)
        return _format_varying_value(option.default)
    return _scalar_to_text(option.default)


def _option_row_text(
    snapshot_row,
    option,
    content_w: int,
    focused: bool,
    editor_buffer: Optional[str] = None,
) -> RenderedLine:
    """Render one declared-option row: name, kind, and the full current value.

    The declared default used to sit in trailing parens on the row,
    competing with the current value for the same fixed-width column
    — a long ``varying`` list would get cut short to make room for a
    default that is only useful when the row is focused anyway. The
    default now lives in the info panel (:func:`right_panel_lines`)
    instead, so the current value gets the row's full remaining width.
    ``editor_buffer`` — not ``None`` only on the focused row while its
    inline editor is open — replaces the stored value with the raw
    text typed so far, so the row updates live as the user types
    instead of only after a commit.
    """
    name = snapshot_row.label or snapshot_row.name
    kind = f"[{option.kind}]".ljust(KIND_LABEL_WIDTH)
    fixed_prefix = f"{name.ljust(LABEL_COL_WIDTH)}{kind} "
    value_budget = max(0, content_w - len(fixed_prefix))
    if editor_buffer is not None:
        current_text = editor_buffer[:value_budget] if value_budget else ""
    else:
        current_text = _format_value(snapshot_row, option, value_budget)

    line = f"{fixed_prefix}{current_text}"
    segments: list[tuple[str, int]] = [(line, curses.A_REVERSE if focused else 0)]
    return RenderedLine(text=line, segments=tuple(segments))


def _with_divider(line: RenderedLine, left_w: int, has_panel: bool) -> RenderedLine:
    """Pad ``line`` to ``left_w`` and append the vertical rule the info panel sits past.

    Applied to every body row, blanks included, so the rule runs
    unbroken down the pane rather than only appearing next to option
    rows.
    """
    if not has_panel:
        return line
    text = line.text.ljust(left_w)[:left_w] + " │"
    attr = line.segments[0][1] if line.segments else 0
    return RenderedLine(text=text, segments=((text, attr),))


def _run_param_row_text(
    run_params, field_name: str, focused: bool, editor_buffer: Optional[str] = None
) -> RenderedLine:
    if editor_buffer is not None:
        value_text = editor_buffer
    elif field_name == "samples":
        value_text = str(run_params.samples)
    else:
        value_text = f"[{_on_off(getattr(run_params, field_name))}]"
    line = f"{field_name.ljust(LABEL_COL_WIDTH)}{value_text}"
    segments: list[tuple[str, int]] = [(line, curses.A_REVERSE if focused else 0)]
    return RenderedLine(text=line, segments=tuple(segments))


def _row_descriptors(options: OptionsState) -> list[tuple[str, object]]:
    """The body's logical rows in display order, blanks included.

    ``("run_param", field_name)``, ``("blank", None)``, or
    ``("option", OptionSnapshotRow)``. Built once and shared by the
    row renderer, :func:`focus_positions`, and the info panel so all
    three agree on where the focused row actually lands.
    """
    descriptors: list[tuple[str, object]] = [
        ("run_param", name) for name in run_param_fields(options.suite_name)
    ]
    ordered = ordered_option_rows(options)
    varying = [row for row in ordered if _option(options.suite_name, row.name).kind == "varying"]
    uniform = [row for row in ordered if _option(options.suite_name, row.name).kind == "uniform"]
    for block in (varying, uniform):
        if not block:
            continue
        if descriptors:
            descriptors.append(("blank", None))
        descriptors.extend(("option", row) for row in block)
    return descriptors


def _editor_prefix_width(kind, target) -> int:
    if kind == "run_param":
        return max(LABEL_COL_WIDTH, len(target))
    return max(LABEL_COL_WIDTH, len(target.label or target.name)) + KIND_LABEL_WIDTH + 1


def editor_cursor_x(options: OptionsState, rows: int, cols: int) -> int:
    """Use the same field width and text viewport as the row renderer."""
    framed = rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME
    inset = 1 + FRAME_H_PAD if framed else 0
    left_w, _ = _column_split(cols - 2 * inset)
    kind, target = _focused_section(options)
    prefix = _editor_prefix_width(kind, target)
    _, cursor = visible_text(options.editor_buffer, options.editor_cursor, left_w - prefix)
    return min(cols - 1, inset + prefix + cursor)


def _focusable_row_numbers(descriptors: list[tuple[str, object]]) -> list[int]:
    return [i for i, (kind, _) in enumerate(descriptors) if kind != "blank"]


def _column_split(content_w: int) -> tuple[int, int]:
    """``(left_w, right_w)`` for the option list and the info panel.

    ``right_w`` is ``0`` (no panel, the list gets the full width) below
    :data:`MIN_COLS_FOR_SIDE_PANEL` — the panel needs real room to be
    worth showing, and this codebase's terminals are assumed wide when
    it is shown at all.
    """
    if content_w < MIN_COLS_FOR_SIDE_PANEL:
        return content_w, 0
    right_w = SIDE_PANEL_WIDTH
    left_w = content_w - right_w - 3
    return left_w, right_w


def _header_text(state: TuiState, options: OptionsState, cols: int) -> str:
    db_label = str(options.db_path) if options.db_path is not None else "<missing>"
    header = f"suite: {options.suite_name} · {HEADER_DB_KEY} {db_label}"
    return header[:cols]


def _focused_section(options: OptionsState) -> tuple[str, object]:
    """The ``(kind, target)`` of the focused row, per :func:`_row_descriptors`.

    Reuses the same descriptor list :func:`render_options` and
    :func:`focus_positions` build so "what's focused" has one
    definition, not three slightly different index calculations.
    """
    descriptors = _row_descriptors(options)
    focusable = _focusable_row_numbers(descriptors)
    if not (0 <= options.focused_option_index < len(focusable)):
        return "none", None
    return descriptors[focusable[options.focused_option_index]]


def right_panel_lines(state: TuiState, width: int) -> list[str]:
    """Wrapped info-panel text for the currently focused row.

    Pure and independent of curses: a run-parameter row gets a short
    fixed description (there is no per-field help prose the way
    declared options have one); a declared-option row gets its name,
    kind, full help text, and its declared default in full (the row
    itself no longer shows a — possibly truncated — default, since
    that competed with the current value for the same fixed-width
    column; here it has the whole panel to itself), all word-wrapped
    to ``width``.
    """
    options = state.options_state
    if options is None or width <= 0:
        return []
    kind, target = _focused_section(options)
    if kind == "none":
        return []
    if kind == "run_param":
        header = target
        body = _RUN_PARAM_HELP.get(target, "")
        lines = [header[:width], ""]
        lines.extend(textwrap.wrap(body, width) or [""])
        return lines
    option = _option(options.suite_name, target.name)
    header = f"{target.label or target.name}  [{option.kind}]"
    lines = [header[:width], ""]
    lines.extend(textwrap.wrap(option.help, width) or [""])
    lines.append("")
    lines.append("Default:")
    default_text = _full_default_text(option)
    lines.extend(textwrap.wrap(default_text, width) or [""])
    return lines


_RUN_PARAM_HELP = {
    "enabled": "Whether this suite runs when the schedule is confirmed with 'r'.",
    "samples": "How many samples this suite's 'sample' command records per configuration.",
    "amend": "Top up stored rows to 'samples' instead of discarding the first new one.",
    "skip_failed": "Skip a configuration on failure instead of stopping the whole run.",
}


def _commit_confirmation(options: OptionsState) -> Optional[str]:
    if options.pending_commit is None:
        return None
    name, encoded = options.pending_commit
    return f"committed {name} = {encoded}"


def _bottom_line(state: TuiState, options: OptionsState) -> str:
    if state.confirm_run_open:
        return "enter: run · esc: cancel"
    if options.error:
        return options.error
    if options.editor_open:
        return "enter: commit · esc: cancel · Ctrl-U: clear"
    if options.pending_commit is not None:
        return _commit_confirmation(options) or HELP_LINE
    return HELP_LINE


def render_options(
    state: TuiState,
    rows: int,
    cols: int,
) -> list[RenderedLine]:
    """Render the options view as a list of :class:`RenderedLine` rows.

    Returns ``rows`` lines. When ``rows >= MIN_ROWS_FOR_FRAME`` and
    ``cols >= MIN_COLS_FOR_FRAME`` the pane is wrapped in a frame with
    horizontal separators under the title and above the footer. The
    caller applies reverse-video highlighting based on
    :func:`focus_positions`; on a wide enough screen it also overlays
    :func:`right_panel_lines` at the coordinates :func:`info_panel_origin`
    reports — this function itself only reserves that space (leaves it
    blank) so the two stay independently testable.
    """
    if rows < 3 or cols < 20:
        return [RenderedLine.plain("") for _ in range(max(rows, 0))]

    options = state.options_state
    if options is None:
        return [RenderedLine.plain("") for _ in range(rows)]

    title = "bench.config"
    title_line = RenderedLine.plain(_fit(title, cols))
    header = _header_text(state, options, cols)
    header_line = RenderedLine.plain(header)

    framed = rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME
    content_w = max(0, (cols - 2) - 2 * FRAME_H_PAD) if framed else cols
    left_w, _right_w = _column_split(content_w)

    descriptors = _row_descriptors(options)
    focusable = _focusable_row_numbers(descriptors)
    focused_body_row = (
        focusable[options.focused_option_index] if 0 <= options.focused_option_index < len(focusable) else None
    )

    has_panel = _right_w > 0
    body_lines: list[RenderedLine] = []
    for index, (kind, target) in enumerate(descriptors):
        row_focused = index == focused_body_row
        live_buffer = options.editor_buffer if (row_focused and options.editor_open) else None
        if live_buffer is not None:
            prefix = _editor_prefix_width(kind, target)
            live_buffer, _ = visible_text(live_buffer, options.editor_cursor, left_w - prefix)
        if kind == "blank":
            row = RenderedLine.plain("")
        elif kind == "run_param":
            row = _run_param_row_text(options.run_params, target, row_focused, live_buffer)
        else:
            option = _option(options.suite_name, target.name)
            row = _option_row_text(target, option, left_w, row_focused, live_buffer)
        body_lines.append(_with_divider(row, left_w, has_panel))

    bottom_line = RenderedLine.plain(_fit(_bottom_line(state, options), cols))

    body_height = max(1, rows - (6 if framed else 3))
    offset = max(0, (focused_body_row or 0) - body_height + 1)
    body_lines = body_lines[offset:]
    if framed:
        return _frame_layout(title_line, header_line, body_lines, bottom_line, rows, cols)
    return _minimal_layout(title_line, header_line, body_lines, bottom_line, rows, cols)


def info_panel_origin(rows: int, cols: int) -> Optional[tuple[int, int, int, int]]:
    """``(top, left, height, width)`` absolute screen coordinates for the info panel.

    ``None`` when the screen is too small/narrow for a frame or too
    narrow for the side panel to be worth showing (see
    :data:`MIN_COLS_FOR_SIDE_PANEL`). The curses loop overlays
    :func:`right_panel_lines` at these coordinates after drawing the
    main list — kept as a separate pass rather than folded into
    :func:`render_options`'s own rows so the focused row's reverse-video
    highlight (applied to the *whole* row by the curses loop) never
    bleeds into the panel text.
    """
    framed = rows >= MIN_ROWS_FOR_FRAME and cols >= MIN_COLS_FOR_FRAME
    if not framed:
        return None
    content_w = max(0, (cols - 2) - 2 * FRAME_H_PAD)
    left_w, right_w = _column_split(content_w)
    if right_w <= 0:
        return None
    top = 4
    height = rows - 6
    left = 1 + FRAME_H_PAD + left_w + 3
    return top, left, height, right_w


def _frame_layout(
    title: RenderedLine,
    header: RenderedLine,
    body_lines: list[RenderedLine],
    bottom: RenderedLine,
    rows: int,
    cols: int,
) -> list[RenderedLine]:
    inner_w = cols - 2
    sep = "─" * inner_w
    frame_top = RenderedLine.plain("┌" + sep + "┐")
    sep_line = RenderedLine.plain("├" + sep + "┤")
    title_wrapped = _wrap_inside(title, inner_w)
    header_wrapped = _wrap_inside(header, inner_w)
    bottom_wrapped = _wrap_inside(bottom, inner_w)
    body_rows = rows - 6
    body: list[RenderedLine] = []
    for i in range(body_rows):
        if i < len(body_lines):
            body.append(_wrap_inside(body_lines[i], inner_w))
        else:
            body.append(RenderedLine.plain("│" + " " * inner_w + "│"))
    return [frame_top, title_wrapped, header_wrapped, sep_line, *body, sep_line, bottom_wrapped]


def _minimal_layout(
    title: RenderedLine,
    header: RenderedLine,
    body_lines: list[RenderedLine],
    bottom: RenderedLine,
    rows: int,
    cols: int,
) -> list[RenderedLine]:
    body_rows = max(0, rows - 3)
    out: list[RenderedLine] = [title, header]
    for i in range(body_rows):
        if i < len(body_lines):
            line = body_lines[i]
        else:
            line = RenderedLine.plain("")
        if len(line.text) > cols:
            line = RenderedLine(text=line.text[:cols], segments=((line.text[:cols], 0),))
        out.append(line)
    out.append(bottom)
    if len(out) > rows:
        out = out[:rows]
    elif len(out) < rows:
        out.extend(RenderedLine.plain("") for _ in range(rows - len(out)))
    return out


def _wrap_inside(line: RenderedLine, inner_w: int) -> RenderedLine:
    content_w = max(0, inner_w - 2 * FRAME_H_PAD)
    text = "│" + " " * FRAME_H_PAD + line.text.ljust(content_w)[:content_w] + " " * FRAME_H_PAD + "│"
    pad_segments = ((" " * FRAME_H_PAD, 0),)
    trailing_pad_w = content_w - len(line.text.ljust(content_w)[:content_w])
    trailing_pad = (" " * trailing_pad_w, 0) if trailing_pad_w > 0 else ("", 0)
    attr = line.segments[0][1] if line.segments else 0
    inner_segment = (line.text.ljust(content_w)[:content_w], attr)
    segments = (
        ("│", 0),
    ) + pad_segments + (inner_segment,) + (trailing_pad,) + pad_segments + (
        ("│", 0),
    )
    return RenderedLine(text=text, segments=segments)


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:width]


def focus_positions(
    state: TuiState,
    rows: int,
) -> Optional[int]:
    """Return the body row to highlight (1-indexed from the title), or ``None``.

    The options view's body starts four rows below the title (title,
    header, separator) and ends one row above the footer separator.
    Blank separator rows between the run-parameters/varying/uniform
    blocks shift later rows down; :func:`_row_descriptors` and
    :func:`_focusable_row_numbers` are the single source of truth for
    that mapping, shared with :func:`render_options`.
    """
    if rows < MIN_ROWS_FOR_FRAME:
        return None
    options = state.options_state
    if options is None:
        return None
    descriptors = _row_descriptors(options)
    focusable = _focusable_row_numbers(descriptors)
    if not focusable:
        return None
    if not (0 <= options.focused_option_index < len(focusable)):
        return None
    body_top = 4
    body_rows = rows - 6
    target = focusable[options.focused_option_index]
    offset = max(0, target - body_rows + 1)
    return target - offset + body_top
