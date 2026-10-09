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

"""Server capacity through load exploration and top-decile repeats across all five SDKs."""

import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys

from common.config_cli import add_config_subparser, run_from_namespace
from common.report import add_serve_arguments, named_report, short_circuit, store_and_serve
from common.sample import named_sample
from server_capacity import run, show

REPORT_NAME = "server_capacity.html"
cmd_new = run.cmd_new
cmd_sample = run.cmd_sample


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(prog="python -m bench.server_capacity", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Build the native server and measurement client")
    migration = commands.add_parser("migrate", help="Rename historical server_limits_simple runs, preserving raw evidence")
    migration.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    new = commands.add_parser("new", help="Seed missing settings")
    new.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    sample = commands.add_parser("sample", help="Explore server loads and repeat the fastest configurations")
    sample.add_argument("num_samples", type=int, help="Repeats per confirmation load; discovery points are measured once")
    sample.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    sample.add_argument("--name", required=True)
    sample.add_argument("--amend", action="store_true")
    sample.add_argument("--skip-failed", action="store_true")
    report = commands.add_parser("show", help="Store and serve the report")
    report.add_argument("db", type=Path, nargs="?", default=Path("bench.db"))
    add_serve_arguments(report)
    add_config_subparser(commands, run.SUITE)
    args = parser.parse_args(argv)
    if args.command == "sample":
        if args.num_samples < run.MIN_SAMPLES:
            parser.error(f"num_samples must be at least {run.MIN_SAMPLES}")
        if args.skip_failed and not args.amend:
            parser.error("--skip-failed requires --amend")
    return args


@named_report
def cmd_show(args):
    short = short_circuit(
        args.db,
        run.SUITE,
        "python -m bench.server_capacity new",
        "python -m bench.server_capacity sample 3 --name limits",
    )
    if short is not None:
        code, message = short
        print(message, file=sys.stderr)
        return code
    store_and_serve(args.db, run.SUITE, REPORT_NAME, show.build_page(args.db, args.cdn), args, f"from {args.db}")
    return 0


def main(argv=None):
    args = parse_arguments(argv)
    try:
        if args.command == "build":
            subprocess.run(
                ["cmake", "-S", str(run.BINARY_DIR.parent), "-B", str(run.BINARY_DIR), "-DCMAKE_BUILD_TYPE=Release"], check=True
            )
            subprocess.run(["cmake", "--build", str(run.BINARY_DIR), "-j", "4"], check=True)
            return 0
        if args.command == "migrate":
            from server_capacity.migrate import migrate

            backup = migrate(args.db)
            cmd_new(args.db)
            print(f"Migrated; backup: {backup}" if backup else "No legacy suite found.")
            return 0
        if args.command == "new":
            return cmd_new(args.db)
        if args.command == "config":
            return run_from_namespace(run.SUITE, args)
        if args.command == "show":
            return cmd_show(args)
        return named_sample(args, run.SUITE, cmd_sample)
    except (OSError, RuntimeError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; completed samples are saved. Resume with --name and --amend.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
