"""Consolidate subscription suites: python -m bench.migrate_subscription [bench.db]."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from common.backup import verified_backup, verify_snapshot, writer_transaction

RETIRED = ("subscriptions", "subscription_delivery")
PREVIOUS = "subscription_counter"
CURRENT = "subscription"
TABLES = (
    "config",
    "schedule_config",
    "benchmark_config",
    "results",
    "failures",
    "skipped",
    "metadata",
    "profiles",
    "reports",
)


def migrate_schedule(encoded):
    """Rename scheduled counter work and discard entries for retired suites."""
    schedule = json.loads(encoded)
    entries = []
    for entry in schedule["entries"]:
        suites = entry["suites"]
        if PREVIOUS in suites and CURRENT in suites:
            raise ValueError("Schedule contains both subscription and subscription_counter")
        retained = {CURRENT if key == PREVIOUS else key: value for key, value in suites.items() if key not in RETIRED}
        if retained or not suites:
            entries.append({**entry, "suites": retained})
    migrated = {**schedule, "entries": entries}
    return json.dumps(migrated) if migrated != schedule else encoded


def migrate(database: Path):
    """Back up and atomically migrate suite identities without rewriting evidence."""
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with writer_transaction(connection):
            counts = {
                table: dict(connection.execute(f"SELECT suite, count(*) FROM {table} GROUP BY suite")) for table in TABLES
            }
            if any(PREVIOUS in counts[table] for table in TABLES) and any(CURRENT in counts[table] for table in TABLES):
                raise ValueError("Database contains both subscription and subscription_counter; refusing to merge runs")
            schedules = []
            for name, encoded in connection.execute("SELECT name, schedule FROM benchmarks WHERE schedule IS NOT NULL"):
                migrated = migrate_schedule(encoded)
                if migrated != encoded:
                    schedules.append((migrated, name))
            removed = sum(counts[table].get(suite, 0) for table in TABLES for suite in RETIRED)
            renamed = sum(counts[table].get(PREVIOUS, 0) for table in TABLES)
            if not (removed or renamed or schedules):
                return dict(removed=0, renamed=0, schedules=0, backup=None)
            backup = verified_backup(connection)
            for table in TABLES:
                connection.execute(f"DELETE FROM {table} WHERE suite IN (?, ?)", RETIRED)
                connection.execute(f"UPDATE {table} SET suite=? WHERE suite=?", (CURRENT, PREVIOUS))
            connection.executemany("UPDATE benchmarks SET schedule=? WHERE name=?", schedules)
            # Cached HTML embeds suite names, filenames and navigation links.
            connection.execute("DELETE FROM reports WHERE suite IN (?, 'index')", (CURRENT,))
            verify_snapshot(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("Migration failed foreign_key_check")
        # Reclaim space occupied by the retired observations and embedded reports.
        connection.execute("VACUUM")
        verify_snapshot(connection)
    return dict(removed=removed, renamed=renamed, schedules=len(schedules), backup=str(backup))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", nargs="?", type=Path, default=Path("bench.db"))
    args = parser.parse_args(argv)
    print(json.dumps(migrate(args.database), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
