"""Colour calibration of the game-painted style: tone, targets, layer transfer, the bake input.

docs/spatial-and-map.md section 31. Synthetic fixtures: no install, no paint store.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.gamedata.paint import LAYERS
from mapgen.gamedata.rockfamily import FAMILIES
from mapgen.gamedata.waterbodies import CLASSES
from mapgen.palette.calibration import scoped_planes
from mapgen.palette.painted import (
    ROCK_GRID_M,
    GroundBake,
    PaintedGround,
    area_ids,
    display_to_ground,
    display_to_linear,
    ground_albedo,
    layer_transfer,
    linear_from_oklab,
    linear_to_srgb,
    oklab,
    painted_colours,
    rock_surface,
    split_weight,
    srgb_to_linear,
    tone,
    transfer_op,
)
from mapgen.palette.styles import PAINTED_PALETTE
from satisfactory_mcp.core.gameassets import versions

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
SWAMP = CLASSES.index("swamp")


def _hex(colour: str) -> np.ndarray:
    return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], np.float32)


def _flat_forward(p: dict, lab: np.ndarray) -> np.ndarray:
    """``painted_colours`` on flat dry ground at mid altitude: OKLab ground to display sRGB."""
    lab = np.array(lab, np.float32)
    lab[1:] *= p["chroma_gain"]
    lab[0] += p["altitude_lift"] * 0.5
    unit = lambda c: np.asarray(c, np.float32) / (np.asarray(c, np.float32) @ LUMA)
    light = p["ambient"] * unit(p["sky"]) + (1 - p["ambient"]) * unit(p["sun"])
    out = linear_from_oklab(lab) * light * p["exposure"] * p["tone"]["gain"]
    y = float(out @ LUMA)
    out = out * tone(y, p["tone"]["knee"], p["tone"]["white"]) / y
    return linear_to_srgb(out)


def test_the_tone_is_identity_below_the_knee_and_takes_white_to_one():
    y = np.linspace(0.0, 1.6, 321, dtype=np.float32)
    out = tone(y, 0.6, 1.6)
    below = y <= 0.6
    np.testing.assert_allclose(out[below], y[below])
    assert out[-1] == pytest.approx(1.0, abs=1e-5)
    assert np.all(np.diff(out) > 0), "monotonic, so it has an inverse"
    assert float(tone(np.float32(0.6001), 0.6, 1.6)) == pytest.approx(0.6001, abs=1e-4)


def _area_targets() -> list[str]:
    cal = PAINTED_PALETTE["calibration"]
    found = []
    for entry in cal["areas"]:
        found += list(entry.get("layers", {}).values()) + list(entry.get("meshes", {}).values())
        found += [entry[k] for k in ("rock", "canopy") if k in entry]
    return found


@pytest.mark.parametrize(
    "target",
    [
        "#d5cbb6",
        "#ca784f",
        "#558653",
        "#ae8271",
        "#99868e",
        "#a29583",
        "#987b61",
        "#85816c",
        *_area_targets(),
    ],
)
def test_a_display_target_survives_the_trip_back_through_light_and_tone(target):
    lab = display_to_ground(PAINTED_PALETTE, target)
    np.testing.assert_allclose(_flat_forward(PAINTED_PALETTE, lab), _hex(target), atol=1.5)


def test_the_transfer_puts_a_pure_layer_on_its_target_and_mixes_by_weight():
    source = np.array([0.4, 0.3, 0.2], np.float32)
    target_lab = oklab(np.array([0.2, 0.35, 0.25], np.float32))
    albedo = np.tile(source, (1, 4, 1))
    weights = {
        "Sand": np.array([[255, 128, 0, 0]], np.uint8),
        "Grass": np.array([[0, 127, 255, 0]], np.uint8),
    }
    ops = {"Sand": transfer_op(oklab(source), target_lab)}
    out = layer_transfer(albedo, weights, ops)
    np.testing.assert_allclose(oklab(out[0, 0]), target_lab, atol=2e-3)
    halfway = (oklab(source) + target_lab) / 2
    assert np.abs(oklab(out[0, 1]) - halfway).max() < 0.01
    np.testing.assert_allclose(out[0, 2], source, atol=1e-5)
    np.testing.assert_allclose(out[0, 3], source, atol=1e-5)


def test_a_transfer_op_turns_hue_and_scales_chroma():
    d_l, m = transfer_op([0.5, 0.1, 0.0], [0.6, 0.0, 0.05])
    assert d_l == pytest.approx(0.1)
    np.testing.assert_allclose(m @ np.array([0.1, 0.0], np.float32), [0.0, 0.05], atol=1e-6)


def test_the_bake_replaces_the_paint_inside_and_feathers_at_its_edge():
    paint = np.full((40, 40, 3), 0.1, np.float32)
    have = np.zeros((40, 40), bool)
    have[:, :20] = True
    bake = GroundBake(np.full((40, 40, 3), 0.5, np.float32), have)
    out, covered, weight = ground_albedo(paint, np.zeros((40, 40), bool), bake, 2.0)
    assert out[20, 2, 0] == pytest.approx(0.5) and out[20, 35, 0] == pytest.approx(0.1)
    assert 0.0 <= weight[20, 19] < 0.5 and np.all(weight[:, 20:] == 0)
    assert np.array_equal(covered, have)


def test_without_a_bake_the_paint_mix_is_the_source():
    paint = np.full((4, 4, 3), 0.1, np.float32)
    have = np.ones((4, 4), bool)
    out, covered, weight = ground_albedo(paint, have, None, 2.0)
    assert out is paint and covered is have and weight is None


def test_a_bake_from_srgb_is_linear_and_drops_black_holes():
    rgb = np.array([[[255, 255, 255], [0, 0, 0], [0, 0, 2]]], np.uint8)
    bake = GroundBake.from_srgb(rgb, np.ones((1, 3), bool))
    np.testing.assert_allclose(bake.linear[0, 0], 1.0)
    assert bake.have.tolist() == [[True, False, False]]


def test_the_calibration_names_real_layers_and_the_style_was_bumped():
    cal = PAINTED_PALETTE["calibration"]
    assert set(cal["layers"]) <= set(LAYERS)
    assert {"coral", "coral_seabed"} <= set(cal["meshes"])
    assert "shell" not in cal["meshes"], "a shell wears its own material's grey"
    assert "shoulder" not in PAINTED_PALETTE, "the tone replaced it"
    assert versions.STYLES["satellite-painted"]["version"] >= 3
    seabed = srgb_to_linear(_hex(cal["meshes"]["coral_seabed"]))
    assert seabed[2] > seabed[0], "the seabed coral stays blue"


def test_every_area_entry_names_real_areas_and_scopes_each_material_once():
    cal = PAINTED_PALETTE["calibration"]
    stems = set(PAINTED_PALETTE["biome_tint_ab"])
    claimed: dict = {}
    for entry in cal["areas"]:
        materials = [f"layer:{k}" for k in entry.get("layers", {})]
        materials += [f"mesh:{k}" for k in entry.get("meshes", {})]
        materials += [k for k in ("rock", "canopy", "water") if k in entry]
        assert materials, entry
        assert set(entry.get("layers", {})) <= set(LAYERS)
        assert set(entry.get("meshes", {})) <= {"coral", "shell"}
        for area in entry["areas"]:
            assert area in stems or area.rsplit("_", 1)[0] in stems, area
            for material in materials:
                stem = area if area in stems else area.rsplit("_", 1)[0]
                for other in (area, stem):
                    assert (other, material) not in claimed, (area, material)
                claimed[(area, material)] = True
    desert = [e for e in cal["areas"] if e.get("rock") == "#ae8271"]
    assert desert and "Area_Savanna" not in desert[0]["areas"], "its rock is grey, not red"


def test_area_ids_match_a_stem_or_one_asset_of_it():
    names = ["Area_crater", "Area_crater", "Area_Swamp", "Area_NoMansLand"]
    assets = ["Area_crater_1", "Area_crater_2", "Area_Swamp_1", None]
    assert area_ids(names, assets, ["Area_crater"]) == [0, 1]
    assert area_ids(names, assets, ["Area_crater_2", "Area_Swamp"]) == [1, 2]
    assert area_ids(names, [], ["Area_crater_1"]) == []


def test_a_split_weight_sums_back_and_follows_the_share():
    weight = np.array([[0, 255, 255, 200, 7]], np.uint8)
    share = np.array([[255, 255, 0, 128, 255]], np.uint8)
    inside, outside = split_weight(weight, share)
    np.testing.assert_array_equal(inside.astype(int) + outside, weight)
    assert inside.tolist() == [[0, 255, 0, 100, 7]]


def testscoped_planes_keep_the_default_outside_and_blend_at_the_edge():
    weight = np.array([[0.0, 0.5, 1.0]], np.float32)
    planes = scoped_planes(np.array([0.1, 0.2, 0.3], np.float32), [(weight, np.ones(3))])
    rgb = np.stack(planes, -1)[0]
    np.testing.assert_allclose(rgb[0], [0.1, 0.2, 0.3])
    np.testing.assert_allclose(rgb[1], [0.55, 0.6, 0.65])
    np.testing.assert_allclose(rgb[2], [1.0, 1.0, 1.0])
    default = np.zeros(3, np.float32)
    assert scoped_planes(default, []) is default


def _bare_ground(names, assets, index, calibration):
    """A PaintedGround with only what ``_calibrate`` and ``_rock`` read."""
    ground = object.__new__(PaintedGround)
    palette = {**PAINTED_PALETTE, "calibration": {**PAINTED_PALETTE["calibration"], **calibration}}
    ground.palette, ground.area_names, ground.area_assets = palette, names, assets
    ground.coarse_index = index[::ROCK_GRID_M, ::ROCK_GRID_M]
    return ground


def test_an_area_layer_target_moves_its_own_side_and_the_global_one_the_rest():
    index = np.zeros((32, 32), np.uint8)
    index[:, 16:] = 1
    ground = _bare_ground(
        ["Area_A", "Area_B"],
        ["Area_A_1", "Area_B_1"],
        index,
        {
            "area_blur_m": 0.4,
            "min_texels": 4,
            "layers": {"Sand_LayerInfo": "#d5cbb6"},
            "areas": [{"areas": ["Area_A"], "layers": {"Sand_LayerInfo": "#c4ab8b"}}],
        },
    )
    albedo = np.tile(np.array([0.4, 0.3, 0.2], np.float32), (32, 32, 1))
    weights = {"Sand_LayerInfo": np.full((32, 32), 255, np.uint8)}
    out = ground._calibrate(albedo, weights)
    p = ground.palette
    np.testing.assert_allclose(oklab(out[5, 5]), display_to_ground(p, "#c4ab8b"), atol=2e-3)
    np.testing.assert_allclose(oklab(out[5, 25]), display_to_ground(p, "#d5cbb6"), atol=2e-3)
    assert set(ground.calibration) == {"Sand_LayerInfo@0", "Sand_LayerInfo"}


def test_rock_takes_its_area_target_and_the_default_elsewhere_at_full_exposure():
    index = np.zeros((32, 32), np.uint8)
    index[:, 16:] = 1
    ground = _bare_ground(
        ["Area_A", "Area_B"],
        [],
        index,
        {
            "area_blur_m": 0.4,
            "rock": "#85816c",
            "areas": [{"areas": ["Area_A"], "rock": "#ae8271"}],
        },
    )
    ground.meta = {"albedo_linear": {"rock": [0.2, 0.2, 0.2]}}
    albedo = np.full((32, 32, 3), 0.25, np.float32)
    rock = np.stack(ground._rock(albedo), -1)
    p = ground.palette
    for (r, c), target in (((2, 1), "#ae8271"), ((2, 6), "#85816c")):
        lab = oklab(rock[r, c])
        np.testing.assert_allclose(lab[1:], display_to_ground(p, target)[1:], atol=2e-3)
        assert lab[0] == pytest.approx(display_to_ground(p, target)[0], abs=2e-3)


def test_a_desert_family_rock_takes_the_desert_target_in_any_area():
    index = np.zeros((32, 32), np.uint8)
    index[:, 16:] = 1
    ground = _bare_ground(
        ["Area_A", "Area_B"],
        [],
        index,
        {
            "area_blur_m": 0.4,
            "min_texels": 4,
            "rock": "#85816c",
            "families": {"desert": "#ae8271"},
            "areas": [{"areas": ["Area_B"], "rock": "#51524d"}],
        },
    )
    ground.meta, ground.source = {"albedo_linear": {"rock": [0.2, 0.2, 0.2]}}, {}
    albedo = np.random.default_rng(5).uniform(0.15, 0.35, (32, 32, 3)).astype(np.float32)
    ground.rock = ground._rock(albedo)
    desert = FAMILIES.index("desert")
    plane = np.zeros((1875, 1875), np.uint8)  # one pixel per 4 m cell of the frame
    plane[:8, 4:8] = desert
    ground.attach_families(plane)
    assert ground.source["rock_family_targets"]["desert"]["cells"] == 32
    p = ground.palette
    own = oklab(np.stack(ground.family_rock[desert], -1)[:8, 4:8].reshape(-1, 3))
    want = display_to_ground(p, "#ae8271")
    np.testing.assert_allclose(own[:, 1:], np.broadcast_to(want[1:], own[:, 1:].shape), atol=2e-3)
    assert np.median(own[:, 0]) == pytest.approx(want[0], abs=2e-3)
    # Drawn: the desert rock on the grey area wears the desert target, its neighbour of no
    # family keeps the area's rock.
    ground.rock_family = np.array([[desert, 0], [desert, 0]], np.uint8)
    ground.family_tint = np.ones((len(FAMILIES), 3), np.float32)
    ground.family_top = np.zeros((len(FAMILIES), 3), np.float32)
    ground.family_has_top = np.zeros(len(FAMILIES), np.float32)
    sample = lambda plane: plane[2:4, 6:8]
    area_rock = np.stack([sample(plane) for plane in ground.rock], -1)
    scene = {"z_m": np.zeros((2, 2), np.float32), "grid": (slice(0, 2), 0, 2, 0, 2, 0.25)}
    out = rock_surface(area_rock, scene, ground, sample)
    np.testing.assert_allclose(oklab(out[0, 0])[1:], want[1:], atol=2e-3)
    np.testing.assert_allclose(oklab(out[0, 1])[1:], display_to_ground(p, "#51524d")[1:], atol=2e-3)


def _water_ground(opaque):
    p = PAINTED_PALETTE
    w = p["water"]
    return SimpleNamespace(
        palette=p,
        albedo=[np.full((1, 2), 0.3, np.float16)] * 3,
        canopy=np.zeros((1, 2), np.uint8),
        canopy_rgb=np.zeros(3, np.float32),
        rock=[np.full((1, 2), 0.2, np.float32)] * 3,
        rock_family=None,
        crown=None,
        titan=None,
        carpet=None,
        mesh_rgb={},
        seabed_coral=np.zeros(3, np.float32),
        water={
            "k": np.asarray(w["k_per_m"], np.float32),
            "body": srgb_to_linear(w["body"]),
            "sky": srgb_to_linear(w["sky"]) * np.float32(w["surface_r"]),
            "deep": srgb_to_linear(w["deep"]),
            "deep_tau_m": np.float32(w["deep_tau_m"]),
            "bed": np.float32(w["bed_wet"]),
            "opaque_tau_m": np.float32(w["opaque_tau_m"]),
            "inland_floor": np.float32(w["inland_floor"]),
        },
        ramp=(0.0, 100.0, np.linspace(0.0, 100.0, 101, dtype=np.float32)),
        opaque_water=opaque,
    )


def test_swamp_water_is_its_opaque_colour_and_other_water_is_untouched():
    shape = (1, 2)
    water = {
        "depth_m": np.full(shape, 2.0, np.float32),
        "cover": np.ones(shape, np.float32),
        "edge": np.zeros(shape, np.float32),
        "above_m": np.full(shape, 99.0, np.float32),
        "below_m": np.full(shape, 99.0, np.float32),
        "ocean": np.zeros(shape, np.float32),
    }
    scene = {
        "z_m": np.full(shape, 50.0, np.float32),
        "borrow": np.ones(shape, np.float32),
        "water": water,
        "ndl": np.full(shape, 0.7, np.float32),
        "ndl_flat": np.float32(0.7),
        "rock_weight": np.zeros(shape, np.float32),
    }
    target = next(e["water"] for e in PAINTED_PALETTE["calibration"]["areas"] if "water" in e)
    swamp = [
        (np.array([[1.0, 0.0]], np.float32), display_to_linear(PAINTED_PALETTE, target), SWAMP)
    ]
    same = lambda plane: np.asarray(plane, np.float32)
    got = painted_colours(scene, _water_ground(swamp), same, same)
    plain = painted_colours(scene, _water_ground([]), same, same)
    np.testing.assert_allclose(got[0, 0], _hex(target), atol=1.5)
    np.testing.assert_allclose(got[0, 1], plain[0, 1])
    assert np.abs(plain[0, 0] - _hex(target)).max() > 10, "the sea fit is teal"
