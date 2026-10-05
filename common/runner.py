"""Shared runner helpers for benchmark configuration and process setup."""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def one_of(*allowed: object) -> Callable[[object], bool | str]:
    """A validator that takes only the listed values, and says which they are."""
    return lambda value: value in allowed or ("must be one of " + ", ".join(repr(option) for option in allowed))


def whole_number(minimum: int) -> Callable[[object], bool | str]:
    """A validator for a plain integer of at least ``minimum``.

    ``bool`` is excluded explicitly: it is a subclass of ``int``, so ``true``
    would otherwise pass for 1 and land in a result row as a count.
    """
    return (
        lambda value: (isinstance(value, int) and not isinstance(value, bool) and value >= minimum)
        or f"must be an integer of at least {minimum}"
    )


def positive_fraction(value: object) -> bool | str:
    """A validator for a positive real number (used for thresholds, ratios, fractions)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value) or "must be a positive number"


def executable(directory: Path, name: str) -> Path:
    """Path to a compiled C binary in ``directory``, with the platform suffix if any."""
    return directory / (name + (".exe" if os.name == "nt" else ""))


def parse_first_json_line(output: str, label: str) -> dict[str, Any]:
    """The first JSON object on any line of ``output``, raised as a clear error if absent."""
    for line in output.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"No result from {label}:\n{output}")


def compact(value: float) -> str:
    """An event count in at most six characters, so result columns never shift.

    The throughput suite's version: ``123``, ``1.5k``, ``12.3M``, ``1.23G``.
    The other suites re-wrap this for their own column widths.
    """
    if value < 1_000:
        return f"{value:.0f}"
    if value < 1_000_000:
        return f"{value / 1e3:.1f}k" if value < 10_000 else f"{value / 1e3:.0f}k"
    if value < 1_000_000_000:
        if value < 10_000_000:
            return f"{value / 1e6:.2f}M"
        return f"{value / 1e6:.1f}M" if value < 100_000_000 else f"{value / 1e6:.0f}M"
    return f"{value / 1e9:.2f}G"


def confirm(question: str, default: bool = True) -> bool:
    """Ask ``question`` on stderr and read a yes/no answer from stdin.

    The prompt goes to stderr rather than stdout, which the runner leaves
    free for a redirect. With no terminal to ask — a pipe, a cron job, a CI
    step — there is no answer to wait for, so the caller is told ``False``
    rather than the process blocking forever on a question nobody will see.
    A blank answer returns ``default``; unknown answers are re-asked.
    """
    if not sys.stdin.isatty():
        return False
    suffix = "(Y/n)" if default else "(y/N)"
    while True:
        print(f"{question} {suffix} ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if not answer:  # EOF: no answer given, so not a yes
            print(file=sys.stderr)
            return False
        answer = answer.strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.", file=sys.stderr)


def pair_to_tuple(pair: str, implementations: tuple[str, ...]) -> tuple[str, str]:
    """Split a ``client:server`` matrix value into its two implementation names."""
    if ":" not in pair:
        raise ValueError(
            f"pair {pair!r}: expected 'client:server' (one of {', '.join(implementations)})"
        )
    client, _, server = pair.partition(":")
    client, server = client.strip(), server.strip()
    if client not in implementations or server not in implementations:
        raise ValueError(f"pair {pair!r}: not a pairing of {' and '.join(implementations)}")
    return client, server


def partition_applicable(
    config_keys: list[dict],
    applicable: Callable[[dict], bool],
) -> tuple[list[dict], list[dict]]:
    """Split ``config_keys`` into ``(kept, skipped)`` by ``applicable(config_key)``.

    The matrix walk is a full cartesian product with no skip mechanism of
    its own, so this helper is the seam between the matrix and each
    suite's per-axis inapplicability rule.
    :meth:`common.bench_db.BenchDB.note_skipped` is the matching write side,
    so a filtered cell lands on the store's ``skipped`` table (not on
    ``results`` or ``failures``) and a clean sweep is distinguishable from a
    run whose matrix was mostly inapplicable.

    ``applicable`` takes one config_key dict and returns ``True`` when the
    cell should be measured, ``False`` when it should be skipped. A suite
    whose every cell applies still defines the predicate (returning
    ``True`` for everything) — opting out at the call site instead would
    hide whether the seam is exercised for that suite, which is the
    regression this helper exists to surface.
    """
    kept: list[dict] = []
    skipped: list[dict] = []
    for config_key in config_keys:
        if applicable(config_key):
            kept.append(config_key)
        else:
            skipped.append(config_key)
    return kept, skipped


def bootstrap_ci(
    samples: Sequence[float],
    confidence: float = 0.95,
    resamples: int = 9999,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    """A percentile bootstrap confidence interval for the mean of ``samples``.

    Returns ``(low, high)`` such that ``confidence`` of equally-likely
    resamples of ``samples`` land inside; either bound is ``None`` when
    ``samples`` has fewer than two observations — there is no interval
    to compute without at least one degree of freedom. The bootstrap is
    the right pick for per-cell latency numbers: the per-run sample
    distribution is not normal, the median is not a tight estimator of
    typical latency, and the closed-form ``mean ± z*s/√n`` assumes the
    symmetric tails the observed distributions do not have.

    The seed argument pins the random number generator so two callers
    that hand in the same ``samples`` get the same interval — important
    for the CLI seam test, which asserts the interval is reproducible
    rather than the exact numbers a fresh PRNG would land on.
    """
    n = len(samples)
    if n < 2:
        return (None, None)
    if confidence <= 0 or confidence >= 1:
        raise ValueError(f"confidence must be between 0 and 1 (exclusive), got {confidence}")
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        resample = [samples[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(resample))
    means.sort()
    tail = (1.0 - confidence) / 2.0
    low_index = max(0, min(resamples - 1, int(tail * resamples)))
    high_index = max(0, min(resamples - 1, int((1.0 - tail) * resamples) - 1))
    return means[low_index], means[high_index]