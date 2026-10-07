"""Rock overhangs: under a rock's top, the underside of its layer and the surface below that.

The direct pass rasterises the rocks' upward faces, the top. Two more passes over the band
find, under that top, the highest downward face (the underside of an overhang, ``under``)
and the highest upward face (``floor``). The draw takes a rock as floating where its
underside clears both the floor and the ground (``OVERHANG_CLEAR_M``). Why the rule has this
shape: docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np

from mapgen.cache import DIRECT_FLOOR_NAME, DIRECT_UNDER_NAME
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.terrain.rasters import (
    BandPlanes,
    Geometry,
    PreparedPlacement,
    fold_band,
    rasterise_direct_band,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid

__all__ = [
    "OVERHANG_CLEAR_M",
    "SAME_SURFACE_CM",
    "overhang_rasters",
    "rasterise_direct_planes",
    "reduce_floor",
    "reduce_under",
]

#: Faces within this of the top are the top's own surface, not another under it.
SAME_SURFACE_CM = 25.0
#: A downward face counts as an underside only this far over its placement's lowest point:
#: below it, it is the rock's foot. The draw's own gap, ``OVERHANG_CLEAR_M``, is no smaller.
UNDERSIDE_RISE_CM = 100.0
#: The gap under a rock's underside that lets light through: less is a closed rock.
OVERHANG_CLEAR_M = 2.0


def _faces(entry: PreparedPlacement, geometry: Geometry, y_lo: float,
           y_hi: float) -> Iterator[tuple[F64Grid, F64Grid, float]]:  # fmt: skip
    """A placement's triangles reaching ``[y_lo, y_hi]``, world cm, with each one's upward
    normal (its sign the facing, 0 where the winding is unknown) and the placement's lowest Z."""
    if entry.y_max_cm < y_lo or entry.y_min_cm > y_hi:
        return
    verts, tris = geometry[entry.mesh]
    world = (verts * entry.scale) @ entry.matrix + entry.offset
    ty = world[:, 1][tris]
    tris = tris[(ty.max(1) >= y_lo) & (ty.min(1) <= y_hi)]
    if not tris.size:
        return
    tri = world[tris]
    if not entry.facing:
        up = np.zeros(len(tri))
    else:
        up = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2] * entry.facing
    yield tri, up, float(world[:, 2].min())


def _box_count(mask: BoolMask) -> F64Grid:
    """The running count of ``mask`` over rows and columns, padded: a box sum in four reads."""
    counts = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), np.float64)
    counts[1:, 1:] = mask.cumsum(0).cumsum(1)
    return counts


def _reaches(entry_tri: F64Grid, counts: F64Grid, raster: MaxZRaster) -> bool:
    """Whether the triangles' box over ``raster``'s grid holds a texel the box sum counts."""
    fx = (entry_tri[:, :, 0] - raster.origin_x_cm) / raster.scale
    fy = (entry_tri[:, :, 1] - raster.origin_y_cm) / raster.scale
    c0, c1 = int(np.floor(fx.min())) - 1, int(np.ceil(fx.max())) + 2
    r0, r1 = int(np.floor(fy.min())) - 1, int(np.ceil(fy.max())) + 2
    r0, c0 = max(r0, 0), max(c0, 0)
    r1, c1 = min(r1, counts.shape[0] - 1), min(c1, counts.shape[1] - 1)
    if r0 >= r1 or c0 >= c1:
        return False
    return bool(counts[r1, c1] - counts[r0, c1] - counts[r1, c0] + counts[r0, c0] > 0)


def overhang_rasters(prepared: Sequence[PreparedPlacement], geometry: Geometry, top: F32Grid,
                     grid: tuple[float, float, float]) -> tuple[F32Grid, F32Grid]:  # fmt: skip
    """``(under, floor)`` over the grid of ``top`` (the upward faces' max-Z, cm), NaN where none.

    ``under`` is the highest downward face below the top that rises ``UNDERSIDE_RISE_CM``
    over its placement's lowest point; ``floor`` the highest upward face below the top, read
    only for placements that reach a texel with an ``under``.
    """
    x0_cm, y0_cm, step_cm = grid
    rows, cols = top.shape
    ceiling = np.where(np.isfinite(top), top - np.float32(SAME_SURFACE_CM), -np.inf)
    ceiling = ceiling.astype(np.float32)
    y_hi = y0_cm + rows * step_cm
    under_raster = MaxZRaster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, ceiling=ceiling)
    for entry in prepared:
        for tri, up, lowest in _faces(entry, geometry, y0_cm, y_hi):
            down = (up < 0) & (tri[:, :, 2].max(1) > lowest + UNDERSIDE_RISE_CM)
            if down.any():
                under_raster.add(tri[down], 1)
    under = under_raster.result()[0]
    floor = np.full(top.shape, np.nan, np.float32)
    if not np.isfinite(under).any():
        return under, floor
    counts = _box_count(np.isfinite(under))
    floor_raster = MaxZRaster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, ceiling=ceiling)
    for entry in prepared:
        for tri, up, _lowest in _faces(entry, geometry, y0_cm, y_hi):
            facing = (up > 0) if entry.facing else (up >= 0)
            if facing.any() and _reaches(tri[facing], counts, floor_raster):
                floor_raster.add(tri[facing], 1)
    return under, floor_raster.result()[0]


def reduce_under(sub: F32Grid, rows: int, cols: int, subsamples: int) -> F32Grid:
    """An underside folded onto the output grid: the lowest of its sub-samples, NaN unless
    every one has an underside, so a pixel half over the ground stays closed."""
    if subsamples == 1:
        return sub
    block = sub.reshape(rows, subsamples, cols, subsamples)
    return block.min((1, 3)).astype(np.float32)


def reduce_floor(sub: F32Grid, rows: int, cols: int, subsamples: int) -> F32Grid:
    """A floor folded onto the output grid: the highest of its sub-samples, NaN where none."""
    if subsamples == 1:
        return sub
    block = sub.reshape(rows, subsamples, cols, subsamples)
    have = np.isfinite(block).any((1, 3))
    highest = np.where(np.isfinite(block), block, -np.inf).max((1, 3))
    return np.where(have, highest, np.nan).astype(np.float32)


def rasterise_direct_planes(prepared: Sequence[PreparedPlacement], geometry: Geometry,
                            x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int,
                            subsamples: int) -> BandPlanes:  # fmt: skip
    """One band of the direct cache: ``rasterise_direct_band``'s planes, and the overhangs'."""
    sub_z, sub_source = rasterise_direct_band(prepared, geometry, x0_cm, y0_cm, scale_cm, rows,
                                              cols, subsamples, with_source=True)  # fmt: skip
    planes = fold_band((sub_z, sub_source), rows, cols, subsamples)
    step = scale_cm / subsamples
    under, floor = overhang_rasters(prepared, geometry, sub_z, (x0_cm, y0_cm, step))
    planes[DIRECT_UNDER_NAME] = reduce_under(under, rows, cols, subsamples)
    planes[DIRECT_FLOOR_NAME] = reduce_floor(floor, rows, cols, subsamples)
    return planes
