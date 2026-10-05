"""Explicit, backed-up rename of historical grid runs without reinterpreting them."""

from contextlib import closing
import json
import sqlite3

from common.backup import verified_backup, verify_snapshot, writer_transaction

OLD = "server_limits_simple"
NEW = "server_capacity"
TABLES = ("config", "schedule_config", "benchmark_config", "results", "failures", "skipped", "metadata", "profiles", "reports")


def migrate(database):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        with writer_transaction(connection):
            old = any(connection.execute(f"SELECT 1 FROM {t} WHERE suite=? LIMIT 1", (OLD,)).fetchone() for t in TABLES)
            if not old:
                return None
            if any(connection.execute(f"SELECT 1 FROM {t} WHERE suite=? LIMIT 1", (NEW,)).fetchone() for t in TABLES):
                raise ValueError("Both suite names exist; refusing to merge evidence. Keep them in separate databases.")
            schedules = []
            for name, encoded in connection.execute("SELECT name, schedule FROM benchmarks WHERE schedule IS NOT NULL"):
                schedule = json.loads(encoded)
                if schedule is None:
                    continue
                changed = False
                for entry in schedule.get("entries", []):
                    suites = entry["suites"]
                    if OLD in suites:
                        if NEW in suites:
                            raise ValueError("Both suite names exist in a saved schedule")
                        suites[NEW] = suites.pop(OLD)
                        changed = True
                if changed:
                    schedules.append((json.dumps(schedule), name))
            backup = verified_backup(connection)
            for table in TABLES:
                connection.execute(f"UPDATE {table} SET suite=? WHERE suite=?", (NEW, OLD))
            connection.executemany("UPDATE benchmarks SET schedule=? WHERE name=?", schedules)
            for name, encoded in connection.execute("SELECT name, value FROM metadata WHERE suite=?", (NEW,)).fetchall():
                value = json.loads(encoded)
                value["method"] = "legacy_grid"
                connection.execute("UPDATE metadata SET value=? WHERE name=? AND suite=?", (json.dumps(value), name, NEW))
            # Historical config snapshots and observations remain unchanged. Only
            # future-run settings drop the retired Cartesian-grid options.
            connection.execute(
                "DELETE FROM config WHERE suite=? AND option IN ('clients', 'outstanding', 'duration_ms')", (NEW,)
            )
            connection.execute("DELETE FROM reports WHERE suite IN (?, 'index')", (NEW,))
            verify_snapshot(connection)
        return str(backup)
