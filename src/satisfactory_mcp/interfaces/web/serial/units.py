"""Save units to wire units: centimetres become metres at one decimal, rotations degrees."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ....domain.spatial import geo

__all__ = ["bbox_m", "cm_to_m", "point_m", "xyz_m", "yaw_deg"]


def cm_to_m(value: float | None) -> float | None:
    """Centimetres to metres, one decimal. The unit rule, in one place."""
    return None if value is None else round(float(value) / 100.0, 1)


def xyz_m(pos: Any) -> dict[str, float | None]:
    """A projection ``pos`` triple as named metre fields."""
    if not pos:
        return {"x_m": None, "y_m": None, "z_m": None}
    padded = list(pos) + [None, None, None]
    return {"x_m": cm_to_m(padded[0]), "y_m": cm_to_m(padded[1]), "z_m": cm_to_m(padded[2])}


def point_m(xy: Any) -> list[float | None]:
    """An ``(x, y)`` pair in centimetres as ``[x_m, y_m]``."""
    return [cm_to_m(xy[0]), cm_to_m(xy[1])]


def bbox_m(placed: dict, machines: Iterable[str]) -> list[float | None] | None:
    """The box around the placed ones of ``machines``, in metres; ``None`` when none is placed."""
    box = geo.bbox([placed[m][:2] for m in machines if m in placed])
    return None if box is None else [cm_to_m(v) for v in box]


def yaw_deg(value: Any) -> float | None:
    """A placement's rotation about world Z, degrees, one decimal; positive turns +X to +Y.

    ``None``, never 0.0, when the projection carries no yaw: a projection older than schema 12
    never recorded one, which is a different claim from "axis-aligned".
    """
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None
