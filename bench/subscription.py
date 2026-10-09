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

"""Counter progress with five server SDKs and one open62541 client."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

from common.bench_db import BenchDB, benchmark_scope
from common.config_cli import add_config_subparser, run_from_namespace
from common.report import add_serve_arguments, named_report, short_circuit, store_and_serve
from subscription.sample import SUITE, cmd_new, cmd_sample
from bench.counter_report import build_page

REPORT_NAME = "subscription.html"


@named_report
def cmd_show(args):
    short = short_circuit(
        args.db, SUITE, "python -m bench.subscription new", "python -m bench.subscription sample 1"
    )
    if short is not None:
        code, message = short
        print(message, file=sys.stderr)
        return code
    store_and_serve(args.db, SUITE, REPORT_NAME, build_page(args.db), args, f"from {args.db}")
    return 0


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="Seed or migrate adaptive-search settings")
    new.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    sample = commands.add_parser("sample", help="Search capacity once for each SDK, interval, and observer")
    sample.add_argument("num_samples", type=int, nargs="?", default=1, choices=(1,))
    sample.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    sample.add_argument("--name", required=True)
    sample.add_argument("--amend", action="store_true", help="Resume saved work and add currently selected configurations")
    sample.add_argument("--skip-failed", action="store_true")
    show = commands.add_parser("show", help="Build or serve the saved report")
    show.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    add_serve_arguments(show)
    add_config_subparser(commands, SUITE)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    try:
        if args.command == "new":
            return cmd_new(args.db)
        if args.command == "config":
            if args.db.is_file():
                cmd_new(args.db)
            return run_from_namespace(SUITE, args)
        if args.command == "show":
            return cmd_show(args)
        if args.skip_failed and not args.amend:
            raise ValueError("--skip-failed requires --amend")
        if not args.db.is_file():
            raise ValueError("Run new before sample")
        cmd_new(args.db)
        name = args.name.strip()
        if not name:
            raise ValueError("Run name cannot be empty")
        with closing(BenchDB(args.db, SUITE)) as store:
            exists = name in store.benchmark_names()
            if exists and not args.amend:
                raise ValueError("Run name already exists; choose another name or --amend")
            if args.amend and not exists:
                raise ValueError("Cannot amend an unknown run")
            if not exists:
                store.create_benchmark(name)
        with benchmark_scope(name):
            return cmd_sample(args.db, args.num_samples, args.amend, args.skip_failed)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; completed observations are saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
