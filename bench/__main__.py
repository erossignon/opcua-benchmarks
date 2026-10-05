#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Seed missing configuration and open the benchmark scheduler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MODULES = (
    "build",
    "show",
    "config",
    "throughput",
    "server_limits",
    "server_capacity",
    "subscription",
)

_DEFAULT_DB = Path("bench.db")

# Suite composition order for ``_seed_db`` — the order in which
# ``python -m bench <db>`` scaffolds each suite's config rows when the
# database has missing settings. Kept as a module-level constant so tests
# can pin it against ``bench.config.SUITE_ORDER`` (a reordering bug is
# the kind of regression a string-based check would miss).
_SEED_ORDER: tuple[str, ...] = (
    "throughput",
    "server_limits",
    "server_capacity",
    "subscription",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the scheduler CLI parser with an explicit help-only mode."""
    return argparse.ArgumentParser(
        prog="python -m bench",
        description=(
            "Top-level bench entry point. With no arguments, or with a single "
            "optional <db> positional, missing settings are seeded into the shared "
            "bench.db and the "
            "schedule/options TUI is opened on it. With --help, every per-suite "
            "and helper module is listed instead.\n\n"
            "Per-suite CLIs:\n\n" + "\n".join(f"  python -m bench.{module}" for module in MODULES)
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=_DEFAULT_DB,
        help="path to the bench.db file to open in the TUI (seeded if missing); defaults to 'bench.db'",
    )
    parser.add_argument(
        "--help", "-h",
        action="store_true",
        dest="show_help",
        help="list every per-suite and helper module instead of opening the TUI",
    )


def _seed_db(db: Path) -> int:
    """Fill missing declared settings without overwriting configured suites."""
    from contextlib import closing
    import json
    from common.bench_db import BenchDB
    from common.suites import SUITE_OPTIONS

    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(BenchDB(db, suite="")) as store:
        if store._connection.execute(
            "SELECT 1 FROM config WHERE suite='server_limits_simple' "
            "UNION ALL SELECT 1 FROM results WHERE suite='server_limits_simple' LIMIT 1"
        ).fetchone():
            print(f"Rename legacy runs first: python -m bench.server_capacity migrate {db}", file=sys.stderr)
            return 1
        for suite in _SEED_ORDER:
            uniform, varying = store.get_config(suite)
            for name, option in SUITE_OPTIONS[suite].items():
                if name not in uniform and name not in varying:
                    store.set_config(suite, name, json.dumps(option.default))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Seed the database and open the scheduler, or print help without scheduling."""
    parser = build_parser()
    _add_arguments(parser)
    args = parser.parse_args(argv)

    if getattr(args, "show_help", False):
        return _print_help_and_run_list(parser)

    db: Path = args.db
    seed_code = _seed_db(db)
    if seed_code != 0:
        return seed_code

    from bench.config import main as config_main

    return config_main([str(db)])


def _print_help_and_run_list(parser: argparse.ArgumentParser) -> int:
    """Print available commands and return 1 to distinguish help from a scheduled run."""
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
