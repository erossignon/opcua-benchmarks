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

"""Read-only saved-run browser and its terminal-independent rendering."""

from __future__ import annotations

import json
import sqlite3
import textwrap
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from bench.config import SUITE_ORDER


@dataclass(frozen=True)
class SavedRun:
    name: str
    created_at: str
    schedule: dict | None
    config: dict[str, dict[str, Any]]
    result_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0


@dataclass(frozen=True)
class RunsState:
    db: Path | None = None
    runs: tuple[SavedRun, ...] = ()
    selected: int = 0
    details_open: bool = False
    scroll: int = 0
    return_view: Literal["schedule", "options"] = "schedule"
    error: str = ""


def load_runs(db: Path | None) -> RunsState:
    if db is None or not db.is_file():
        return RunsState(db=db, error="Database does not exist.")
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("BEGIN")
            records = connection.execute("SELECT name, created_at, schedule FROM benchmarks ORDER BY rowid DESC").fetchall()
            configs: dict[str, dict[str, dict[str, Any]]] = {}
            for name, suite, option, value in connection.execute(
                "SELECT name, suite, option, value FROM benchmark_config ORDER BY suite, option"
            ):
                configs.setdefault(name, {}).setdefault(suite, {})[option] = json.loads(value)
            counts = {
                table: dict(connection.execute(f"SELECT name, count(*) FROM {table} GROUP BY name"))
                for table in ("results", "failures", "skipped")
            }
            runs = tuple(
                SavedRun(
                    name=name,
                    created_at=created_at,
                    schedule=json.loads(schedule) if schedule else None,
                    config=configs.get(name, {}),
                    result_count=counts["results"].get(name, 0),
                    failure_count=counts["failures"].get(name, 0),
                    skipped_count=counts["skipped"].get(name, 0),
                )
                for name, created_at, schedule in records
            )
    except (OSError, sqlite3.Error, ValueError) as error:
        return RunsState(db=db, error=f"Cannot read saved runs: {error}")
    return RunsState(db=db, runs=runs)


def _counts(run: SavedRun) -> str:
    return f"{run.result_count} results · {run.failure_count} failures · {run.skipped_count} skipped"


def _detail_lines(run: SavedRun, width: int) -> list[str]:
    lines = [f"Name: {run.name}", f"Saved: {run.created_at}", _counts(run), "", "Run parameters"]
    entries = run.schedule.get("entries", []) if run.schedule else []
    if not entries:
        lines.append("  Not recorded for this run.")
    for index, entry in enumerate(entries, start=1):
        lines.append(f"  Entry {index}")
        lines.append(f'    start_delay_minutes: {entry["start_delay_minutes"]}')
        for suite, params in entry["suites"].items():
            lines.append(f"    {suite}")
            lines.extend(f"      {option}: {json.dumps(value)}" for option, value in params.items())
    lines.extend(["", "Saved configuration"])
    if not run.config:
        lines.append("  No configuration snapshot recorded.")
    suites = [suite for suite in SUITE_ORDER if suite in run.config]
    suites.extend(sorted(set(run.config) - set(suites)))
    for suite in suites:
        lines.append(f"  {suite}")
        for option, value in run.config[suite].items():
            lines.append(f"    {option}: {json.dumps(value, ensure_ascii=False)}")
        lines.append("")
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=max(1, width), subsequent_indent="  " if width > 2 else "") or [""])
    return wrapped


def runs_after_key(state: RunsState, key: str, rows: int, cols: int) -> RunsState:
    if not state.runs:
        return state
    if state.details_open:
        maximum = max(0, len(_detail_lines(state.runs[state.selected], cols)) - max(1, rows - 3))
        position = state.scroll
        if key in ("escape", "left"):
            return replace(state, details_open=False, scroll=0)
    else:
        maximum = len(state.runs) - 1
        position = state.selected
        if key in ("enter", "right"):
            return replace(state, details_open=True, scroll=0)
    if key in ("down", "j"):
        position += 1
    elif key in ("up", "k"):
        position -= 1
    elif key == "pagedown":
        position += max(1, rows - 3)
    elif key == "pageup":
        position -= max(1, rows - 3)
    elif key == "g":
        position = 0
    elif key == "G":
        position = maximum
    position = max(0, min(position, maximum))
    return replace(state, scroll=position) if state.details_open else replace(state, selected=position)


def render_runs(state: RunsState, rows: int, cols: int) -> tuple[list[str], int | None]:
    if rows < 4 or cols < 1:
        return ["" for _ in range(max(0, rows))], None
    body_height = rows - 3
    title = "Saved runs — select a run to inspect its configuration"
    footer = "↑/↓: select · enter: config · esc: back · q: quit"
    highlight = None
    if state.error:
        body = textwrap.wrap(state.error, width=cols)
    elif not state.runs:
        body = ["No saved runs in this database."]
    elif state.details_open:
        run = state.runs[state.selected]
        title = f"Saved configuration — {run.name}"
        content = _detail_lines(run, cols)
        offset = min(state.scroll, max(0, len(content) - body_height))
        body = content[offset : offset + body_height]
        footer = f"↑/↓/PgUp/PgDn: scroll · esc: runs · {offset + 1}-{offset + len(body)}/{len(content)}"
    else:
        offset = max(0, state.selected - body_height + 1)
        body = [f"{run.name}  ·  {_counts(run)}  ·  {run.created_at}" for run in state.runs[offset : offset + body_height]]
        highlight = 2 + state.selected - offset
    body = body[:body_height] + [""] * max(0, body_height - len(body))
    lines = [title, f'Database: {state.db or "—"}', *body, footer]
    return [line[:cols] for line in lines], highlight
