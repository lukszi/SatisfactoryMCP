"""Save units to wire units: centimetres become metres at one decimal, rotations degrees."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import SupportsFloat, TypeAlias, overload

from typing_extensions import TypedDict

from ....domain.spatial import geo

__all__ = ["Placed", "PlayerPosition", "bbox_m", "cm_to_m", "point_m", "xyz_m", "yaw_deg"]

#: Instance leaf to where it stands, in centimetres: what ``candidates.positions`` returns.
Placed: TypeAlias = Mapping[str, tuple[float, float, float]]


class PlayerPosition(TypedDict):
    """Where the player last stood, or three nulls -- never a missing branch.

    A save with no pawn, as a dedicated-server world has, sends three nulls rather than
    dropping the key: the page branches on ``x_m === null`` to decide whether there is a
    you-are-here to draw at all, and all three go null together.
    """

    x_m: float | None
    y_m: float | None
    z_m: float | None


@overload
def cm_to_m(value: float) -> float: ...
@overload
def cm_to_m(value: float | None) -> float | None: ...
def cm_to_m(value: float | None) -> float | None:
    """Centimetres to metres, one decimal. The unit rule, in one place."""
    return None if value is None else round(float(value) / 100.0, 1)


def xyz_m(pos: Sequence[float | None] | None) -> PlayerPosition:
    """A projection ``pos`` triple as named metre fields."""
    if not pos:
        return {"x_m": None, "y_m": None, "z_m": None}
    padded = [*pos, None, None, None]
    return {"x_m": cm_to_m(padded[0]), "y_m": cm_to_m(padded[1]), "z_m": cm_to_m(padded[2])}


def point_m(xy: Sequence[float]) -> tuple[float, float]:
    """An ``(x, y)`` pair in centimetres as ``[x_m, y_m]``."""
    return cm_to_m(xy[0]), cm_to_m(xy[1])


def bbox_m(placed: Placed, machines: Iterable[str]) -> tuple[float, float, float, float] | None:
    """The box around the placed ones of ``machines``, in metres; ``None`` when none is placed."""
    box = geo.bbox([placed[m][:2] for m in machines if m in placed])
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return cm_to_m(x0), cm_to_m(y0), cm_to_m(x1), cm_to_m(y1)


def yaw_deg(value: object) -> float | None:
    """A placement's rotation about world Z, degrees, one decimal; positive turns +X to +Y.

    ``None``, never 0.0, when the projection carries no yaw: a projection older than schema 12
    never recorded one, which is a different claim from "axis-aligned".
    """
    if not isinstance(value, (SupportsFloat, str)):
        return None
    try:
        return round(float(value), 1)
    except ValueError:
        return None
