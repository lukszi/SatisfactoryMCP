"""Perched water below a drop: a lake's box reaching past its lip over the basin below.

docs/spatial-and-map.md section 38. Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.palette.perched import LIP_DROP_M, perched_levels
from satisfactory_mcp.domain.spatial import heightfield as hf

ROWS, COLS = 160, 200
BOX_DM, SWAMP_DM = 948, -167
LAKE = (slice(20, 140), slice(20, 100))
BASIN = (slice(40, 120), slice(100, 140))
SWAMP = (slice(30, 130), slice(140, 190))


def _field(water_dm, grades, height_dm):
    return SimpleNamespace(
        _water_raster=lambda: water_dm,
        _water_quality_raster=lambda: grades,
        _height_dm=height_dm,
        x0_cm=0.0,
        y0_cm=0.0,
        spacing_cm=100.0,
    )


def _fall(swamp=True):
    """The wide fall at (1784, 559) in miniature: a lake 5.8 m deep under its box at 94.8 m,
    high cliffs all round, and past its lip the basin 113 m below, which the box also covers.
    The basin opens east onto a swamp at -16.7 m, or with ``swamp`` off onto dry ground
    standing half a metre above its bed."""
    height = np.full((ROWS, COLS), 1100, np.int16)
    water = np.full((ROWS, COLS), hf.NODATA, np.int16)
    height[LAKE], water[LAKE] = 890, BOX_DM
    height[BASIN], water[BASIN] = -181, BOX_DM
    height[SWAMP] = -190 if swamp else -176
    if swamp:
        water[SWAMP] = SWAMP_DM
    grades = np.where(water != hf.NODATA, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return water, grades, height


def test_the_basin_below_a_lip_takes_the_level_of_the_water_it_joins():
    water, grades, height = _fall()
    out, meta = perched_levels(_field(water, grades, height))
    assert (out[BASIN] == SWAMP_DM).all(), "the basin stands at the swamp's level"
    assert (out[LAKE] == BOX_DM).all(), "the lake above the lip keeps its box level"
    assert (out[SWAMP] == SWAMP_DM).all()
    assert meta["bodies"] == 1 and meta["largest"][0]["below_a_drop"]
    assert meta["bodies_cut_at_a_drop"] == 1


def test_the_whole_body_would_not_spill_so_the_basin_is_judged_on_its_own():
    """Before the cut the lake's high banks outvoted the basin's swamp: nothing moved."""
    water, grades, height = _fall()
    flat = height.copy()
    flat[BASIN] = 880
    out, meta = perched_levels(_field(water, grades, flat))
    assert np.array_equal(out, water)
    assert meta["bodies"] == 0 and meta["bodies_cut_at_a_drop"] == 0


def test_a_basin_joining_no_water_takes_its_own_low_shoreline():
    water, grades, height = _fall(swamp=False)
    out, _meta = perched_levels(_field(water, grades, height))
    basin = out[BASIN].astype(np.int32)
    assert (basin <= -176).all() and (basin >= -181).all(), "between its bed and its banks"
    assert (out[LAKE] == BOX_DM).all()


def test_a_pit_in_a_lake_whose_level_is_right_is_left_alone():
    """An underwater cliff: the deep side is cut off by the drop, but every bank it has is
    the lake at the same level, so it is no basin below a fall."""
    height = np.full((ROWS, COLS), 1100, np.int16)
    water = np.full((ROWS, COLS), hf.NODATA, np.int16)
    height[LAKE], water[LAKE] = 900, BOX_DM
    pit = (slice(60, 100), slice(40, 80))
    height[pit] = BOX_DM - round(3 * LIP_DROP_M * hf.DM_PER_M)
    grades = np.where(water != hf.NODATA, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    out, meta = perched_levels(_field(water, grades, height))
    assert np.array_equal(out, water)
    assert meta["bodies"] == 0 and meta["bodies_cut_at_a_drop"] == 1
