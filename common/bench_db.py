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

"""SQLite-backed bench database shared by every suite."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import datetime
import json
import os
import platform
import socket
import sqlite3
from pathlib import Path
from typing import Any

from common.backup import verified_backup, writer_transaction
from common.bench_schema import SCHEMA
from common.suites import SUITE_OPTIONS

# Metadata fields that say when the row was written rather than what it was
# written on, so :meth:`BenchDB.metadata_differences` ignores them.
_VOLATILE_METADATA = frozenset({"timestamp_utc", "partial", "ua_dotnet_diagnostics", "node_opcua_diagnostics"})

_BENCHMARK_NAME: ContextVar[str | None] = ContextVar("benchmark_name", default=None)


@contextmanager
def benchmark_scope(name: str | None):
    """Bind in-process suite runners and report builders to one saved benchmark."""
    token = _BENCHMARK_NAME.set(name)
    try:
        yield
    finally:
        _BENCHMARK_NAME.reset(token)


class BenchDB:
    """One suite's slice of a shared ``bench.db``.

    Bound at construction to a database path (a real file, or ``":memory:"``
    for a store that never touches disk) and a ``suite`` name; every query
    and write is scoped to that suite, so two :class:`BenchDB` instances
    opened against the same file with different suite names never see each
    other's rows. ``name`` selects a saved benchmark and its configuration
    snapshot. Without it, the active :func:`benchmark_scope` applies; outside
    a scope, artifacts belong to the latest run and config edits target the
    current settings used to prepare the next run.

    ``key_fields`` is an opt-in list of field names whose values participate
    in a row's identity, with every other field of the ``config_key``
    ignored for keying purposes. ``None`` (the default) keeps every field.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        suite: str,
        *,
        key_fields: list[str] | None = None,
        name: str | None = None,
    ) -> None:
        self.suite = suite
        self.key_fields: list[str] | None = list(key_fields) if key_fields is not None else None
        self._connection = sqlite3.connect(path)
        self._transaction_depth = 0
        self._backed_up = False
        self.last_backup = None
        columns = self._connection.execute("PRAGMA table_info(results)").fetchall()
        if columns and "name" not in {column[1] for column in columns}:
            self._connection.close()
            raise sqlite3.OperationalError(f"Database needs upgrading: run python -m bench.upgrade {str(path)!r}")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(SCHEMA)
        selected = name if name is not None else _BENCHMARK_NAME.get()
        self._use_snapshot = selected is not None
        self.name = selected if selected is not None else next(reversed(self.benchmark_names()), "unnamed-1")
        self.name = self.name.strip()
        if not self.name:
            self._connection.close()
            raise ValueError("Benchmark name must not be empty")
        self.metadata: dict = _environment()
        self.stored_metadata: dict = self._load_metadata()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self, *, protect=False):
        """Commit a bounded batch atomically, with at most one verified backup.

        Nested store methods join this transaction. Use ``protect=True`` before
        direct destructive SQL; ordinary methods detect replacement themselves.
        Do not hold this context across measurements or open another writer.
        Any nested failure makes the whole batch roll back, even if caught.
        """
        if self._transaction_depth:
            self._transaction_depth += 1
            try:
                if protect:
                    self._backup()
                yield
            except BaseException:
                self._transaction_failed = True
                raise
            finally:
                self._transaction_depth -= 1
            return
        with writer_transaction(self._connection):
            self._transaction_depth = 1
            self._backed_up = False
            self._transaction_failed = False
            try:
                if protect:
                    self._backup()
                yield
                if self._transaction_failed:
                    raise sqlite3.OperationalError("Nested database operation failed; batch rolled back")
            finally:
                self._transaction_depth = 0

    def _backup(self):
        if not self._backed_up:
            self.last_backup = verified_backup(self._connection)
            self._backed_up = True

    def _protect_rows(self, table, where, parameters):
        if self._connection.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", parameters).fetchone():
            self._backup()

    def invalidate_reports(self, *, name=None, suites=None):
        """Back up and remove selected suite reports and their shared index."""
        name = self.name if name is None else name
        suites = {self.suite} if suites is None else set(suites)
        if not suites:
            return
        with self.transaction():
            for suite in sorted(suites | {"index"}):
                self._protect_rows("reports", "name = ? AND suite = ?", (name, suite))
                self._connection.execute("DELETE FROM reports WHERE name = ? AND suite = ?", (name, suite))

    def benchmark_names(self) -> list[str]:
        return [row[0] for row in self._connection.execute("SELECT name FROM benchmarks ORDER BY rowid")]

    def create_benchmark(self, name: str, schedule: dict | None = None) -> None:
        """Reserve a unique name and snapshot the currently configured options."""
        name = name.strip()
        if not name:
            raise ValueError("Benchmark name must not be empty")
        try:
            with self.transaction():
                self._insert_benchmark(name, schedule)
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Benchmark name {name!r} already exists") from error

    def _insert_benchmark(self, name: str, schedule: dict | None) -> None:
        self._connection.execute(
            "INSERT INTO benchmarks (name, created_at, schedule) VALUES (?, ?, ?)",
            (name, datetime.datetime.now(datetime.timezone.utc).isoformat(), json.dumps(schedule)),
        )
        self._connection.execute(
            "INSERT INTO benchmark_config SELECT ?, suite, option, kind, value FROM config", (name,)
        )

    def restart_benchmark(self, name: str, schedule: dict | None = None) -> None:
        """Atomically replace one named run and its artifacts with a fresh snapshot."""
        name = name.strip()
        with self.transaction():
            if name not in self.benchmark_names():
                raise ValueError(f"Unknown benchmark name {name!r}")
            self._backup()
            for table in ("results", "failures", "skipped", "metadata", "profiles", "reports", "benchmark_config", "benchmarks"):
                self._connection.execute(f"DELETE FROM {table} WHERE name = ?", (name,))
            self._insert_benchmark(name, schedule)

    def restart_suites(self, name: str, suites: set[str], schedule: dict) -> None:
        """Atomically reset selected suites, preserving the rest of the named run."""
        name = name.strip()
        with self.transaction():
            row = self._connection.execute("SELECT schedule FROM benchmarks WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown benchmark name {name!r}")
            if not suites:
                return
            saved = (json.loads(row[0]) if row[0] else None) or {}
            entries = []
            # Retain other suites' saved targets so they can still be amended.
            for source, selected in ((saved, False), (schedule, True)):
                for entry in source.get("entries", []):
                    parameters = {suite: config for suite, config in entry["suites"].items() if (suite in suites) == selected}
                    if any(config.get("enabled", False) for config in parameters.values()):
                        entries.append(entry | {"suites": parameters})
            self._backup()
            for suite in sorted(suites):
                for table in ("results", "failures", "skipped", "metadata", "profiles", "reports", "benchmark_config"):
                    self._connection.execute(f"DELETE FROM {table} WHERE name = ? AND suite = ?", (name, suite))
                self._connection.execute(
                    "INSERT INTO benchmark_config SELECT ?, suite, option, kind, value FROM config WHERE suite = ?",
                    (name, suite),
                )
            self._connection.execute("DELETE FROM reports WHERE name = ? AND suite = 'index'", (name,))
            self._connection.execute(
                "UPDATE benchmarks SET schedule = ? WHERE name = ?",
                (json.dumps(saved | schedule | {"entries": entries}), name),
            )

    def _ensure_benchmark(self) -> None:
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO benchmarks (name, created_at) VALUES (?, ?)",
            (self.name, datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
        if cursor.rowcount:
            self._connection.execute(
                "INSERT INTO benchmark_config SELECT ?, suite, option, kind, value FROM config",
                (self.name,),
            )

    # --- results -------------------------------------------------------

    @property
    def results(self) -> dict[Any, dict]:
        """``{frozen_key: value}`` for every row this suite owns.

        The frozen key is the same shape :func:`_freeze` produces, so a
        caller can look a row up with ``store.results[_freeze(config_key,
        store.key_fields)]``.
        """
        cursor = self._connection.execute(
            "SELECT key, value FROM results WHERE name = ? AND suite = ?",
            (
                self.name,
                self.suite,
            ),
        )
        return {_load_key(key): json.loads(value) for key, value in cursor}

    def write_result(self, config_key: dict, value: dict, *, append_field: str | None = None) -> None:
        """Replace the row for ``config_key`` with ``value``.

        Also deletes any ``failures`` row recorded for the same key, so a
        successful re-measurement of a cell that previously failed clears
        that failure — a cell no longer reads as both succeeded and failed.
        ``append_field`` identifies a legacy raw-observation list; extending its
        exact saved prefix avoids a backup. The caller guarantees that other
        fields are configuration or derived summaries. A changed prefix is a
        replacement and requires a backup, as does deleting a saved failure.
        """
        key = _dump_key(_freeze(config_key, self.key_fields))
        with self.transaction():
            previous = self._connection.execute(
                "SELECT config_key, value FROM results WHERE name = ? AND suite = ? AND key = ?",
                (self.name, self.suite, key),
            ).fetchone()
            if previous:
                old = json.loads(previous[1]).get(append_field) if append_field else None
                new = value.get(append_field) if append_field else None
                preserves = (
                    json.loads(previous[0]) == config_key and isinstance(old, list) and isinstance(new, list)
                    and len(new) >= len(old) and new[:len(old)] == old
                )
                if not preserves:
                    self._backup()
            self._protect_rows("failures", "name = ? AND suite = ? AND key = ?", (self.name, self.suite, key))
            self.invalidate_reports()
            self._ensure_benchmark()
            self._connection.execute(
                """
                INSERT INTO results (name, suite, key, config_key, value) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name, suite, key) DO UPDATE SET config_key = excluded.config_key, value = excluded.value
                """,
                (self.name, self.suite, key, json.dumps(config_key), json.dumps(value)),
            )
            self._connection.execute(
                "DELETE FROM failures WHERE name = ? AND suite = ? AND key = ?", (self.name, self.suite, key)
            )
            self._save_metadata()

    def drop_result(self, config_key: dict) -> bool:
        """Delete the row for ``config_key``, if there is one. Returns whether one was there."""
        key = _dump_key(_freeze(config_key, self.key_fields))
        with self.transaction():
            self._protect_rows("results", "name = ? AND suite = ? AND key = ?", (self.name, self.suite, key))
            self._ensure_benchmark()
            cursor = self._connection.execute(
                "DELETE FROM results WHERE name = ? AND suite = ? AND key = ?", (self.name, self.suite, key)
            )
            dropped = cursor.rowcount > 0
            if dropped:
                self.invalidate_reports()
                self._save_metadata()
        return dropped

    # --- failures / skipped ---------------------------------------------

    @property
    def failures(self) -> list[dict]:
        """Every failure entry this suite owns, in the order they were recorded."""
        return self._notes("failures")

    @property
    def skipped(self) -> list[dict]:
        """Every skipped-cell entry this suite owns, in the order they were recorded."""
        return self._notes("skipped")

    def note_failure(self, failure: dict) -> None:
        """Record ``failure`` in the ``failures`` table.

        Deduped by ``configuration`` the same way :meth:`Config.note_failure`
        is today: an existing entry under the same label is replaced rather
        than accumulating a second one. An entry without a ``configuration``
        label skips the dedup and is simply appended.
        """
        self._note("failures", failure)

    def note_skipped(self, skipped: dict) -> None:
        """Record ``skipped`` in its own table, kept distinct from ``failures``.

        Same ``configuration``-label dedup rule as :meth:`note_failure`. A
        failure means "this should have worked and did not"; a skip means
        "this was not a measurement we intended to make" — conflating the
        two tables would make a clean sweep indistinguishable from a run
        where most of the matrix was never applicable.
        """
        self._note("skipped", skipped)

    def clear_failure(self, label: str) -> None:
        """Delete the failure recorded under ``label``, if any."""
        with self.transaction():
            self._protect_rows("failures", "name = ? AND suite = ? AND configuration = ?", (self.name, self.suite, label))
            self._ensure_benchmark()
            cursor = self._connection.execute(
                "DELETE FROM failures WHERE name = ? AND suite = ? AND configuration = ?", (self.name, self.suite, label)
            )
            if cursor.rowcount:
                self.invalidate_reports()
                self._save_metadata()

    def _note(self, table: str, entry: dict) -> None:
        label = entry.get("configuration")
        config_key = entry.get("config_key")
        key = _dump_key(_freeze(config_key, self.key_fields)) if isinstance(config_key, dict) else None
        with self.transaction():
            if label is not None:
                self._protect_rows(table, "name = ? AND suite = ? AND configuration = ?", (self.name, self.suite, label))
            self.invalidate_reports()
            self._ensure_benchmark()
            if label is not None:
                self._connection.execute(
                    f"DELETE FROM {table} WHERE name = ? AND suite = ? AND configuration = ?", (self.name, self.suite, label)
                )
            self._connection.execute(
                f"INSERT INTO {table} (name, suite, key, config_key, configuration, value) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.name,
                    self.suite,
                    key,
                    json.dumps(config_key) if config_key is not None else None,
                    label,
                    json.dumps(entry),
                ),
            )
            self._save_metadata()

    def _notes(self, table: str) -> list[dict]:
        cursor = self._connection.execute(
            f"SELECT value FROM {table} WHERE name = ? AND suite = ? ORDER BY id",
            (
                self.name,
                self.suite,
            ),
        )
        return [json.loads(value) for (value,) in cursor]

    # --- metadata --------------------------------------------------------

    def metadata_differences(self) -> dict[str, tuple[Any, Any]]:
        """``{field: (stored, current)}`` where the suite was last measured elsewhere.

        Same comparison rules as :meth:`Config.metadata_differences`: volatile
        fields (:data:`_VOLATILE_METADATA`) and fields the stored metadata
        does not have are ignored, and ``total_memory_bytes`` gets a slack
        tolerance rather than exact equality.
        """
        return {
            name: (self.stored_metadata[name], current)
            for name, current in self.metadata.items()
            if name not in _VOLATILE_METADATA
            and name in self.stored_metadata
            and not _same_environment(name, self.stored_metadata[name], current)
        }

    def save_metadata(self, *, progress: bool = False) -> None:
        """Persist :attr:`metadata` now, without any other write alongside it.

        Every other write (:meth:`write_result`, :meth:`note_failure`, …)
        already persists :attr:`metadata` as part of its own commit; this is
        for a caller — a run that ends without a further write of its own,
        say — that wants a metadata-only change (like flipping ``partial``
        to ``False`` on completion) durable on its own. ``progress=True`` is
        reserved for run-state advancement that preserves earlier observations;
        explicit metadata replacement is backed up by default.
        """
        with self.transaction():
            if not progress:
                self._protect_rows("metadata", "name = ? AND suite = ?", (self.name, self.suite))
            self.invalidate_reports()
            self._ensure_benchmark()
            self._save_metadata()

    def _load_metadata(self) -> dict:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE name = ? AND suite = ?",
            (
                self.name,
                self.suite,
            ),
        ).fetchone()
        return json.loads(row[0]) if row is not None else {}

    def _save_metadata(self) -> None:
        if not self._use_snapshot:
            saved = self._connection.execute(
                "SELECT option, kind, value FROM benchmark_config WHERE name = ? AND suite = ? ORDER BY option",
                (self.name, self.suite),
            ).fetchall()
            current = self._connection.execute(
                "SELECT option, kind, value FROM config WHERE suite = ? ORDER BY option", (self.suite,)
            ).fetchall()
            if saved and saved != current:
                self._backup()
            self._connection.execute("DELETE FROM benchmark_config WHERE name = ? AND suite = ?", (self.name, self.suite))
            self._connection.execute(
                "INSERT INTO benchmark_config SELECT ?, suite, option, kind, value FROM config WHERE suite = ?",
                (self.name, self.suite),
            )
        self._connection.execute(
            """
            INSERT INTO metadata (name, suite, value) VALUES (?, ?, ?)
            ON CONFLICT(name, suite) DO UPDATE SET value = excluded.value
            """,
            (self.name, self.suite, json.dumps(self.metadata)),
        )

    # --- config table ---------------------------------------------------

    def complete_config_snapshot(self) -> None:
        """Fill missing saved settings for a suite that has not recorded results."""
        if not self._use_snapshot:
            return
        declared = SUITE_OPTIONS.get(self.suite, {})
        with self.transaction():
            self._ensure_benchmark()
            saved = {row[0] for row in self._connection.execute(
                "SELECT option FROM benchmark_config WHERE name = ? AND suite = ?", (self.name, self.suite)
            )}
            missing = declared.keys() - saved
            if not missing:
                return
            if self._connection.execute(
                "SELECT 1 FROM results WHERE name = ? AND suite = ? LIMIT 1", (self.name, self.suite)
            ).fetchone():
                return
            current = dict(self._connection.execute("SELECT option, value FROM config WHERE suite = ?", (self.suite,)))
            if not saved and not current:
                return
            self._connection.executemany(
                "INSERT INTO benchmark_config (name, suite, option, kind, value) VALUES (?, ?, ?, ?, ?)",
                [(self.name, self.suite, option, declared[option].kind,
                  current.get(option, json.dumps(declared[option].default))) for option in missing],
            )

    def get_config(self, suite: str, name: str | None = None) -> Any:
        """Return a single option's value, or ``(uniform, varying)`` for ``suite``.

        With ``name``: the JSON-decoded value of ``(suite, name)``; raises
        :class:`KeyError` when no such row exists. Without ``name``:
        ``(uniform, varying)`` as two dicts in declaration order. The
        declaration order is whatever :data:`common.suites.SUITE_OPTIONS`
        carries for ``suite`` — options that exist in the database but
        not in the registry are dropped, so a stale row never leaks into
        a read. A suite with no registered options returns two empty
        dicts.
        """
        table = "benchmark_config" if self._use_snapshot else "config"
        scope = "name = ? AND " if self._use_snapshot else ""
        params = (self.name, suite) if self._use_snapshot else (suite,)
        if name is not None:
            row = self._connection.execute(
                f"SELECT value FROM {table} WHERE {scope}suite = ? AND option = ?", (*params, name)
            ).fetchone()
            if row is None:
                raise KeyError(f"no config row for suite={suite!r} option={name!r}")
            return json.loads(row[0])
        declared = SUITE_OPTIONS.get(suite) or {}
        uniform_order = [name for name, opt in declared.items() if opt.kind == "uniform"]
        varying_order = [name for name, opt in declared.items() if opt.kind == "varying"]
        rows = self._connection.execute(f"SELECT option, value FROM {table} WHERE {scope}suite = ?", params).fetchall()
        stored = {name: json.loads(value) for name, value in rows}
        uniform = {name: stored[name] for name in uniform_order if name in stored}
        varying = {name: stored[name] for name in varying_order if name in stored}
        return uniform, varying

    def set_config(self, suite: str, name: str, raw_value: str) -> Any:
        """Coerce ``raw_value`` against the option's ``coerce`` and write the row.

        The ``coerce`` callable comes from
        :data:`common.suites.SUITE_OPTIONS[suite][name]`. A bad value raises
        the same exception ``coerce`` raised, and the table is left
        untouched. Returns the parsed value so a caller that just coerced a
        string can use it without re-parsing.

        A literal ``null`` written for an option whose declared default is
        ``None`` (the runner writes it at sample time) is stored as
        :data:`None` without running the per-option validator: the validator
        is shaped for the runner-written value, so rejecting ``null`` here
        would only ever leave the row unset or force a second ``--unset``
        round-trip the runner overwrites anyway.
        """
        suite_options = SUITE_OPTIONS.get(suite)
        if suite_options is None:
            raise KeyError(f"unknown suite {suite!r}")
        option = suite_options.get(name)
        if option is None:
            raise KeyError(f"unknown option {name!r} for suite {suite!r}")
        if raw_value == "null" and option.default is None:
            value = None
        else:
            value = option.coerce(raw_value)
        if self._use_snapshot:
            with self.transaction():
                previous = self._connection.execute(
                    "SELECT kind, value FROM benchmark_config WHERE name = ? AND suite = ? AND option = ?",
                    (self.name, suite, name),
                ).fetchone()
                if previous and previous != (option.kind, json.dumps(value)):
                    self._backup()
                self._ensure_benchmark()
                self._connection.execute(
                    """INSERT INTO benchmark_config (name, suite, option, kind, value) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(name, suite, option) DO UPDATE SET kind = excluded.kind, value = excluded.value""",
                    (self.name, suite, name, option.kind, json.dumps(value)),
                )
            return value
        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO config (suite, option, kind, value) VALUES (?, ?, ?, ?)
                ON CONFLICT(suite, option) DO UPDATE SET kind = excluded.kind, value = excluded.value
                """,
                (suite, name, option.kind, json.dumps(value)),
            )
        return value

    def unset_config(self, suite: str, name: str) -> bool:
        """Remove the row for ``(suite, name)``; returns whether one was there."""
        with self.transaction():
            if self._use_snapshot:
                self._protect_rows("benchmark_config", "name = ? AND suite = ? AND option = ?", (self.name, suite, name))
            cursor = self._connection.execute(
                (
                    "DELETE FROM benchmark_config WHERE name = ? AND suite = ? AND option = ?"
                    if self._use_snapshot
                    else "DELETE FROM config WHERE suite = ? AND option = ?"
                ),
                (self.name, suite, name) if self._use_snapshot else (suite, name),
            )
            return cursor.rowcount > 0

    # --- schedule run-parameters (bench.config's schedule view) ----------

    def get_schedule_config(self, suite: str) -> dict[str, Any] | None:
        """Return ``suite``'s persisted run-parameters, or ``None`` if never written.

        The four fields mirror :class:`bench.config.SuiteConfig`:
        ``enabled``, ``samples``, ``amend``, ``skip_failed``. Unlike
        :meth:`get_config`, this is not validated against
        :data:`common.suites.SUITE_OPTIONS` — the run-parameters are
        scheduler bookkeeping, not a benchmark's own declared settings.
        """
        row = self._connection.execute(
            "SELECT enabled, samples, amend, skip_failed FROM schedule_config WHERE suite = ?",
            (suite,),
        ).fetchone()
        if row is None:
            return None
        enabled, samples, amend, skip_failed = row
        return {
            "enabled": bool(enabled),
            "samples": samples,
            "amend": bool(amend),
            "skip_failed": bool(skip_failed),
        }

    def set_schedule_config(
        self,
        suite: str,
        *,
        enabled: bool,
        samples: int,
        amend: bool,
        skip_failed: bool,
    ) -> None:
        """Upsert ``suite``'s run-parameters row.

        Always writes the full row: the four fields are one unit (they
        come from a single :class:`~bench.config.SuiteConfig`), so there
        is no partial-write case to reconcile.
        """
        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO schedule_config (suite, enabled, samples, amend, skip_failed)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(suite) DO UPDATE SET
                    enabled = excluded.enabled,
                    samples = excluded.samples,
                    amend = excluded.amend,
                    skip_failed = excluded.skip_failed
                """,
                (suite, int(enabled), int(samples), int(amend), int(skip_failed)),
            )

    # --- stored profiles --------------------------------------

    def put_profile(self, config_key: dict, kind: str, content: bytes) -> None:
        """Store ``content`` (raw bytes of a ``.callgrind``/``.log``/``.txt`` artifact)."""
        key = _dump_key(_freeze(config_key, self.key_fields))
        with self.transaction():
            self._protect_rows("profiles", "name = ? AND suite = ? AND key = ? AND kind = ?", (self.name, self.suite, key, kind))
            self.invalidate_reports()
            self._ensure_benchmark()
            self._connection.execute(
                """
                INSERT INTO profiles (name, suite, key, kind, content) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name, suite, key, kind) DO UPDATE SET content = excluded.content
                """,
                (self.name, self.suite, key, kind, content),
            )

    def get_profile(self, config_key: dict, kind: str) -> bytes | None:
        """The stored bytes for ``(config_key, kind)``, or ``None`` if nothing is stored."""
        key = _dump_key(_freeze(config_key, self.key_fields))
        row = self._connection.execute(
            "SELECT content FROM profiles WHERE name = ? AND suite = ? AND key = ? AND kind = ?",
            (self.name, self.suite, key, kind),
        ).fetchone()
        return bytes(row[0]) if row is not None else None

    def export_profile(self, config_key: dict, kind: str, destination: str | os.PathLike[str]) -> None:
        """Write the stored ``(config_key, kind)`` profile back to a real file at ``destination``.

        For tools (KCachegrind) that only understand a filesystem path.
        Raises :class:`KeyError` if nothing is stored under that key/kind.
        """
        content = self.get_profile(config_key, kind)
        if content is None:
            raise KeyError(f"no {kind!r} profile stored for {config_key!r} in suite {self.suite!r}")
        Path(destination).write_bytes(content)

    # --- reports (show pages) --------------------------------------------

    def put_report(self, page: str) -> None:
        """Store ``page`` (a rendered HTML report) as this suite's report, replacing any previous one."""
        with self.transaction():
            self._protect_rows("reports", "name = ? AND suite = ?", (self.name, self.suite))
            self._ensure_benchmark()
            self._connection.execute(
                """
                INSERT INTO reports (name, suite, content) VALUES (?, ?, ?)
                ON CONFLICT(name, suite) DO UPDATE SET content = excluded.content
                """,
                (self.name, self.suite, page.encode("utf-8")),
            )

    def get_report(self) -> str | None:
        """This suite's stored report, or ``None`` if nothing is stored."""
        row = self._connection.execute(
            "SELECT content FROM reports WHERE name = ? AND suite = ?",
            (
                self.name,
                self.suite,
            ),
        ).fetchone()
        return bytes(row[0]).decode("utf-8") if row is not None else None

    def export_report(self, destination: str | os.PathLike[str]) -> None:
        """Write the stored report back to a real file at ``destination``, for serving.

        Reports live only in the database; a caller that needs to hand one to
        an HTTP server (which needs a real file to serve) calls this to
        materialize it, the same way :meth:`export_profile` does for
        callgrind data. Raises :class:`KeyError` if nothing is stored.
        """
        page = self.get_report()
        if page is None:
            raise KeyError(f"no report stored for suite {self.suite!r}")
        Path(destination).write_text(page, encoding="utf-8")


# --- module-level helpers ----------------------------------------------


def _environment() -> dict:
    """What machine, OS, and interpreter this is, for a suite's metadata row."""
    return {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_count_logical": os.cpu_count(),
        "total_memory_bytes": _total_memory_bytes(),
        "python_version": platform.python_version(),
    }


def _cpu_model() -> str | None:
    """Best-effort human-readable CPU model name."""
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="replace") as info:
            for line in info:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _total_memory_bytes() -> int | None:
    """Total physical RAM in bytes, or None if it cannot be determined."""
    try:
        with open("/proc/meminfo", encoding="ascii") as info:
            for line in info:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def _same_environment(name: str, stored: Any, current: Any) -> bool:
    """Whether two values of metadata field ``name`` describe the same machine.

    Equality, with one field excepted: ``total_memory_bytes`` is measured
    rather than stated and drifts by a page or two across reboots, so it
    gets a megabyte of slack rather than exact equality.
    """
    if name == "total_memory_bytes" and all(isinstance(value, (int, float)) for value in (stored, current)):
        return abs(stored - current) <= 1024 * 1024
    return stored == current


def _freeze(config_key: dict | None, key_fields: list[str] | None = None) -> tuple:
    """Hashable, order-independent form of a config_key dict.

    Keys and values are normalised to strings so a list of ints, a list of
    strs, and the same numbers as strings all collide on the same row.
    ``key_fields``, when set, restricts the result to the named fields; a
    field not in the list is treated as if it were uniform. ``None`` keeps
    every field.
    """
    config_key = config_key or {}
    if key_fields is not None:
        allowed = frozenset(key_fields)
        items = ((str(name), str(value)) for name, value in config_key.items() if name in allowed)
    else:
        items = ((str(name), str(value)) for name, value in config_key.items())
    return tuple(sorted(items))


def _dump_key(frozen: tuple) -> str:
    """Canonical string form of a frozen key, for use as a SQLite column value."""
    return json.dumps(list(frozen))


def _load_key(text: str) -> tuple:
    """Inverse of :func:`_dump_key`."""
    return tuple(tuple(pair) for pair in json.loads(text))
