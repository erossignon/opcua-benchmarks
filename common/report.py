#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Store and serve reports, and classify available results."""

from __future__ import annotations

import argparse
from contextlib import closing
from functools import wraps
import http.server
import socketserver
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Literal

from common.bench_db import BenchDB, benchmark_scope

# Result classes a document can be in for the page. ``complete`` means there is
# at least one row the page can render; ``partial`` means there are rows but at
# least one of the page's hard dependencies is missing (a stored profile blob,
# say) so the page renders what it can; ``none`` means there is nothing to
# render (no database, or a database with zero rows and zero failures). The
# suites' rules differ, and the helper is the one place they live so
# per-suite ``cmd_show`` and the combined ``bench.show`` cannot disagree.
ResultClass = Literal["complete", "partial", "none"]

# The shared SQLite database every suite's :class:`BenchDB` opens. Every
# suite is classified by querying its slice of this database directly.
_KNOWN_SUITES = frozenset(
    {
        "throughput",
        "server_limits",
        "server_capacity",
        "subscription",
    }
)


def named_report(build):
    """Select a saved run consistently for every store opened by a report builder."""

    @wraps(build)
    def wrapped(args):
        if not args.db.exists():
            return build(args)
        try:
            with closing(BenchDB(args.db, suite="")) as store:
                selected = getattr(args, "name", None)
                name = selected.strip() if selected is not None else store.name
                if getattr(args, "name", None) is not None and name not in store.benchmark_names():
                    print(f"Unknown benchmark name {name!r}", file=sys.stderr)
                    return 1
        except (OSError, sqlite3.Error) as error:
            print(error, file=sys.stderr)
            return 1
        with benchmark_scope(name):
            return build(args)

    return wrapped


def classify_results(database: Path, suite: str) -> ResultClass:
    """Classify renderable results as complete, partial, or none; reject unknown suites."""
    if suite not in _KNOWN_SUITES:
        raise ValueError(f"unknown suite {suite!r}")
    if not database.exists():
        return "none"
    with closing(BenchDB(database, suite=suite)) as store:

        def present(table):
            return (
                store._connection.execute(
                    f"SELECT 1 FROM {table} WHERE name = ? AND suite = ? LIMIT 1", (store.name, suite)
                ).fetchone()
                is not None
            )

        if suite == "server_capacity":
            # This suite renders incomplete searches and their limitations.
            # Report availability must not hide valid SDK bars when another
            # SDK exhausts its search budget or encounters a worker failure.
            return "complete" if present("results") or present("failures") else "none"
        if suite == "throughput":
            return "complete" if present("results") else "partial" if present("failures") else "none"
        if suite == "subscription":
            return "complete" if present("results") or present("failures") or store.stored_metadata else "none"
        if suite == "server_limits":
            return "complete" if present("results") else "none"


def short_circuit(
    database: Path,
    suite: str,
    new_cli: str,
    sample_cli: str,
) -> tuple[int, str] | None:
    """Return ``(exit_code, message)`` when ``cmd_show`` should stop, else ``None``.

    Two ``none`` flavours, in order: a missing database (a real failure —
    exit 1) and a database with no rows and no failures (scaffolding ran
    but no measurement landed — exit 0, recovery is ``sample``). A
    database with rows returns ``None``, meaning "proceed to render". The
    combined ``bench.show`` uses this too so its per-tab verdict and each
    suite's verdict cannot disagree.
    """
    if not database.exists():
        return 1, (
            f"{database} does not exist. Run '{new_cli} {database}' "
            "to create it (or 'python -m bench' to create every suite)."
        )
    if classify_results(database, suite) == "none":
        return 0, f"{database} has nothing to render; run '{sample_cli} {database}' to populate it."
    return None


def add_serve_arguments(parser: argparse.ArgumentParser, *, cdn_help: str | None = None) -> None:
    """Add ``--build-only``/``--port``/``--cdn`` to a show.py parser.

    Serving on localhost is the default; ``--build-only`` opts out of it and
    just writes the file. ``cdn_help`` lets a caller keep its own --cdn
    wording (the suites disagree on how much smaller the CDN version is)
    without duplicating the other two arguments to do it.
    """
    parser.add_argument("--name", help="Saved benchmark name (default: latest run).")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Only write the report file; don't serve it on localhost.",
    )
    parser.add_argument("--port", type=int, default=8765, help="Port to serve the report on. 0 picks a free one.")
    parser.add_argument(
        "--cdn",
        action="store_true",
        help=cdn_help
        or (
            "Load plotly.js from the CDN instead of inlining it. Cuts the output "
            "from about 5 MB to about 100 kB, but the report then needs network access."
        ),
    )


def serve(destination: Path, port: int) -> None:
    """Serve the report's directory on localhost until interrupted."""
    directory = str(destination.parent.resolve())

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *arguments, **keywords):
            super().__init__(*arguments, directory=directory, **keywords)


        def log_message(self, *_arguments):  # noqa: D102 - quiet by design
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", port), Handler) as httpd:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/{destination.name}"
        print(f"Serving {destination.name} at {url} — Ctrl-C to stop", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.", file=sys.stderr)


def store_and_serve(
    database: Path,
    suite: str,
    report_name: str,
    page: str,
    args: argparse.Namespace,
    description: str,
) -> None:
    """Store ``page`` as ``suite``'s report, print the summary line, then serve it unless ``--build-only``."""
    with closing(BenchDB(database, suite=suite)) as store:
        store.put_report(page)
    print(f"Stored {report_name} ({len(page) / 1_048_576:.1f} MiB) {description}", file=sys.stderr)
    if args.build_only:
        return
    with tempfile.TemporaryDirectory(prefix="bench-show-") as working:
        destination = Path(working) / report_name
        destination.write_text(page, encoding="utf-8")
        serve(destination, args.port)
