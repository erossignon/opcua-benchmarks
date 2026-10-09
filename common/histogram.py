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

"""Logarithmic latency histogram shared by every suite.

Mirrors :file:`common/histogram.h`; the two halves are kept honest by
:mod:`common.tests.test_histogram_contract`. The histogram is logarithmic
with :data:`SUBBUCKETS` linear steps inside each octave: bucket
``octave * SUBBUCKETS + sub`` covers ``[2**octave * (1 + sub/SUBBUCKETS),
2**octave * (1 + (sub+1)/SUBBUCKETS))``.

One bucket per octave would be cheaper, but a percentile read off it is only
known to a factor of two — which is not precision, it is a different number,
and it makes a configured threshold unreachable: against a 0.512 ms baseline
the only p99 values that exist are 0.512, 1.024, 2.048, 4.096, 8.192, so a
"10x" rule cannot fire until 16x. :data:`SUBBUCKETS` of 16 puts every bucket
within about 7% of its neighbour, which a threshold and a reported figure can
both stand on.
"""

from __future__ import annotations

# Must match the O6_HISTOGRAM_* constants in common/histogram.h.
SUBBUCKETS = 16
OCTAVES = 30
BUCKETS = SUBBUCKETS * OCTAVES


def bucket_for(latency_us: int) -> int:
    """The bucket a latency (in microseconds) falls into.

    The octave is the largest power of two not exceeding the latency, then the
    sub-bucket is the linear step inside that octave, capped at the last
    bucket. A latency of zero lands in bucket zero — the only input where the
    math would otherwise underflow.
    """
    if latency_us <= 0:
        return 0
    octave = 0
    while octave + 1 < OCTAVES and latency_us >= (1 << (octave + 1)):
        octave += 1
    base = 1 << octave
    sub = (latency_us - base) * SUBBUCKETS // base
    if sub >= SUBBUCKETS:
        sub = SUBBUCKETS - 1
    return octave * SUBBUCKETS + sub


def bucket_upper_us(index: int) -> float:
    """The exclusive upper edge of histogram bucket ``index``, in microseconds.

    The inverse of :func:`bucket_for`: a latency counted here is known to be
    below this, and no less than the edge one bucket down.
    """
    octave = index // SUBBUCKETS
    sub = index % SUBBUCKETS
    base = float(1 << octave)
    return base + base * (sub + 1) / SUBBUCKETS


def merge(histograms: list[list[int]]) -> list[int]:
    """Sum per-bucket counts across client processes into one distribution."""
    merged = [0] * BUCKETS
    for histogram in histograms:
        for index, count in enumerate(histogram[:BUCKETS]):
            merged[index] += count
    return merged


def percentile_ms(histogram: list[int], fraction: float) -> float | None:
    """Approximate the ``fraction`` percentile latency, in milliseconds.

    Resolution is one histogram bucket, about 7%, which is what the
    sub-bucketing buys over a plain octave scale — enough for a reported
    figure and for a configured threshold to be judged against, rather than
    only enough to say which power of two a latency landed on. Returns
    ``None`` for an empty histogram: no calls completed, so there is no
    latency to report.
    """
    total = sum(histogram)
    if total <= 0:
        return None
    target = max(1, int(fraction * total + 0.9999))
    accumulated = 0
    for index, count in enumerate(histogram):
        accumulated += count
        if accumulated >= target:
            return bucket_upper_us(index) / 1000.0
    return bucket_upper_us(BUCKETS - 1) / 1000.0
