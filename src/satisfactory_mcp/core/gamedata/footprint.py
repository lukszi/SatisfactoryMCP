"""Machine footprints, derived from mClearanceData.

Every buildable carries `mClearanceData`, a list of boxes describing the space it occupies,
and **the boxes can be rotated**: the Fuel-Powered Generator's clearance is thin boxes at
45-degree increments approximating a round machine, so the largest one alone reads 22 x 4 m
where the real footprint is 22 x 22. Every box is therefore transformed by its
`RelativeTransform` and the union taken.

Which boxes count is the other rule. `ExcludeForSnapping` boxes are approach clearances and
never the building's own volume. `CT_Soft` boxes are skipped only while a hard box exists --
on a machine they are the overlap allowances around the hard volume -- but every architecture
piece (foundation, wall, pillar, ramp, beam) carries ONLY soft boxes, so a buildable with no
hard box falls back to the union of its soft ones rather than reporting no size at all.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from ..jsontypes import JsonObject, JsonValue
from .uestruct import as_float, as_list, parse_struct

__all__ = ["FOUNDATION_M", "Footprint", "Packed", "extract_footprint"]

#: Standard foundation edge length. Everything in Satisfactory grids to this.
FOUNDATION_M = 8.0

#: Longest side of a default block, as a multiple of its shortest. A judgement call, not
#: a game rule, and the only number in this module that is not derived: the unconstrained
#: tile optimum is routinely a ribbon (77 Water Extractors pack cheapest as 40x702 m,
#: saving 4% over a squarish block), which is correct arithmetic and not a build. Pass
#: `columns=` to override it in either direction.
MAX_BLOCK_ASPECT = 4.0


def _foundations_covering(width_m: float, depth_m: float) -> int:
    """8 m foundations a ``width_m`` x ``depth_m`` rectangle covers, at least one each way."""
    return max(1, math.ceil(width_m / FOUNDATION_M)) * max(1, math.ceil(depth_m / FOUNDATION_M))


@dataclass(frozen=True)
class Footprint:
    """Axis-aligned bounding box of a building, in metres."""

    width_m: float  # X
    depth_m: float  # Y
    height_m: float  # Z

    @property
    def area_m2(self) -> float:
        return self.width_m * self.depth_m

    @property
    def foundations(self) -> int:
        """8 m foundations covered by one machine, ignoring shared edges."""
        return _foundations_covering(self.width_m, self.depth_m)

    def __str__(self) -> str:
        return f"{self.width_m:g}x{self.depth_m:g}x{self.height_m:g}m"

    def pack(self, count: int, columns: int = 0) -> Packed:
        """Lay ``count`` of this machine out on foundations, and measure the result.

        ``foundations`` above is per-machine and ignores shared edges, so ``count x
        foundations`` is an upper bound rather than a build: two Water Extractors side by
        side span 40 m and need 5 tiles, not 6.

        ``columns`` forces an arrangement; 1 gives a single row, whose LENGTH is what
        platform modules get measured against. Left at 0, every column count is tried and the
        cheapest one inside ``MAX_BLOCK_ASPECT`` wins, ties broken toward the squarer block.
        The result is never worse than ``count x foundations``, because the single row is
        always a candidate and ``ceil`` is subadditive.
        """
        machine_count = max(1, int(count))

        def measure(column_count: int) -> Packed:
            column_count = max(1, min(column_count, machine_count))
            rows = math.ceil(machine_count / column_count)
            width, depth = column_count * self.width_m, rows * self.depth_m
            return Packed(
                count=machine_count,
                columns=column_count,
                rows=rows,
                width_m=width,
                depth_m=depth,
                foundations=_foundations_covering(width, depth),
            )

        if columns:
            return measure(int(columns))
        options = [measure(column_count) for column_count in range(1, machine_count + 1)]
        buildable = [
            packed
            for packed in options
            if max(packed.width_m, packed.depth_m)
            <= MAX_BLOCK_ASPECT * min(packed.width_m, packed.depth_m)
        ]
        # The single row is a candidate whatever its aspect: it is the shape the
        # `count x foundations` bound assumes, and dropping it lets a 5-machine block come
        # out at 6 tiles against that bound's 5.
        return min(
            [*buildable, measure(machine_count)],
            key=lambda packed: (packed.foundations, abs(packed.width_m - packed.depth_m)),
        )


@dataclass(frozen=True)
class Packed:
    """A rectangular block of identical machines, snapped to the 8 m grid."""

    count: int
    columns: int
    rows: int
    width_m: float
    depth_m: float
    foundations: int

    def __str__(self) -> str:
        return f"{self.columns}x{self.rows} = {self.width_m:,.0f}x{self.depth_m:,.0f}m"


def _rotate(q: JsonObject, v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Rotate a vector by a quaternion (x, y, z, w).

    v' = v + 2 * cross(q_xyz, cross(q_xyz, v) + w*v)
    """
    qx, qy, qz, qw = (as_float(q.get(k)) for k in ("X", "Y", "Z", "W"))
    if (qx, qy, qz, qw) == (0.0, 0.0, 0.0, 0.0):
        return v
    vx, vy, vz = v
    # t = cross(q_xyz, v) + w*v
    tx = qy * vz - qz * vy + qw * vx
    ty = qz * vx - qx * vz + qw * vy
    tz = qx * vy - qy * vx + qw * vz
    # v + 2 * cross(q_xyz, t)
    return (
        vx + 2.0 * (qy * tz - qz * ty),
        vy + 2.0 * (qz * tx - qx * tz),
        vz + 2.0 * (qx * ty - qy * tx),
    )


def _corners(box_min: JsonObject, box_max: JsonObject) -> list[tuple[float, float, float]]:
    x0, y0, z0 = (as_float(box_min.get(k)) for k in ("X", "Y", "Z"))
    x1, y1, z1 = (as_float(box_max.get(k)) for k in ("X", "Y", "Z"))
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def extract_footprint(raw: JsonValue) -> Footprint | None:
    """Union AABB of a building's own clearance boxes, in metres. Hard boxes when the
    buildable has any, otherwise its soft ones -- see the module docstring."""
    entries = [
        e
        for e in as_list(parse_struct(raw))
        if isinstance(e, dict)
        # Approach clearance is never the building's volume, hard or soft.
        and str(e.get("ExcludeForSnapping", "")).strip().lower() != "true"
    ]
    hard = _union(e for e in entries if e.get("Type") != "CT_Soft")
    # Fallback, never a union of both: mixing soft into hard grows the Fuel Generator past
    # the 20x20 its own hard boxes measure.
    return hard if hard is not None else _union(entries)


def _union(entries: Iterable[JsonObject]) -> Footprint | None:
    """Axis-aligned union of transformed clearance boxes, or None for no boxes."""
    low_cm = [float("inf")] * 3
    high_cm = [float("-inf")] * 3
    seen = False

    for entry in entries:
        box = entry.get("ClearanceBox")
        if not isinstance(box, dict):
            continue
        box_min, box_max = box.get("Min"), box.get("Max")
        if not isinstance(box_min, dict) or not isinstance(box_max, dict):
            continue

        transform = entry.get("RelativeTransform") or {}
        rotation = transform.get("Rotation") if isinstance(transform, dict) else None
        translation = transform.get("Translation") if isinstance(transform, dict) else None
        tx, ty, tz = (
            (as_float(translation.get(k)) for k in ("X", "Y", "Z"))
            if isinstance(translation, dict)
            else (0.0, 0.0, 0.0)
        )

        for corner in _corners(box_min, box_max):
            point = _rotate(rotation, corner) if isinstance(rotation, dict) else corner
            for axis, (value, offset) in enumerate(zip(point, (tx, ty, tz))):
                world = value + offset
                low_cm[axis] = min(low_cm[axis], world)
                high_cm[axis] = max(high_cm[axis], world)
            seen = True

    if not seen:
        return None
    return Footprint(
        width_m=round((high_cm[0] - low_cm[0]) / 100.0, 2),
        depth_m=round((high_cm[1] - low_cm[1]) / 100.0, 2),
        height_m=round((high_cm[2] - low_cm[2]) / 100.0, 2),
    )
