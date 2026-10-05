"""Per-class inland water in the game-painted style: the class plane, its sampling, its optics.

docs/spatial-and-map.md section 28. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM  # noqa: E402
from mapgen.gamedata.waterbodies import (  # noqa: E402
    CLASSES,
    HOT_SPRING_BOX_MAX_M,
    MATERIAL_CLASS,
    OCEAN,
    WATER_BODIES_NAME,
    body_class,
    classify,
)
from mapgen.palette.painted import (  # noqa: E402
    WATER_TABLE_COLUMNS,
    load_water_bodies,
    painted_colours,
    srgb_to_linear,
    water_table,
)
from mapgen.palette.shore import OCEAN_LEVEL_M  # noqa: E402
from mapgen.palette.styles import PAINTED_PALETTE  # noqa: E402
from mapgen.terrain.sample import ClassMix, class_taps, taps_linear  # noqa: E402

ID = {name: i for i, name in enumerate(CLASSES)}
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
    assert body_class("FGWaterVolume", [], box, NO_SPRINGS) is None
    assert set(MATERIAL_CLASS.values()) <= set(CLASSES)


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
    assert (plane[5:10, 20:50] == ID["river"]).all(), "the rest of a river body takes its class"
    assert (plane[20:30, 20:30] == ID["swamp"]).all()
    assert (plane[40:45, 20:25] == ID["lake"]).all(), "unclaimed inland water is a lake"
    assert (plane[40:45, 40:45] == ID["swamp"]).all(), "unless its biome says swamp"
    assert counts["bodies_claimed"] == 2 and counts["filled_by_biome"] == 50


def test_a_box_claims_only_water_at_its_own_level():
    level, wet, bodies, biome = _world()
    bodies["actors"].append(["BP_Water_C", _box(0, 0, 60, 60, 200.0), ["SulfurPond_Inst"]])
    plane, _ = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
    assert not (plane == ID["sulfur"]).any()


# ----------------------------------------------------------------------- sampling


def _taps(rows, cols, shape):
    return taps_linear(np.asarray(rows, np.float32), shape[0]), taps_linear(
        np.asarray(cols, np.float32), shape[1]
    )


def test_a_uniform_pixel_takes_its_row_exactly_and_dry_taps_do_not_dilute():
    plane = np.zeros((4, 4), np.uint8)
    plane[:, 2:] = ID["swamp"]
    plane[2:, :2] = ID["river"]
    table = np.arange(len(CLASSES) * 2, dtype=np.float32).reshape(-1, 2) + 0.1
    mix = ClassMix(class_taps(plane, _taps([0.0, 0.5, 2.5], [2.5, 1.5, 0.2], plane.shape)), OCEAN)
    rows = mix.of(table)
    np.testing.assert_array_equal(rows[0, 0], table[ID["swamp"]])
    np.testing.assert_array_equal(rows[0, 1], table[ID["swamp"]])
    np.testing.assert_array_equal(rows[0, 2], table[OCEAN])
    np.testing.assert_allclose(rows[2, 1], (table[ID["swamp"]] + table[ID["river"]]) / 2)
    assert mix.classes() >= {ID["swamp"], ID["river"]}


# ----------------------------------------------------------------------- optics


def test_every_inland_class_has_optics_and_a_missing_one_draws_as_the_ocean():
    classes = PAINTED_PALETTE["water_classes"]
    assert set(CLASSES[2:]) <= set(classes)
    table = water_table(PAINTED_PALETTE)
    assert table.shape == (len(CLASSES), sum(WATER_TABLE_COLUMNS))
    bare = {k: v for k, v in PAINTED_PALETTE.items() if k != "water_classes"}
    np.testing.assert_array_equal(water_table(bare)[ID["swamp"]], table[OCEAN])
    swamp = classes["swamp"]
    assert swamp["turbidity"] > classes["river"]["turbidity"], "a swamp is murkier than a river"


def test_an_old_paint_store_has_no_water_bodies(tmp_path):
    assert load_water_bodies(tmp_path, {"files": {}}) is None
    (tmp_path / WATER_BODIES_NAME).write_text(json.dumps({"actors": []}), encoding="utf-8")
    assert load_water_bodies(tmp_path, {"files": {WATER_BODIES_NAME: {}}}) == {"actors": []}


def _ground(n):
    palette = PAINTED_PALETTE
    water = palette["water"]
    rock = [np.full((1, n), 0.2, np.float32) for _ in range(3)]
    return SimpleNamespace(
        palette=palette,
        albedo=[np.full((1, n), v, np.float32) for v in (0.35, 0.3, 0.2)],
        canopy=np.zeros((1, n), np.float32),
        canopy_rgb=np.zeros(3, np.float32),
        rock=rock,
        mesh_rgb={},
        seabed_coral=np.zeros(3, np.float32),
        water={
            "k": np.asarray(water["k_per_m"], np.float32),
            "body": srgb_to_linear(water["body"]),
            "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
            "deep": srgb_to_linear(water["deep"]),
            "deep_tau_m": np.float32(water["deep_tau_m"]),
            "bed": np.float32(water["bed_wet"]),
        },
        ramp=(0.0, 100.0, np.linspace(0.0, 100.0, 101, dtype=np.float32)),
    )


def _scene(n, optics=None):
    depth = np.linspace(0.0, 6.0, n, dtype=np.float32)[None, :]
    zero = np.zeros((1, n), np.float32)
    water = {
        "depth_m": depth,
        "cover": np.ones((1, n), np.float32),
        "edge": zero,
        "ocean": zero,
        "above_m": zero + 10,
        "below_m": zero + 10,
    }
    return {
        "z_m": zero + 5,
        "borrow": np.ones((1, n), np.float32),
        "ndl": np.ones((1, n), np.float32),
        "ndl_flat": np.float32(1.0),
        "rock_weight": zero,
        "mesh_weight": None,
        "water": water,
        "water_optics": optics,
    }


def _optics(ground, cls, n):
    row = water_table(PAINTED_PALETTE)[ID[cls]]
    k, body, deep, tau, turbidity, tint = np.split(
        np.tile(row, (1, n, 1)), np.cumsum(WATER_TABLE_COLUMNS)[:-1], axis=-1
    )
    return {**ground.water, "k": k, "body": body, "deep": deep, "deep_tau_m": tau,
            "turbidity": turbidity, "tint": tint}  # fmt: skip


def test_the_ocean_row_draws_exactly_what_the_ocean_always_drew():
    n = 32
    ground = _ground(n)

    def sample(plane):
        return plane

    plain = painted_colours(_scene(n), ground, sample, sample)
    rowed = painted_colours(_scene(n, _optics(ground, "ocean", n)), ground, sample, sample)
    np.testing.assert_array_equal(plain, rowed)
    swamp = painted_colours(_scene(n, _optics(ground, "swamp", n)), ground, sample, sample)
    assert (swamp[0, -1, 0] > swamp[0, -1, 2]) and (plain[0, -1, 2] > plain[0, -1, 0])
