"""Argument parsing and startup helpers shared by every benchmark server.

Both :mod:`common.servers.asyncua_server` and :mod:`common.servers.o6_server`
took the same CLI flags, the same ``--array-sizes`` parser, and the same
security-args sanity check; what differed was the framework glue. This module
holds the parts that are framework-agnostic, so each server script is left
with only the calls into its library and the line that announces readiness.

The :func:`parse_server_args` parser is the single source of truth for the
server CLI: ``--port``, ``--security``, the certificate triple, and
``--array-sizes``. It rejects the obvious bad inputs (``port`` out of range,
empty/negative array sizes, encrypted mode without all three cert paths) at
parse time so neither framework's main body has to repeat those checks.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def parse_server_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the shared CLI flags every benchmark server accepts.

    Returns a namespace with ``port``, ``security``, ``certificate``,
    ``private_key``, ``trust_certificate``, and ``array_sizes`` (a list of
    positive integers, order preserved, duplicates removed). Bad inputs
    raise via :meth:`argparse.ArgumentParser.error`, which exits the script
    with status 2 and a one-line message — the same shape every other
    runner in this repo uses for "the CLI rejected your input".
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4840)
    parser.add_argument("--security", choices=("None", "Basic256Sha256"), default="None")
    parser.add_argument("--certificate")
    parser.add_argument("--private-key")
    parser.add_argument("--trust-certificate")
    parser.add_argument("--array-sizes", default="")
    args = parser.parse_args(argv)
    if not 0 < args.port < 65536:
        parser.error("port must be between 1 and 65535")
    try:
        args.array_sizes = _parse_size_list(args.array_sizes)
    except ValueError as error:
        parser.error(str(error))
    if args.security != "None" and not all(
        (args.certificate, args.private_key, args.trust_certificate)
    ):
        parser.error("encrypted runs require certificate, private key, and trust certificate")
    return args


def _parse_size_list(text: str) -> list[int]:
    """Split a comma-separated list of positive integers, deduplicated.

    Order is preserved because the position in the list fixes each array
    variable's NodeId on both ends; duplicates are dropped because a single
    server cannot host two variables at the same NodeId even if asked.
    """
    sizes: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            raise ValueError(f"{token!r} is not an integer array size") from None
        if value <= 0:
            raise ValueError(f"array sizes must be positive, got {value}")
        if value not in sizes:
            sizes.append(value)
    return sizes