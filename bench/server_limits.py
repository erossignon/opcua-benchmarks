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

"""CLI for server resource-limit measurements."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from common.config_cli import add_config_subparser, run_from_namespace as config_run_from_namespace
from common.report import named_report
from common.sample import named_sample
from common.report import add_serve_arguments, short_circuit, store_and_serve
from server_limits import run, show
from server_limits.run import MIN_SAMPLES

REPORT_NAME = "server_limits.html"
DATABASE_NAME = "bench.db"


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bench.server_limits",
        description=(
            "Escalate concurrent load (client processes x async pipeline depth) against "
            "each OPC UA server implementation, fired by a fixed open62541 C client, until "
            "it errors, its tail latency blows up, its throughput collapses, or it dies. "
            "Addressed at a bench.db file. Use 'new' to seed this suite's default config "
            "into it, 'sample' to (re)trace it, and 'show' to build its report."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new = subparsers.add_parser(
        "new",
        help="create <db> and seed this suite's default config rows into it",
    )
    new.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=Path("bench.db"),
        help="path to the bench.db file to create and seed (the parent directory is created if needed); defaults to 'bench.db'",
    )

    sample = subparsers.add_parser(
        "sample",
        help="trace every implementation's capacity frontier and write it into <db>",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "The matrix is the cartesian product of the suite's varying options minus the "
            "cells ``applicable()`` skips; its uniform options apply to every configuration. "
            "<num_samples> is the *kept* samples per cell — the runner does "
            "one extra pass and discards the first, so "
            "a run with num_samples=7 actually invokes each cell eight times. Without "
            "--amend the run remeasures every cell from scratch; with --amend existing rows "
            "are kept and topped up to <num_samples>."
        ),
    )
    sample.add_argument(
        "num_samples",
        type=int,
        help="kept samples wanted per cell; existing rows are extended to this many with --amend",
    )
    sample.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=Path("bench.db"),
        help="path to the bench.db file holding this suite's config and results; defaults to 'bench.db'",
    )
    sample.add_argument(
        "--amend",
        action="store_true",
        help=(
            "keep existing rows and top them up to <num_samples> instead of remeasuring "
            "every cell from scratch; the ramp for each implementation visits exactly the "
            "(clients, outstanding) cells that already have rows"
        ),
    )
    sample.add_argument(
        "--skip-failed",
        action="store_true",
        help=(
            "leave the configurations recorded under 'failures' alone instead of "
            "retrying them; useful once a failure is known to be a limit of the "
            "implementation rather than a flake (needs --amend)"
        ),
    )

    sample.add_argument("--name", help="save a new named run, or select its stored configuration with --amend")

    show_parser = subparsers.add_parser(
        "show",
        help="build this suite's report from <db>",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    show_parser.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=Path("bench.db"),
        help="path to the bench.db file holding this suite's config and results; defaults to 'bench.db'",
    )
    add_serve_arguments(
        show_parser,
        cdn_help=(
            "Load plotly.js from the CDN instead of inlining it. Cuts the output "
            "from a few MB to under 100 kB, but the report then needs network access."
        ),
    )

    add_config_subparser(subparsers, "server_limits")

    args = parser.parse_args(argv)
    if args.command == "sample":
        if args.num_samples < MIN_SAMPLES:
            parser.error(
                f"num_samples must be at least {MIN_SAMPLES} — the lower bound is what "
                "the bootstrap-style signal needs to land on"
            )
        if args.skip_failed and not args.amend:
            parser.error("--skip-failed only makes sense with --amend")
    return args


def cmd_new(db: Path) -> int:
    """Seed this suite's config rows into ``db``.

    Creates ``db``'s parent directory if needed; the database may already
    exist with config rows seeded, in which case ``run.cmd_new`` asks
    before overwriting them.
    """
    db.parent.mkdir(parents=True, exist_ok=True)
    return run.cmd_new(argparse.Namespace(database=db))


def cmd_sample(db: Path, num_samples: int, amend: bool, skip_failed: bool) -> int:
    return run.cmd_sample(
        argparse.Namespace(
            database=db,
            num_samples=num_samples,
            amend=amend,
            skip_failed=skip_failed,
        )
    )


@named_report
def cmd_show(args: argparse.Namespace) -> int:
    short = short_circuit(
        args.db,
        "server_limits",
        "python -m bench.server_limits new",
        "python -m bench.server_limits sample",
    )
    if short is not None:
        code, message = short
        print(message, file=sys.stderr)
        return code
    try:
        metadata, uniform, results, failures = show.load_document(args.db)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    payload = show.build_payload(metadata, results, failures)
    plotly_tag = (
        f'<script src="{show.PLOTLY_CDN}" charset="utf-8"></script>' if args.cdn else f"<script>{show.get_plotlyjs()}</script>"
    )
    page = show.build_page(metadata, payload, uniform, plotly_tag)
    store_and_serve(
        args.db,
        "server_limits",
        REPORT_NAME,
        page,
        args,
        f"from {len(payload['points'])} load level(s) across {len(payload['implementations'])} implementation(s) in {args.db}",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.command == "new":
        return cmd_new(args.db)
    if args.command == "sample":
        return named_sample(args, "server_limits", cmd_sample)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "config":
        return config_run_from_namespace("server_limits", args)
    return 2  # unreachable: required=True subparsers


if __name__ == "__main__":
    raise SystemExit(main())
