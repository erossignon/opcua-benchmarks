#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Build and serve a tabbed report from the stored suite results."""

from __future__ import annotations

import argparse
from contextlib import closing
import sys
from dataclasses import dataclass
from pathlib import Path

import tempfile

from bench import (
    server_limits,
    server_capacity,
    throughput,
    subscription,
)
from bench.show_index import SHOW_SUITES, SuiteTab, render_index_page
from common.bench_db import BenchDB
from common.report import named_report
from common.report import add_serve_arguments, classify_results, short_circuit

_INDEX_SUITE = "index"


@dataclass(frozen=True)
class _SuiteSpec:
    """What the combined CLI needs to know to run one suite's ``cmd_show``.

    ``module`` is the suite's CLI module (``bench.throughput`` etc.),
    imported at module load so each ``cmd_show`` is the same function
    object the per-suite ``main`` calls. ``report_name`` is the canonical
    filename the suite's report is served under; ``new_cli`` and
    ``sample_cli`` are the recovery commands the index's empty-state card
    points at when the suite is ``none``.

    The dataclass is frozen so the per-suite table below can be trusted
    as a lookup table, not as a mutable dispatcher.
    """

    module: object
    report_name: str
    new_cli: str
    sample_cli: str


# Per-suite wiring, in the same fixed order as :data:`SHOW_SUITES`. A new
# suite only has to land here (and in the renderer's ``SHOW_SUITES``); no
# ``if``/``elif`` chain grows anywhere else.
_SUITES: tuple[_SuiteSpec, ...] = (
    _SuiteSpec(
        module=throughput,
        report_name=throughput.REPORT_NAME,
        new_cli="python -m bench.throughput new",
        sample_cli="python -m bench.throughput sample",
    ),
    _SuiteSpec(
        module=server_limits,
        report_name=server_limits.REPORT_NAME,
        new_cli="python -m bench.server_limits new",
        sample_cli="python -m bench.server_limits sample",
    ),
    _SuiteSpec(
        module=server_capacity,
        report_name=server_capacity.REPORT_NAME,
        new_cli="python -m bench.server_capacity new",
        sample_cli="python -m bench.server_capacity sample 3 --name limits",
    ),
    _SuiteSpec(
        module=subscription,
        report_name=subscription.REPORT_NAME,
        new_cli="python -m bench.subscription new",
        sample_cli="python -m bench.subscription sample 1",
    ),
)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse report selection, rendering, and serving options."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.show",
        description=(
            "Build a tabbed index page that brings the suite reports together — "
            "throughput, server_limits, server_capacity, subscription — into one "
            "self-contained HTML page with one iframe per suite. By default, existing "
            "per-suite .html files on disk are reused; missing ones are built. --force "
            "always rebuilds. Serves on 127.0.0.1:<port> by default; --build-only skips "
            "the server."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=Path("bench.db"),
        help="path to the bench.db file to build the index over; defaults to 'bench.db'",
    )
    add_serve_arguments(parser)
    parser.add_argument("--list-names", action="store_true", help="List saved benchmark names and exit.")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Rebuild every per-suite report, regardless of whether one is already in the database. "
            "The default reuses stored reports verbatim and only builds the ones "
            "that are missing."
        ),
    )
    return parser.parse_args(argv)


def _namespace_for(spec: _SuiteSpec, database: Path, args: argparse.Namespace) -> argparse.Namespace:
    """Build the arguments for a suite report without starting its HTTP server."""
    namespace = argparse.Namespace(db=database, cdn=args.cdn, port=args.port, build_only=True)
    return namespace


def _empty_state_for(spec: _SuiteSpec, database: Path, suite: str, message: str) -> tuple[str, str, str | None]:
    """Translate a per-suite ``short_circuit`` message into ``SuiteTab`` fields.

    Two flavours in order:

    * missing database → ``short_circuit`` returns ``(1, msg)``, message
      names the ``new_cli``;
    * zero rows, no failures → ``(0, msg)``, message names the
      ``sample_cli``.

    The renderer wants the *what* and the *fix*, not the exact exit
    code (the index page is rendered either way); the count line gets a
    best-effort string when the message implies one. The database-missing
    case cannot reach this function — the combined CLI short-circuits
    on it before the per-suite loop starts.
    """
    if "nothing to render" in message:
        cli = spec.sample_cli
        count = _count_for(database, suite)
    else:
        cli = spec.new_cli
        count = None
    return message, cli, count


def _count_for(database: Path, suite: str) -> str | None:
    """A one-line count from ``database`` for the empty-state card, or ``None``.

    The per-suite messages already say what is missing, but the card
    also shows the *count* when the database exists and is just empty
    (``0 row(s)`` for throughput/server_limits, ``73 recorded
    failure(s)`` for throughput when only failures remain). The verdict
    itself does not give us the count, so the card reaches a level
    deeper than ``classify_results`` to print it. The helper swallows
    every exception — a database that ``short_circuit`` already tolerated
    must not raise again here and derail the page.
    """
    try:
        verdict = classify_results(database, suite)
    except Exception:  # noqa: BLE001 - card is optional
        return None
    if verdict != "none":
        return None
    if suite == "throughput":
        store = BenchDB(database, suite=suite)
        if store.results:
            return None  # shouldn't happen, classify said none
        return f"{len(store.failures)} recorded failure(s)"
    if suite == "server_limits":
        return None
    return None


def _report_stored(database: Path, suite: str) -> bool:
    """``True`` iff ``suite`` already has a report stored in ``database``."""
    if not database.exists():
        return False
    with closing(BenchDB(database, suite=suite)) as store:
        return (
            store._connection.execute("SELECT 1 FROM reports WHERE name = ? AND suite = ?", (store.name, suite)).fetchone()
            is not None
        )


def _should_rebuild(database: Path, suite: str, force: bool) -> bool:
    """``True`` iff the per-suite report should be regenerated now.

    Two cases, in order:

    * ``force=True`` (``--force``): always rebuild, regardless of
      whether a report is already stored.
    * ``force=False`` (the default): trust whatever report is already
      stored in the database — only build a missing one. There is no
      completeness check: if a previous run produced a page, that page
      is used verbatim, even if a newer ``sample`` has added rows.
      ``--force`` is the explicit way to opt in to a rebuild.

    The class verdict (``complete`` | ``partial`` | ``none``) is *not*
    consulted here — that decision belongs to the index renderer, which
    uses it to draw the badge. A report left behind by a previous run is
    treated as authoritative for the rebuild decision. Older subscription and
    server-capacity pages are regenerated when their report version changes.
    """
    if force:
        return True
    if suite in ("subscription", "server_capacity"):
        if suite == "subscription":
            from bench.counter_report import REPORT_MARKER
        else:
            from server_capacity.show import REPORT_MARKER

        with closing(BenchDB(database, suite=suite)) as store:
            row = store._connection.execute(
                "SELECT substr(content, 1, 4096) FROM reports WHERE name = ? AND suite = ?", (store.name, suite)
            ).fetchone()
            return row is None or REPORT_MARKER.encode() not in row[0]
    return not _report_stored(database, suite)


def _tab_for(spec: _SuiteSpec, database: Path, suite: str, args: argparse.Namespace) -> SuiteTab:
    """Build the ``SuiteTab`` for one suite: rebuild if needed, classify, attach report filename.

    Three branches, in order:

    * ``short_circuit`` returned — the database has nothing to render for
      this suite, the tab is an empty-state card with no iframe.
      ``short_circuit`` for a *missing database* returns ``(1, msg)``; the
      combined CLI short-circuits on it before the per-suite loop starts,
      so this function only sees the ``(0, msg)`` (empty) case.
    * The per-suite report is already stored in the database and
      ``--force`` is not set — reuse it; classify against the document
      so the tab's badge reflects the current verdict.
    * Otherwise, rebuild: invoke the suite's ``cmd_show`` with
      ``build_only=True`` so it stores the report but does not block on
      its own server. Classify against the freshly stored report.

    The renderer's only input is ``SuiteTab``; this function is where
    the per-suite orchestration happens.
    """
    short = short_circuit(database, suite, spec.new_cli, spec.sample_cli)
    if short is not None:
        message = short[1]
        reason, cli, count = _empty_state_for(spec, database, suite, message)
        return SuiteTab(suite=suite, state="none", reason=reason, recovery_cli=cli, document_count=count)

    if not _should_rebuild(database, suite, force=args.force):
        # Reuse the stored report. Classify against the database so the
        # tab badge reflects the current verdict (a previous run might
        # have left a fresh report behind for a database that is now
        # ``partial``).
        state = classify_results(database, suite)
        if state == "complete":
            return SuiteTab(suite=suite, state="complete", report_filename=spec.report_name)
        if state == "partial":
            return SuiteTab(
                suite=suite,
                state="partial",
                report_filename=spec.report_name,
                reason=f"{spec.report_name} rendered with degraded data",
                recovery_cli=spec.sample_cli,
            )
        # ``none`` rows + a stale report already stored (a previous run
        # had data, the latest sample has none): drop the iframe and
        # show the empty-state card so the page does not embed a stale
        # page whose body contradicts the database's current verdict.
        reason, cli, count = _empty_state_for(spec, database, suite, f"{database} has nothing to render")
        return SuiteTab(suite=suite, state="none", reason=reason, recovery_cli=cli, document_count=count)

    namespace = _namespace_for(spec, database, args)
    try:
        spec.module.cmd_show(namespace)
    except (OSError, ValueError, KeyError) as error:
        return SuiteTab(
            suite=suite,
            state="partial",
            reason=f"{spec.report_name} could not be built: {error}",
            recovery_cli=spec.sample_cli,
        )

    state = classify_results(database, suite)
    if state == "complete" and _report_stored(database, suite):
        return SuiteTab(suite=suite, state="complete", report_filename=spec.report_name)
    if state == "partial":
        return SuiteTab(
            suite=suite,
            state="partial",
            reason=f"{spec.report_name} rendered with degraded data",
            recovery_cli=spec.sample_cli,
        )
    # ``none`` verdict after ``cmd_show`` ran — the file is empty or
    # missing. Use the empty-state card with a generic message.
    return SuiteTab(
        suite=suite,
        state="none",
        reason=f"{spec.report_name} was not produced",
        recovery_cli=spec.sample_cli,
    )


def _summarise_issues(tabs: list[SuiteTab]) -> str:
    """A ``stderr`` summary listing every ``partial`` tab and its fix.

    ``none`` tabs are *not* listed here: ``none`` is the "no results yet"
    state (an empty bench document or a document that does not exist yet),
    and the index page already shows a friendly empty-state card for it
    on its own. The ``partial`` state is the only verdict that means
    "the page rendered but something is wrong with the data"; that is
    the one worth flagging in ``stderr`` and exiting non-zero for. A
    reader who wants every empty tab surfaced can read the index page.
    """
    lines = ["The following suites did not render fully:"]
    for tab in tabs:
        if tab.state != "partial":
            continue
        lines.append(f"  - {tab.suite}: run '{tab.recovery_cli} <db>' to recover")
    return "\n".join(lines)


def build_tabs(database: Path, args: argparse.Namespace) -> list[SuiteTab]:
    """Walk every suite and return one ``SuiteTab`` per suite, in fixed order.

    The function is the single seam tests substitute: it takes the
    database the CLI was handed and the parsed args, walks ``SHOW_SUITES``
    in fixed order, and returns one ``SuiteTab`` per suite. The CLI
    orchestrator (``main``) is just "call this, then render the page,
    then pick an exit code", so a test can drop in its own
    ``_tab_for`` and exercise the rendering + exit-code logic alone.
    The default implementation walks each suite against the database
    unchanged.
    """
    specs_by_suite = {suite: spec for spec, suite in zip(_SUITES, SHOW_SUITES)}
    tabs = []
    for suite in SHOW_SUITES:
        print(f"Preparing {suite} report…", file=sys.stderr, flush=True)
        tabs.append(_tab_for(specs_by_suite[suite], database, suite, args))
    return tabs


def _has_issues(tabs: list[SuiteTab]) -> bool:
    """``True`` iff at least one tab is ``partial``.

    ``partial`` is the verdict that means "the page rendered but the
    data is degraded" — that is worth flagging in ``stderr`` and
    returning a non-zero exit code for. ``none`` (an empty bench
    document, or one that does not exist yet) is not an issue: the
    index page already shows an empty-state card with the recovery
    command, and treating "no results yet" as a failure makes a fresh
    ``python -m bench.show`` exit non-zero before any measurement has
    landed, which is the opposite of what the page is for.
    """
    return any(tab.state == "partial" for tab in tabs)


def main(argv: list[str] | None = None) -> int:
    """The CLI seam: parse, short-circuit the missing database, run, render, exit."""
    args = parse_arguments(argv)
    if args.list_names:
        if not args.db.exists():
            print(f"{args.db} does not exist", file=sys.stderr)
            return 1
        store = BenchDB(args.db, suite="")
        try:
            print("\n".join(store.benchmark_names()))
        finally:
            store.close()
        return 0
    return _show(args)


@named_report
def _show(args) -> int:
    database = args.db

    if not database.exists():
        print(
            (
                f"{database} does not exist. Run 'python -m bench {database}' to seed "
                "every suite's config there (or 'python -m bench.<throughput|server_limits|server_capacity|subscription> new "
                f"{database}' for one at a time)."
            ),
            file=sys.stderr,
        )
        return 1

    tabs = build_tabs(database, args)

    page = render_index_page(database.name, tabs)
    with closing(BenchDB(database, suite=_INDEX_SUITE)) as store:
        store.put_report(page)

    if _has_issues(tabs):
        print(_summarise_issues(tabs), file=sys.stderr)
        return 2

    if args.build_only:
        return 0

    # No-server exit codes are pinned above; serving happens on the
    # complete-or-empty happy path (no ``partial`` tabs to flag).
    # Serving with a degraded page open in a tab would just put the
    # user face-to-face with the same message the empty-state card on
    # the index already carries.
    #
    # Every report lives only in the database (index included), but the
    # index page's tabs are same-origin iframes that reference each
    # per-suite report by relative filename, so serving needs all of them
    # materialized as real files next to each other. A private temporary
    # directory holds that snapshot for the life of the server and is
    # torn down the moment it stops; nothing is left on disk either way.
    from common.report import serve

    with tempfile.TemporaryDirectory(prefix="bench-show-index-") as working:
        working_dir = Path(working)
        destination = working_dir / "index.html"
        destination.write_text(page, encoding="utf-8")
        for suite, tab in zip(SHOW_SUITES, tabs):
            if tab.state == "complete" and tab.report_filename:
                with closing(BenchDB(database, suite=suite)) as store:
                    store.export_report(working_dir / tab.report_filename)
        with closing(BenchDB(database, suite="")) as store:
            serve(destination, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
