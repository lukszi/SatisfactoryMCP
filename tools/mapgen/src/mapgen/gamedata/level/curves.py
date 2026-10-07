"""Tagged properties with their array index and flags, and the ``FRichCurve`` the lighting keys.

A curve's keys are evaluated as the engine does: constant, linear, or the cubic Hermite written
as a Bezier. docs/map/calibration.md section 43 says which curves the lighting reads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from itertools import pairwise
from typing import NamedTuple

__all__ = [
    "CurveKey",
    "RichCurve",
    "Tag",
    "evaluate",
    "runtime_curves",
    "tag_float",
    "tag_floats",
    "tag_stream",
]

#: The tag's flag bits (UE 5.4 and later).
_HAS_INDEX, _HAS_GUID, _HAS_EXTENSIONS, _BOOL_TRUE = 0x01, 0x02, 0x04, 0x10

#: ``ERichCurveInterpMode``.
_LINEAR, _CONSTANT = 0, 1


class Tag(NamedTuple):
    """One tag: its name, outer type, payload, flag byte and array position."""

    name: str | None
    kind: str | None
    payload: bytes
    flags: int
    position: int

    @property
    def true(self) -> bool:
        """A ``BoolProperty``'s value, which lives in the flags."""
        return bool(self.flags & _BOOL_TRUE)


class CurveKey(NamedTuple):
    """One ``FRichCurveKey``: interpolation mode, time, value and the two tangents."""

    mode: int
    time: float
    value: float
    arrive: float
    leave: float


@dataclass(frozen=True)
class RichCurve:
    """An ``FRichCurve``: its keys in time order, and the value it holds without any."""

    keys: tuple[CurveKey, ...]
    default: float | None


def _skip_type(body: bytes, pos: int, names: list[str]) -> tuple[int, str | None]:
    index = struct.unpack_from("<I", body, pos)[0]
    inner = struct.unpack_from("<i", body, pos + 8)[0]
    slot = index & 0x3FFFFFFF
    kind = names[slot] if (index >> 30) == 0 and slot < len(names) else None
    pos += 12
    for _ in range(inner):
        pos, _inner = _skip_type(body, pos, names)
    return pos, kind


def tag_stream(body: bytes, names: list[str], pos: int = 0) -> tuple[list[Tag], int]:
    """Every tag up to the stream's ``None``, and the offset after it; a malformed tail stops."""
    out: list[Tag] = []
    while pos + 8 <= len(body):
        index, number = struct.unpack_from("<II", body, pos)
        pos += 8
        if index == 0 and number == 0:
            break
        slot = index & 0x3FFFFFFF
        name = names[slot] if (index >> 30) == 0 and slot < len(names) else None
        try:
            pos, kind = _skip_type(body, pos, names)
            size, flags = struct.unpack_from("<iB", body, pos)
            pos += 5
            array_index = struct.unpack_from("<i", body, pos)[0] if flags & _HAS_INDEX else 0
        except (struct.error, IndexError, RecursionError):
            break
        pos += (4 if flags & _HAS_INDEX else 0) + (16 if flags & _HAS_GUID else 0)
        if flags & _HAS_EXTENSIONS:
            pos += 2 if body[pos] & 0x02 else 1
        if size < 0 or pos + size > len(body):
            break
        out.append(Tag(name, kind, body[pos : pos + size], flags, array_index))
        pos += size
    return out, pos


def tag_float(tag: Tag) -> float | None:
    """A float, double or int tag's number."""
    width = {"FloatProperty": "<f", "DoubleProperty": "<d", "IntProperty": "<i"}.get(tag.kind or "")
    if width is None or len(tag.payload) != struct.calcsize(width):
        return None
    return float(struct.unpack(width, tag.payload)[0])


def tag_floats(tag: Tag) -> tuple[float, ...] | None:
    """A ``LinearColor`` (four floats) or a ``Vector`` or ``Rotator`` (three doubles)."""
    if tag.kind != "StructProperty":
        return None
    if len(tag.payload) == 16:
        return struct.unpack("<4f", tag.payload)
    if len(tag.payload) == 24:
        return struct.unpack("<3d", tag.payload)
    return None


def _rich_curve(payload: bytes, names: list[str]) -> RichCurve:
    keys: list[CurveKey] = []
    default: float | None = None
    for tag in tag_stream(payload, names)[0]:
        if tag.name == "Keys" and len(tag.payload) >= 4:
            count = struct.unpack_from("<i", tag.payload)[0]
            size = (len(tag.payload) - 4) // count if count > 0 else 0
            for i in range(count if size >= 27 else 0):
                at = 4 + i * size
                time, value, arrive, _weight, leave = struct.unpack_from("<5f", tag.payload, at + 3)
                keys.append(CurveKey(tag.payload[at], time, value, arrive, leave))
        elif tag.name == "DefaultValue":
            default = tag_float(tag)
    return RichCurve(tuple(keys), default)


def runtime_curves(payload: bytes, names: list[str]) -> list[RichCurve]:
    """A ``RuntimeFloatCurve`` as one curve, a ``RuntimeCurveLinearColor`` as four (RGBA)."""
    curves: dict[int, RichCurve] = {}
    for tag in tag_stream(payload, names)[0]:
        if tag.name in ("ColorCurves", "EditorCurveData"):
            curves[tag.position] = _rich_curve(tag.payload, names)
    empty = RichCurve((), None)
    return [curves.get(i, empty) for i in range(max(curves) + 1)] if curves else [empty]


def evaluate(curve: RichCurve, t: float) -> float | None:
    """The curve at ``t``, held flat past its ends; its default without keys."""
    keys = curve.keys
    if not keys:
        return curve.default
    if t <= keys[0].time:
        return keys[0].value
    for k0, k1 in pairwise(keys):
        if not k0.time <= t <= k1.time:
            continue
        span = k1.time - k0.time
        if span <= 0 or k0.mode == _CONSTANT:
            return k0.value
        u = (t - k0.time) / span
        if k0.mode == _LINEAR:
            return k0.value + (k1.value - k0.value) * u
        p1, p2 = k0.value + k0.leave * span / 3.0, k1.value - k1.arrive * span / 3.0
        v = 1.0 - u
        return v**3 * k0.value + 3 * v * v * u * p1 + 3 * v * u * u * p2 + u**3 * k1.value
    return keys[-1].value
