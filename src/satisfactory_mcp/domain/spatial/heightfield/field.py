"""A loaded field and its point lookups: a texel, every surface at a point, one surface's height.

How a point is read, and which surface a hint picks: docs/map/heightfield.md sections 22 to 24.
"""

from __future__ import annotations

import math

import numpy as np

from . import cave_masks, collision_pack
from .areas import FieldAreas
from .codec import RasterGrid
from .planes import (
    DM_PER_M,
    NODATA,
    PROV_CLIFF_VALUES,
    PROV_LANDSCAPE,
    TERRAIN_NAME,
    TOP_NAME,
    WATER_DRY,
)
from .readings import AMBIGUOUS_M, SURFACES, Reading, Surface, Surfaces

__all__ = ["Field"]

#: Four vertices spanning more than this are a cliff edge, not a slope: the point reads the
#: nearest one instead of a blend of rock top and the ground below it.
BLEND_MAX_STEP_M = 2.0

#: A landscape ground reading this close to the terrain plane is that plane rounded to
#: decimetres, so the terrain's 7.8 mm value answers instead.
REFINE_M = 0.1

#: Under ``inside``, a collision surface at most this far under the hint is the cave floor.
CAVE_FLOOR_REACH_M = 3.0

#: A sampled value and the ``(row, col)`` of the vertex that dominated it.
_Sample = tuple[float, tuple[int, int]]


class Field(FieldAreas):
    """A loaded heightmap: its planes, a georeference, the accuracy it measured, and lookups.

    Built by ``load_field``. With ``cache`` on, a plane decodes once into ``cache/<name>.npy``
    and is memory-mapped after.
    """

    def near_rock(self, x_cm: float, y_cm: float) -> bool:
        """Whether any vertex of the quad holding a point is cliff."""
        c0 = math.floor((x_cm - self.x0_cm) / self.spacing_cm)
        r0 = math.floor((y_cm - self.y0_cm) / self.spacing_cm)
        block = self.provenance_plane[max(r0, 0) : r0 + 2, max(c0, 0) : c0 + 2]
        return bool(np.isin(block, PROV_CLIFF_VALUES).any())

    def collision_surfaces(self, x_cm: float, y_cm: float, found: Surfaces) -> Surfaces | None:
        """``found`` with its rock surfaces read off the collision pack, or ``None``.

        ``ground`` is the highest of the landscape and the cliff set, ``top`` the highest of
        everything, and every other up-facing surface on the line is a floor. ``None`` where
        there is no pack, or neither a hit nor landscape under the point.
        """
        index = self.rocks()
        if index is None:
            return None
        hits = index.hits(float(x_cm), float(y_cm))
        base = [] if found.terrain_m is None else [found.terrain_m]
        ground_hits = hits.standing(collision_pack.GROUND_KINDS)
        every = hits.standing()
        if not base and not every:
            return None
        ground = max(base + ground_hits) if base or ground_hits else found.ground_m
        top = max(v for v in (ground, *every) if v is not None)
        named = {round(v, 2) for v in (ground, top, found.terrain_m) if v is not None}
        return Surfaces(
            ground_m=None if ground is None else round(ground, 3),
            terrain_m=found.terrain_m,
            top_m=round(top, 3),
            provenance=found.provenance,
            floors=tuple(round(z, 3) for z in every if round(z, 2) not in named),
        )

    def cave_at(
        self,
        x_cm: float,
        y_cm: float,
        hint_z_cm: float | None = None,
        lowest_m: float | None = None,
    ) -> str:
        """``cave_masks.CAVE_VALUES`` at a point; ``none`` wherever no mask was generated."""
        found = self.caves()
        if found is None:
            return cave_masks.NONE
        return found.classify(x_cm, y_cm, hint_z_cm, lowest_m)

    def texel_reading(self, x_cm: float, y_cm: float) -> Reading | None:
        """The ground at the texel nearest a world coordinate, or ``None`` where nothing is known.

        ``None`` covers off the grid and a no-data texel alike: to the caller both say that
        nothing is known about that spot.
        """
        where = self.texel(x_cm, y_cm)
        if where is None:
            return None
        row, col = where
        raw = int(self.height_dm[row, col])
        if raw == NODATA:
            return None
        provenance = int(self.provenance_plane[row, col])
        water_m, quality = self._water_at(row, col)
        return Reading(
            z_m=raw / DM_PER_M,
            provenance=provenance,
            accuracy_m=self.accuracy_m(provenance),
            water_m=water_m,
            water_quality=quality,
            cave=self.cave_at(x_cm, y_cm),
        )

    def surfaces(
        self, x_cm: float, y_cm: float, *, bilinear: bool = True, top: bool = True
    ) -> Surfaces | None:
        """Every surface at a point, or ``None`` off the grid or where none has data."""
        x_cm, y_cm = float(x_cm), float(y_cm)
        where = self.texel(x_cm, y_cm)
        if where is None:
            return None
        ground = self._ground_m(self.height_dm, x_cm, y_cm, bilinear)
        top_plane = self.plane(TOP_NAME) if top else None
        rock_top = self._ground_m(top_plane, x_cm, y_cm, bilinear)
        terrain = self._terrain_m(x_cm, y_cm, bilinear)
        if ground is None and rock_top is None and terrain is None:
            return None
        row, col = ground[1] if ground is not None else where
        return Surfaces(
            ground_m=None if ground is None else round(ground[0], 3),
            terrain_m=None if terrain is None else round(terrain, 3),
            top_m=None if rock_top is None else round(rock_top[0], 3),
            provenance=int(self.provenance_plane[row, col]),
        )

    def height_at(
        self,
        x_cm: float,
        y_cm: float,
        *,
        surface: Surface = "ground",
        hint_z_cm: float | None = None,
        bilinear: bool = True,
    ) -> Reading | None:
        """The height at a point on one surface, or ``None`` where that surface knows nothing.

        With ``hint_z_cm`` the surface is chosen by ``Surfaces.pick`` instead of by
        ``surface``, and ``Reading.surface`` says which answered.
        """
        if surface not in SURFACES:
            raise ValueError(f"surface {surface!r} is not one of {', '.join(SURFACES)}")
        x_cm, y_cm = float(x_cm), float(y_cm)
        where = self.texel(x_cm, y_cm)
        found = self.surfaces(
            x_cm, y_cm, bilinear=bilinear, top=surface == "top" or hint_z_cm is not None
        )
        if where is None or found is None:
            return None
        cave = self.cave_at(x_cm, y_cm, hint_z_cm, min(v for _, v in found.candidates()))
        exact = None
        if surface != "terrain" and (
            hint_z_cm is not None or surface == "top" or self.near_rock(x_cm, y_cm)
        ):
            exact = self.collision_surfaces(x_cm, y_cm, found)
        cave_floor = False
        if hint_z_cm is not None:
            picked, found, cave_floor = _hinted(
                found, exact, hint_z_cm / 100.0, in_cave=cave == cave_masks.INSIDE
            )
            if picked is None:
                return None
            answered, z_m = picked
        else:
            found = exact or found
            answered, z_m = surface, found.on(surface)
            if z_m is None:
                return None
        if (
            answered == "ground"
            and found.provenance == PROV_LANDSCAPE
            and found.terrain_m is not None
            and abs(z_m - found.terrain_m) <= REFINE_M
        ):
            z_m = found.terrain_m
        provenance = PROV_LANDSCAPE if answered == "terrain" else found.provenance
        water_m, quality = self._water_at(*where)
        return Reading(
            z_m=z_m,
            provenance=provenance,
            accuracy_m=self.accuracy_m(provenance),
            water_m=water_m,
            water_quality=quality,
            surface=answered,
            terrain_z_m=found.terrain_m,
            ambiguous=self._ambiguous(found),
            cave=cave,
            cave_floor=cave_floor,
        )

    def _ambiguous(self, found: Surfaces) -> bool:
        """Whether the ground may be a roof: over the landscape by ``AMBIGUOUS_M``, or rock."""
        if not self.has_terrain:
            return found.provenance in PROV_CLIFF_VALUES
        if found.ground_m is None:
            return False
        if found.terrain_m is None:
            return found.provenance in PROV_CLIFF_VALUES
        return found.ground_m - found.terrain_m > AMBIGUOUS_M

    def _water_at(self, row: int, col: int) -> tuple[float | None, int]:
        """The water level over a texel and its ``waterq`` grade; ``(None, WATER_DRY)`` if dry."""
        water = self.water_raster()
        if water is None or int(water[row, col]) == NODATA:
            return None, WATER_DRY
        grades = self.water_quality_raster()
        quality = WATER_DRY if grades is None else int(grades[row, col])
        return int(water[row, col]) / DM_PER_M, quality

    def _ground_m(
        self, plane: RasterGrid | None, x_cm: float, y_cm: float, bilinear: bool
    ) -> _Sample | None:
        if plane is None:
            return None
        got = _sample(
            plane,
            NODATA,
            (self.x0_cm, self.y0_cm, self.spacing_cm),
            x_cm,
            y_cm,
            bilinear,
            BLEND_MAX_STEP_M * DM_PER_M,
        )
        return None if got is None else (got[0] / DM_PER_M, got[1])

    def _terrain_m(self, x_cm: float, y_cm: float, bilinear: bool) -> float | None:
        plane = self.plane(TERRAIN_NAME)
        tg = self.terrain_grid
        if plane is None or tg is None:
            return None
        got = _sample(
            plane,
            0,
            (tg["x0_cm"], tg["y0_cm"], tg["spacing_cm"]),
            x_cm,
            y_cm,
            bilinear,
            triangles=True,
        )
        if got is None:
            return None
        return (got[0] - tg["zero"]) / tg["units_per_m"] + tg["offset_m"]


def _hinted(
    found: Surfaces, exact: Surfaces | None, hint_m: float, *, in_cave: bool
) -> tuple[tuple[str, float] | None, Surfaces, bool]:
    """``(the pick, the surfaces it came from, whether it is a cave floor)`` for a hint.

    In a cave only a collision surface from ``AMBIGUOUS_M`` above the hint to
    ``CAVE_FLOOR_REACH_M`` under it is a floor; anything else is picked off the planes alone.
    """
    surfaces = exact or found
    picked = surfaces.pick(hint_m)
    if not in_cave:
        return picked, surfaces, False
    if (
        exact is not None
        and picked is not None
        and -AMBIGUOUS_M <= hint_m - picked[1] <= CAVE_FLOOR_REACH_M
    ):
        return picked, exact, True
    return found.pick(hint_m), found, False


def _sample(
    plane: RasterGrid,
    nodata: int,
    grid: tuple[float, float, float],
    x_cm: float,
    y_cm: float,
    bilinear: bool,
    max_step: float = math.inf,
    triangles: bool = False,
) -> _Sample | None:
    """Raw value at a point and the vertex that dominated it, or ``None``.

    Bilinear over the vertices with non-zero weight; if any of those is no data, or they span
    more than ``max_step`` (a cliff edge), the heaviest valid one answers alone.
    ``triangles`` reads the engine's landscape instead: two flat triangles per quad, split on
    the (r, c)-(r+1, c+1) diagonal.
    """
    x0, y0, spacing = grid
    fx, fy = (x_cm - x0) / spacing, (y_cm - y0) / spacing
    h, w = plane.shape
    if not (0 <= round(fx) < w and 0 <= round(fy) < h):
        return None
    if not bilinear:
        r, c = round(fy), round(fx)
        v = int(plane[r, c])
        return None if v == nodata else (float(v), (r, c))
    c0, r0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - c0, fy - r0
    block: list[list[int]] = np.asarray(plane[max(r0, 0) : r0 + 2, max(c0, 0) : c0 + 2]).tolist()
    taps: list[tuple[float, int, int, int]] = []
    for dr, dc, weight in _weights(tx, ty, triangles):
        if weight <= 0.0:
            continue
        r, c = r0 + dr, c0 + dc
        inside = 0 <= r < h and 0 <= c < w
        v = block[r - max(r0, 0)][c - max(c0, 0)] if inside else nodata
        taps.append((weight, r, c, v))
    good = [t for t in taps if t[3] != nodata]
    if not good:
        return None
    lead = max(good, key=lambda t: t[0])
    values = [t[3] for t in taps]
    if len(good) < len(taps) or max(values) - min(values) > max_step:
        return float(lead[3]), (lead[1], lead[2])
    return sum(t[0] * t[3] for t in taps), (lead[1], lead[2])


def _weights(tx: float, ty: float, triangles: bool) -> list[tuple[int, int, float]]:
    """``(row offset, column offset, weight)`` of each quad vertex at ``(tx, ty)`` inside it."""
    if not triangles:
        return [
            (dr, dc, wy * wx)
            for dr, wy in ((0, 1.0 - ty), (1, ty))
            for dc, wx in ((0, 1.0 - tx), (1, tx))
        ]
    if tx >= ty:
        return [(0, 0, 1.0 - tx), (0, 1, tx - ty), (1, 1, ty)]
    return [(0, 0, 1.0 - ty), (1, 0, ty - tx), (1, 1, tx)]
