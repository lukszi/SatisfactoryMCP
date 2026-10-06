"""Tree crowns: the sprites and records in the paint store, their stamping, their colour.

docs/spatial-and-map.md section 36. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from mapgen.gamedata import crowns as data
from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM
from mapgen.gamedata.paint import RADIUS_BINS_M, canopy_cover
from mapgen.palette.painted import over_crowns
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.terrain.crowns import crown_band, load_crowns, sprite_levels

LEAF = (0.1, 0.3, 0.05)


def _card(x0, y0, x1, y1, z):
    """A flat rectangle as two triangles, mesh-local cm."""
    verts = np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], np.float32)
    return verts, np.array([[0, 1, 2], [0, 2, 3]], np.int64)


def _tau(opacity):
    return np.array([-np.log(1.0 - opacity)] + [0.0] * 255, np.float32)


def _matrix(x, y, z, yaw_deg=0.0, scale=1.0, tilt_deg=0.0):
    yaw, tilt = np.radians(yaw_deg), np.radians(tilt_deg)
    turn = np.array([[np.cos(yaw), np.sin(yaw), 0], [-np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    lean = np.array([[1, 0, 0], [0, np.cos(tilt), np.sin(tilt)], [0, -np.sin(tilt), np.cos(tilt)]])
    m = np.eye(4)
    m[:3, :3] = scale * (turn @ lean)
    m[3, :3] = (x, y, z)
    return m


def _store(tmp_path, sprites, records, materials):
    blob, index = data.encode_sprites(sprites)
    (tmp_path / data.SPRITES_NAME).write_bytes(blob)
    (tmp_path / data.CROWNS_NAME).write_bytes(data.encode_records(records))
    species = [
        {"name": f"S{i}", "materials": materials, "sprite": entry} for i, entry in enumerate(index)
    ]
    meta = {"crowns": {"species": species}, "files": {data.CROWNS_NAME: {}}}
    return load_crowns(tmp_path, meta)


# ----------------------------------------------------------------------- the paint input


def test_a_sprite_is_the_mesh_from_above_and_stacked_cards_are_denser():
    verts, tris = _card(-200, -100, 200, 100, 1200)
    one = data.rasterise_sprite(verts, tris, np.zeros(2, np.int64), _tau(0.5))
    assert one["cover"].max() == round(0.5 * 255)
    assert one["top_cm"].max() == 1200 and one["slot"][one["cover"] > 0].max() == 0
    covered = (one["cover"] > 0).sum() * data.SPRITE_M**2
    assert covered == pytest.approx(4.0 * 2.0, rel=0.05), "a 4 m x 2 m card covers 8 m^2"
    v2 = np.vstack([verts, verts + [0, 0, 100]])
    t2 = np.vstack([tris, tris + 4])
    two = data.rasterise_sprite(v2, t2, np.zeros(4, np.int64), _tau(0.5))
    assert two["cover"].max() == round(0.75 * 255), "two half-clear cards let a quarter through"
    assert two["top_cm"].max() == 1300, "the top is the higher card"
    assert data.rasterise_sprite(verts, tris[:0], np.zeros(0, np.int64), _tau(0.5)) is None


def test_a_records_yaw_scale_and_lean_come_from_its_matrix():
    trees = {"/Trees/A": np.array([_matrix(100, 200, 300, 30, 2.0), _matrix(0, 0, 0, 0, 1, 20)])}
    records, stats = data.crown_records(trees, ["A"])
    first, second = records
    assert (first["x"], first["y"], first["z"]) == (100, 200, 300)
    assert first["yaw"] == pytest.approx(30.0, abs=1e-4)
    assert first["scale"] == pytest.approx(2.0) and first["scale_z"] == pytest.approx(2.0)
    assert first["axis_z"] == pytest.approx(1.0)
    assert np.hypot(second["axis_x"], second["axis_y"]) == pytest.approx(np.sin(np.radians(20)))
    assert stats == {"instances": 2, "tilt_max_deg": 20.0}
    assert data.crown_records({"/Trees/B": trees["/Trees/A"]}, ["A"])[1]["instances"] == 0


def test_records_and_sprites_survive_the_store():
    verts, tris = _card(-100, -100, 100, 100, 500)
    sprite = data.rasterise_sprite(verts, tris, np.zeros(2, np.int64), _tau(0.9))
    blob, index = data.encode_sprites([sprite, sprite])
    back = data.decode_sprites(blob, index)
    assert len(back) == 2 and index[1]["offset"] > 0
    for key in ("cover", "top_cm", "slot"):
        assert (back[1][key] == sprite[key]).all()
    records, _ = data.crown_records({"/A": np.array([_matrix(1, 2, 3, 45)])}, ["A"])
    assert data.decode_records(data.encode_records(records)).tolist() == records.tolist()


def test_the_crown_top_plane_is_the_highest_crown_texel():
    verts, tris = _card(-150, -150, 150, 150, 800)
    sprite = data.rasterise_sprite(verts, tris, np.zeros(2, np.int64), _tau(0.9))
    records, _ = data.crown_records({"/A": np.array([_matrix(1000, 1000, 50)])}, ["A"])
    top = data.stamp_tops(records, [sprite], 20, 0.0, 0.0, 100.0)
    assert np.nanmax(top) == pytest.approx(850.0)
    assert np.isfinite(top[8:12, 8:12]).all() and np.isnan(top[0, 0])


def test_a_mask_is_an_alpha_that_varies_and_names_sort_materials():
    opaque = np.ones((4, 4), np.float32)
    half = np.tile(np.repeat(np.array([0.0, 1.0], np.float32), 4), (8, 1))
    assert data.leaf_mask([opaque], (4, 4)).all()
    mask = data.leaf_mask([opaque, half], (4, 4))
    assert mask.shape == (4, 4) and mask.mean() == 0.5, "the first alpha that varies, resized"
    assert data.material_kind("/X/MI_Kapok_Bark_02") == "bark"
    assert data.material_kind("/X/MI_Lianas_02") == "skip"
    assert data.material_kind("/X/MI_CatP1_BB") == "skip"
    assert data.material_kind("/X/MI_Kapok_Branch_02") == "leaf"
    assert (
        data.material_kind("/X/GreenTree_01", {"vector": {"Moss Color Tint": (1, 1, 1, 1)}})
        == "bark"
    )


def test_the_canopy_takes_the_measured_radius_scaled_per_tree():
    x, y = ORIGIN_X_CM + 2000.0, ORIGIN_Y_CM + 2000.0
    tree = np.array([_matrix(x, y, 0.0)])
    big = np.array([_matrix(x, y, 0.0, scale=2.0)])
    small_cover, _ = canopy_cover({"/T": tree}, 40, {"/T": 3.0})
    big_cover, counts = canopy_cover({"/T": big}, 40, {"/T": 3.0})
    assert big_cover.sum() > 3.0 * small_cover.sum()
    assert set(map(float, counts)) <= set(RADIUS_BINS_M.tolist())


# ----------------------------------------------------------------------- drawing


def _l_sprite():
    """An L: a 6 m bar along +x and a half-metre stub along +y, both at 10 m."""
    va, ta = _card(0, 0, 600, 100, 1000)
    vb, tb = _card(0, 100, 100, 150, 1000)
    verts, tris = np.vstack([va, vb]), np.vstack([ta, tb + 4])
    return data.rasterise_sprite(verts, tris, np.zeros(4, np.int64), _tau(0.95))


def _band(crowns, step_cm=22.9, size=80, x0=-900.0, y0=-900.0):
    return crown_band(crowns, x0, y0, step_cm, size, size)


def test_a_tree_stamps_its_sprite_turned_by_its_yaw(tmp_path):
    sprite = _l_sprite()
    materials = [{"linear": list(LEAF)}]
    records, _ = data.crown_records({"/A": np.array([_matrix(0, 0, 0)])}, ["A"])
    flat = _store(tmp_path, [sprite], records, materials)
    band = _band(flat)
    rows, cols = np.nonzero(band["cover"] > 0.5)
    assert np.ptp(cols) > 3 * np.ptp(rows), "unturned, the bar runs east"
    records, _ = data.crown_records({"/A": np.array([_matrix(0, 0, 0, 90)])}, ["A"])
    turned = _store(tmp_path, [sprite], records, materials)
    rows, cols = np.nonzero(_band(turned)["cover"] > 0.5)
    assert np.ptp(rows) > 3 * np.ptp(cols), "turned 90 degrees, it runs south"
    colour = band["rgb"][band["cover"] > 0.9] / band["cover"][band["cover"] > 0.9][:, None]
    np.testing.assert_allclose(colour.mean(0), LEAF, rtol=1e-3)
    top = band["top_cm"]
    assert np.nanmax(top) == pytest.approx(1000.0, abs=1.0) and np.isnan(top[0, 0])


def test_the_taller_crown_is_drawn_over_the_lower_one(tmp_path):
    verts, tris = _card(-300, -300, 300, 300, 1000)
    sprite = data.rasterise_sprite(verts, tris, np.zeros(2, np.int64), _tau(0.95))
    records, _ = data.crown_records(
        {"/A": np.array([_matrix(0, 0, 500)]), "/B": np.array([_matrix(0, 0, 0)])}, ["A", "B"]
    )
    blob, index = data.encode_sprites([sprite, sprite])
    (tmp_path / data.SPRITES_NAME).write_bytes(blob)
    (tmp_path / data.CROWNS_NAME).write_bytes(data.encode_records(records))
    species = [
        {"name": "A", "materials": [{"linear": [1.0, 0.0, 0.0]}], "sprite": index[0]},
        {"name": "B", "materials": [{"linear": [0.0, 0.0, 1.0]}], "sprite": index[1]},
    ]
    crowns = load_crowns(
        tmp_path, {"crowns": {"species": species}, "files": {data.CROWNS_NAME: {}}}
    )
    band = _band(crowns)
    centre = band["rgb"][40, 40] / band["cover"][40, 40]
    assert centre[0] > 0.9 and centre[2] < 0.1, "the crown standing 5 m higher is on top"
    assert band["top_cm"][40, 40] == pytest.approx(1500.0, abs=1.0)


def test_a_coarse_sheet_keeps_a_crowns_area(tmp_path):
    sprite = _l_sprite()
    records, _ = data.crown_records({"/A": np.array([_matrix(0, 0, 0)])}, ["A"])
    crowns = _store(tmp_path, [sprite], records, [{"linear": list(LEAF)}])
    fine = _band(crowns, 22.9, 80).get("cover").sum() * 0.229**2
    coarse = _band(crowns, 183.2, 10).get("cover").sum() * 1.832**2
    assert coarse == pytest.approx(fine, rel=0.2), "the mip average keeps the area"
    assert fine == pytest.approx(6.5 * 0.95, rel=0.1)
    assert len(sprite_levels(sprite, [LEAF])) > 3


def test_a_band_with_no_tree_is_empty(tmp_path):
    records, _ = data.crown_records({"/A": np.array([_matrix(50000, 50000, 0)])}, ["A"])
    crowns = _store(tmp_path, [_l_sprite()], records, [{"linear": list(LEAF)}])
    band = _band(crowns)
    assert not band["cover"].any() and np.isnan(band["top_cm"]).all()


# ----------------------------------------------------------------------- colour


def _scene(z_m, water=0.0):
    shape = (1, 3)
    return {
        "z_m": np.full(shape, z_m, np.float32),
        "ndl_flat": np.float32(np.sin(np.radians(45.0))),
        "water": {"cover": np.full(shape, water, np.float32)},
    }


def _crown_terms(top_cm=1500.0):
    shape = (1, 3)
    return {
        "cover": np.ones(shape, np.float32),
        "rgb": np.tile(np.array(LEAF, np.float32), (*shape, 1)),
        "top_cm": np.full(shape, top_cm, np.float32),
        "ndl": np.full(shape, np.sin(np.radians(45.0)), np.float32),
    }


def test_a_crown_is_hidden_under_a_higher_surface_and_thinned_over_water():
    ground = np.full((1, 3, 3), 0.5, np.float32)
    p = PAINTED_PALETTE
    shown = over_crowns(ground, _crown_terms(), _scene(5.0), p, 0.4, 1.0)
    assert (np.abs(shown - ground) > 0.1).all(), "a crown above the ground is drawn"
    hidden = over_crowns(ground, _crown_terms(), _scene(40.0), p, 0.4, 1.0)
    np.testing.assert_allclose(hidden, ground)
    wet = over_crowns(ground, _crown_terms(), _scene(5.0, water=1.0), p, 0.4, 1.0)
    share = (wet - ground) / (shown - ground)
    np.testing.assert_allclose(share, p["crowns"]["over_water"], rtol=1e-4)


def test_the_painted_palette_draws_crowns_instead_of_the_soft_canopy():
    style = PAINTED_PALETTE["crowns"]
    assert style["draw"] is True and style["canopy_kept"] == 0.0
    assert set(style) >= {"opacity", "darkening", "chroma", "dome_gain", "shade_clamp",
                          "hidden_below_m", "over_water"}  # fmt: skip
    json.dumps(style)
