"""A block's horizon atlas made a cell at a time, with the planes the default sun reads.

No ``(cells, half_px, half_px)`` float stack: each cell is encoded as it is marched, and a
direction's cells are refolded together for the coarser levels (``refold.refold``). The march
takes a pixel more on each side, which only the default sun's planes keep, so they upsample
from their neighbours past the block's edge (``light_tiles.upsampled``).
docs/map/light-and-crowns.md section 29, "Strips, memory and workers".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.lighting.horizon import HORIZON_DIRS, encode_horizon
from mapgen.lighting.light_tiles import encode_linear
from mapgen.lighting.model import CROWN_CELL, HZ_CELLS, sun_cells
from mapgen.lighting.refold import TREE_GROUPS, path_elevations, refold
from mapgen.lighting.spans.bake import (
    BlockSpans,
    Cell,
    canopy_cells,
    horizon_cells,
    plain_bands,
    shade_cells,
)
from mapgen.lighting.spans.holes import Holes
from mapgen.lighting.spans.march import Bands
from mapgen.lighting.sun import DEFAULT_SUN
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid

__all__ = ["BakedHorizons", "bake_horizons"]


class BakedHorizons(NamedTuple):
    """A block's horizons: the atlas bytes, the coarser levels' source, the default sun's
    cells (the ground's, then the trees' together over it) and, by direction, the canopy's
    own horizon toward it, and the bands the default sun's cells were marched with, all but
    the first two with a ring; and where a band is folded into any atlas cell."""

    atlas: U8Grid
    quarter: U8Grid
    sun: list[F32Grid]
    canopy: list[F32Grid]
    bands: dict[int, Bands]
    folded: BoolMask


def _quarter(quarter: U8Grid, d: int, ground: F32Grid, trees: list[F32Grid | None]) -> None:
    """Direction ``d``'s horizons for the coarser levels, at quarter resolution: its ground
    cell, then its trees' cells when the block has any, 0 for a group it has not
    (``refold.refold``)."""
    planes = [ground]
    if any(plane is not None for plane in trees):
        planes += [np.zeros_like(ground) if plane is None else plane for plane in trees]
    found = encode_linear(refold(np.stack(planes, -1), path_elevations()[d : d + 1]))
    for i in range(len(planes)):
        quarter[..., d + i * HORIZON_DIRS] = found[..., i]


def bake_horizons(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    spans: BlockSpans,
    half_px: int,
    march: bool,
    holes: Holes | None = None,
) -> BakedHorizons:
    """The atlas bytes, the coarser levels' source and the default sun's planes, a cell at a
    time. The trees' ambient occlusion cell is left to the block (``stage.bake_block``)."""
    hz_u8 = np.zeros((HZ_CELLS, half_px, half_px), np.uint8)
    quarter = np.zeros((half_px // 2, half_px // 2, HZ_CELLS), np.uint8)
    blank = np.zeros((half_px + 2, half_px + 2), np.float32)
    sun = [blank] * (CROWN_CELL + HORIZON_DIRS)
    canopy = sun[:HORIZON_DIRS]
    bands: dict[int, Bands] = {}
    keep, shaded = sun_cells(DEFAULT_SUN[0]), shade_cells(DEFAULT_SUN[0])
    wanted = {k for k in (*keep, *shaded) if k < CROWN_CELL}
    cells = horizon_cells(z_half, halo - 1, spacing_m, spans, holes, wanted) if march else iter(())
    folded = np.zeros((half_px, half_px), bool)
    group = _empty_group()
    direction = 0
    for cell in cells:
        if cell.k < CROWN_CELL:  # a direction's ground cell comes first, then its trees'
            if group[0] is not None:
                _quarter(quarter, direction, group[0], group[1:])
            group, direction = _empty_group(), cell.k
        _store(cell, hz_u8, group, folded)
        if cell.k in keep:
            sun[cell.k] = cell.deg
        if cell.k in shaded and cell.bands is not None:
            bands[cell.k] = cell.bands
    if group[0] is not None:
        _quarter(quarter, direction, group[0], group[1:])
    trees: set[int] = set()
    if march:
        trees = {k - CROWN_CELL for k in (*keep, *shaded) if k >= CROWN_CELL}
    for d, whole, marched in canopy_cells(z_half, halo - 1, spacing_m, spans, holes, trees):
        sun[CROWN_CELL + d] = canopy[d] = whole
        if CROWN_CELL + d in shaded:
            bands[CROWN_CELL + d] = marched
    if bands:
        bands.update({k: plain_bands(sun[k]) for k in shaded if k not in bands and march})
    return BakedHorizons(hz_u8, quarter, sun, canopy, bands, folded)


def _empty_group() -> list[F32Grid | None]:
    """A direction's cells as they come: its ground's, then its trees' groups."""
    return [None for _ in range(TREE_GROUPS + 1)]


def _store(cell: Cell, hz_u8: U8Grid, group: list[F32Grid | None], folded: BoolMask) -> None:
    """A marched cell into the atlas bytes, its direction's group, and where it folds a band."""
    deg = cell.deg[1:-1, 1:-1]
    hz_u8[cell.k] = encode_horizon(deg)
    group[cell.k // HORIZON_DIRS] = deg
    if cell.band_in is not None:
        folded |= cell.band_in[1:-1, 1:-1]
