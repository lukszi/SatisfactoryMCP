"""A higher body's water box over lower water draws no rectangle.

docs/spatial-and-map.md section 38, "Boxes over lower water". Synthetic fixtures: no install,
no field.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM
from mapgen.gamedata.rivers import RIVER_CLASS, ribbon_planes, sample_rivers
from mapgen.gamedata.water import lower_bodies
from mapgen.palette.rivers import RIVER_MAX_DEPTH_M, RiverWater, river_terms
from mapgen.palette.shore import OCEAN_LEVEL_M, blend_water
from satisfactory_mcp.domain.spatial import heightfield as hf

X0, Y0 = ORIGIN_X_CM / 100, ORIGIN_Y_CM / 100


def _box(kind, c0, r0, c1, r1, top_m):
    """A box over texel columns c0..c1 and rows r0..r1 inclusive, its top at ``top_m``."""
    return [
        kind,
        [(X0 + c0) * 100, (Y0 + r0) * 100, 0.0, (X0 + c1) * 100, (Y0 + r1) * 100, top_m * 100],
    ]


def _levels(boxes, wet, ground_m):
    """The field's rule: the highest surface box top over each wet texel, dry where the
    measured ground stands at or above it."""
    level = np.full(wet.shape, -np.inf)
    for _kind, (x0, y0, _z0, x1, y1, z1) in boxes:
        c0, c1 = round(x0 / 100 - X0), round(x1 / 100 - X0) + 1
        r0, r1 = round(y0 / 100 - Y0), round(y1 / 100 - Y0) + 1
        level[r0:r1, c0:c1] = np.maximum(level[r0:r1, c0:c1], z1 / 100)
    wet = wet & (level > ground_m)
    water = np.where(wet, np.round(level * hf.DM_PER_M), hf.NODATA).astype(np.int16)
    grades = np.where(wet, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return water, grades


def _lakes():
    """A lake at 0 m on a bed at -5 m (columns 0 to 49), a cliff, and a lake at 20 m on a
    bed at 18 m (columns 55 to 79) whose box reaches back over columns 30 to 49."""
    ground = np.full((40, 80), 18.0)
    ground[:, :50] = -5.0
    ground[:, 50:55] = 30.0
    ground[10:15, 70:75] = -3.0  # a pit in the high lake, below the low lake's level
    wet = np.ones(ground.shape, bool)
    wet[:, 50:55] = False
    boxes = [_box("BP_Water_C", 0, 0, 49, 39, 0.0), _box("FGWaterVolume", 30, 0, 79, 39, 20.0)]
    return ground, wet, boxes


def test_a_lower_lake_takes_back_the_box_top_over_its_own_bed():
    ground, wet, boxes = _lakes()
    water, grades = _levels(boxes, wet, ground)
    assert water[20, 40] == 200, "the field puts the high top on the low lake"
    height = np.round(ground * hf.DM_PER_M).astype(np.int16)
    out, taken = lower_bodies(water, grades, height, boxes, OCEAN_LEVEL_M)
    assert (out[:, :50] == 0).all(), "the overhang is the low lake's again"
    assert (out[:, 55:] == 200).all(), "the high lake keeps its top, its pit included"
    assert taken == 40 * 20
    assert water[20, 40] == 200, "the input is not changed"


def test_a_river_box_and_the_ocean_take_nothing_back():
    ground, wet, boxes = _lakes()
    for kind, top in ((RIVER_CLASS, 0.0), ("BP_Water_C", OCEAN_LEVEL_M)):
        lowered = [_box(kind, 0, 0, 49, 39, top), boxes[1]]
        water, grades = _levels(lowered, wet, ground - (0 if top == 0.0 else 20))
        height = np.round((ground - (0 if top == 0.0 else 20)) * hf.DM_PER_M).astype(np.int16)
        out, taken = lower_bodies(water, grades, height, lowered, OCEAN_LEVEL_M)
        assert taken == 0 and np.array_equal(out, water), kind


def _section(x0, x1, y, z, hw):
    p0 = [(X0 + x0) * 100, (Y0 + y) * 100, z * 100]
    p1 = [(X0 + x1) * 100, (Y0 + y) * 100, z * 100]
    tangent = [b - a for a, b in zip(p0, p1, strict=True)]
    return [*p0, *tangent, *p1, *tangent, hw * 100, hw * 100]


def _basin():
    """A lake at 153.9 m on a 150 m bed (rows 0 to 19) above a fall's basin at 100 m. The
    river below the fall is a wide plane at 104 m over the basin and under the lake. The
    plunge river's box (111.4 m) covers the basin; the lake's volume (153.9 m) and the short
    river over the lip (154.4 m) reach over part of it, as at (-1077, -294)."""
    ground = np.full((120, 160), 100.0)
    ground[:20] = 150.0
    wet = np.ones(ground.shape, bool)
    boxes = [
        _box(RIVER_CLASS, 0, 20, 159, 119, 111.4),
        _box("FGWaterVolume", 60, 0, 130, 90, 153.9),
        _box(RIVER_CLASS, 80, 10, 120, 80, 154.4),
    ]
    rivers = [{"actor": "BP_River_PROT_C_0", "meshes": ["SM_RiverPlane"],
               "sections": [_section(-20, 180, 60, 104.0, 70.0)]}]  # fmt: skip
    water, grades = _levels(boxes, wet, ground)
    field = SimpleNamespace(
        _height_dm=np.round(ground * hf.DM_PER_M).astype(np.int16),
        _water_raster=lambda: water,
        _water_quality_raster=lambda: grades,
    )
    return RiverWater({"rivers": rivers, "boxes": boxes}, field), water


def test_a_lake_box_over_a_fall_basin_leaves_no_rectangle():
    rivers, field_water = _basin()
    assert field_water[60, 100] == 1544 and field_water[60, 40] == 1114, "the fixture's boxes"
    basin = rivers.grades[25:115, 5:155]
    assert (basin == hf.WATER_DRY).all(), "inside the boxes and out, the ribbon draws the basin"
    assert (rivers.presence[25:115, 5:155] > 0).all()
    assert rivers.stats["over_the_river_km2"] > 0


def test_a_lake_whose_bed_stands_above_the_plane_under_it_keeps_its_level():
    rivers, _field_water = _basin()
    assert (rivers.water_dm[2:18, 62:128] == 1539).all(), "the upper lake is not the river's"
    assert (rivers.grades[2:18, 62:128] == hf.WATER_MEASURED).all()


def test_a_plane_hanging_over_its_ground_gives_way_to_one_in_its_channel():
    shape = (40, 120)
    low = {"actor": "a", "meshes": [], "sections": [_section(10, 110, 20, 10.0, 8.0)]}
    high = {"actor": "b", "meshes": [], "sections": [_section(10, 110, 20, 50.0, 8.0)]}
    samples = sample_rivers([low, high])
    ground = np.full(shape, 9.0, np.float32)
    plain = ribbon_planes(samples, reach_m=4.0, shape=shape)
    held = ribbon_planes(samples, reach_m=4.0, shape=shape, hang=(ground, RIVER_MAX_DEPTH_M))
    assert plain["level_m"][20, 60] == pytest.approx(50.0), "without the ground, the higher"
    assert held["level_m"][20, 60] == pytest.approx(10.0), "the river in its channel shows"


def test_the_river_draws_through_the_blur_past_the_last_wet_texel():
    z = np.zeros((4, 40), np.float32)
    zero = np.zeros(z.shape, np.float32)
    # The blur of a lake's edge, past its last wet texel: no measured share, read as deep.
    halo = blend_water(None, zero + 0.3, zero + 1.0, None, 40.0)
    halo["wet"] = zero
    river = np.full(z.shape, 1.0, np.float32)
    drawn = river_terms(halo, z, river, np.ones_like(z), 0.25)
    assert drawn["river"][0, 20] == pytest.approx(1.0) and drawn["cover"][0, 20] == 1.0
    halo["wet"] = zero + 0.5
    under = river_terms(halo, z, river, np.ones_like(z), 0.25)
    assert not under["river"].any(), "real deep water above the river still hides it"
    del halo["wet"]
    assert not river_terms(halo, z, river, np.ones_like(z), 0.25)["river"].any()
