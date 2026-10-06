"""Per-class inland water in the game-painted style: the class plane, its sampling, its optics.

docs/spatial-and-map.md section 33. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.waterbodies import (
    ACTOR_CLASS,
    BODY_STEP_M,
    CLASSES,
    HOT_SPRING_BOX_MAX_M,
    MATERIAL_CLASS,
    MOUTH_BLEND,
    MOUTH_FEATHER_M,
    MOUTH_STEPS,
    OCEAN,
    SWAMP,
    WATER_BODIES_NAME,
    body_class,
    class_shares,
    classify,
    feather_mouths,
    level_bodies,
    open_sea,
)
from mapgen.palette.optics import class_optics as plane_optics
from mapgen.palette.painted import (
    WATER_TABLE_COLUMNS,
    PaintedGround,
    load_water_bodies,
    painted_colours,
    water_table,
)
from mapgen.palette.shore import OCEAN_LEVEL_M
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.terrain.sample import ClassMix, class_taps, taps_linear
from tests.support.map_scenes import CLASS_ID, class_optics, painted_ground_stub, water_scene

NO_SPRINGS = np.zeros((0, 3))


def _box(c0, r0, c1, r1, z_m):
    """A world box over texel columns c0..c1 and rows r0..r1, flat at ``z_m``."""
    x0, x1 = ORIGIN_X_CM + c0 * SPACING_CM, ORIGIN_X_CM + c1 * SPACING_CM
    y0, y1 = ORIGIN_Y_CM + r0 * SPACING_CM, ORIGIN_Y_CM + r1 * SPACING_CM
    return [x0, y0, z_m * 100, x1, y1, z_m * 100]


# ----------------------------------------------------------------------- classes


def test_the_material_names_the_class():
    box = _box(0, 0, 10, 10, 100.0)
    assert body_class("BP_Water_C", ["MI_WaterSwamp_Muddy"], box, NO_SPRINGS) == "swamp"
    assert body_class("BP_Water_C", ["SulfurPond_Inst"], box, NO_SPRINGS) == "sulfur"
    assert body_class("BP_River_PROT_C", ["MI_SLW_River_Base_01"], box, NO_SPRINGS) == "river"
    assert body_class("BP_Water_C", ["MI_Lake_Turquoise_01"], box, NO_SPRINGS) == "lake"
    assert body_class("FGWaterVolume", [], box, NO_SPRINGS) is None
    assert set(MATERIAL_CLASS.values()) <= set(CLASSES)


def test_translucent_water_is_its_own_class_and_a_known_material_still_wins():
    """The Blue Crater is a ``BP_TranslucentWater_C``, which names no material; the sulfur
    ponds are ``BP_Water_C`` with ``SulfurPond_Inst`` and keep their row."""
    box = _box(0, 0, 10, 10, 100.0)
    assert body_class("BP_TranslucentWater_C", [], box, NO_SPRINGS) == "translucent"
    assert body_class("BP_TranslucentWater_C", ["SulfurPond_Inst"], box, NO_SPRINGS) == "sulfur"
    assert body_class("BP_Water_C", ["SulfurPond_Inst"], box, NO_SPRINGS) == "sulfur"
    assert set(ACTOR_CLASS.values()) <= set(CLASSES)
    spring = np.array([[box[0] + 500, box[1] + 500, 100 * 100.0]])
    assert body_class("BP_TranslucentWater_C", [], box, spring) == "translucent"


def test_a_translucent_box_claims_its_water_instead_of_the_lake_fallback():
    level, wet, bodies, biome = _world()
    translucent = ["BP_TranslucentWater_C", _box(18, 38, 27, 47, 80.0), []]
    plane, counts = classify(level, wet, {**bodies, "actors": [*bodies["actors"], translucent]},
                             biome, OCEAN_LEVEL_M)  # fmt: skip
    assert (plane[40:45, 20:25] == CLASS_ID["translucent"]).all()
    assert (plane[40:45, 40:45] == CLASS_ID["swamp"]).all(), (
        "water outside the box keeps its fallback"
    )
    assert counts["bodies_claimed"] == 3 and counts["classes"]["translucent"] == 25


def test_a_small_lake_holding_a_terrace_is_a_hot_spring_and_a_big_one_is_not():
    small = _box(0, 0, 40, 40, 100.0)
    spring = np.array([[small[0] + 500, small[1] + 500, 100 * 100.0]])
    assert body_class("BP_Water_C", ["MM_Lake_01"], small, spring) == "hot_spring"
    side = int(HOT_SPRING_BOX_MAX_M) + 10
    big = _box(0, 0, side, side, 100.0)
    assert body_class("BP_Water_C", ["MM_Lake_01"], big, spring) == "lake"
    far = spring + [[0, 0, -5000]]
    assert body_class("BP_Water_C", ["MM_Lake_01"], small, far) == "lake"


def _world():
    """60x60 texels: sea on the west, a river, a swamp pond, and two bodies nothing claims."""
    wet = np.zeros((60, 60), bool)
    level = np.full((60, 60), np.nan, np.float32)
    wet[:, :10] = True
    level[:, :10] = OCEAN_LEVEL_M + 0.4
    wet[5:10, 20:50] = True
    level[5:10, 20:50] = 40.0
    wet[20:30, 20:30] = True
    level[20:30, 20:30] = 12.0
    wet[40:45, 20:25] = True
    level[40:45, 20:25] = 80.0
    wet[40:45, 40:45] = True
    level[40:45, 40:45] = 80.0
    bodies = {
        "actors": [
            ["BP_River_PROT_C", _box(20, 5, 30, 10, 40.0), ["MI_SLW_River_Base_01"]],
            ["BP_Water_C", _box(20, 20, 30, 30, 12.0), ["MI_WaterSwamp_Muddy"]],
            ["FGWaterVolume", _box(40, 40, 45, 45, 80.0), []],
        ],
        "hot_springs": [],
    }
    biome = np.zeros((60, 60), np.uint8)
    biome[:, 35:] = 1
    return level, wet, bodies, (biome, ["Area_GrassFields", "Area_Swamp"])


def test_classify_claims_fills_and_falls_back():
    level, wet, bodies, biome = _world()
    plane, counts = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert (plane[~wet] == 0).all()
    assert (plane[:, :10] == OCEAN).all(), "unclaimed water at the ocean level is ocean"
    assert (plane[5:10, 20:50] == CLASS_ID["river"]).all(), (
        "the rest of a river body takes its class"
    )
    assert (plane[20:30, 20:30] == CLASS_ID["swamp"]).all()
    assert (plane[40:45, 20:25] == CLASS_ID["lake"]).all(), "unclaimed inland water is a lake"
    assert (plane[40:45, 40:45] == CLASS_ID["swamp"]).all(), "unless its biome says swamp"
    assert counts["bodies_claimed"] == 2 and counts["filled_by_biome"] == 50


def test_a_box_claims_only_water_at_its_own_level():
    level, wet, bodies, biome = _world()
    bodies["actors"].append(["BP_Water_C", _box(0, 0, 60, 60, 200.0), ["SulfurPond_Inst"]])
    plane, _ = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert not (plane == CLASS_ID["sulfur"]).any()


RIVER_BOX = ["BP_River_PROT_C", ["MI_SLW_River_Base_01"]]
LAKE_BOX = ["BP_Water_C", ["MM_Lake_01"]]
GRASS = ["Area_GrassFields"]


def _actor(kind, c0, r0, c1, r1, z0, z1=None):
    box = _box(c0, r0, c1, r1, z0)
    box[5] = (z0 if z1 is None else z1) * 100
    return [kind[0], box, kind[1]]


def _edges_inside_bodies(plane, level):
    """Neighbouring wet texels of two classes whose levels agree: a seam inside one body."""
    count = 0
    for a, b, la, lb in (
        (plane[:-1], plane[1:], level[:-1], level[1:]),
        (plane[:, :-1], plane[:, 1:], level[:, :-1], level[:, 1:]),
    ):
        with np.errstate(invalid="ignore"):
            same = np.abs(la - lb) <= BODY_STEP_M
        count += int(((a != b) & (a > 0) & (b > 0) & same).sum())
    return count


def _lake_and_channel():
    """An 80x80 world: a lake at 50 m, and a river channel at 51.5 m running into its north
    shore. The river's box is one AABB around the channel that reaches into the lake."""
    wet = np.zeros((80, 80), bool)
    level = np.full((80, 80), np.nan, np.float32)
    wet[20:70, 10:70] = True
    level[20:70, 10:70] = 50.0
    wet[0:20, 35:40] = True
    level[0:20, 35:40] = 51.5
    river = _actor(RIVER_BOX, 30, 0, 45, 40, 48.0, 52.0)
    return level, wet, river


def test_a_river_box_reaching_into_a_lake_leaves_the_lake_one_class():
    """The ribbon draws the river; the box's rectangle in the lake would only draw a seam."""
    level, wet, river = _lake_and_channel()
    biome = (np.zeros((80, 80), np.uint8), GRASS)
    for lake in ([_actor(LAKE_BOX, 5, 15, 75, 75, 50.0)], []):
        bodies = {"actors": [*lake, river], "hot_springs": []}
        plane, counts = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
        assert (plane[20:70, 10:70] == CLASS_ID["lake"]).all()
        assert (plane[0:20, 35:40] == CLASS_ID["river"]).all(), (
            "the channel at its own level is river"
        )
        assert _edges_inside_bodies(plane, level) == 0
        assert counts["river_box_texels_given_back"] == 21 * 16, "lake rows 20-40, cols 30-45"
    labels = level_bodies(wet, level)
    assert len(np.unique(labels[wet])) == 2 and not labels[~wet].any()


def test_a_small_pond_inside_a_big_box_keeps_its_class():
    wet = np.ones((40, 40), bool)
    level = np.full((40, 40), 20.0, np.float32)
    pond = ["BP_Water_C", ["MI_Lake_Blue_01"]]
    bodies = {"actors": [_actor(pond, 10, 10, 15, 15, 20.0), _actor(LAKE_BOX, 0, 0, 40, 40, 20.0)]}
    plane, _ = classify(level, wet, bodies, (np.zeros((40, 40), np.uint8), GRASS), 0.0)
    assert (plane[10:16, 10:16] == CLASS_ID["lake_blue"]).all()
    assert (plane == CLASS_ID["lake"]).sum() == 40 * 40 - 36


def test_a_body_a_river_box_mostly_covers_is_a_river_whole():
    """A pool the river's box mostly covers is river throughout, over a bigger lake box's
    claim; a smaller pond's box inside it keeps its class."""
    wet = np.zeros((60, 60), bool)
    wet[10:50, 10:50] = True
    level = np.where(wet, np.float32(30.0), np.nan).astype(np.float32)
    bodies = {
        "actors": [
            _actor(RIVER_BOX, 10, 10, 40, 50, 25.0, 31.0),
            _actor(LAKE_BOX, 0, 38, 60, 60, 30.0),
            _actor(["BP_Water_C", ["MI_Lake_Blue_01"]], 20, 20, 25, 25, 30.0),
        ],
        "hot_springs": [],
    }
    plane, counts = classify(level, wet, bodies, (np.zeros((60, 60), np.uint8), GRASS), 0.0)
    assert (plane[20:26, 20:26] == CLASS_ID["lake_blue"]).all(), "a small pond keeps its own class"
    rest = wet.copy()
    rest[20:26, 20:26] = False
    assert (plane[rest] == CLASS_ID["river"]).all()
    assert counts["river_box_texels_given_back"] == 0


def test_a_river_box_over_sea_level_water_draws_no_rectangle_in_it():
    """Sea-level water no box claims is ocean, so a river box over part of it gives way."""
    n = 240
    wet = np.zeros((n, n), bool)
    wet[:, n // 2 :] = True
    wet[100:140, 40:100] = True
    wet[116:124, 100 : n // 2] = True
    level = _sea_level(wet)
    bodies = {"actors": [_actor(RIVER_BOX, 30, 90, 60, 150, -30.0, 5.0)], "hot_springs": []}
    biome = (np.zeros((n, n), np.uint8), GRASS)
    plane, counts = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert (plane[wet] == OCEAN).all()
    assert counts["river_box_texels_given_back"] == 40 * 21, "lagoon columns 40-60"


def _sea_level(wet):
    return np.where(wet, np.float32(OCEAN_LEVEL_M), np.nan).astype(np.float32)


def test_a_box_at_the_ocean_level_never_claims_the_open_sea():
    """The swamp's 230 m boxes stand at the sea's level and reach over it: the open sea
    stays ocean, and a lagoon behind a mouth narrower than the opening keeps the box."""
    n = 240
    wet = np.zeros((n, n), bool)
    wet[:, n // 2 :] = True
    wet[100:140, 40:100] = True
    wet[116:124, 100 : n // 2] = True
    level = _sea_level(wet)
    box = _box(30, 90, 200, 150, OCEAN_LEVEL_M)
    bodies = {"actors": [["BP_Water_C", box, ["MI_WaterSwamp_Muddy"]]], "hot_springs": []}
    sea = open_sea(level, wet, OCEAN_LEVEL_M)
    assert sea[:, n // 2 :].all(), "water reaching the map's edge is open"
    assert not sea[100:140, :100].any(), "a lagoon behind an 8 m mouth is not"
    biome = (np.zeros((n, n), np.uint8), ["Area_Swamp"])
    plane, counts = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert (plane[90:150, n // 2 : 200] == OCEAN).all(), "the box stops at the open sea"
    assert (plane[100:140, 40:100] == CLASS_ID["swamp"]).all(), "and keeps its lagoon"
    assert counts["open_sea_texels"] == int(sea.sum())


def test_sea_level_water_the_map_edge_does_not_reach_keeps_its_box():
    n = 400
    wet = np.zeros((n, n), bool)
    wet[120:280, 120:280] = True
    level = _sea_level(wet)
    assert not open_sea(level, wet, OCEAN_LEVEL_M).any()
    box = _box(100, 100, 300, 300, OCEAN_LEVEL_M)
    bodies = {"actors": [["BP_Water_C", box, ["MI_WaterSwamp_Muddy"]]], "hot_springs": []}
    biome = (np.zeros((n, n), np.uint8), ["Area_Swamp"])
    plane, counts = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert (plane[wet] == CLASS_ID["swamp"]).all() and counts["open_sea_texels"] == 0


def test_a_lake_box_under_the_sea_claims_no_water_at_the_sea_s_level():
    """The Rocky Desert box 5 cm under the sea reaches half across a lagoon whose level is the
    sea's: the lagoon stays ocean with no seam at the box's edge. A pond under the sea at its
    own box's level keeps its class."""
    n = 240
    wet = np.zeros((n, n), bool)
    wet[:, n // 2 :] = True
    wet[100:140, 40:100] = True
    wet[116:124, 100 : n // 2] = True
    level = _sea_level(wet)
    wet[20:40, 20:40] = True
    level[20:40, 20:40] = OCEAN_LEVEL_M - 0.5
    bodies = {
        "actors": [
            _actor(LAKE_BOX, 30, 90, 70, 150, OCEAN_LEVEL_M - 0.046),
            _actor(LAKE_BOX, 15, 15, 45, 45, OCEAN_LEVEL_M - 0.55),
        ],
        "hot_springs": [],
    }
    biome = (np.zeros((n, n), np.uint8), GRASS)
    plane, _ = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert (plane[100:140, 40:100] == OCEAN).all()
    assert (plane[20:40, 20:40] == CLASS_ID["lake"]).all()
    assert _edges_inside_bodies(plane, level) == 0


# ----------------------------------------------------------------------- swamp mouths


def _mouth():
    """200x200 texels: the sea east of column 100, a swamp channel meeting it 0.3 m higher,
    a swamp pond 2 m higher beside the sea, and an ocean pocket cut off by 2 m of land."""
    plane = np.zeros((200, 200), np.uint8)
    level = np.full((200, 200), np.nan, np.float32)
    plane[:, 100:], level[:, 100:] = OCEAN, OCEAN_LEVEL_M
    plane[60:140, :100], level[60:140, :100] = SWAMP, OCEAN_LEVEL_M + 0.3
    plane[150:190, 60:100], level[150:190, 60:100] = SWAMP, OCEAN_LEVEL_M + 2.0
    plane[50:58, 80:98], level[50:58, 80:98] = OCEAN, OCEAN_LEVEL_M
    return plane, level


def test_swamp_blends_into_the_ocean_across_the_line_they_meet_on():
    plane, level = _mouth()
    before = plane.copy()
    changed = feather_mouths(plane, level)
    share = class_shares()[plane, SWAMP]
    assert changed == int((plane >= MOUTH_BLEND).sum()) > 0
    row = share[100]
    assert (np.diff(row[:200]) <= 0).all(), "the swamp share falls monotonically seawards"
    assert 0.5 < row[99] < 0.6 and 0.4 < row[100] < 0.5, "a half at the line"
    reach = int(np.ceil(MOUTH_FEATHER_M)) + 1
    far = np.ones(plane.shape, bool)
    far[:, 100 - reach : 100 + reach] = False
    assert (plane[far] == before[far]).all(), "nothing moves past the feather"
    assert (plane[150:190, 60:100] == SWAMP).all(), "a pond 2 m up is another body"
    assert (plane[50:58, 80:98] == OCEAN).all(), "water across land is not reached"
    assert (plane[60:140, 100 - reach : 100 + reach] >= MOUTH_BLEND).mean() > 0.9


def test_a_blend_draws_the_mix_of_the_swamp_and_ocean_rows():
    table = class_shares()
    assert table.shape == (MOUTH_BLEND + MOUTH_STEPS, len(CLASSES))
    np.testing.assert_array_equal(table[:MOUTH_BLEND], np.eye(MOUTH_BLEND, len(CLASSES)))
    np.testing.assert_allclose(table.sum(1), 1.0)
    assert (np.diff(table[MOUTH_BLEND:, SWAMP]) > 0).all()
    rows = water_table(PAINTED_PALETTE)
    k = MOUTH_BLEND + MOUTH_STEPS // 4
    plane = np.full((2, 2), k, np.uint8)
    optics = plane_optics(plane, rows, {}, _taps([0.0], [0.0], plane.shape), shares=(SWAMP,))
    swamp = table[k, SWAMP]
    mixed = rows[OCEAN] + swamp * (rows[CLASS_ID["swamp"]] - rows[OCEAN])
    np.testing.assert_allclose(optics["k"][0, 0], mixed[:3], rtol=1e-6)
    np.testing.assert_allclose(optics["share"][SWAMP][0, 0], swamp, rtol=1e-6)


# ----------------------------------------------------------------------- sampling


def _taps(rows, cols, shape):
    return taps_linear(np.asarray(rows, np.float32), shape[0]), taps_linear(
        np.asarray(cols, np.float32), shape[1]
    )


def test_a_uniform_pixel_takes_its_row_exactly_and_dry_taps_do_not_dilute():
    plane = np.zeros((4, 4), np.uint8)
    plane[:, 2:] = CLASS_ID["swamp"]
    plane[2:, :2] = CLASS_ID["river"]
    table = np.arange(len(CLASSES) * 2, dtype=np.float32).reshape(-1, 2) + 0.1
    mix = ClassMix(class_taps(plane, _taps([0.0, 0.5, 2.5], [2.5, 1.5, 0.2], plane.shape)), OCEAN)
    rows = mix.of(table)
    np.testing.assert_array_equal(rows[0, 0], table[CLASS_ID["swamp"]])
    np.testing.assert_array_equal(rows[0, 1], table[CLASS_ID["swamp"]])
    np.testing.assert_array_equal(rows[0, 2], table[OCEAN])
    np.testing.assert_allclose(
        rows[2, 1], (table[CLASS_ID["swamp"]] + table[CLASS_ID["river"]]) / 2
    )
    assert mix.classes() >= {CLASS_ID["swamp"], CLASS_ID["river"]}


# ----------------------------------------------------------------------- optics


def test_every_inland_class_has_optics_and_a_missing_one_draws_as_the_ocean():
    classes = PAINTED_PALETTE["water_classes"]
    assert set(CLASSES[2:]) <= set(classes)
    table = water_table(PAINTED_PALETTE)
    assert table.shape == (MOUTH_BLEND + MOUTH_STEPS, sum(WATER_TABLE_COLUMNS))
    bare = {k: v for k, v in PAINTED_PALETTE.items() if k != "water_classes"}
    np.testing.assert_array_equal(water_table(bare)[CLASS_ID["swamp"]], table[OCEAN])
    swamp = classes["swamp"]
    assert swamp["turbidity"] > classes["river"]["turbidity"], "a swamp is murkier than a river"


def test_an_old_paint_store_has_no_water_bodies(tmp_path):
    assert load_water_bodies(tmp_path, {"files": {}}) is None
    (tmp_path / WATER_BODIES_NAME).write_text(json.dumps({"actors": []}), encoding="utf-8")
    assert load_water_bodies(tmp_path, {"files": {WATER_BODIES_NAME: {}}}) == {"actors": []}


def test_the_ocean_row_draws_exactly_what_the_ocean_always_drew():
    n = 32
    ground = painted_ground_stub(n)

    def sample(plane):
        return plane

    plain = painted_colours(water_scene(n), ground, sample, sample)
    rowed = painted_colours(
        water_scene(n, class_optics(ground, "ocean", n)), ground, sample, sample
    )
    np.testing.assert_array_equal(plain, rowed)
    swamp = painted_colours(
        water_scene(n, class_optics(ground, "swamp", n)), ground, sample, sample
    )
    assert (swamp[0, -1, 0] > swamp[0, -1, 2]) and (plain[0, -1, 2] > plain[0, -1, 0])


def test_the_classes_are_read_off_the_water_as_drawn_not_the_fields_box_levels():
    """Classification runs after rivers and perched water: given planes win over the field's."""
    from satisfactory_mcp.domain.spatial import heightfield as hf

    shape = (4, 6)
    dry = np.full(shape, hf.NODATA, np.int16)
    field = SimpleNamespace(
        _water_raster=lambda: dry, _water_quality_raster=lambda: np.zeros(shape, np.uint8)
    )
    ground = object.__new__(PaintedGround)
    ground.water_class, ground.source = None, {}
    ground._bodies = {"actors": []}
    ground._biome = ({"width": 1, "area": np.zeros((1, 1), np.uint8)}, ["Area_Ocean"])
    sea = np.full(shape, round(OCEAN_LEVEL_M * hf.DM_PER_M), np.int16)
    grades = np.full(shape, hf.WATER_MEASURED, np.uint8)
    found = ground.classify_water(field, (sea, grades))
    assert (ground.water_class == OCEAN).all()
    assert found is ground.source["water_classes"] and found["classes"] == {"ocean": sea.size}
    ground.classify_water(field)
    assert not ground.water_class.any(), "the field's own planes hold no water"
