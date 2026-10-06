"""The versioned JSON files the asks, pins and advice stores keep, read as typed documents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar, cast

from ...core import filelock
from ...core.jsontypes import JsonValue

__all__ = ["count", "locked_update"]

Doc = TypeVar("Doc", bound=Mapping[str, object])
T = TypeVar("T")


def count(value: JsonValue, default: int) -> int:
    """A counter as the file holds it; ``default`` for none, zero or a value that is no number."""
    return int(value) if isinstance(value, int | float) and value else default


def locked_update(
    path: Path, read: Callable[[], Doc], change: Callable[[Doc], tuple[T, bool]]
) -> T:
    """Run ``change(read()) -> (result, dirty)`` under ``path``'s lock; write when dirty."""

    def apply(data: dict[str, object]) -> tuple[T, bool]:
        return change(cast(Doc, data))

    return filelock.update_versioned_json(path, lambda: dict(read()), apply)
