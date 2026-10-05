#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Shared commands for inspecting and editing suite options."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from common.bench_db import BenchDB
from common.suites import SUITE_OPTIONS, ConfigOption

_DEFAULT_DB = Path("bench.db")

_HELP_TABLE_INDENT = "    "


def _did_you_mean(typo: str, candidates: Sequence[str]) -> str:
    r"""A ``difflib``-based hint that names the closest declared option.

    Returns the closest match when its similarity ratio clears
    ``difflib.SequenceMatcher``'s default cutoff (``0.6``), or an
    empty string when nothing is close enough. The caller wraps the
    return value in a one-line message; the empty case means the
    message is suppressed.
    """
    matches = difflib.get_close_matches(typo, candidates)
    return matches[0] if matches else ""


def _render_value(value: Any) -> str:
    """A value rendered the same way a config CLI prints it.

    Lists render as a Python-ish list (single-quoted strings, comma
    between) so the result is what a user typing the value back in
    would write. Numbers and strings use their own ``repr``; ``None``
    is the literal ``None``.
    """
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    if isinstance(value, str):
        return repr(value)
    return repr(value)


def _wrap_text(text: str, width: int = 78) -> str:
    """Wrap ``text`` on word boundaries at ``width`` columns."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return "\n".join(lines)


def _help_table(options: dict[str, ConfigOption]) -> str:
    """The per-setting table that prints before argparse's standard usage.

    Four columns: ``name``, ``kind``, ``default``, ``help``, with rows in
    declaration order — uniform first (the runner-writes-these
    settings), then varying (the matrix axes). The ``default`` cell
    is truncated to a short summary when the value is a long list,
    so the table fits a standard 120-column terminal. The full default
    is what ``config <db> <name>`` prints, not what ``--help`` does.
    """
    rows: list[tuple[str, str, str, str]] = []
    for name, option in options.items():
        default_text = _short_default(option.default)
        rows.append((name, option.kind, default_text, option.help))
    headers = ("name", "kind", "default", "help")
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(3)]
    body = [_format_help_row(row, widths) for row in rows]
    header_line = "  ".join(headers[i].ljust(widths[i]) for i in range(3)) + "  help"
    separator = "  ".join("-" * widths[i] for i in range(3)) + "  " + "-" * 4
    rendered = [header_line, separator, *body]
    return "\n".join(rendered)


def _short_default(value: Any) -> str:
    """A one-line summary of an option's default, for the ``--help`` table.

    Long lists are collapsed to ``[a, b, …, n]`` so the default
    column never grows past a single line. The full default is what
    ``config <db> <name>`` prints; the ``--help`` table only needs to
    advertise *what kind* of default a field has.
    """
    if value is None:
        return "(runner)"
    if not isinstance(value, list):
        return _render_value(value)
    if len(value) <= 4:
        return _render_value(value)
    return _render_value(value[:3])[:-1] + ", …]"


def _format_help_row(row: tuple[str, str, str, str], widths: list[int]) -> str:
    """One row of the ``--help`` table, with the ``help`` cell wrapped to a fixed width.

    The first three columns are padded to ``widths``; the fourth wraps
    on word boundaries to ``width = 120 - sum(widths) - 8`` so the
    right edge does not run off a standard terminal.
    """
    name, kind, default_text, help_text = row
    help_width = max(20, 120 - sum(widths) - 8)
    wrapped = _wrap_text(help_text, width=help_width)
    first, *rest = wrapped.splitlines() or [""]
    line = f"{name.ljust(widths[0])}  {kind.ljust(widths[1])}  {default_text.ljust(widths[2])}  {first}"
    for extra in rest:
        line += "\n" + _HELP_TABLE_INDENT + extra
    return line


_CONFIG_DESCRIPTION = (
    "Read or write this suite's declared options in the bench.db file. "
    "Without a name, print every option and its current value. With a "
    "name alone, print just that one value. With a name and one or more "
    "values, write them. Use --unset to write the declared default back."
)


def _add_config_arguments(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Add the ``config`` subcommand's positionals and flags to ``parser``.

    Shared by :func:`_build_parser` (the standalone ``main`` entry
    point) and :func:`add_config_subparser` (wired into a suite's own
    top-level CLI) so the two never drift on an arg's ``nargs``,
    default, or help text.
    """
    parser.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=_DEFAULT_DB,
        help="path to the bench.db file to read or write; defaults to 'bench.db'",
    )
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="option to read or write; omit to show every option's current value",
    )
    parser.add_argument(
        "values",
        nargs="*",
        help="value(s) to write; for a 'uniform' option exactly one, for a 'varying' option one or more",
    )
    parser.add_argument(
        "--unset",
        dest="unset",
        metavar="NAME",
        default=None,
        help="write the declared default of NAME back into the table",
    )
    parser.add_argument(
        "--help",
        action="store_true",
        dest="show_help",
        help=help_text,
    )


def _build_parser(options: dict[str, ConfigOption]) -> argparse.ArgumentParser:
    """Build the ``config`` subparser, with the ``--help`` table prepended."""
    parser = _RemappedArgumentParser(
        prog="config",
        description=_CONFIG_DESCRIPTION,
        add_help=False,
    )
    _add_config_arguments(parser, "print the per-setting table and argparse's standard usage, then exit 0")
    return parser


def add_config_subparser(subparsers, suite_name: str) -> argparse.ArgumentParser:
    """Wire a ``config`` subparser into a suite CLI's top-level ``subparsers``."""
    parser = subparsers.add_parser(
        "config",
        help="read or write this suite's declared options in <db>",
        description=_CONFIG_DESCRIPTION,
        add_help=False,
    )
    _add_config_arguments(parser, "print the per-setting table and standard usage, then exit 0")
    parser.suite_name = suite_name
    return parser


_ARGPARSE_ERROR_EXIT = 3


class _RemappedArgumentParser(argparse.ArgumentParser):
    """argparse parser whose ``error()`` exits with the suite's argparse-failure code.

    Shared config parsing exits ``3`` on argparse-level failures;
    argparse's default of ``2`` would be a one-line complaint in a
    shell script (``echo $?`` after a typo returns ``2``, and the
    other three exit codes ``0``/``1``/``2`` are already used for
    success / bad value / unknown name — leaving ``3`` for argparse).
    """

    def error(self, message):  # noqa: D401 - argparse's contract is `error(message)`
        """Print the error to stderr and exit with :data:`_ARGPARSE_ERROR_EXIT`."""
        self.exit(_ARGPARSE_ERROR_EXIT, f"{self.prog}: error: {message}\n")


def run_from_namespace(suite_name: str, args: argparse.Namespace) -> int:
    """Run the ``config`` subcommand for ``suite_name`` against a parsed namespace.

    A per-suite ``bench.<suite>`` module uses :func:`add_config_subparser`
    to wire ``config`` into its own parent parser, then hands the
    parsed namespace here — avoiding the round-trip through ``argv``
    that would re-stringify every positional. The two entry points
    (``main`` for a direct ``python -m bench.<suite> config`` call and
    ``run_from_namespace`` for the suite's parent dispatch) share
    :func:`_dispatch`, so a future flag lands in one place.
    """
    options = SUITE_OPTIONS.get(suite_name)
    if options is None:
        print(f"unknown suite {suite_name!r}", file=sys.stderr)
        return 2
    return _dispatch(suite_name, options, args)


def main(suite_name: str, argv: Sequence[str] | None = None) -> int:
    """Run the ``config`` subcommand for ``suite_name`` against ``argv``.

    ``argv`` is the user's command-line tail — the part after
    ``python -m bench.<suite> config``. ``None`` reads :data:`sys.argv`,
    matching :func:`argparse.ArgumentParser.parse_args`'s default
    behaviour. The four exit codes are documented at the module level.
    """
    options = SUITE_OPTIONS.get(suite_name)
    if options is None:
        print(f"unknown suite {suite_name!r}", file=sys.stderr)
        return 2

    parser = _build_parser(options)
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)

    if "--help" in argv or "-h" in argv:
        sys.stdout.write(_help_table(options) + "\n\n")
        sys.stdout.write(parser.format_help())
        return 0

    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        code = exit_.code if isinstance(exit_.code, int) else 1
        if code == 2:
            return _ARGPARSE_ERROR_EXIT
        return code

    return _dispatch(suite_name, options, args)


def _dispatch(suite_name: str, options: dict[str, ConfigOption], args: argparse.Namespace) -> int:
    """Route a parsed ``config`` namespace to the right ``_cmd_*`` function.

    A per-suite ``bench.<suite>`` module can build its own parser with
    :func:`add_config_subparser` (sharing the same positionals and
    flags) and hand the parsed namespace here, instead of re-encoding
    the args back into an argv list. The two entry points — argv-based
    and namespace-based — share every command path so a future
    per-suite flag lands in one place.
    """
    if getattr(args, "show_help", False):
        return _print_help(suite_name, options)
    db_path = args.db
    if args.unset is not None and (args.name is not None or args.values):
        print("config: error: --unset takes a name and no values", file=sys.stderr)
        return _ARGPARSE_ERROR_EXIT
    if args.unset is not None:
        return _cmd_unset(suite_name, options, db_path, args.unset)
    if args.name is None:
        if args.values:
            print("config: error: values require an option name", file=sys.stderr)
            return _ARGPARSE_ERROR_EXIT
        return _cmd_show_all(suite_name, options, db_path)
    if not args.values:
        return _cmd_show_one(suite_name, options, db_path, args.name)
    return _cmd_set(suite_name, options, db_path, args.name, args.values)


def _print_help(suite_name: str, options: dict[str, ConfigOption]) -> int:
    """Print the per-setting table followed by the standard argparse usage.

    Extracted from :func:`main` so :func:`run_from_namespace` can
    share the same output without going through argv: a parent-parser
    dispatch that has already parsed ``--help`` hands the namespace
    here, and the same table+usage block prints as a fresh ``python -m
    bench.<suite> config --help`` call would have produced.
    """
    parser = _build_parser(options)
    sys.stdout.write(_help_table(options) + "\n\n")
    sys.stdout.write(parser.format_help())
    return 0


def _cmd_show_all(suite_name: str, options: dict[str, ConfigOption], db_path: Path) -> int:
    """``config <db>`` — print every declared option's resolved value.

    Reads must not create the file (a ``show`` on an empty directory
    should fail cleanly, not silently materialise an empty database).
    The output is grouped uniform-first, then varying, both in
    declaration order; each row carries the option's ``help`` prose
    underneath.
    """
    if not db_path.exists():
        print(
            f"{db_path} does not exist. Run 'python -m bench.{suite_name} new {db_path}' "
            "to create it (or 'python -m bench' to create every suite).",
            file=sys.stderr,
        )
        return 1
    store = BenchDB(db_path, suite=suite_name)
    uniform_order = [(name, options[name]) for name in options if options[name].kind == "uniform"]
    varying_order = [(name, options[name]) for name in options if options[name].kind == "varying"]
    written = 0
    for name, _ in uniform_order:
        written += _print_option(store, suite_name, name)
    for name, _ in varying_order:
        written += _print_option(store, suite_name, name)
    if written == 0:
        return 1
    return 0


def _print_option(store: BenchDB, suite_name: str, name: str) -> int:
    """Print one option's resolved value with its help; return 1 if found, 0 if missing.

    A missing option in the database — a ``new`` was never run, or the
    user unset a row — is reported on stdout (not stderr) with the
    declared help underneath, so the ``show all`` output names every
    declared option, present or not, in one stream a caller can pipe.
    The return value lets :func:`_cmd_show_all` tell "some rows found"
    from "database exists but was never seeded".
    """
    option = SUITE_OPTIONS[suite_name][name]
    try:
        value = store.get_config(suite_name, name)
    except KeyError:
        print(f"{name} = <unset>")
        if option.help:
            for line in _wrap_text(option.help, width=90).splitlines():
                print(f"  {line}")
        return 0
    print(f"{name} = {_render_value(value)}")
    if option.help:
        for line in _wrap_text(option.help, width=90).splitlines():
            print(f"  {line}")
    return 1


def _cmd_show_one(suite_name: str, options: dict[str, ConfigOption], db_path: Path, name: str) -> int:
    """``config <db> <name>`` — print one option's resolved value.

    Unknown name: exit ``2`` with a did-you-mean hint. Missing
    database: exit ``1`` with the ``new`` command. Missing row in an
    existing database: exit ``1`` (the option is declared but never
    seeded).
    """
    if name not in options:
        suggestion = _did_you_mean(name, list(options))
        message = f"unknown option {name!r} for suite {suite_name!r}"
        if suggestion:
            message += f"; did you mean {suggestion!r}?"
        print(message, file=sys.stderr)
        return 2
    if not db_path.exists():
        print(
            f"{db_path} does not exist. Run 'python -m bench.{suite_name} new {db_path}' "
            "to create it (or 'python -m bench' to create every suite).",
            file=sys.stderr,
        )
        return 1
    store = BenchDB(db_path, suite=suite_name)
    try:
        value = store.get_config(suite_name, name)
    except KeyError:
        print(f"{name} is not set in {db_path}; run 'python -m bench.{suite_name} new {db_path}' to seed it.", file=sys.stderr)
        return 1
    print(_render_value(value))
    return 0


def _cmd_set(
    suite_name: str,
    options: dict[str, ConfigOption],
    db_path: Path,
    name: str,
    values: list[str],
) -> int:
    """``config <db> <name> <v1> [<v2> ...]`` — write one option.

    Every value is coerced through the option's ``coerce`` *before*
    the first write, so a single bad value leaves the database
    untouched. ``coerce`` for ``uniform`` accepts a single
    JSON-encoded scalar; for ``varying`` it accepts a single
    JSON-encoded list. The CLI encodes ``values`` accordingly.

    For ``varying`` options, each argv token is parsed as JSON if it
    parses cleanly (``clients 1 5 20`` → ``[1, 5, 20]``); tokens that
    are not valid JSON fall back to the raw string
    (``security None Basic256Sha256`` → ``["None", "Basic256Sha256"]``).
    Both paths round-trip through ``BenchDB.set_config`` with the
    typed elements the per-element validator expects.

    Unknown name: exit ``2``. Coerce failure: exit ``1`` with a
    message naming the option and the bad value.
    """
    if name not in options:
        suggestion = _did_you_mean(name, list(options))
        message = f"unknown option {name!r} for suite {suite_name!r}"
        if suggestion:
            message += f"; did you mean {suggestion!r}?"
        print(message, file=sys.stderr)
        return 2

    option = options[name]
    if option.kind == "varying":
        parsed: list[Any] = []
        for token in values:
            try:
                parsed.append(json.loads(token))
            except json.JSONDecodeError:
                parsed.append(token)
        raw = json.dumps(parsed)
    else:
        if len(values) != 1:
            print(
                f"option {name!r} is uniform and takes exactly one value; got {len(values)}.",
                file=sys.stderr,
            )
            return 1
        raw = values[0]

    store = BenchDB(db_path, suite=suite_name)
    try:
        store.set_config(suite_name, name, raw)
    except (ValueError, KeyError) as error:
        first_value = values[0] if values else ""
        print(f"invalid value for {name!r} ({first_value!r}): {error}", file=sys.stderr)
        return 1
    return 0


def _cmd_unset(suite_name: str, options: dict[str, ConfigOption], db_path: Path, name: str) -> int:
    """``config <db> --unset <name>`` — write the declared default back.

    Unknown name: exit ``2``. A field whose declared default is
    ``None`` (the runner writes that one) is rejected: a write here
    would only ever be overwritten, so the CLI refuses and points the
    user at the runner.
    """
    if name not in options:
        suggestion = _did_you_mean(name, list(options))
        message = f"unknown option {name!r} for suite {suite_name!r}"
        if suggestion:
            message += f"; did you mean {suggestion!r}?"
        print(message, file=sys.stderr)
        return 2

    option = options[name]
    if option.default is None:
        print(
            f"option {name!r} has no declared default — the runner writes it at sample time; " "leaving it unset.",
            file=sys.stderr,
        )
        return 1

    store = BenchDB(db_path, suite=suite_name)
    store.set_config(suite_name, name, json.dumps(option.default))
    return 0
