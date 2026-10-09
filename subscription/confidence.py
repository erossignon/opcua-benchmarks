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

"""Approximate uncertainty of mean progress over time, not of the capacity boundary."""

import math

import numpy as np


def estimate(interval_counts, expected_per_interval):
    """Circular block bootstrap preserves local time dependence and includes zero-progress intervals."""
    n = len(interval_counts)
    if n < 5:
        return dict(level=0.95, lower=None, upper=None, intervals=n, status="insufficient data")
    values = np.asarray(interval_counts, dtype=float) / expected_per_interval
    block = math.ceil(math.sqrt(n))
    result = dict(
        level=0.95,
        method="circular block bootstrap",
        scope="mean progress across items over time",
        intervals=n,
        block_intervals=block,
        resamples=2000,
        status="limited data" if n / block < 5 else "approximate",
    )
    if np.ptp(values) == 0:
        # A flat observed trace cannot reveal the probability of an unseen pause.
        return dict(result, lower=None, upper=None, status="no observed variation")
    rng = np.random.default_rng(0)
    starts = rng.integers(n, size=(2000, math.ceil(n / block)))
    indices = (starts[:, :, None] + np.arange(block)) % n
    indices = indices.reshape(2000, -1)[:, :n]
    means = values[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return dict(result, lower=float(lower), upper=float(upper))
