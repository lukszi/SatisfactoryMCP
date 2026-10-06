"""JSON as it crosses a boundary: what ``json.load`` returns, before a schema check names it.

The project's stand-in for ``typing.Any``, which ruff bans (docs/DEVELOPING.md, "Types").
Narrow a ``JsonValue`` with ``isinstance``, or ``cast`` it once to a TypedDict where a schema
or version check already guards the read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from typing_extensions import TypeAliasType

__all__ = ["JsonArray", "JsonObject", "JsonScalar", "JsonValue"]

JsonScalar: TypeAlias = str | int | float | bool | None
# Named at runtime, because pydantic cannot resolve a plain string alias in a response model.
# pyright reads the plain alias: its TypeAliasType loses the self-reference when JsonObject is
# the first name another module evaluates.
if TYPE_CHECKING:
    JsonValue: TypeAlias = "JsonScalar | list[JsonValue] | dict[str, JsonValue]"
else:
    JsonValue = TypeAliasType("JsonValue", "JsonScalar | list[JsonValue] | dict[str, JsonValue]")
JsonArray: TypeAlias = list[JsonValue]
JsonObject: TypeAlias = dict[str, JsonValue]
