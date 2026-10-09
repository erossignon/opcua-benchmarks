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

"""Named CLI sampling using the same snapshots and scope as the TUI runner."""

from contextlib import closing
import sqlite3
import sys

from common.bench_db import BenchDB, benchmark_scope


def named_sample(args, suite, measure):
    """Reserve or resume a named run, preserving the runner's exit code.

    Store writes invalidate reports after amendment validation.
    """
    if args.name is None:
        return measure(args.db, args.num_samples, args.amend, args.skip_failed)
    name = args.name.strip()
    if not args.db.exists():
        print(f"Run python -m bench.{suite} new {args.db} first.", file=sys.stderr)
        return 1
    try:
        with closing(BenchDB(args.db, suite=suite)) as store:
            if not name:
                raise ValueError("Benchmark name must not be empty")
            if args.amend:
                if name not in store.benchmark_names():
                    raise ValueError(f"Unknown benchmark name {name!r}")
            else:
                store.create_benchmark(name)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    with benchmark_scope(name):
        return measure(args.db, args.num_samples, args.amend, args.skip_failed)
