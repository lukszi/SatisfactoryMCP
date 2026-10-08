"""A block's ambient occlusion in the bake: the ground's, and what the trees add to it.

``lighting/stage.py`` bakes the block; the occlusion itself is ``lighting/occlusion.py``.
docs/map/light-and-crowns.md section 29, "The trees in the light".
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np

from mapgen.lighting.atlas import BakedHorizons
from mapgen.lighting.horizon import crown_surface
from mapgen.lighting.light_tiles import downsample, optional_array, padded_window, work_array
from mapgen.lighting.model import AO_CELL
from mapgen.lighting.occlusion import ao_margin, occlusion, relative_occlusion
from mapgen.lighting.spans.slabs import SLAB_DIR_NAME, SlabStore
from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["TreesAo", "ambient_cell", "block_occlusion"]


class TreesAo(NamedTuple):
    """A block's ambient occlusion under the trees, at full resolution: of the canopy top the
    painted layer draws, and what it takes beyond the ground's own."""

    visible: F32Grid
    relative: F32Grid


def block_occlusion(
    work: Path, block: tuple[int, int, int], spacing_m: float, step_rows: int
) -> tuple[F32Grid, TreesAo | None]:
    """The block's ambient occlusion: the ground's, and under the trees' (None without an
    occluder), ``step_rows`` at a time. What floats occludes nothing: the arches and
    overhangs are left out of the heights that occlude, as the sky view's spans let the sky
    in beneath them."""
    r0, c0, n = block
    m = ao_margin(spacing_m)
    z = work_array(work, "z", np.float32, "r")
    top = optional_array(work, "occluder", np.float32)
    cover = optional_array(work, "occluder_cover", np.uint8)
    slabs = SlabStore(work / SLAB_DIR_NAME)
    ground = np.empty((n, n), np.float32)
    visible = None if top is None else np.empty((n, n), np.float32)
    for start in range(0, n, step_rows):
        rows = slice(start, min(start + step_rows, n))
        window = (r0 + rows.start - m, r0 + rows.stop + m, c0 - m, c0 + n + m)
        heights = padded_window(z, *window)
        found = slabs.full(window, heights)
        solid = heights if found is None else found[0]
        drawn = heights[m : heights.shape[0] - m, m : heights.shape[1] - m]
        ground[rows] = occlusion(solid, spacing_m, drawn)
        if top is not None and visible is not None:
            share = None if cover is None else padded_window(cover, *window, 0.0) / np.float32(255)
            crowns = padded_window(top, *window, np.nan)
            canopy = crown_surface(solid, crowns, share)
            seen = crown_surface(heights, crowns, share)
            visible[rows] = occlusion(
                canopy, spacing_m, seen[m : seen.shape[0] - m, m : seen.shape[1] - m]
            )
    if visible is None:
        return ground, None
    return ground, TreesAo(visible, relative_occlusion(visible, ground))


def ambient_cell(horizons: BakedHorizons, relative: F32Grid) -> None:
    """The trees' occlusion as the atlas's cell, at half resolution, and for the coarser
    levels at quarter."""
    half = downsample(relative)
    horizons.atlas[AO_CELL] = np.round(np.clip(half, 0, 1) * 255)
    horizons.quarter[..., AO_CELL] = np.round(np.clip(downsample(half), 0, 1) * 255)
