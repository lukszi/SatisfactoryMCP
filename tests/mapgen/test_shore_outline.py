"""The open sea's shore past the landscape, drawn along its smoothed outline.

docs/map/renders.md, "The fill's shore". Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.palette.water.open_sea import open_sea
from mapgen.palette.water.outline import SHORE_CLEAR_M
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.domain.spatial import heightfield as hf

OCEAN_DM = round(OCEAN_LEVEL_M * hf.DM_PER_M)
N = 200


def _shore(ground_dm=OCEAN_DM + 3, province=hf.PROV_CLIFF, slope=0.3):
    """Ground at ``ground_dm`` west of a straight shore running ``slope`` texels east a row,
    cut into 1 m texels, and measured sea 1.5 m deep east of it."""
    cols = np.arange(N)[None, :]
    dry = cols < 60 + slope * np.arange(N)[:, None]
    height = np.where(dry, ground_dm, OCEAN_DM - 15).astype(np.int16)
    water = np.where(dry, hf.NODATA, OCEAN_DM).astype(np.int16)
    grades = np.where(dry, hf.WATER_DRY, hf.WATER_MEASURED).astype(np.uint8)
    field = SimpleNamespace(
        height_dm=height,
        provenance_plane=np.full(height.shape, province, np.uint8),
        water_raster=lambda: water,
        water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100,
        y0_cm=BOUNDS_M["y_min_m"] * 100,
        spacing_cm=100.0,
        width=N,
        height=N,
    )
    heights = height.astype(np.float32)
    sea = open_sea(field, (heights, None), None, np.zeros(height.shape, np.uint8), OCEAN_LEVEL_M)
    return sea, height, heights, dry


def _waterline(heights):
    """Where each row's height, read linearly between texel centres, crosses the sea's level."""
    out = []
    for row in heights[20:-20]:
        above = row - OCEAN_DM
        col = int(np.flatnonzero((above[:-1] >= 0) & (above[1:] < 0))[0])
        out.append(col + above[col] / (above[col] - above[col + 1]))
    return np.array(out)


def _wobble(line):
    """How far the waterline strays from the straight line fitted through it, in metres."""
    rows = np.arange(len(line))
    return float(np.abs(line - np.polyval(np.polyfit(rows, line, 1), rows)).max())


def test_the_shore_is_drawn_along_its_smoothed_outline_not_texel_by_texel():
    sea, height, heights, _dry = _shore()
    assert _wobble(_waterline(height.astype(np.float32))) > 0.4, "before: 1 m stair-steps"
    assert _wobble(_waterline(heights)) < 0.1, "after: a smooth line"
    assert sea.meta["shore_outline_texels"] > 0


def test_no_texel_changes_side_and_texels_off_the_outline_keep_their_height():
    sea, height, heights, dry = _shore()
    clear = SHORE_CLEAR_M * hf.DM_PER_M
    assert np.array_equal(sea.grades == hf.WATER_DRY, dry), "the masks are as decided"
    assert (heights[dry] >= OCEAN_DM + clear - 1e-3).all()
    assert (heights[~dry] <= OCEAN_DM - clear + 1e-3).all()
    moved = heights != height
    assert moved.sum() == sea.meta["shore_outline_texels"]
    far = np.abs(np.arange(N)[None, :] - (60 + 0.3 * np.arange(N)[:, None])) > 4
    assert not moved[far].any(), "only texels within a few metres of the outline move"
    assert np.abs(heights - height).max() <= 1.5 * hf.DM_PER_M


def test_a_cliff_and_the_landscape_keep_their_shore():
    _sea, height, heights, dry = _shore(ground_dm=OCEAN_DM + 200)
    assert np.array_equal(heights[dry], height[dry]), "a 20 m cliff is not cut down"
    sea, height, heights, _dry = _shore(province=hf.PROV_LANDSCAPE)
    assert np.array_equal(heights, height), "the landscape's own shore is measured"
    assert sea.meta["shore_outline_texels"] == 0
