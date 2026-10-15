"""Water a fall pours from stands at its lip, not on the cliff its box runs on over.

docs/map/water.md section 38, "Held at a lip". Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.palette.water.lips import fall_lips, hold_at_lips
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.palette.water.surface import water_surfaces
from satisfactory_mcp.domain.spatial import heightfield as hf

N = 120
#: The lake's bed, its box's top, and its fall's lip, in decimetres; the cliff runs east of
#: column ``CLIFF`` and the lip stands 10 m out from its top, as the game places them.
BED_DM, BOX_DM, LIP_DM = 70, 120, 100
CLIFF, LIP_COL = 70, 80


def _field(water_dm, grades, height_dm):
    return SimpleNamespace(
        water_raster=lambda: water_dm,
        water_quality_raster=lambda: grades,
        height_dm=height_dm,
        x0_cm=0.0,
        y0_cm=0.0,
        spacing_cm=100.0,
    )


def _lip(z_dm=LIP_DM, x_m=float(LIP_COL)):
    """A 60 m lip across the middle rows, pouring east, its top modules 27 m long."""
    record = {
        "x": x_m,
        "y": N / 2,
        "z": z_dm / hf.DM_PER_M,
        "along": [0.0, 1.0],
        "out": [1.0, 0.0],
        "width_m": 60.0,
        "height_m": 40.0,
        "top_len_m": 27.4,
        "splash": [],
    }
    return fall_lips([record])


def _lake_over_its_fall():
    """A lake on a 7 m bed, open to the east, where the ground drops off a cliff to -30 m.
    Its box, at 12 m, runs on over the cliff to the lake's east end. The artwork leaves its
    shallows dry, half a metre over the bed, as round the island at (2040, -1880)."""
    rows, cols = np.mgrid[0:N, 0:N]
    height = np.full((N, N), BED_DM + 5, np.int16)
    lake = (np.abs(rows - N // 2) < 35) & (cols > 20) & (cols < 85)
    height[lake] = BED_DM
    height[:, CLIFF:] = -300
    water = np.where(lake, BOX_DM, hf.NODATA).astype(np.int16)
    grades = np.where(lake, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return water, grades, height, lake


def test_a_lowered_body_is_raised_to_its_lip_upstream_only():
    before = np.full((N, N), BOX_DM, np.int16)
    level = np.full((N, N), 40, np.int16)
    out, meta = hold_at_lips(_field(None, None, None), before, level, _lip())
    upstream, downstream = out[:, :LIP_COL], out[:, LIP_COL + 1 :]
    assert (upstream == LIP_DM).all(), "held at the lip, never above it"
    assert (downstream == 40).all(), "past the lip the membrane runs on down the cliff"
    assert meta == {"lips": 1, "texels": int((out != level).sum())}
    assert (level == 40).all(), "the input is not written"


def test_the_hold_never_lifts_water_above_its_box_or_touches_what_was_not_lowered():
    before = np.full((N, N), BOX_DM, np.int16)
    level = np.full((N, N), 40, np.int16)
    level[:, :30] = BOX_DM
    out, _meta = hold_at_lips(_field(None, None, None), before, level, _lip(z_dm=BOX_DM + 30))
    assert (out <= before).all()
    assert (out[:, :30] == BOX_DM).all()
    dry = np.full((N, N), hf.NODATA, np.int16)
    assert hold_at_lips(_field(None, None, None), dry, dry, _lip())[1]["texels"] == 0


def test_a_lip_far_from_the_lowered_water_or_at_the_ocean_holds_nothing():
    before = np.full((N, N), BOX_DM, np.int16)
    level = np.full((N, N), 40, np.int16)
    level[:, :] = np.where(np.arange(N) < 20, 40, BOX_DM)[None, :]
    far, meta = hold_at_lips(_field(None, None, None), before, level, _lip())
    assert np.array_equal(far, level) and meta["lips"] == 0
    ocean = _lip(z_dm=round(OCEAN_LEVEL_M * hf.DM_PER_M))
    assert hold_at_lips(_field(None, None, None), before, level, ocean)[1]["lips"] == 0
    assert hold_at_lips(_field(None, None, None), before, level, None)[0] is level


def test_a_lake_whose_box_runs_over_its_fall_draws_at_its_lip_not_on_its_bed():
    """Without the lip the re-levelling spans the cliff edge and sinks the lake to its bed:
    the flat sand box at (2040, -1880). With it, the lake keeps 3 m of water."""
    water, grades, height, lake = _lake_over_its_fall()
    field = _field(water, grades, height)
    reach = (np.zeros((N, N), np.uint8), {"ocean_texels": 0, "reach_texels": 0})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("mapgen.palette.water.surface.ocean_reach", lambda _field, _drawn=None: reach)
        sunk = water_surfaces(field, False)
        held = water_surfaces(field, False, lips=_lip())
    basin = lake & (np.arange(N)[None, :] < CLIFF - 5)
    assert sunk.level is not None and held.level is not None
    assert (sunk.level[basin] - height[basin]).mean() < 10, "the fixture sinks the lake"
    assert (held.level[basin] == LIP_DM).all()
    assert held.perched is not None and held.perched["lips"]["lips"] == 1
