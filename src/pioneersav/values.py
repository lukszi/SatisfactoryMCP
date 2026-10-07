"""What a decoded value is: the one type every property, container element and trailer field
comes back as.

The shapes themselves -- which type reads as which list -- are in docs/savparse-notes.md,
``### Properties``. A ``Sequence`` rather than a ``list`` in the union, so that a reader that
builds a ``list[float]`` is a reader of values without a copy.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from .references import ObjectReference

__all__ = ["Properties", "PropertyTypes", "SaveValue"]

#: One decoded value. ``None`` is a payload skipped by its declared size.
SaveValue: TypeAlias = "int | float | str | bytes | ObjectReference | Sequence[SaveValue] | None"

#: An object's properties as ``[name, value]`` pairs, in file order.
Properties: TypeAlias = list[list[SaveValue]]

#: The parallel ``[name, typeName, paramCount, ..., flags]`` rows: what each value WAS.
PropertyTypes: TypeAlias = list[list[str | int]]
