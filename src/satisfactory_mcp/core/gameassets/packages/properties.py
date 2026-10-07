"""Tagged-property streams, and the decoders that turn one payload into a value."""

from __future__ import annotations

import struct
from typing import NamedTuple, TypeAlias

__all__ = [
    "PropertyTag",
    "RelativeTransform",
    "Vec3",
    "property_tags",
    "read_float",
    "read_int32",
    "read_triple",
    "read_vector_array",
    "relative_transform",
    "tagged_properties",
]

#: An ``FVector`` or ``FRotator``: three components, whichever width they were cooked at.
Vec3: TypeAlias = tuple[float, float, float]

#: A component's serialised ``(location, rotation, scale)``, ``None`` where it holds the default.
RelativeTransform: TypeAlias = tuple[Vec3 | None, Vec3 | None, Vec3 | None]

#: The tag's flag byte (``EPropertyTagFlags``): what follows it, and a bool's value.
_HAS_INDEX, _HAS_GUID, _HAS_EXTENSIONS, _BOOL_TRUE = 0x01, 0x02, 0x04, 0x10
#: An extension byte with this bit has two more bytes, the overridable operation and logic.
_OVERRIDABLE = 0x02

#: The component properties a relative transform is serialised in, in transform order.
_RELATIVE_KEYS = ("RelativeLocation", "RelativeRotation", "RelativeScale3D")


class PropertyTag(NamedTuple):
    """One tag of a property stream: its name, outer type, payload, flag byte and position in
    a fixed-size array (0 for a plain property)."""

    name: str | None
    kind: str | None
    payload: bytes
    flags: int
    array_index: int

    @property
    def true(self) -> bool:
        """A ``BoolProperty``'s value, which lives in the flags: its payload is empty."""
        return bool(self.flags & _BOOL_TRUE)


def _skip_property_type(body: bytes, names: list[str], pos: int) -> tuple[int, str | None]:
    """Walk one property type tree; return the position after it and its outermost type."""
    index, _number = struct.unpack_from("<II", body, pos)
    inner = struct.unpack_from("<i", body, pos + 8)[0]
    slot = index & 0x3FFFFFFF
    kind = names[slot] if (index >> 30) == 0 and slot < len(names) else None
    pos += 12
    for _ in range(inner):
        pos, _inner_kind = _skip_property_type(body, names, pos)
    return pos, kind


def property_tags(body: bytes, names: list[str], pos: int = 1) -> tuple[list[PropertyTag], int]:
    """Walk a tagged-property stream the way the generators' outputs were made: each tag's
    payload, with the byte after ``Size`` kept as its flags; and the offset after it.

    This walk reads no array index, GUID or extension block, and stops only at a ``None``
    that is the package's name 0. ``tagged_properties`` reads both; switching a generator
    over moves its output (docs/backlog.md, "One tagged-property walk"). An export body
    starts one byte in and a nested struct payload starts at 0, which is what *pos* is for;
    the end offset comes back so a ``TArray<FStruct>`` can walk element by element. A
    malformed run stops the walk rather than raising -- a truncated tail costs one actor's
    transform, a raise costs the whole package.
    """
    out: list[PropertyTag] = []
    limit = len(body)
    while pos + 8 <= limit:
        name_index, name_number = struct.unpack_from("<II", body, pos)
        if name_index == 0 and name_number == 0:  # the None that terminates the stream
            pos += 8
            break
        slot = name_index & 0x3FFFFFFF
        name = names[slot] if (name_index >> 30) == 0 and slot < len(names) else None
        pos += 8
        try:
            pos, kind = _skip_property_type(body, names, pos)
        except (struct.error, IndexError, RecursionError):
            break
        if pos + 5 > limit:
            break
        size = struct.unpack_from("<I", body, pos)[0]
        value_byte = body[pos + 4]
        pos += 5  # uint32 size, then the flag byte
        if size > limit - pos + 1:
            break
        out.append(PropertyTag(name, kind, body[pos : pos + size], value_byte, 0))
        pos += size
    return out, pos


def tagged_properties(body: bytes, names: list[str], pos: int = 1) -> tuple[list[PropertyTag], int]:
    """Walk a tagged-property stream to its ``None``; return its tags and the offset after it.

    After ``Size`` comes the flag byte, which says whether an array index, a GUID or an
    extension block follows before the payload, and holds a ``BoolProperty``'s value. The
    stream ends at a ``None`` name, wherever the package keeps it. *pos* and a malformed run
    are as ``property_tags`` has them.
    """
    out: list[PropertyTag] = []
    limit = len(body)
    while pos + 8 <= limit:
        name_index, name_number = struct.unpack_from("<II", body, pos)
        slot = name_index & 0x3FFFFFFF
        name = names[slot] if (name_index >> 30) == 0 and slot < len(names) else None
        pos += 8
        if name_number == 0 and (name_index == 0 or name == "None"):
            break
        try:
            pos, kind = _skip_property_type(body, names, pos)
            size, flags = struct.unpack_from("<iB", body, pos)
            pos += 5
            index = struct.unpack_from("<i", body, pos)[0] if flags & _HAS_INDEX else 0
            pos += (4 if flags & _HAS_INDEX else 0) + (16 if flags & _HAS_GUID else 0)
            if flags & _HAS_EXTENSIONS:
                pos += 3 if body[pos] & _OVERRIDABLE else 1
        except (struct.error, IndexError, RecursionError):
            break
        if size < 0 or pos + size > limit:
            break
        out.append(PropertyTag(name, kind, body[pos : pos + size], flags, index))
        pos += size
    return out, pos


def read_triple(payload: bytes) -> Vec3 | None:
    """An ``FVector``/``FRotator`` payload: three doubles, or three floats in an old cook."""
    if len(payload) == 24:
        return struct.unpack("<3d", payload)
    if len(payload) == 12:
        return struct.unpack("<3f", payload)
    return None


def read_float(payload: bytes) -> float | None:
    """A ``FloatProperty`` or ``DoubleProperty`` payload, whichever width arrived."""
    if len(payload) == 4:
        return struct.unpack("<f", payload)[0]
    if len(payload) == 8:
        return struct.unpack("<d", payload)[0]
    return None


def read_int32(payload: bytes) -> int | None:
    """An ``IntProperty`` payload."""
    return struct.unpack("<i", payload)[0] if len(payload) == 4 else None


def read_vector_array(payload: bytes | None) -> list[Vec3]:
    """A ``TArray<FVector>``: uint32 count, then that many triples of double or float."""
    if not payload or len(payload) < 4:
        return []
    count = struct.unpack_from("<I", payload, 0)[0]
    for width, fmt in ((24, "<3d"), (12, "<3f")):
        if len(payload) == 4 + count * width:
            return [struct.unpack_from(fmt, payload, 4 + index * width) for index in range(count)]
    return []


def relative_transform(
    props: dict[str, bytes], defaults: RelativeTransform = (None, None, None)
) -> RelativeTransform:
    """A component's ``(location, rotation, scale)`` triples, each from its own property where
    serialised and from ``defaults`` where not: an instance writes only what differs."""
    location, rotation, scale = (
        read_triple(props[key]) if key in props else default
        for key, default in zip(_RELATIVE_KEYS, defaults, strict=True)
    )
    return location, rotation, scale
