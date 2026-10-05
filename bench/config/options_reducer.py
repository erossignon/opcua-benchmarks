#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Apply option-editor keystrokes without terminal or database I/O."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Iterable

from bench.config.text_editor import edit_text
from bench.config.options import OptionSnapshotRow, OptionsState
from bench.config.tui_reducer import TuiState, handle_run_name
from common.suites import SUITE_OPTIONS


def _option(suite_name: str, option_name: str):
    return SUITE_OPTIONS[suite_name][option_name]


# Editable run parameters for each suite.
RUN_PARAM_FIELDS: dict[str, tuple[str, ...]] = {
    "subscription": ("enabled", "amend", "skip_failed"),
    "throughput": ("enabled", "samples", "amend", "skip_failed"),
    "server_capacity": ("enabled", "samples", "amend", "skip_failed"),
    "server_limits": ("enabled", "samples", "amend", "skip_failed"),
}


def run_param_fields(suite_name: str) -> tuple[str, ...]:
    """The run-parameter fields applicable to ``suite_name``, in display order."""
    return RUN_PARAM_FIELDS.get(suite_name, ())


def run_param_field_count(suite_name: str) -> int:
    return len(run_param_fields(suite_name))


def ordered_option_rows(state: OptionsState) -> tuple[OptionSnapshotRow, ...]:
    """Use the same varying-then-uniform order for display and editing."""
    return tuple(sorted(state.snapshot, key=lambda row: _option(state.suite_name, row.name).kind != "varying"))


def _focused_row(state: OptionsState) -> tuple[str, Any]:
    """Return ``("run_param", field_name)`` or ``("option", OptionSnapshotRow)``.

    :attr:`~bench.config.options.OptionsState.focused_option_index` is a
    single flat index spanning the run-parameters block followed by the
    declared-options snapshot; this is the one place that splits it back
    into which section the focus is in.
    """
    n_params = run_param_field_count(state.suite_name)
    if state.focused_option_index < n_params:
        return "run_param", run_param_fields(state.suite_name)[state.focused_option_index]
    return "option", ordered_option_rows(state)[state.focused_option_index - n_params]


def _scalar_to_typed_form(item: Any) -> str:
    """Render a single typed value the way a user would type it back.

    ``None`` becomes ``null`` (the literal the editor accepts to clear
    a row), numbers stay bare, strings lose their JSON quotes. Used to
    build the comma-separated buffer for ``varying`` options and the
    scalar buffer for ``uniform`` ones.
    """
    if item is None:
        return "null"
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (int, float)):
        return repr(item) if isinstance(item, float) else str(item)
    if isinstance(item, str):
        return item
    return json.dumps(item)


def _editor_buffer_for(suite_name: str, option_name: str, value: Any) -> str:
    """Build the editor's pre-fill buffer for an option's current value.

    For ``uniform`` options this is the bare scalar form
    (``2000``/``opc.tcp://127.0.0.1:4840``/``null``); for ``varying``
    options the comma-separated form (``None, Basic256Sha256``/``null,
    0.5, 0.75, 0.9``). A stored value of ``None`` (``is_set=False`` or a
    literal ``None``) renders as ``null`` so the user can press
    :kbd:`Enter` to keep the runner-written default.
    """
    option = _option(suite_name, option_name)
    if option.kind == "varying":
        if not isinstance(value, list):
            return "null"
        return ", ".join(_scalar_to_typed_form(item) for item in value)
    return _scalar_to_typed_form(value)


def encode_scalar(text: str) -> Any:
    """Return the typed Python value the database stores for a one-line editor token.

    Recognises the same shapes the user types into the editor and the
    registered suites' ``ConfigOption`` declarations produce: the literal
    ``null`` becomes ``None``; an integer literal (``2000``, ``1``)
    becomes an ``int``; a float literal (``0.5``, ``0.75``) becomes a
    ``float``; everything else — including strings with embedded
    colons (``opc.tcp://127.0.0.1:4840``) and embedded commas (the
    ``varying`` comma-split tolerates them by trimming each token) —
    becomes a ``str`` equal to the trimmed text. The boolean literals
    ``true`` / ``false`` map to ``True`` / ``False`` so the helper
    covers every JSON scalar a future suite might declare.
    """
    token = text.strip()
    if token == "null":
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def encode_varying(text: str) -> list[Any]:
    """Split ``text`` on ``,``, apply :func:`encode_scalar` to each element.

    Whitespace around each token is stripped; an empty token (a
    trailing comma, or a buffer that is only whitespace) is dropped
    so the result is the list of typed values the user typed, with no
    phantom ``None`` from a stray separator. Strings that themselves
    contain commas are not a current ``ConfigOption`` shape, but the
    helper tolerates them: each token's typed form is whatever
    :func:`encode_scalar` returns for it, and a token that is a string
    with a comma survives untouched because the split happens before
    :func:`encode_scalar` is called.
    """
    return [encode_scalar(token) for token in (piece.strip() for piece in text.split(",")) if token]


def _encode_uniform_buffer(buffer: str) -> str:
    """JSON-encode the editor buffer for a ``uniform`` option.

    The buffer is a single typed token (``2000``/``opc.tcp://...``/``null``),
    so the encoding collapses to one :func:`encode_scalar` call.
    """
    return json.dumps(encode_scalar(buffer))


def _encode_varying_buffer(buffer: str) -> str:
    """JSON-encode the editor buffer for a ``varying`` option.

    The buffer is a comma-separated list of typed tokens; each element
    is encoded via :func:`encode_varying`. An all-whitespace buffer
    round-trips to an empty list (``[]``), matching what every
    declared ``varying`` ``coerce`` accepts as a "no axis values"
    state.
    """
    return json.dumps(encode_varying(buffer))


def _apply_editor_key(state: OptionsState, key: str) -> OptionsState:
    buffer, cursor = edit_text(state.editor_buffer, state.editor_cursor, key)
    return replace(state, editor_buffer=buffer, editor_cursor=cursor, error="")


def _attempt_commit_option(state: OptionsState, focused: OptionSnapshotRow) -> OptionsState:
    """Pre-validate the editor buffer and set ``pending_commit`` or the error field.

    A failed pre-check leaves the editor open and the snapshot
    untouched; a successful one closes the editor and records the
    encoded JSON for the curses layer to persist. The error field
    names the option and the rejected value so the status line tells
    the user which row was bad and what was typed, the way the CLI's
    own ``invalid value for {name!r} ({value!r})`` message does.
    """
    option = _option(state.suite_name, focused.name)
    if option.kind == "varying":
        encoded = _encode_varying_buffer(state.editor_buffer)
    else:
        encoded = _encode_uniform_buffer(state.editor_buffer)
    name = focused.name
    try:
        if encoded != "null" or option.default is not None:
            option.coerce(encoded)
    except ValueError as error:
        return replace(
            state,
            editor_open=True,
            error=f"{focused.label or focused.name} ({state.editor_buffer!r}): {error}",
            pending_commit=None,
        )
    return replace(
        state,
        editor_open=False,
        editor_buffer="",
        editor_cursor=None,
        error="",
        pending_commit=(name, encoded),
    )


def _attempt_commit_run_param(state: OptionsState, field_name: str) -> OptionsState:
    """Pre-validate the ``samples`` editor buffer — the only run-parameter with an editor.

    ``enabled``/``amend``/``skip_failed`` flip immediately on
    :kbd:`space` and never open the editor, matching the schedule
    view's own toggle fields.
    """
    try:
        value = int(state.editor_buffer.strip())
    except ValueError:
        return replace(
            state,
            editor_open=True,
            error=f"{field_name} ({state.editor_buffer!r}): not a whole number",
            pending_commit=None,
        )
    new_params = replace(state.run_params, samples=value)
    return replace(
        state,
        editor_open=False,
        editor_buffer="",
        editor_cursor=None,
        error="",
        run_params=new_params,
        pending_run_param_write=new_params,
    )


def _attempt_commit(state: OptionsState) -> OptionsState:
    kind, target = _focused_row(state)
    if kind == "run_param":
        return _attempt_commit_run_param(state, target)
    return _attempt_commit_option(state, target)


def _handle_run_param_key(state: OptionsState, key: str, field_name: str) -> OptionsState:
    if key == "space":
        if field_name not in ("enabled", "amend", "skip_failed"):
            return state
        new_params = replace(state.run_params, **{field_name: not getattr(state.run_params, field_name)})
        return replace(state, run_params=new_params, pending_run_param_write=new_params)
    if key == "enter" and field_name == "samples":
        return replace(
            state,
            editor_open=True,
            editor_buffer=str(state.run_params.samples),
            editor_cursor=None,
            error="",
        )
    return state


def _handle_option_key(state: OptionsState, key: str, focused: OptionSnapshotRow) -> OptionsState:
    if key == "d" and focused.name.startswith("control:"):
        option = _option(state.suite_name, focused.name)
        buffer = _editor_buffer_for(state.suite_name, focused.name, option.default)
        return _attempt_commit_option(replace(state, editor_buffer=buffer), focused)
    if key == "d" and focused.name.startswith("case:"):
        return replace(state, error="Use s/f to restore a complete preset; case fields have no independent defaults.")
    if key == "d":
        option = _option(state.suite_name, focused.name)
        if option.default is None:
            return replace(
                state,
                error=(
                    f"option {focused.name!r} has no declared default — "
                    "the runner writes it at sample time; leaving it unset."
                ),
            )
        return replace(state, pending_default_write=focused.name)
    if key == "enter":
        return replace(
            state,
            editor_open=True,
            editor_buffer=_editor_buffer_for(state.suite_name, focused.name, focused.current),
            editor_cursor=None,
            error="",
        )
    return state


def _handle_normal(state: OptionsState, key: str) -> OptionsState:
    total = run_param_field_count(state.suite_name) + len(state.snapshot)
    if key in ("down", "j"):
        if total == 0:
            return state
        next_index = min(state.focused_option_index + 1, total - 1)
        return replace(state, focused_option_index=next_index)
    if key in ("up", "k"):
        next_index = max(state.focused_option_index - 1, 0)
        return replace(state, focused_option_index=next_index)
    kind, target = _focused_row(state)
    if kind == "run_param":
        return _handle_run_param_key(state, key, target)
    return _handle_option_key(state, key, target)


def _handle_editor(state: OptionsState, key: str) -> OptionsState:
    if key == "enter":
        return _attempt_commit(state)
    if key == "escape":
        return replace(
            state,
            editor_open=False,
            editor_buffer="",
            editor_cursor=None,
            error="",
            pending_commit=None,
        )
    return _apply_editor_key(state, key)


def options_after_keystrokes(
    state: TuiState,
    keys: Iterable[str],
) -> TuiState:
    """Apply ``keys`` to ``state`` while its view is ``"options"``; return the next state.

    Pure: same input, same output. The function mutates
    ``state.options_state`` (editor, focus, error, intent flags) and
    may flip ``state.view`` back to ``"schedule"`` on
    :kbd:`escape`/:kbd:`left` outside the editor. The
    :attr:`~bench.config.tui_reducer.TuiState.confirm_run_open` flag is
    raised on :kbd:`r` and accepts a name on :kbd:`enter`,
    and :attr:`~bench.config.tui_reducer.TuiState.outcome` flips to
    ``"quitting"`` on :kbd:`q` — the same shape the schedule view's
    reducer uses so the curses layer can share its exit path.
    """
    for key in keys:
        if state.confirm_run_open:
            state = handle_run_name(state, key)
            if state.outcome == "running":
                return state
            continue
        options_state = state.options_state
        if options_state is None:
            return state
        if options_state.editor_open:
            state = replace(state, options_state=_handle_editor(options_state, key))
            continue
        if key == "q":
            state = replace(state, outcome="quitting")
            continue
        if key in ("escape", "left"):
            return replace(state, view="schedule")
        if key == "r":
            state = replace(state, confirm_run_open=True, run_name_cursor=None, run_name_error="")
            continue
        state = replace(state, options_state=_handle_normal(options_state, key))
    return state
