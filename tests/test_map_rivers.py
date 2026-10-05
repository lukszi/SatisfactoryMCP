"""Recipe 7: the rivers drawn from the game's own river splines.

docs/spatial-and-map.md section 28. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.cache import cached_rivers, river_stamp, write_rivers  # noqa: E402
from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM  # noqa: E402
from mapgen.gamedata.rivers import (  # noqa: E402
    RIVER_CLASS,
    box_tops,
    hermite,
    ribbon_planes,
    sample_rivers,
)
from mapgen.palette.rivers import RiverWater, river_terms  # noqa: E402
from mapgen.palette.shore import blend_water, optical_depth, water_composite  # noqa: E402
from mapgen.palette.styles import SHORE_OPTICS, WATER_DEEP, WATER_SHALLOW  # noqa: E402
from mapgen.palette.water import WATER_DEPTH_FULL_M  # noqa: E402
from mapgen.tiles.recipes import RECIPE  # noqa: E402
from satisfactory_mcp.core.gameassets import versions  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402

X0, Y0 = ORIGIN_X_CM / 100, ORIGIN_Y_CM / 100
SHAPE = (120, 160)


def _section(x0, x1, y, z0, z1, hw0, hw1):
    """A straight section in world cm, tangents along the chord: a straight Hermite."""
    p0 = [(X0 + x0) * 100, (Y0 + y) * 100, z0 * 100]
    p1 = [(X0 + x1) * 100, (Y0 + y) * 100, z1 * 100]
    tangent = [b - a for a, b in zip(p0, p1, strict=True)]
    return [*p0, *tangent, *p1, *tangent, hw0 * 100, hw1 * 100]


def _river(*sections):
    return {"actor": "BP_River_PROT_C_0", "meshes": ["SM_RiverPlane"], "sections": list(sections)}


def test_a_section_is_its_hermite_cubic_end_to_end():
    row = np.asarray(_section(10, 50, 30, 5.0, 3.0, 4.0, 6.0))
    ends = hermite(row[None, :], np.array([0.0, 0.5, 1.0]))[0]
    assert ends[0] == pytest.approx(row[0:3]) and ends[2] == pytest.approx(row[6:9])
    assert ends[1] == pytest.approx((row[0:3] + row[6:9]) / 2), "straight tangents stay straight"


def test_samples_follow_the_curve_and_only_open_ends_get_a_cap():
    joined = _river(_section(10, 50, 30, 5.0, 4.0, 4.0, 6.0), _section(50, 90, 30, 4.0, 3.0, 6.0, 6.0))
    s = sample_rivers([joined])
    assert np.diff(s["x"])[np.diff(s["section"]) == 0].max() <= 0.5 + 1e-9
    assert s["hw"].min() == pytest.approx(4.0) and s["hw"].max() == pytest.approx(6.0)
    capped = np.flatnonzero(np.abs(s["cap"]).sum(1) > 0)
    assert len(capped) == 2, "the joint between the two sections is not an end"
    assert s["cap"][capped[0]] == pytest.approx([-1, 0]) and s["cap"][capped[-1]] == pytest.approx([1, 0])


def test_the_ribbon_is_the_plane_across_and_ends_square():
    s = sample_rivers([_river(_section(20, 100, 60, 10.0, 6.0, 5.0, 5.0))])
    planes = ribbon_planes(s, reach_m=4.0, shape=SHAPE)
    u, level = planes["u"], planes["level_m"]
    assert u[60, 60] == pytest.approx(0.0, abs=1e-6)
    assert u[63, 60] == pytest.approx(0.6, abs=1e-6) and u[65, 60] == pytest.approx(1.0, abs=1e-6)
    assert level[60, 20] == pytest.approx(10.0, abs=0.01) and level[60, 100] == pytest.approx(6.0, abs=0.01)
    assert level[63, 60] == pytest.approx(8.0, abs=0.01), "the plane is flat across"
    assert np.isnan(u[60 + 5 + 5, 60]), "past the edge plus the reach there is no ribbon"
    assert u[60, 102] > 1.0 and u[60, 18] > 1.0, "the plane stops at its open ends, no round cap"


def test_where_two_planes_overlap_the_higher_shows():
    low = _river(_section(20, 140, 60, 2.0, 2.0, 6.0, 6.0))
    high = _river(_section(20, 140, 64, 5.0, 5.0, 6.0, 6.0))
    planes = ribbon_planes(sample_rivers([low, high]), reach_m=4.0, shape=SHAPE)
    assert planes["level_m"][61, 80] == pytest.approx(5.0), "on both planes, nearer the low one"
    assert planes["level_m"][55, 80] == pytest.approx(2.0), "on the low plane only"


def test_box_tops_split_the_river_boxes_from_every_other_surface():
    boxes = [
        (RIVER_CLASS, ((X0 + 10) * 100, (Y0 + 10) * 100, 0, (X0 + 30) * 100, (Y0 + 30) * 100, 800)),
        ("BP_LakeWater_C", ((X0 + 20) * 100, (Y0 + 20) * 100, 0, (X0 + 40) * 100, (Y0 + 40) * 100, 300)),
        ("BP_WaterFallTool_02_C", ((X0 + 0) * 100, (Y0 + 0) * 100, 0, (X0 + 50) * 100, (Y0 + 50) * 100, 900)),
    ]  # fmt: skip
    river, other = box_tops(boxes, True, SHAPE), box_tops(boxes, False, SHAPE)
    assert river[25, 25] == pytest.approx(8.0) and np.isnan(river[35, 35])
    assert other[25, 25] == pytest.approx(3.0), "a waterfall is not a surface"


def _flat_terms(shape, cover=0.0, depth_m=0.0):
    zero = np.zeros(shape, np.float32)
    terms = blend_water(None, zero + cover, zero + depth_m / WATER_DEPTH_FULL_M, None, 40.0)
    terms["depth_m"] = zero + depth_m
    return terms


def _valley(cols=200, spacing=0.25, slope=0.2):
    """A V-shaped channel, lowest (0 m) at column 100."""
    x = (np.arange(cols) - 100) * spacing
    return np.tile((slope * np.abs(x)).astype(np.float32), (6, 1)), spacing


def test_a_river_covers_its_channel_to_one_pixel_at_each_bank():
    z, spacing = _valley()
    river = np.full(z.shape, 1.0, np.float32)
    terms = river_terms(_flat_terms(z.shape), z, river, np.ones_like(z), spacing)
    cover = terms["cover"][3]
    partial = np.flatnonzero((cover > 0.01) & (cover < 0.99))
    assert len(partial) <= 4, "a bank is one pixel on each side, not a feather"
    assert cover[100] == 1.0 and cover[0] == 0.0
    assert terms["depth_m"][3, 100] == pytest.approx(1.0)
    assert terms["river"][3, 100] == pytest.approx(1.0) and terms["banks"][3, 0] == 1.0


def test_nothing_changes_where_the_ribbon_is_absent():
    z, spacing = _valley()
    before = _flat_terms(z.shape, cover=0.3, depth_m=2.0)
    after = river_terms(before, z, np.full(z.shape, np.nan, np.float32), np.ones_like(z), spacing)
    for key in ("cover", "depth", "depth_m", "banks"):
        assert np.array_equal(after[key], before[key]), key
    absent = river_terms(before, z, np.full(z.shape, 1.0, np.float32), np.zeros_like(z), spacing)
    assert np.array_equal(absent["cover"], before["cover"])


def test_a_higher_lake_hides_the_river_and_a_lower_one_does_not():
    z = np.zeros((4, 40), np.float32)
    lake = _flat_terms(z.shape, cover=1.0, depth_m=2.0)
    under = river_terms(lake, z, np.full(z.shape, 1.0, np.float32), np.ones_like(z), 0.25)
    assert np.array_equal(under["depth_m"], lake["depth_m"]) and not under["river"].any()
    over = river_terms(lake, z, np.full(z.shape, 2.5, np.float32), np.ones_like(z), 0.25)
    assert over["depth_m"][0, 0] == pytest.approx(2.5) and over["river"][0, 0] == 1.0


def test_a_shallow_river_still_reads_as_water_once_in_from_its_bank():
    z, spacing = _valley(slope=0.02)
    terms = river_terms(_flat_terms(z.shape), z, np.full(z.shape, 0.2, np.float32), np.ones_like(z), spacing)
    style = {"min_depth_m": 0.6, "bank_m": 2.5}
    seen = optical_depth(terms, style)[3]
    assert seen[100] == pytest.approx(0.6) and seen[100] > terms["depth_m"][3, 100]
    assert seen[61] < 0.3, "at the waterline the bed shows through"
    plain = _flat_terms(z.shape, cover=1.0, depth_m=0.2)
    assert np.array_equal(optical_depth(plain, style), plain["depth_m"]), "lakes are left alone"


def test_without_rivers_the_shore_composite_is_unchanged():
    z, spacing = _valley()
    land = np.full((*z.shape, 3), 120.0, np.float32)
    terms = _flat_terms(z.shape, cover=0.5, depth_m=1.0)
    legacy = {k: v for k, v in terms.items() if k not in ("banks", "river", "river_below_m")}
    args = (np.ones_like(z), SHORE_OPTICS["terrain"], WATER_SHALLOW, WATER_DEEP, 0.75, 0.25)
    assert np.array_equal(water_composite(land, terms, *args), water_composite(land, legacy, *args))


def _field(height_m, water_m, grades):
    return SimpleNamespace(
        _height_dm=np.round(height_m * 10).astype(np.int16),
        _water_raster=lambda: np.where(np.isnan(water_m), hf.NODATA, np.round(water_m * 10)).astype(np.int16),
        _water_quality_raster=lambda: grades.astype(np.uint8),
    )  # fmt: skip


def test_the_river_boxes_water_is_taken_back_and_lakes_are_relevelled():
    ground = np.full(SHAPE, 4.0, np.float32)
    ground[50:70, :] = 1.0  # the channel
    ground[90:110, 100:150] = 0.0  # a lake bed
    water = np.full(SHAPE, np.nan, np.float32)
    water[45:75, 10:150] = 30.0  # the river AABB's top, levelled over the artwork's water
    water[90:110, 100:150] = 30.0  # ...and over the lake it overhangs
    water[5:15, 5:15] = 2.5  # a pond nobody else claims
    grades = np.where(np.isnan(water), hf.WATER_DRY, hf.WATER_MEASURED)
    boxes = [
        [RIVER_CLASS, [(X0 + 0) * 100, (Y0 + 40) * 100, 0, (X0 + 159) * 100, (Y0 + 115) * 100, 3000]],
        ["BP_LakeWater_C", [(X0 + 95) * 100, (Y0 + 85) * 100, 0, (X0 + 155) * 100, (Y0 + 115) * 100, 250]],
        ["FGWaterVolume", [(X0 + 0) * 100, (Y0 + 0) * 100, 0, (X0 + 20) * 100, (Y0 + 20) * 100, 250]],
    ]  # fmt: skip
    cached = {"rivers": [_river(_section(10, 150, 60, 2.0, 2.0, 8.0, 8.0))], "boxes": boxes}
    rivers = RiverWater(cached, _field(ground, water, grades))
    assert rivers.grades[60, 80] == hf.WATER_DRY, "the box's water in the channel is gone"
    assert rivers.presence[60, 80] == 255 and rivers.level_dm[60, 80] == 20
    assert rivers.water_dm[100, 120] == 25 and rivers.grades[100, 120] == hf.WATER_MEASURED
    assert rivers.water_dm[10, 10] == 25, "water no river box levelled is untouched"
    assert rivers.stats["rivers"] == 1 and rivers.stats["dropped_km2"] > 0


def test_the_river_cache_is_keyed_on_build_and_reader(tmp_path):
    stamp = river_stamp("build 1", 1)
    write_rivers(tmp_path, stamp, [_river()], [("BP_Water_C", (0, 0, 0, 1, 1, 1))])
    assert cached_rivers(tmp_path, stamp)["boxes"] == [["BP_Water_C", [0, 0, 0, 1, 1, 1]]]
    assert cached_rivers(tmp_path, river_stamp("build 2", 1)) is None
    assert cached_rivers(tmp_path, river_stamp("build 1", 2)) is None


def test_recipe_7_is_current_and_reads_the_river_splines():
    assert RECIPE == versions.RENDER_RECIPE_CURRENT == 7
    assert versions.READER_VERSIONS["river_splines"] == 1
    for layer, optics in SHORE_OPTICS.items():
        assert optics["river"]["min_depth_m"] > 0 and optics["river"]["bank_m"] > 0, layer
