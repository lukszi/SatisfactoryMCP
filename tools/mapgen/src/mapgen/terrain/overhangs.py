"""Rock overhangs: under a rock's top, the underside of its layer and the surface below that.

The direct pass rasterises the rocks' upward faces, the top. Two more passes over the band
find, under that top, the highest downward face (the underside of an overhang, ``under``)
and the highest upward face (``floor``). The draw takes a rock as floating where its
underside clears both the floor and the ground (``OVERHANG_CLEAR_M``). Why the rule has this
shape: docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from mapgen.cache import DIRECT_FLOOR_NAME, DIRECT_UNDER_NAME
from mapgen.terrain.maxz.faces import extent
from mapgen.terrain.maxz.raster import max_z_raster
from mapgen.terrain.rasters import (
    Geometry,
    PlacedFaces,
    PreparedPlacement,
    band_placements,
    draw_up,
    placement_world,
)
from mapgen.terrain.rasters_banded import BandPlanes, fold_band
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, FloatGrid, I32Grid, I64Grid

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


def _box_count(mask: BoolMask) -> F64Grid:
    """The running count of ``mask`` over rows and columns, padded: a box sum in four reads."""
    counts = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), np.float64)
    counts[1:, 1:] = mask.cumsum(0).cumsum(1)
    return counts


def _reaches(
    world: FloatGrid,
    tris: I64Grid,
    rows: I32Grid,
    counts: F64Grid,
    grid: tuple[float, float, float],
) -> bool:
    """Whether the triangles' box over the grid holds a texel the box sum counts."""
    x_lo, x_hi, y_lo, y_hi = extent(world, tris, rows, grid)
    c0, c1 = int(np.floor(x_lo)) - 1, int(np.ceil(x_hi)) + 2
    r0, r1 = int(np.floor(y_lo)) - 1, int(np.ceil(y_hi)) + 2
    r0, c0 = max(r0, 0), max(c0, 0)
    r1, c1 = min(r1, counts.shape[0] - 1), min(c1, counts.shape[1] - 1)
    if r0 >= r1 or c0 >= c1:
        return False
    return bool(counts[r1, c1] - counts[r0, c1] - counts[r1, c0] + counts[r0, c0] > 0)


def overhang_rasters(
    prepared: Sequence[PreparedPlacement],
    geometry: Geometry,
    top: F32Grid,
    grid: tuple[float, float, float],
) -> tuple[F32Grid, F32Grid]:
    """``(under, floor)`` over the grid of ``top`` (the upward faces' max-Z, cm), NaN where none.

    ``under`` is the highest downward face below the top that rises ``UNDERSIDE_RISE_CM``
    over its placement's lowest point; ``floor`` the highest upward face below the top, read
    only for placements that reach a texel with an ``under``.
    """
    y_hi = grid[1] + top.shape[0] * grid[2]
    placed = list(band_placements(prepared, geometry, grid[1], y_hi, UNDERSIDE_RISE_CM))
    return _overhangs(placed, geometry, top, grid)


def _overhangs(
    placed: Sequence[PlacedFaces],
    geometry: Geometry,
    top: F32Grid,
    grid: tuple[float, float, float],
) -> tuple[F32Grid, F32Grid]:
    """``overhang_rasters`` of the placements over the band, already sorted into faces."""
    x0_cm, y0_cm, step_cm = grid
    rows, cols = top.shape
    ceiling = np.where(np.isfinite(top), top - np.float32(SAME_SURFACE_CM), -np.inf)
    ceiling = ceiling.astype(np.float32)
    under_raster = max_z_raster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, ceiling=ceiling)
    for item in placed:
        if item.faces.down.size:
            world = placement_world(item.entry, geometry)
            under_raster.add_indexed(world, geometry[item.entry.mesh][1], 1, item.faces.down)
    under = under_raster.result()[0]
    del under_raster
    floor = np.full(top.shape, np.nan, np.float32)
    if not np.isfinite(under).any():
        return under, floor
    counts = _box_count(np.isfinite(under))
    floor_raster = max_z_raster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, ceiling=ceiling)
    for item in placed:
        up, tris = item.faces.up, geometry[item.entry.mesh][1]
        if not up.size:
            continue
        world = placement_world(item.entry, geometry)
        if _reaches(world, tris, up, counts, grid):
            floor_raster.add_indexed(world, tris, 1, up)
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


def rasterise_direct_planes(
    prepared: Sequence[PreparedPlacement],
    geometry: Geometry,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
) -> BandPlanes:
    """One band of the direct cache: ``rasterise_direct_band``'s planes, and the overhangs'.

    Each placement over the band is sorted into faces once for the three passes, top,
    underside and floor; again for the overhangs only where sub-sampling moves the band's
    lower edge.
    """
    y_hi = y0_cm + rows * scale_cm
    placed = list(band_placements(prepared, geometry, y0_cm, y_hi, UNDERSIDE_RISE_CM))
    step = scale_cm / subsamples
    raster = max_z_raster(cols * subsamples, rows * subsamples, x0_cm, y0_cm, step, sample=0.5)
    draw_up(raster, placed, geometry)
    sub_z, sub_source, _density = raster.result()
    del raster
    planes = fold_band((sub_z, sub_source), rows, cols, subsamples)
    sub_hi = y0_cm + rows * subsamples * step
    if sub_hi != y_hi:
        placed = list(band_placements(prepared, geometry, y0_cm, sub_hi, UNDERSIDE_RISE_CM))
    under, floor = _overhangs(placed, geometry, sub_z, (x0_cm, y0_cm, step))
    planes[DIRECT_UNDER_NAME] = reduce_under(under, rows, cols, subsamples)
    planes[DIRECT_FLOOR_NAME] = reduce_floor(floor, rows, cols, subsamples)
    return planes
