"""The one exception type the whole parser raises.

It lives in a module of its own so that ``reader``, the bottom layer, can raise it without
importing anything above it. ``ValueError`` is the base because callers outside this package
catch that.
"""

from __future__ import annotations

__all__ = ["ParseError", "expect"]


class ParseError(ValueError):
    """A structural surprise, always with the byte offset that surprised us.

    The save under a running game is rewritten every few minutes, so reading a file
    mid-write is routine rather than exceptional. Every raise site names what was expected
    and where.
    """


def expect(condition: bool, offset: int, message: str) -> None:
    """Raise a ``ParseError`` located at body ``offset`` unless ``condition`` holds."""
    if not condition:
        raise ParseError(f"at body offset {offset}: {message}")
