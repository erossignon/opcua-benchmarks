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

"""Adaptive counter-search policy, replaceable by an embedding checkout."""

from subscription.search import capacity, MIN_PROGRESS

OPTIONS = {}
DEFAULTS = {}


def validate(settings):
    if settings["items_start"] > settings["items_max"]:
        raise ValueError("items_start must not exceed items_max")


def search(settings, probe, known):
    """Probe returns progress fractions; the search applies its pass threshold."""
    return capacity(
        settings["items_start"],
        settings["items_max"],
        lambda items: probe(items) >= MIN_PROGRESS,
        known={items: fraction >= MIN_PROGRESS for items, fraction in known.items()},
    )


def describe(settings):
    return "Adaptive search doubles or halves, then refines to a 10% bracket or one item."
