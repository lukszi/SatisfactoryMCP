"""Reading the parser's object tree without tripping its silent hazards.

Each helper guards one: a ComponentHeader has no typePath, a BoolProperty is a uint8 where 16
means True, UE omits empty arrays, and an ObjectReference has no ``__repr__``. The full list
is in ``docs/save-projection.md`` §6. The ``to_*`` and ``as_sequence`` helpers type a value
the way ``float()``, ``int()`` and iteration would use it: the same answer, the same error.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from ..schema import Position

if TYPE_CHECKING:
    from .parser import ActorHeader, ComponentHeader, ParsedObject, ParsedSave, SaveValue

__all__ = [
    "as_sequence",
    "class_from_type_path",
    "iter_objects",
    "owner_class",
    "position_of",
    "properties_of",
    "ref_class",
    "ref_path",
    "struct_fields",
    "to_float",
    "to_int",
    "truthy",
    "yaw_of",
]


def truthy(value: object) -> bool:
    """BoolProperty comes back as a uint8 where 16 is True. `v == 1` is wrong."""
    return bool(value)


def to_float(value: SaveValue) -> float:
    """``float(value)``: a number or a numeric string, else the ``TypeError`` it raises."""
    if isinstance(value, (int, float, str, bytes)):
        return float(value)
    raise TypeError(
        f"float() argument must be a string or a real number, not '{type(value).__name__}'"
    )


def to_int(value: SaveValue) -> int:
    """``int(value)``, on ``to_float``'s terms."""
    if isinstance(value, (int, float, str, bytes)):
        return int(value)
    raise TypeError(
        "int() argument must be a string, a bytes-like object or a real number, "
        f"not '{type(value).__name__}'"
    )


def as_sequence(value: SaveValue) -> Sequence[SaveValue]:
    """``value`` to index or iterate, or the ``TypeError`` indexing it would raise."""
    if isinstance(value, Sequence):
        return value
    raise TypeError(f"'{type(value).__name__}' object is not subscriptable")


def class_from_type_path(type_path: str) -> str:
    """``/Game/.../Build_X.Build_X_C`` -> ``Build_X_C``; ``""`` for a component."""
    return type_path.rsplit(".", 1)[-1] if type_path else ""


def ref_path(value: object) -> str | None:
    """instanceName of an ObjectReference, or None. Never use repr() on these."""
    path = getattr(value, "pathName", None)
    return str(path) if path else None


def ref_class(value: object) -> str | None:
    """Class name from an ObjectReference OR a bare asset-path string.

    Some structs store references as plain path strings (an inventory stack's ``Item`` is
    ``[path, int]``), so both shapes must resolve.
    """
    path = ref_path(value)
    if path is None and isinstance(value, str) and value:
        path = value
    if not path:
        return None
    tail = path.rsplit(".", 1)[-1]
    return tail or None


def properties_of(obj: ParsedObject) -> dict[str, SaveValue]:
    """properties is a list of [name, value] pairs; absent means empty."""
    out: dict[str, SaveValue] = {}
    for entry in obj.properties:
        try:
            name, value = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        if isinstance(name, str):
            out[name] = value
    return out


def struct_fields(entry: SaveValue) -> dict[str, SaveValue]:
    """Flatten one element of a StructProperty array into {field: value}.

    The parser emits a struct as ``[values, propertyTypes]`` and some nested structs arrive
    already flattened, so both shapes are accepted; iterating the outer list blindly yields a
    list where a field name is expected.
    """
    if not isinstance(entry, list):
        return {}
    candidate: Sequence[SaveValue] = entry
    values = entry[0] if len(entry) == 2 else None
    if (
        isinstance(values, list)
        and isinstance(entry[1], list)
        and values
        and all(isinstance(pair, list) and pair and isinstance(pair[0], str) for pair in values)
    ):
        candidate = values
    out: dict[str, SaveValue] = {}
    for pair in candidate:
        if isinstance(pair, list) and len(pair) >= 2 and isinstance(pair[0], str):
            out[pair[0]] = pair[1]
    return out


def iter_objects(
    save: ParsedSave,
) -> Iterator[tuple[str, ActorHeader | ComponentHeader, ParsedObject]]:
    """Yield (type_path, header, object) over every level.

    ComponentHeader lacks typePath, hence the getattr default: attribute access would raise
    partway through a 44k-object walk.
    """
    for level in save.levels:
        headers: list[ActorHeader | ComponentHeader] = (
            getattr(level, "actorAndComponentObjectHeaders", None) or []
        )
        objects: list[ParsedObject] = getattr(level, "objects", None) or []
        for header, obj in zip(headers, objects):
            type_path: str = getattr(header, "typePath", "") or ""
            yield type_path, header, obj


def position_of(header: object) -> Position | None:
    """An actor header's position, rounded to millimetres, or None where it will not read."""
    position = getattr(header, "position", None)
    if not position:
        return None
    try:
        return [
            round(float(position[0]), 1),
            round(float(position[1]), 1),
            round(float(position[2]), 1),
        ]
    except (TypeError, IndexError, ValueError):
        return None


def yaw_of(quat: SaveValue) -> float | None:
    """Top-down facing in degrees from a placement quaternion ``(x, y, z, w)``, or None.

    Positive yaw turns +X towards +Y, in ``(-180, 180]``. None, never 0.0, when the rotation
    will not read: 0.0 is a measurement meaning axis-aligned. Convention in
    ``docs/save-projection.md`` §6.16.
    """
    try:
        x, y, z, w = (to_float(v) for v in as_sequence(quat))
    except (TypeError, ValueError):
        return None
    deg = round(math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))), 2)
    # A float32 half-turn lands on -179.999..., so rounding alone would emit both -180 and 180.
    return 180.0 if deg == -180.0 else deg


def owner_class(owner: str) -> str:
    """``Build_StorageContainerMk1_C_2147441119`` -> ``Build_StorageContainerMk1_C``.

    A name with no numeric tail comes back whole rather than losing its ``_C``.
    """
    head, sep, tail = owner.rpartition("_")
    return head if sep and tail.isdigit() else owner
