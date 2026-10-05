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
