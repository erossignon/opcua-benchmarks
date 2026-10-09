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

"""Plain single-line editing shared by the terminal's text fields."""

from __future__ import annotations


def edit_text(text: str, cursor: int | None, key: str) -> tuple[str, int]:
    """Insert or delete at the cursor; a newly opened field starts at its end."""
    cursor = len(text) if cursor is None else max(0, min(cursor, len(text)))
    if key == "left":
        cursor = max(0, cursor - 1)
    elif key == "right":
        cursor = min(len(text), cursor + 1)
    elif key == "home":
        cursor = 0
    elif key == "end":
        cursor = len(text)
    elif key == "backspace" and cursor:
        text = text[: cursor - 1] + text[cursor:]
        cursor -= 1
    elif key == "delete":
        text = text[:cursor] + text[cursor + 1 :]
    elif key == "clear":
        text, cursor = "", 0
    else:
        key = " " if key == "space" else key
        if len(key) == 1 and key.isprintable():
            text = text[:cursor] + key + text[cursor:]
            cursor += 1
    return text, cursor


def visible_text(text: str, cursor: int | None, width: int) -> tuple[str, int]:
    """Keep the caret and the surrounding text inside a single-line field."""
    cursor = len(text) if cursor is None else max(0, min(cursor, len(text)))
    width = max(1, width)
    start = max(0, cursor - width + 1)
    return text[start : start + width], cursor - start
