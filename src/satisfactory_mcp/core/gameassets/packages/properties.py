"""Tagged-property streams, and the decoders that turn one payload into a value."""

from __future__ import annotations

import struct

__all__ = [
    "property_tags",
    "read_float",
    "read_int32",
    "read_triple",
    "read_vector_array",
    "relative_transform",
]

#: The component properties a relative transform is serialised in, in transform order.
_RELATIVE_KEYS = ("RelativeLocation", "RelativeRotation", "RelativeScale3D")


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


def property_tags(
    body: bytes, names: list[str], pos: int = 1
) -> tuple[list[tuple[str | None, str | None, bytes, int]], int]:
    """Walk a tagged-property stream, yielding ``(name, type, payload, value byte)``.

    The value byte follows ``Size`` in the tag and is dead weight for every type except
    ``BoolProperty``, whose payload is empty and whose value lives there and nowhere else. An
    export body starts one byte in and a nested struct payload starts at 0, which is what *pos*
    is for; the end offset comes back so a ``TArray<FStruct>`` can walk element by element. A
    malformed run stops the walk rather than raising -- a truncated tail costs one actor's
    transform, a raise costs the whole package.
    """
    out: list[tuple[str | None, str | None, bytes, int]] = []
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
        pos += 5  # uint32 size, then one byte that is the value of a bool
        if size > limit - pos + 1:
            break
        out.append((name, kind, body[pos : pos + size], value_byte))
        pos += size
    return out, pos


def read_triple(payload: bytes) -> tuple[float, float, float] | None:
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


def read_vector_array(payload: bytes | None) -> list[tuple[float, float, float]]:
    """A ``TArray<FVector>``: uint32 count, then that many triples of double or float."""
    if not payload or len(payload) < 4:
        return []
    count = struct.unpack_from("<I", payload, 0)[0]
    for width, fmt in ((24, "<3d"), (12, "<3f")):
        if len(payload) == 4 + count * width:
            return [struct.unpack_from(fmt, payload, 4 + index * width) for index in range(count)]
    return []


def relative_transform(props: dict[str, bytes], defaults: tuple = (None, None, None)) -> tuple:
    """A component's ``(location, rotation, scale)`` triples, each from its own property where
    serialised and from ``defaults`` where not: an instance writes only what differs."""
    return tuple(
        read_triple(props[key]) if key in props else default
        for key, default in zip(_RELATIVE_KEYS, defaults, strict=True)
    )
