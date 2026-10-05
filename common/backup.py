# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Verified SQLite snapshots while the caller holds the database writer lock."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4


def verify_snapshot(connection):
    """Raise if SQLite cannot verify the completed snapshot."""
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise sqlite3.DatabaseError("Backup failed PRAGMA quick_check")


def verified_backup(connection):
    """Snapshot committed data under a caller-owned BEGIN IMMEDIATE transaction.

    A separate read connection avoids backing up the connection holding the
    write transaction. The reserved writer lock excludes intervening commits
    through verification and mutation, in WAL and rollback-journal modes.
    Private in-memory stores have no persistent restore artifact.
    """
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("Backup requires a writer transaction")
    filename = connection.execute("PRAGMA database_list").fetchone()[2]
    if not filename:
        return None
    source = Path(filename)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = source.with_name(f"{source.name}.{stamp}.{uuid4().hex}.bak")
    incomplete = target.with_name(target.name + ".incomplete")
    try:
        with incomplete.open("xb"):
            pass
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(incomplete)) as destination:
                reader.backup(destination)
                verify_snapshot(destination)
        with incomplete.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
        # Publish without replacing an existing artifact, even on a collision.
        os.link(incomplete, target)
        try:
            directory_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            target.unlink()
            raise
        incomplete.unlink()
        print(f"Verified database backup: {target}", file=sys.stderr, flush=True)
        return target
    except BaseException:
        print(f"Database backup failed; incomplete snapshot: {incomplete}", file=sys.stderr, flush=True)
        raise


@contextmanager
def writer_transaction(connection):
    """Hold SQLite's writer reservation until the entire operation commits."""
    if connection.in_transaction:
        raise sqlite3.ProgrammingError("Writer transaction must begin before any writes")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
