"""The top raster: arches and foliage boulders on the render's grid, the arches kept apart.

The arches are rasterised for their top (max-Z) and their underside (the next surface down,
else min-Z), and the holes
their open mesh edges leave are filled (``terrain/archfill.py``); the boulders alone are the
surface the arches stand over. docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.cache import (
    ARCH_COVERAGE_NAME,
    ARCH_UNDER_NAME,
    DIRECT_COVERAGE_NAME,
    DIRECT_Z_NAME,
    TOP_SOLID_COVERAGE_NAME,
    TOP_SOLID_Z_NAME,
)
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.terrain.archfill import column_pieces, fill_arch_holes
from mapgen.terrain.overhangs import SAME_SURFACE_CM
from mapgen.terrain.rasters import (
    TOP_FOLIAGE_BATCH,
    BandPlanes,
    TopItems,
    add_placements,
    placed,
    reduce_direct,
)
from satisfactory_mcp.core.arrays import F32Grid

__all__ = [
    "FILL_HALO_M",
    "TopBand",
    "arch_rasters",
    "rasterise_top_band",
    "rasterise_top_planes",
    "top_band_planes",
]

#: Rows the fill reads past a band's edges, so a hole cut by the edge is filled as a whole
#: sheet fills it: 32 px at full size, wider than any hole the fill closes.
FILL_HALO_M = 7.3

#: Columns of no arch that split a band into the pieces the fill runs on.
_PIECE_GAP_M = 2 * FILL_HALO_M


class TopBand(NamedTuple):
    """One band of the top raster on its sub-sampled grid, max-Z in cm, NaN where none.

    ``top`` is the arches (filled) and the boulders, ``solid`` the boulders alone, and
    ``under`` the arches' underside.
    """

    top: F32Grid
    solid: F32Grid
    under: F32Grid


def arch_rasters(items: TopItems, origin: tuple[float, float, float], rows: int, cols: int,
                 row0: int = 0) -> tuple[F32Grid, F32Grid]:  # fmt: skip
    """The arches' top and underside over ``rows`` from ``row0`` of the grid whose first
    texel is ``origin`` (x, y, step in cm), NaN where none.

    The top is ``add_placements``' raster of the arches. The underside is the highest arch
    surface ``SAME_SURFACE_CM`` or more below it: under a deck that crosses another, its own
    underside, never the lower deck's. Where none is, it is the lowest surface, the same
    triangles rasterised upside down.
    """
    x0_cm, y0_cm, step_cm = origin
    high = MaxZRaster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, row0=row0)
    low = MaxZRaster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, row0=row0)
    y_lo, y_hi = y0_cm + row0 * step_cm, y0_cm + (row0 + rows) * step_cm
    add_placements(high, items.arches, items.shapes, y_lo, y_hi)
    top = high.result()[0]
    ceiling = np.where(np.isfinite(top), top - np.float32(SAME_SURFACE_CM), -np.inf)
    below = MaxZRaster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5, row0=row0,
                       ceiling=ceiling.astype(np.float32))  # fmt: skip
    for entry in items.arches:
        found = placed(entry, items.shapes, y_lo, y_hi)
        if found is not None:
            world, tris = found
            low.add(world[tris] * np.array([1.0, 1.0, -1.0], world.dtype), entry.mesh_id + 1)
            below.add(world[tris], entry.mesh_id + 1)
    under = below.result()[0]
    return top, np.where(np.isfinite(under), under, -low.result()[0]).astype(np.float32)


def _boulders(items: TopItems, x0_cm: float, y0_cm: float, step_cm: float, rows: int,
              cols: int) -> F32Grid:  # fmt: skip
    raster = MaxZRaster(cols, rows, x0_cm, y0_cm, step_cm, sample=0.5)
    y_hi = y0_cm + rows * step_cm
    for mesh, group in items.boulders.items():
        verts, tris = items.shapes[mesh]
        picked = group.mats[(group.y_hi_cm >= y0_cm) & (group.y_lo_cm <= y_hi)]
        for start in range(0, len(picked), TOP_FOLIAGE_BATCH):
            chunk = picked[start : start + TOP_FOLIAGE_BATCH]
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), 1)
    return raster.result()[0]


def _filled(top: F32Grid, under: F32Grid, spacing_m: float) -> tuple[F32Grid, F32Grid]:
    """The fill run on each column piece that holds an arch; the same as on the whole band."""
    top, under = top.copy(), under.copy()
    for c0, c1 in column_pieces(np.isfinite(top), int(np.ceil(_PIECE_GAP_M / spacing_m))):
        got = fill_arch_holes(top[:, c0:c1], under[:, c0:c1], spacing_m)
        top[:, c0:c1], under[:, c0:c1] = got.top, got.under
    return top, under


def rasterise_top_band(items: TopItems, x0_cm: float, y0_cm: float, scale_cm: float, rows: int,
                       cols: int, subsamples: int) -> TopBand:  # fmt: skip
    """One band of arches and boulders on the render's pixel centres, sub-sampled.

    The arches are drawn ``FILL_HALO_M`` past the band's edges for the fill, on the band's
    own grid, then cut back: where nothing is filled the band is the plain raster's.
    """
    step = scale_cm / subsamples
    spacing_m = step / 100.0
    halo = int(np.ceil(FILL_HALO_M / spacing_m))
    sub_rows, sub_cols = rows * subsamples, cols * subsamples
    top, under = arch_rasters(items, (x0_cm, y0_cm, step), sub_rows + 2 * halo, sub_cols,
                              row0=-halo)  # fmt: skip
    top, under = _filled(top, under, spacing_m)
    top, under = top[halo : halo + sub_rows], under[halo : halo + sub_rows]
    solid = _boulders(items, x0_cm, y0_cm, step, sub_rows, sub_cols)
    return TopBand(np.fmax(top, solid), solid, under)


def rasterise_top_planes(items: TopItems, x0_cm: float, y0_cm: float, scale_cm: float,
                         rows: int, cols: int, subsamples: int) -> BandPlanes:  # fmt: skip
    """One band of the top cache: ``rasterise_top_band`` folded onto the output grid."""
    band = rasterise_top_band(items, x0_cm, y0_cm, scale_cm, rows, cols, subsamples)
    return top_band_planes(band, rows, cols, subsamples)


def top_band_planes(band: TopBand, rows: int, cols: int, subsamples: int) -> BandPlanes:
    """A ``TopBand`` folded onto the output grid as the top cache's planes."""
    top_z, top_cov = reduce_direct(band.top, rows, cols, subsamples)
    solid_z, solid_cov = reduce_direct(band.solid, rows, cols, subsamples)
    under_z, under_cov = reduce_direct(band.under, rows, cols, subsamples)
    return {
        DIRECT_Z_NAME: top_z,
        DIRECT_COVERAGE_NAME: top_cov,
        TOP_SOLID_Z_NAME: solid_z,
        TOP_SOLID_COVERAGE_NAME: solid_cov,
        ARCH_UNDER_NAME: under_z,
        ARCH_COVERAGE_NAME: under_cov,
    }
