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

"""Keeping a benchmark's own processes from taking the machine with them."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable

try:
    import resource  # POSIX only: the limits here are skipped where it is absent
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

# How much likelier than everything else on the machine a benchmark process is
# to be picked by the kernel's OOM killer, on the Linux scale of -1000 to 1000.
# Both values are high enough to be chosen before anything the runner did not
# start; the server sits below the clients so that a sample which cannot fit
# loses a client (one failed configuration) rather than the server every other
# configuration in its block is still using.
CLIENT_OOM_SCORE_ADJ = 900
SERVER_OOM_SCORE_ADJ = 500
# Address space a worker maps before it has done any work, which a limit has to
# carry on top of the share it means to grant. An o6\Python worker reserves
# about 715 MB of it — numpy's OpenBLAS buffers and glibc's per-thread arenas —
# while holding 55 MB resident; an asyncua worker about 199 MB, a C one almost
# nothing. Mapped is not used, so it is not what a memory budget is there to
# ration; keeping it out of the shares is what leaves a budget a statement
# about working memory.
#
# To re-validate this number: start one worker under
# ``valgrind --tool=massif --pages-as-heap=no --time-unit=B`` (or read
# ``/proc/<pid>/status``'s VmPeak / VmHWM at the barrier), let it sit idle
# until the curve flattens, and take the largest of the numbers. The reserve
# should be at least that, with headroom for whatever the next version of
# numpy imports. Last re-measured: 2026-08 against numpy 2.x on this repo.
WORKER_ADDRESS_SPACE_RESERVE_MB = 768
# How a worker that ran out of memory says so, which is once per language it
# could have been written in: a Python MemoryError, a C++ std::bad_alloc, a
# failed malloc, and Bad_OutOfMemory (0x80030000) for the implementations that
# turn the allocation failure into an OPC UA status code before it ever reaches
# a traceback. A worker the kernel's OOM killer chose says nothing at all and
# is recognised by its SIGKILL instead.
OUT_OF_MEMORY_MARKERS = ("MemoryError", "bad_alloc", "OutOfMemory", "2147680256", "0x80030000", "Cannot allocate memory")


def available_memory_bytes() -> int | None:
    """What the machine can hand out right now, or None where it cannot be read.

    ``MemAvailable`` rather than ``MemTotal``: what a benchmark may take is
    what is free with the editor, the browser, and everything else already
    holding what they hold, and that is the figure a limit derived from it has
    to respect.
    """
    try:
        with open("/proc/meminfo", encoding="ascii") as info:
            for line in info:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def child_limits(address_space_bytes: int, oom_score_adj: int) -> Callable[[], None] | None:
    """A ``preexec_fn`` that bounds one worker and volunteers it to the OOM killer.

    Both settings are applied in the forked child, so they are in force before
    the worker has run a line and cannot be raced by a fast allocation.
    ``address_space_bytes`` of zero applies no ``RLIMIT_AS``, which is what a
    process that maps far more than it touches wants (see the module docstring).

    Returns ``None`` where neither setting applies (Windows), which is what
    ``subprocess.Popen`` wants for "no preexec_fn". The write to
    ``/proc/self/oom_score_adj`` is best-effort: a POSIX system without a
    Linux-shaped ``/proc`` still gets the limit.
    """
    if os.name != "posix":
        return None

    def limit() -> None:  # runs in the forked child, between fork and exec
        if address_space_bytes and resource is not None and hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))
        try:
            descriptor = os.open("/proc/self/oom_score_adj", os.O_WRONLY)
            try:
                os.write(descriptor, str(oom_score_adj).encode("ascii"))
            finally:
                os.close(descriptor)
        except OSError:
            pass

    return limit


def out_of_memory(return_code: int | None, output: str) -> bool:
    """Whether a worker's exit reads like it ran out of memory."""
    return return_code == -signal.SIGKILL or any(marker in output for marker in OUT_OF_MEMORY_MARKERS)
