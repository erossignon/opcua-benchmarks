#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Shared command-line and measurement helpers for the Python benchmarks."""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from common.contract import (
    ARRAY_FIRST_NODE_ID,
    DEFAULT_ENDPOINT,
    FIRST_NODE_ID,
    NODE_COUNT,
    NodeCursor,
)

MAX_SAMPLES = 31
# Named array sizes, in elements. These are pixel counts: an Int32 array of
# 4k elements is a 33 MB message, which is the largest payload the suite moves.
NAMED_ARRAY_SIZES = {
    "vga": 640 * 480,
    "hd": 1280 * 720,
    "full_hd": 1920 * 1080,
    "4k": 3840 * 2160,
}
# The two halves of the wall-time budget, set together by --max-values.
# The ceiling caps how many values one client moves per sample; the floor stops
# it from cutting a sample down to a handful of calls, where scheduling noise
# would dominate. Whichever binds later wins: calls =
# min(iterations, max(MIN_SERVICE_CALLS, MAX_VALUES // values_per_call)).
DEFAULT_MAX_VALUES_PER_SAMPLE = 2_000_000
MIN_SERVICE_CALLS = 100
# Per-request response timeout. The default (5 s for both open62541 and
# asyncua) is too tight when many clients hammer a single server: a latency
# spike — e.g. the Python o6 server under a warm-up thundering herd — makes a
# request return BadTimeout and aborts the worker. The timeout only bounds how
# long a client waits, so raising it does not affect the throughput of
# successful requests.
REQUEST_TIMEOUT_MS = 60_000


@dataclass(frozen=True)
class Options:
    iterations: int
    warmup: int
    samples: int
    endpoint: str
    worker: str | None
    operation: str
    max_outstanding: int
    seed: int
    security: str
    certificate: str | None
    private_key: str | None
    trust_certificate: str | None
    batch_size: int
    array_size: int
    array_sizes: list[int]

    @property
    def values_per_call(self) -> int:
        """Values moved by one service call: nodes per call times elements per node."""
        return self.batch_size * self.array_size

    @property
    def array_node_id(self) -> str:
        """The NodeId of the array variable this worker was pointed at."""
        return array_node_id(self.array_sizes, self.array_size)


def parse_size_list(text: str) -> list[int]:
    """Parse a comma-separated list of element counts, resolving named sizes.

    Accepts plain integers and the names in :data:`NAMED_ARRAY_SIZES`
    (``vga``, ``hd``, ``full_hd``, ``4k``). Order is preserved and duplicates
    are dropped, because the position in this list is what fixes each array
    variable's NodeId on both ends.
    """
    sizes: list[int] = []
    for token in text.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in NAMED_ARRAY_SIZES:
            value = NAMED_ARRAY_SIZES[token]
        else:
            try:
                value = int(token)
            except ValueError:
                raise ValueError(f"{token!r} is neither an integer nor one of " f"{', '.join(NAMED_ARRAY_SIZES)}") from None
        if value <= 0:
            raise ValueError(f"array and batch sizes must be positive, got {value}")
        if value not in sizes:
            sizes.append(value)
    return sizes


def array_node_id(array_sizes: list[int], array_size: int) -> str:
    """NodeId of the array variable holding ``array_size`` elements."""
    if array_size <= 1:
        raise ValueError("scalar runs use the scalar node block, not an array node")
    try:
        index = array_sizes.index(array_size)
    except ValueError:
        raise ValueError(f"array size {array_size} is not in the server's array set {array_sizes}") from None
    return f"ns=1;i={ARRAY_FIRST_NODE_ID + index}"


def service_calls(
    iterations: int,
    values_per_call: int,
    max_values: int = DEFAULT_MAX_VALUES_PER_SAMPLE,
    min_calls: int = MIN_SERVICE_CALLS,
) -> int:
    """Service calls to perform per sample: ``iterations``, unless that is too much data.

    ``iterations`` is the requested call count and is used as-is whenever the
    payload is small enough. A larger payload is cut back so one client moves
    at most ``max_values`` per sample, but never below ``min_calls`` — a sample
    of three calls measures scheduling noise, not throughput. The runner
    reports the count it actually used, so a cut is visible in the results
    rather than something to infer.
    """
    if values_per_call <= 1 or max_values <= 0:
        return iterations
    affordable = max(min_calls, max_values // values_per_call)
    return min(iterations, affordable)


def array_payload(size: int) -> list[int]:
    """The deterministic Int32 array every implementation writes and serves."""
    return [index % 1000 for index in range(size)]


def parse_options(description: str) -> Options:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--worker", choices=("sync", "async"))
    parser.add_argument("--operation", choices=("read", "write"), default="read")
    parser.add_argument("--max-outstanding", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--security", choices=("None", "Basic256Sha256"), default="None")
    parser.add_argument("--certificate")
    parser.add_argument("--private-key")
    parser.add_argument("--trust-certificate")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Nodes addressed by one Read/Write service call.",
    )
    parser.add_argument(
        "--array-size",
        type=int,
        default=1,
        help="Elements per node. 1 reads the scalar block; more selects an array variable.",
    )
    parser.add_argument(
        "--array-sizes",
        default="",
        help=(
            "Every array size the server was started with, comma separated. "
            "Position in this list fixes each array variable's NodeId."
        ),
    )
    args = parser.parse_args()
    # Catch the silent-typo class of bug: a field added to :class:`Options`
    # without a matching argparse flag (or vice versa) would otherwise raise
    # at ``Options(**vars(args))`` with a message that names the missing
    # field — *after* the CLI has been validated, in a place a reader does
    # not connect to the cause. Asserting the two stay in lockstep at parse
    # time moves the failure to a message that points at the source.
    options_fields = {field.name for field in dataclasses.fields(Options)}
    argparse_keys = set(vars(args))
    missing_in_argparse = options_fields - argparse_keys
    if missing_in_argparse:
        parser.error(
            f"Options declares {sorted(missing_in_argparse)} but parse_options did not "
            "add an argparse flag for it; keep the dataclass and the parser in sync"
        )
    if args.iterations <= 0 or args.warmup <= 0:
        parser.error("iterations and warm-up must be positive")
    if args.samples <= 0 or args.samples > MAX_SAMPLES:
        parser.error(f"samples must be between 1 and {MAX_SAMPLES}")
    if args.max_outstanding <= 0 or args.seed <= 0:
        parser.error("max outstanding and seed must be positive")
    if args.batch_size <= 0 or args.array_size <= 0:
        parser.error("batch size and array size must be positive")
    if args.batch_size > 1 and args.array_size > 1:
        parser.error("batched array access is not part of the matrix; vary one at a time")
    if args.security != "None" and not all((args.certificate, args.private_key, args.trust_certificate)):
        parser.error("encrypted runs require certificate, private key, and trust certificate")
    try:
        args.array_sizes = parse_size_list(args.array_sizes)
    except ValueError as error:
        parser.error(str(error))
    if args.array_size > 1 and args.array_size not in args.array_sizes:
        parser.error(f"--array-size {args.array_size} is missing from --array-sizes")
    return Options(**vars(args))


def distinct_node_ids() -> list[str]:
    """The :data:`NODE_COUNT` scalar NodeIds, in index order.

    :class:`NodeCursor` from :mod:`common.contract` yields indices into this
    table, so a client resolves each NodeId once instead of once per
    operation.
    """
    return [f"ns=1;i={FIRST_NODE_ID + index}" for index in range(NODE_COUNT)]


def write_values(node_id_strings: list[str]) -> list[int]:
    return [int(text.rsplit("=", 1)[1]) for text in node_id_strings]


def emit_worker_result(
    benchmark: str,
    operation: Callable[[int], int],
    options: Options,
    *,
    implementation: str = "o6-python/open62541",
) -> None:
    checksum = operation(options.warmup)
    print("O6_BENCHMARK_READY", flush=True)
    if sys.stdin.readline() == "":
        raise RuntimeError("benchmark barrier closed before release")
    start = time.monotonic_ns()
    checksum ^= operation(options.iterations)
    end = time.monotonic_ns()
    print(
        json.dumps(
            {
                "benchmark": benchmark,
                "implementation": implementation,
                "operations": options.iterations,
                "values_per_operation": options.values_per_call,
                "start_ns": start,
                "end_ns": end,
                "checksum": str(checksum),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


async def emit_async_worker_result(
    benchmark: str,
    operation: Callable[[int], Any],
    options: Options,
    *,
    implementation: str = "o6-python/open62541",
) -> None:
    checksum = await operation(options.warmup)
    print("O6_BENCHMARK_READY", flush=True)
    if sys.stdin.readline() == "":
        raise RuntimeError("benchmark barrier closed before release")
    start = time.monotonic_ns()
    checksum ^= await operation(options.iterations)
    end = time.monotonic_ns()
    print(
        json.dumps(
            {
                "benchmark": benchmark,
                "implementation": implementation,
                "operations": options.iterations,
                "values_per_operation": options.values_per_call,
                "start_ns": start,
                "end_ns": end,
                "checksum": str(checksum),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def emit_measurement(
    benchmark: str,
    operation: Callable[[int], int],
    options: Options,
    *,
    implementation: str = "o6-python/open62541",
) -> None:
    checksum = operation(options.warmup)
    samples = []
    for _ in range(options.samples):
        start = time.monotonic_ns()
        checksum ^= operation(options.iterations)
        samples.append((time.monotonic_ns() - start) / options.iterations)
    samples.sort()
    median = statistics.median(samples)
    print(
        json.dumps(
            {
                "benchmark": benchmark,
                "implementation": implementation,
                "iterations": options.iterations,
                "values_per_operation": options.values_per_call,
                "samples": options.samples,
                "median_ns_per_op": round(median, 3),
                "min_ns_per_op": round(samples[0], 3),
                "max_ns_per_op": round(samples[-1], 3),
                "median_ops_per_second": round(1_000_000_000.0 / median, 3),
                "median_values_per_second": round(options.values_per_call * 1_000_000_000.0 / median, 3),
                "checksum": str(checksum),
            },
            separators=(",", ":"),
        )
    )
