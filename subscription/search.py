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

"""A bounded search with one observation per count and no confirmation passes."""

MIN_PROGRESS = 0.99


def capacity(start, maximum, probe, *, known=None):
    """Double/halve to bracket, then narrow to 10% or one item."""
    if not 1 <= start <= maximum:
        raise ValueError("Expected 1 <= items_start <= items_max")
    tested = {count: passed for count, passed in (known or {}).items() if 1 <= count <= maximum}
    passing = [count for count, passed in tested.items() if passed]
    if passing:
        start = max(passing)
    else:
        start = min([start] + list(tested))
    failed_above = [count for count, passed in tested.items() if not passed and count >= start]
    ceiling = min([maximum] + failed_above)

    def passes(items):
        if items not in tested:
            tested[items] = bool(probe(items))
        return tested[items]

    lower, upper = 0, None
    count = start
    if passes(count):
        lower = count
        while lower < ceiling:
            count = min(ceiling, lower * 2)
            if not passes(count):
                upper = count
                break
            lower = count
    else:
        upper = count
        while count > 1:
            count = max(1, count // 2)
            if passes(count):
                lower = count
                break
            upper = count
    while lower and upper is not None and upper - lower > max(1, lower // 10):
        count = (lower + upper) // 2
        if passes(count):
            lower = count
        else:
            upper = count
    return dict(
        passing_items=lower,
        failing_items=upper,
        at_least=lower == maximum,
        tested_items=list(tested),
        minimum_progress=MIN_PROGRESS,
    )
