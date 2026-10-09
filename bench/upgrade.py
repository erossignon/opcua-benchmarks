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

"""Upgrade a legacy bench.db to named runs: python -m bench.upgrade [bench.db]."""

from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from common.backup import verified_backup, writer_transaction
from common.bench_schema import SCHEMA

_ARTIFACT_TABLES = ("results", "failures", "skipped", "metadata", "profiles", "reports")


def upgrade_database(path: Path) -> Path | None:
    """Back up and transactionally upgrade a database; return the backup path."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with writer_transaction(connection):
            columns = connection.execute("PRAGMA table_info(results)").fetchall()
            if not columns:
                raise ValueError("Not a benchmark database: missing results table")
            if "name" in {column[1] for column in columns}:
                return None
            backup = verified_backup(connection)
            present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in _ARTIFACT_TABLES:
                if table in present:
                    connection.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
            for index in ("failures_suite_configuration", "failures_suite_key", "skipped_suite_configuration"):
                connection.execute(f"DROP INDEX IF EXISTS {index}")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO benchmarks (name, created_at) VALUES (?, ?)",
                ("unnamed-1", datetime.datetime.now(datetime.timezone.utc).isoformat()),
            )
            connection.execute("INSERT INTO benchmark_config SELECT 'unnamed-1', suite, option, kind, value FROM config")
            for table in _ARTIFACT_TABLES:
                if table in present:
                    columns_sql = ", ".join(row[1] for row in connection.execute(f"PRAGMA table_info(legacy_{table})"))
                    connection.execute(
                        f"INSERT INTO {table} (name, {columns_sql}) SELECT 'unnamed-1', {columns_sql} FROM legacy_{table}"
                    )
                    connection.execute(f"DROP TABLE legacy_{table}")
            connection.execute("PRAGMA user_version = 1")
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", type=Path, default=Path("bench.db"))
    args = parser.parse_args(argv)
    try:
        backup = upgrade_database(args.db)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Upgraded {args.db}; existing run: unnamed-1; backup: {backup}" if backup else f"{args.db} is already upgraded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
