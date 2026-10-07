"""JSON as it crosses a boundary: what ``json.load`` returns, before a schema check names it.

The project's stand-in for ``typing.Any``, which ruff bans (docs/DEVELOPING.md, "Types").
Narrow a ``JsonValue`` with ``isinstance`` or the readers below, or ``cast`` it once to a
TypedDict where a schema or version check already guards the read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from typing_extensions import TypeAliasType, TypeIs

__all__ = [
    "JsonArray",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "as_float",
    "as_int",
    "is_object_dict",
    "is_object_list",
    "require_list",
    "require_object",
]

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


def is_object_dict(value: object) -> TypeIs[dict[str, object]]:
    """Whether a value read as ``object`` is a dict; its keys are taken to be strings, as
    every boundary this project reads (JSON, the plan log, a request body) writes them."""
    return isinstance(value, dict)


def is_object_list(value: object) -> TypeIs[list[object]]:
    """Whether a value read as ``object`` is a list."""
    return isinstance(value, list)


def require_object(value: JsonValue) -> JsonObject:
    """``value`` when it is a JSON object; ``TypeError`` otherwise."""
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object, not {type(value).__name__}")
    return value


def require_list(value: JsonValue) -> JsonArray:
    """``value`` when it is a JSON array; ``TypeError`` otherwise."""
    if not isinstance(value, list):
        raise TypeError(f"expected a JSON array, not {type(value).__name__}")
    return value


def as_float(value: JsonValue) -> float:
    """``float(value)``: the same answer, and the same ``TypeError`` for null, an object or an
    array (a string that is no number is ``float``'s ``ValueError``)."""
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"expected a number, not {type(value).__name__}")


def as_int(value: JsonValue) -> int:
    """``int(value)``, on ``as_float``'s terms."""
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"expected a number, not {type(value).__name__}")
