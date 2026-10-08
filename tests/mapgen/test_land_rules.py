"""Three land rules of the game-painted style: the Spire Coast rock from its own material, wet
sand derived from sand by a rule, and the red Kapok's crimson keyed by species.

docs/spatial-and-map.md sections 27, 30, 31 and 36. Synthetic fixtures: no install.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.cache import MeshPlanes, cached_mesh_family, cached_meshes, mesh_stamp
from mapgen.colour import oklab
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.rocks import families as rockfamily
from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD
from mapgen.palette.painted.calibration import (
    derived_hex,
    display_to_crown,
    display_to_ground,
    display_to_linear,
    with_derived,
)
from mapgen.palette.painted.derive.camera import lab_of_hex
from mapgen.palette.painted.ground import ROCK_GRID_M, PaintedGround
from mapgen.palette.painted.surfaces import mesh_surface
from mapgen.palette.painted.trees import crown_lab, crown_layer, over_crowns, species_targets
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.render.draw.painting import _band_family
from mapgen.terrain import render_meshes
from mapgen.terrain.crown_atlas import ALPHA, CHANNELS, RGB
from mapgen.terrain.render_meshes import (
    MESH_CLASS_MASK,
    MESH_CORAL,
    MESH_FAMILY_SHIFT,
    MESH_ROCK,
    MeshGroup,
    PreparedMeshes,
    mesh_items,
    mesh_pass,
    rasterise_mesh_band,
)

CAL = PAINTED_PALETTE["calibration"]
STYLE = PAINTED_PALETTE["crowns"]
FOREST = rockfamily.FAMILIES.index("forest")
GRASS = rockfamily.FAMILIES.index("grass")
PILLAR = "/Game/FactoryGame/World/Environment/Rock/Cliff/Mesh/CliffPillar_03"
CORAL = "/Game/FactoryGame/World/Environment/Foliage/Coral/SM_CoralTreeSmall_01"
SEA_ROCK = "/Game/FactoryGame/World/Environment/Rock/SeaRock/SM_SeaRock_01"


# ----------------------------------------------------------------------- the Spire Coast rock


def test_the_spire_coast_has_no_rock_of_its_own_and_falls_back_to_the_default():
    spire = [e for e in CAL["areas"] if "rock" in e and "Area_SpireCoast" in e["areas"]]
    assert spire == [], "the dark grey came from one backlit face"
    assert CAL["rock"] == "#85816c"


def _chroma_hue(hex_colour: str) -> tuple[float, float]:
    lab = lab_of_hex(hex_colour)
    return float(np.hypot(lab[1], lab[2])), float(np.degrees(np.arctan2(lab[2], lab[1])) % 360)


@pytest.mark.parametrize("area", ["Area_DesertCanyons", "Area_RockyDesert"])
def test_the_desert_canyons_and_rocky_desert_cliffs_wear_the_grey_default(area):
    own = [e for e in CAL["areas"] if "rock" in e and area in e["areas"]]
    assert own == [], "the game's own bake has their cliffs grey with sand tops"
    assert _chroma_hue(CAL["rock"])[0] < 0.035


def test_the_desert_rock_family_and_the_dune_desert_stay_terracotta():
    dune = [e["rock"] for e in CAL["areas"] if "rock" in e and "Area_DuneDesert" in e["areas"]]
    for colour in (CAL["families"]["desert"], *dune):
        chroma, hue = _chroma_hue(colour)
        assert chroma > 0.05 and 25.0 < hue < 70.0, colour


def _mesh_ground():
    n = len(rockfamily.FAMILIES)
    top = np.zeros((n, 3), np.float32)
    has = np.zeros(n, np.float32)
    top[FOREST], has[FOREST] = (0.05, 0.08, 0.03), 1.0
    return SimpleNamespace(
        rock_family=None, family_rock={}, family_tint=np.ones((n, 3), np.float32),
        family_top=top, family_has_top=has, palette={"rock_top": {"up": [0.6, 0.85]}},
        mesh_rgb={}, seabed_coral=np.zeros(3, np.float32), family_top_rgb={},
    )  # fmt: skip


def _mesh_scene(cls, family=None):
    shape = (4, len(cls))
    scene = {
        "z_m": np.zeros(shape, np.float32), "grid": (slice(0, 4), 0, 4, 0, len(cls), 0.25),
        "mesh_weight": np.ones(shape, np.float32),
        "mesh_class": np.tile(np.array([cls], np.uint8), (4, 1)),
        "water": {"cover": np.zeros(shape, np.float32)},
    }  # fmt: skip
    if family is not None:
        scene["mesh_family"] = np.tile(np.array([family], np.uint8), (4, 1))
    return scene


def test_a_render_only_rock_wears_its_own_family_and_top_layer():
    area_rock = np.full((4, 3, 3), 0.25, np.float32)
    g = np.zeros((4, 3, 3), np.float32)
    ground = _mesh_ground()
    scene = _mesh_scene([MESH_ROCK, MESH_ROCK, MESH_CORAL], [FOREST, 0, 0])
    ground.mesh_rgb = {MESH_CORAL: np.array([0.4, 0.3, 0.35], np.float32)}
    out = mesh_surface(g, area_rock, scene, ground, lambda plane: plane)
    np.testing.assert_allclose(out[2, 0], (0.05, 0.08, 0.03), atol=1e-6, err_msg="forest top")
    np.testing.assert_allclose(out[2, 1], 0.25, err_msg="no family: the area's rock")
    np.testing.assert_allclose(out[2, 2], (0.4, 0.3, 0.35), err_msg="coral keeps its colour")
    wall = dict(scene, z_m=np.tile(np.arange(3, dtype=np.float32) * 2.0, (4, 1)))
    np.testing.assert_allclose(mesh_surface(g, area_rock, wall, ground, None)[2, 0], 0.25,
                               err_msg="a steep face is bare rock")  # fmt: skip
    plain = _mesh_scene([MESH_ROCK], None)
    out = mesh_surface(g[:, :1], area_rock[:, :1], plain, ground, None)
    np.testing.assert_allclose(out[2, 0], 0.25, err_msg="a cache without the plane")


def test_mesh_items_carry_each_rock_s_family_and_leave_coral_alone(monkeypatch):
    shape = (np.array([[0, 0, 0], [100, 0, 0], [0, 100, 0]], np.float32), np.array([[0, 1, 2]]))
    monkeypatch.setattr(render_meshes, "read_shape", lambda *a: (shape, "test"))
    asked: list = []

    def worn(_s, _c, _i, mesh, material, _caches):
        asked.append((mesh, material))
        return {"MI_Forest": FOREST, None: GRASS}[material]

    monkeypatch.setattr(render_meshes, "worn_family", worn)
    row = lambda mesh: (mesh, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1)
    sweep = {
        "meshes": [PILLAR, CORAL],
        "placements": np.array([row(0), row(0), row(1)], np.float64),
        "placement_materials": np.array([0, -1, -1], np.int32),
        "materials": ["MI_Forest"],
        "extra_foliage": {SEA_ROCK: [np.eye(4)]},
    }
    prepared, meta = mesh_items(None, None, None, sweep)
    codes = prepared.items[PILLAR].codes
    assert (codes & MESH_CLASS_MASK).tolist() == [MESH_ROCK, MESH_ROCK]
    assert (codes >> MESH_FAMILY_SHIFT).tolist() == [FOREST, GRASS], "override, then its own"
    assert prepared.items[CORAL].codes.tolist() == [MESH_CORAL]
    assert (prepared.items[SEA_ROCK].codes >> MESH_FAMILY_SHIFT).tolist() == [GRASS]
    assert all(mesh != CORAL for mesh, _m in asked), "coral is not a rock"
    assert prepared.families and meta["rock_families"] == {"forest": 1, "grass": 2}


def test_the_band_keeps_each_instance_s_code():
    verts = np.array([[0, 0, 500], [400, 0, 500], [400, 400, 500], [0, 400, 500]], np.float32)
    x0, y0 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["y_min_m"] * 100
    mats = np.tile(np.eye(4, dtype=np.float32), (2, 1, 1))
    mats[0, 3, :2], mats[1, 3, :2] = (x0, y0), (x0 + 1000, y0)
    code = np.array([MESH_ROCK | FOREST << MESH_FAMILY_SHIFT, MESH_ROCK], np.uint16)
    group = MeshGroup(code, mats, mats[:, 3, 1] - 600, mats[:, 3, 1] + 600)
    prepared = PreparedMeshes({PILLAR: group}, {PILLAR: (verts, np.array([[0, 1, 2], [0, 2, 3]]))})
    z, src = rasterise_mesh_band(prepared, x0, y0, 100.0, 8, 16)
    assert src[2, 2] == code[0] and src[2, 12] == code[1] and src[2, 6] == 0
    assert np.isfinite(z[2, 2]) and not np.isfinite(z[2, 6])


def test_the_mesh_cache_holds_a_family_plane_and_the_pass_hands_it_on(tmp_path, monkeypatch):
    pytest.importorskip("zstandard")

    def band(_prepared, _x0, _y0, _step, rows, cols):
        code = np.zeros((rows, cols), np.uint8)
        code[:, : cols // 2] = MESH_ROCK | FOREST << MESH_FAMILY_SHIFT
        code[:, cols // 2 :] = MESH_CORAL
        return np.full((rows, cols), 900.0, np.float32), code

    monkeypatch.setattr(render_meshes, "rasterise_mesh_band", band)
    build = lambda: (PreparedMeshes({}, {}, families=True), {"instances": {}})
    maps, _source = mesh_pass(tmp_path, 32, "b1", "render_meshes", build, "m", True)
    assert len(maps) == 3
    z, cls, family = (np.asarray(m[:]) for m in maps)
    assert cls[0, 0] == MESH_ROCK and cls[0, 31] == MESH_CORAL
    assert family[0, 0] == FOREST and family[0, 31] == 0
    stamp = mesh_stamp(32, "b1", render_meshes.READER_VERSIONS["render_meshes"])
    assert cached_meshes(tmp_path, stamp) is not None
    assert cached_mesh_family(tmp_path, stamp) is not None
    assert _band_family(MeshPlanes(*maps), slice(0, 2)).shape == (2, 32)
    assert _band_family(MeshPlanes(*maps[:2]), slice(0, 2)) is None
    assert _band_family(None, slice(0, 2)) is None
    del maps, z, cls, family


# ----------------------------------------------------------------------- wet sand by rule

RULE = {"from": "Sand_LayerInfo", "lightness": 0.8, "chroma": 1.0, "hue_deg": -10.0}


def test_wet_sand_is_a_rule_on_the_sand_target_not_a_hex():
    assert "WetSand_LayerInfo" not in CAL["layers"]
    assert CAL["derived"]["WetSand_LayerInfo"] == RULE
    assert derived_hex("#d5cbb6", RULE) == "#a29583"
    assert derived_hex("#c4ab8b", RULE) == "#987b61", "the deserts' sand"
    assert derived_hex("#d5cbb6", {}) == "#d5cbb6"


def test_the_rule_reaches_every_scope_with_a_sand_target_and_no_other():
    cal = with_derived(CAL)
    assert cal["layers"]["WetSand_LayerInfo"] == "#a29583"
    for before, after in zip(CAL["areas"], cal["areas"], strict=True):
        sand = before.get("layers", {}).get("Sand_LayerInfo")
        if sand is None:
            assert after == before
        else:
            assert after["layers"]["WetSand_LayerInfo"] == derived_hex(sand, RULE)
    written = {"Sand_LayerInfo": "#d5cbb6", "WetSand_LayerInfo": "#000000"}
    kept = with_derived({**CAL, "layers": written})
    assert kept["layers"]["WetSand_LayerInfo"] == "#000000", "a target written out wins"


def test_wet_sand_lands_on_its_rule_in_each_scope():
    index = np.zeros((32, 32), np.uint8)
    index[:, 16:] = 1
    ground = object.__new__(PaintedGround)
    cal = {**CAL, "area_blur_m": 0.4, "min_texels": 4,
           "layers": {"Sand_LayerInfo": "#d5cbb6"},
           "areas": [{"areas": ["Area_A"], "layers": {"Sand_LayerInfo": "#c4ab8b"}}]}  # fmt: skip
    ground.palette = {**PAINTED_PALETTE, "calibration": cal}
    ground.area_names, ground.area_assets = ["Area_A", "Area_B"], []
    ground.coarse_index = index[::ROCK_GRID_M, ::ROCK_GRID_M]
    albedo = np.tile(np.array([0.32, 0.29, 0.24], np.float32), (32, 32, 1))
    albedo[16:] = (0.2, 0.2, 0.25)
    weights = {"Sand_LayerInfo": np.zeros((32, 32), np.uint8),
               "WetSand_LayerInfo": np.zeros((32, 32), np.uint8)}  # fmt: skip
    weights["Sand_LayerInfo"][:16] = weights["WetSand_LayerInfo"][16:] = 255
    out = ground._calibrate(albedo, weights)
    p = ground.palette
    for (r, c), want in (((24, 5), "#987b61"), ((24, 25), "#a29583"), ((5, 25), "#d5cbb6")):
        np.testing.assert_allclose(oklab(out[r, c]), display_to_ground(p, want), atol=2e-3)
    assert {"WetSand_LayerInfo@0", "WetSand_LayerInfo"} <= set(ground.calibration)


def test_the_wet_band_is_neutral_or_warm_and_mild():
    tint = PAINTED_PALETTE["shore"]["wet_band"]["tint"]
    assert tint[0] >= tint[1] >= tint[2], "warm or neutral, never towards blue"
    assert min(tint) >= 0.85, "the wet sand layer carries the wet colour; the band only hints"


# ----------------------------------------------------------------------- the canopy

GREEN, RED, PINK = (0.06, 0.12, 0.035), (0.22, 0.04, 0.04), (0.44, 0.08, 0.11)
NAMES = ["SM_Kapok_01", "SM_Kapok_03", "SM_Bamboo_01"]


def test_the_canopy_targets_are_one_global_green_and_red_by_species():
    assert [e for e in CAL["areas"] if "canopy" in e] == [], "no area canopy entries"
    assert CAL["canopy"] == "#558653"
    assert CAL["species"] == {"SM_Kapok_03": "#7c4955"}
    assert CAL["crowns"]["blue_palm"] == "#3d627d"


def _crowns(colours, species, names=NAMES):
    """What the calibration reads of a ``CrownSet``: its records, its species' tiles (as
    ``crown_atlas`` lays a texel out) and their names."""
    levels = []
    for colour in colours:
        level = np.zeros((4, 4, CHANNELS), np.float32)
        level[1:3, 1:3, ALPHA] = 1.0
        level[1:3, 1:3, RGB] = colour
        levels.append([level, level[::2, ::2].copy()])
    records = np.zeros(len(species), CROWN_RECORD)
    records["species"], records["scale"] = species, 1.0
    return SimpleNamespace(levels=levels, records=records, names=list(names))


def test_a_species_target_moves_only_that_species_onto_it():
    crowns = _crowns([GREEN, RED, PINK], [0, 1, 1, 2])
    before = [copy.deepcopy(lv) for lv in crowns.levels]
    levels, measured = species_targets(
        crowns, STYLE, {"SM_Kapok_03": "#7c4955", "Absent": "#000000"}, PAINTED_PALETTE
    )
    assert (
        set(measured) == {"species@SM_Kapok_03"} and measured["species@SM_Kapok_03"]["trees"] == 2
    )
    level = levels[1][0]
    lab = crown_lab(level[1, 1, RGB] / level[1, 1, ALPHA], STYLE)
    np.testing.assert_allclose(lab, display_to_crown(PAINTED_PALETTE, "#7c4955"), atol=2e-3)
    assert levels[1][0][0, 0, RGB].tolist() == [0.0, 0.0, 0.0], "no cover, no colour"
    for k in (0, 2):
        for got, want in zip(levels[k], before[k], strict=True):
            np.testing.assert_array_equal(got, want)
    for got, want in zip(crowns.levels, before, strict=True):
        for got_mip, want_mip in zip(got, want, strict=True):
            np.testing.assert_array_equal(got_mip, want_mip, err_msg="the crowns stay as they are")


def test_the_red_kapok_is_crimson_in_any_area_and_the_green_ones_are_not():
    crowns = _crowns([GREEN, RED, PINK], np.repeat([0, 1, 2], 10))
    ground = object.__new__(PaintedGround)
    ground.palette = {**PAINTED_PALETTE, "calibration": {**CAL, "min_texels": 5}}
    ground.crowns, ground.crown_measured = crowns, {}
    ground.area_names, ground.area_assets = ["Area_JungleSpires"], []
    ground.coarse_index = np.zeros((4, 4), np.uint8)
    ground._calibrate_crowns(ground.palette["calibration"])
    ops = ground.crown_ops
    assert "species@SM_Kapok_03" in ground.crown_measured
    p = ground.palette
    colours = [crowns.levels[k][0][1, 1, RGB] for k in range(3)]
    shape = (1, 3)
    terms = {"cover": np.ones(shape, np.float32), "top_cm": np.full(shape, 1500.0, np.float32),
             "rgb": np.array([colours], np.float32), "ndl": np.full(shape, 0.7, np.float32)}  # fmt: skip
    scene = {"z_m": np.zeros(shape, np.float32), "ndl_flat": np.float32(0.7),
             "water": {"cover": np.zeros(shape, np.float32),
                       "depth_m": np.zeros(shape, np.float32)}}  # fmt: skip
    exposure = np.float32(p["exposure"] * p["tone"]["gain"])

    def draw(taken):
        layer = crown_layer(terms, scene, p, np.float32(p["ambient"]), exposure, taken)
        return over_crowns(np.zeros((*shape, 3), np.float32), layer)

    drawn = draw(ops)
    np.testing.assert_allclose(drawn[0, 1], display_to_linear(p, "#7c4955"), rtol=3e-3)
    np.testing.assert_allclose(drawn[0, 0], display_to_linear(p, "#558653"), rtol=3e-3)
    np.testing.assert_allclose(drawn[0, 2], draw(())[0, 2], err_msg="the pink bamboo stays")
