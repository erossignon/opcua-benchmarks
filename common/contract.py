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

"""Wire contract shared by every suite. Mirrors common/contract.h; the two
halves are kept honest by common/tests/test_contract.py."""

from __future__ import annotations

DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:4840"
FIRST_NODE_ID = 1001
NODE_COUNT = 100
ARRAY_FIRST_NODE_ID = 2001
MAX_ARRAY_SIZES = 16

# The unified server readiness marker. Each runner matches this as a
# substring; each server (in common/servers/) prints it.
SERVER_READY = "benchmark server ready"

_LCG_MULTIPLIER = 1664525
_LCG_INCREMENT = 1013904223
_LCG_MASK = 0xFFFFFFFF


class NodeCursor:
    """The LCG NodeId walker. Yields indices in [0, NODE_COUNT)."""

    __slots__ = ("_state",)

    def __init__(self, seed: int = 0) -> None:
        self._state = seed & _LCG_MASK

    def next(self) -> int:
        self._state = (self._state * _LCG_MULTIPLIER + _LCG_INCREMENT) & _LCG_MASK
        return self._state % NODE_COUNT

    __call__ = next


def configure_client(client, max_outstanding: int) -> None:
    """Raise the open62541 client cap that bites pipelines deeper than 32.

    The o6\\Python client inherits the open62541 default of 32 outstanding
    asynchronous service calls; the 33rd is refused with BadTooManyOperations.
    Fixed once here instead of in four places on 2026-08-13.
    """
    client.config.maxAsyncServiceCalls = max_outstanding


def now_ns() -> int:
    """Monotonic clock in nanoseconds."""
    import time
    return time.monotonic_ns()
