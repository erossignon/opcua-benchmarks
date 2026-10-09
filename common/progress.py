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

"""Terminal progress display for a long benchmark run."""

from __future__ import annotations

import shutil
import sys
import time
from typing import TextIO


def plural(word: str, count: int) -> str:
    """``word`` as a count of ``count`` reads it — naive, and enough here.

    The words this is asked for are the caller's ``unit`` and ``group``, which
    are short nouns it chose itself; anything this does not get right can be
    passed already pluralised. Consonant + ``y`` is handled because it is the
    one ordinary English plural a bare ``+ "s"`` gets visibly wrong, and the
    nouns a benchmark runner reaches for ("entry", "retry") land on it.
    """
    if count == 1:
        return word
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        return f"{word[:-1]}ies"
    return f"{word}s"


class Progress:
    """A progress bar with a two-line live area and finished work listed above.

    ``total`` is counted in whatever the caller calls a unit — measurement
    steps rather than items, where the two differ. A run that takes a varying
    number of steps per item (a resumed one, say, which tops some items up and
    skips others) would otherwise mis-estimate the time remaining, because the
    items left are not the work left.

    Progress is reported through four calls: :meth:`begin` announces an item,
    :meth:`state` says where inside it the run is, :meth:`advance` retires it
    onto a line of its own, and :meth:`finish` closes the display.
    """

    FILLED = "#"
    EMPTY = "-"
    # Move the cursor up one line, and erase the line it is on. Two lines
    # cannot be rewritten in place with a carriage return alone, which is all a
    # one-line bar needs. Only ever written to a terminal — ``interactive`` is
    # false for a pipe or a file, where the live area is not drawn at all.
    UP = "\033[A"
    ERASE = "\r\033[2K"
    # How wide the display may get, however wide the terminal is. Set high
    # enough that long status phases (an "outstanding=N" reading, say) are not
    # truncated on a wide terminal — readability beats saving columns.
    MAX_COLUMNS = 200

    def __init__(
        self,
        total: int,
        *,
        unit: str = "step",
        group: str = "item",
        right_width: int = 24,
        stream: TextIO | None = None,
    ) -> None:
        """Set up a display for ``total`` units of work.

        ``unit`` and ``group`` are singular nouns for what is counted and what
        it is counted across; they appear only in the closing summary.

        ``right_width`` is the width reserved for the right-hand column, which
        both the retired lines and the phase on the status line are written
        into — which is what puts them in the same column. Size it for the
        wider of the two: a right-hand field longer than this is truncated to
        the width of the terminal, a shorter one simply stops short of the end
        of the line.
        """
        self.total = max(1, total)
        self.unit = unit
        self.group = group
        self.right_width = right_width
        self.stream = stream if stream is not None else sys.stderr
        self.completed = 0
        self.lines = 0
        self.label = ""
        self.phase = ""
        self.started = time.monotonic()
        self.interactive = self.stream.isatty()
        # Whether the two-line area is on screen and therefore needs erasing
        # before anything else is written.
        self.drawn = False
        self.estimated_remaining = None
        self.estimated_at = self.started

    def estimate(self, remaining: int, seconds: float) -> None:
        """Update a display-only forecast, excluding saved observations on resume."""
        self.total = self.completed + max(0, remaining)
        self.estimated_remaining = max(0, seconds)
        self.estimated_at = time.monotonic()
        self._draw()

    def write(self, message: str) -> None:
        """Print an untruncated diagnostic without corrupting the live area."""
        self._erase()
        print(message, file=self.stream, flush=True)
        self._draw()

    def begin(self, label: str) -> None:
        """Announce the item about to run."""
        if self.estimated_remaining is not None and self.total <= self.completed:
            self.estimate(1, max(1, self.estimated_remaining))
        self.label = label
        self.phase = "starting"
        self._draw()

    def state(self, phase: str) -> None:
        """Say where inside the current item the run is.

        This is the only thing that moves during a long item, and it is what
        distinguishes slow from stuck: work that takes minutes between one
        retired line and the next is otherwise indistinguishable from a hang.
        """
        self.phase = phase
        self._draw()

    def advance(self, left: str = "", right: str = "", steps: int = 1) -> None:
        """Retire the finished item onto its own line, then redraw.

        ``steps`` is how many units this item accounted for, so the bar tracks
        work even though one line is printed per item.
        """
        self.completed = min(self.total, self.completed + max(0, steps))
        self.lines += 1
        if left or right:
            self._line(left, right)
        self.phase = ""
        self._draw()

    def finish(self) -> None:
        """Close the live area so later output starts on a clean line."""
        self._erase()
        self.stream.flush()
        elapsed = self._clock(time.monotonic() - self.started)
        print(
            f"{self.completed}/{self.total} {plural(self.unit, self.completed)} "
            f"across {self.lines} {plural(self.group, self.lines)} in {elapsed}",
            file=self.stream,
        )

    # --- internals ---------------------------------------------------------

    def _columns(self) -> int:
        return min(shutil.get_terminal_size((100, 24)).columns, self.MAX_COLUMNS)

    def _counter(self) -> str:
        """The ``(done/total)`` prefix, padded so the columns never shift.

        Counted to the width of the total rather than of the moment, so the
        label after it does not slide right the first time the count reaches
        another digit.
        """
        return f"({self.completed:>{len(str(self.total))}}/{self.total})"

    def _row(self, left: str, right: str) -> str:
        """One display row: counter, label, right-hand field.

        Both the retired lines and the status line are built here, which is
        what puts the status line's phase in the same column as the right-hand
        fields above it. That field is the point of the line, so a narrow
        terminal eats the label rather than it.
        """
        counter = self._counter()
        columns = self._columns()
        room = columns - len(counter) - self.right_width - 4
        if room < 8:
            return f"{counter} {left}"[: columns - 1]
        if len(left) > room:
            left = left[: room - 1] + "…"
        return f"{counter} {left:<{room}}  {right}"[: columns - 1]

    def _line(self, left: str, right: str) -> None:
        """Print one retired line, guaranteed to fit the width."""
        self._erase()
        print(self._row(left, right), file=self.stream, flush=True)

    @staticmethod
    def _clock(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"

    def _remaining(self) -> str:
        if self.estimated_remaining is not None:
            remaining = self.estimated_remaining - (time.monotonic() - self.estimated_at)
            return self._clock(max(1 if self.completed < self.total else 0, remaining))
        if not self.completed:
            return "--:--:--"
        per_unit = (time.monotonic() - self.started) / self.completed
        return self._clock(per_unit * (self.total - self.completed))

    def _status(self) -> str:
        """The upper line: the item running, and where inside it.

        Laid out as a retired line would be, so the phase lands under the
        right-hand column and the label under the labels — the status line
        reads as the next entry in the list above it, still being filled in.
        """
        return self._row(self.label, self.phase)

    def _bar(self) -> str:
        """The lower line: overall progress, with no per-item text.

        The label lives on the status line above, so the bar itself gets the
        width the label would otherwise have taken.
        """
        counter = self._counter()
        percent = f"{100 * self.completed // max(1, self.total):3d}%"
        if self.estimated_remaining is not None:
            percent = "~" + percent.strip()
        remaining = self._remaining()
        fixed = len(counter) + len(percent) + len(remaining) + 9
        bar_width = max(8, min(40, self._columns() - fixed))
        filled = bar_width * self.completed // max(1, self.total)
        bar = self.FILLED * filled + self.EMPTY * (bar_width - filled)
        return f"{counter} [{bar}] {percent} eta {remaining}"

    def _erase(self) -> None:
        """Remove the live area, leaving the cursor where it began."""
        if not self.drawn:
            return
        self.stream.write(self.ERASE + self.UP + self.ERASE)
        self.drawn = False

    def _draw(self) -> None:
        if not self.interactive:
            return
        self._erase()
        self.stream.write(self._status() + "\n" + self._bar())
        self.stream.flush()
        self.drawn = True
