"""Rock on the painted layer: the top layer where the cliff master's mask puts it, the arches'
own family, the look laid on a band, and the families' top colours.

docs/map/painted.md section 30, docs/map/calibration.md section 31. Synthetic
fixtures: no install.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.colour import oklab
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.palette.painted.calibration import display_to_ground, scoped_planes
from mapgen.palette.painted.rock_look.atlas import rock_look
from mapgen.palette.painted.rock_look.reference import KIND_CLIFF, LookPixels, look_texels
from mapgen.palette.painted.rock_look.surface import top_mask
from mapgen.palette.painted.surfaces import (
    cliff_layer,
    family_tables,
    family_targets,
    layer_tops,
    rock_surface,
    top_targets,
)
from mapgen.palette.styles import PAINTED_PALETTE
from tests.support.rock_textures import rock_textures

FOREST = FAMILIES.index("forest")
GRASS = FAMILIES.index("grass")
SAND = FAMILIES.index("sand")
REDJUNGLE = FAMILIES.index("redjungle")
CLIFF = FAMILIES.index("cliff")
MOSS = (0.05, 0.08, 0.03)
ARCH = np.array([0.3, 0.28, 0.25], np.float32)


def _ground(look=None):
    n = len(FAMILIES)
    top = np.zeros((n, 3), np.float32)
    has = np.zeros(n, np.float32)
    top[FOREST], has[FOREST] = MOSS, 1.0
    top[SAND], has[SAND] = (0.4, 0.33, 0.25), 1.0
    return SimpleNamespace(
        rock_family=None, family_rock={}, family_tint=np.ones((n, 3), np.float32),
        family_top=top, family_has_top=has, palette=copy.deepcopy(PAINTED_PALETTE),
        family_top_rgb={}, rock_look=look, arch_rgb=ARCH, cliff_layer=None,
    )  # fmt: skip


def _scene(z, spacing=0.25, **extra):
    rows, cols = z.shape
    grid = (slice(0, rows), 0, rows, 0, cols, spacing)
    return {"z_m": np.asarray(z, np.float32), "grid": grid, **extra}


def _slope(nz, shape=(16, 16), spacing=0.25):
    """A plane whose normal's up component is ``nz``, rising east."""
    rise = np.sqrt(1.0 / (nz * nz) - 1.0) * spacing
    return np.tile(np.arange(shape[1]) * rise, (shape[0], 1)).astype(np.float32)


# ---------------------------------------------------------------------- the palette


def test_the_palette_draws_the_look_and_no_patches():
    assert "rock_top" not in PAINTED_PALETTE
    assert set(PAINTED_PALETTE["rock_look"]) == {"about", "albedo", "shade", "layer"}
    assert PAINTED_PALETTE["calibration"]["tops"] == {"forest": "#505936", "redjungle": "#ad6351"}


def test_the_arches_are_a_lighter_warm_grey_than_the_default_rock():
    arches = display_to_ground(PAINTED_PALETTE, PAINTED_PALETTE["calibration"]["arches"])
    rock = display_to_ground(PAINTED_PALETTE, PAINTED_PALETTE["calibration"]["rock"])
    assert arches[0] > rock[0] + 0.01
    assert np.hypot(*arches[1:]) < 0.04 and arches[2] > 0, "a warm grey"


# ---------------------------------------------------------------------- the top layer's mask


def test_the_top_layer_follows_the_cliff_master_s_slope_mask():
    up = np.array([0.3, 0.49, 0.6, 0.74, 0.76, 1.0], np.float32)
    mask = top_mask(up, np.full(6, 1.5, np.float32), np.full(6, 1.2, np.float32))
    np.testing.assert_allclose(mask, np.clip(up**1.5 * 3.4 - 1.2, 0, 1), rtol=1e-5)
    assert mask[0] == mask[1] == 0.0 and mask[-2] == mask[-1] == 1.0
    assert 0.0 < mask[2] < mask[3] < 1.0


def test_a_flat_top_wears_its_family_s_top_layer_and_a_wall_none():
    rock = np.full((16, 16, 3), 0.4, np.float32)
    code = np.full((16, 16), FOREST, np.uint8)
    flat = rock_surface(rock, _scene(np.zeros((16, 16))), _ground(), None, code)
    np.testing.assert_allclose(flat, np.broadcast_to(MOSS, flat.shape), atol=1e-6)
    wall = rock_surface(rock, _scene(_slope(0.3)), _ground(), None, code)
    np.testing.assert_allclose(wall[2:-2, 2:-2], 0.4, atol=1e-6)
    half = rock_surface(rock, _scene(_slope(0.62)), _ground(), None, code)[8, 8]
    weight = top_mask(np.float32(0.62), np.float32(1.5), np.float32(1.2))
    np.testing.assert_allclose(half, 0.4 * (1 - weight) + np.float32(MOSS) * weight, rtol=1e-4)


def test_an_arch_over_a_cliff_wears_the_arches_colour_by_its_lift():
    ground = _ground()
    ground.rock_family = np.full((2, 3), FOREST, np.uint8)
    rock = np.full((2, 3, 3), 0.3, np.float32)
    lift = np.array([[0.0, 0.5, 1.0]] * 2, np.float32)
    out = rock_surface(rock, _scene(np.zeros((2, 3)), top_weight=lift), ground)
    np.testing.assert_allclose(out[0, 0], MOSS, atol=1e-6)
    np.testing.assert_allclose(out[0, 1], (np.float32(MOSS) + ARCH) / 2, atol=1e-6)
    np.testing.assert_allclose(out[0, 2], ARCH, atol=1e-6)
    ground.arch_rgb = None
    np.testing.assert_allclose(rock_surface(rock, _scene(np.zeros((2, 3)), top_weight=lift),
                                            ground)[0, 2], 0.3, atol=1e-6)  # fmt: skip


def test_an_area_entry_s_arches_hold_inside_it_and_the_palette_s_outside():
    ground = _ground()
    ground.rock_family = np.full((2, 2), FOREST, np.uint8)
    dune = np.array([0.5, 0.3, 0.2], np.float32)
    inside = np.array([[1.0, 0.0], [1.0, 0.0]], np.float32)
    ground.arch_rgb = scoped_planes(ARCH, [(inside, dune)])
    lift = np.ones((2, 2), np.float32)
    out = rock_surface(np.full((2, 2, 3), 0.3, np.float32), _scene(np.zeros((2, 2)), top_weight=lift),
                       ground, lambda plane: plane)  # fmt: skip
    np.testing.assert_allclose(out[:, 0], [dune, dune], atol=1e-6)
    np.testing.assert_allclose(out[:, 1], [ARCH, ARCH], atol=1e-6)
    entry = next(e for e in PAINTED_PALETTE["calibration"]["areas"] if "arches" in e)
    assert entry["areas"] == ["Area_DuneDesert"], "the Dune Desert's terracotta arches"


# ---------------------------------------------------------------------- the look on a band


def _textured(**overrides):
    return _ground(rock_look(rock_textures(**overrides), 0.25))


def test_constant_textures_and_flat_maps_draw_the_flat_colours():
    flat = _textured(albedo=(120, 110, 100), normals=(128, 128))
    rock = np.full((24, 24, 3), 0.35, np.float32)
    code = np.full((24, 24), GRASS, np.uint8)
    for z in (np.zeros((24, 24)), _slope(0.5, (24, 24))):
        np.testing.assert_allclose(rock_surface(rock, _scene(z), flat, None, code), 0.35,
                                   rtol=0.03)  # fmt: skip


def test_textured_rock_is_its_colour_times_the_texel_at_each_pixel_centre():
    ground = _textured(normals=(128, 128))
    rock = np.full((12, 20, 3), 0.35, np.float32)
    code = np.full((12, 20), GRASS, np.uint8)
    lo, c0, spacing = 40, 300, 0.25
    scene = _scene(np.zeros((12, 20)))
    scene["grid"] = (slice(0, 12), lo, lo + 12, c0, c0 + 20, spacing)
    drawn = rock_surface(rock, scene, ground, None, code)
    rows, cols = np.mgrid[0:12, 0:20]
    px = LookPixels(
        x_m=((c0 + cols + 0.5) * spacing).ravel(), y_m=((lo + rows + 0.5) * spacing).ravel(),
        normal=np.tile(np.float32([0, 0, 1]), (240, 1)), kind=np.full(240, KIND_CLIFF, np.uint8),
        top=np.full(240, -1, np.int32),
    )  # fmt: skip
    body = look_texels(ground.rock_look, px).body.reshape(12, 20, 3)
    np.testing.assert_allclose(drawn, 0.35 * body, rtol=1e-3)
    assert drawn.std() > 0.01


def test_only_the_picked_pixels_take_the_look():
    rock = np.full((16, 16, 3), 0.35, np.float32)
    code = np.full((16, 16), GRASS, np.uint8)
    pick = np.zeros((16, 16), bool)
    pick[4:8, 4:8] = True
    drawn = rock_surface(rock, _scene(_slope(0.4)), _textured(), None, code, pick)
    np.testing.assert_allclose(drawn[~pick], 0.35, atol=1e-6)
    assert not np.allclose(drawn[pick], 0.35, atol=1e-3)


def test_the_cliff_layer_takes_the_look_by_its_paint_weight():
    ground = _textured()
    weight = np.zeros((16, 16), np.uint8)
    weight[:, 8:] = 255
    ground.cliff_layer = weight
    albedo = np.full((16, 16, 3), 0.3, np.float32)
    drawn = cliff_layer(albedo, _scene(np.zeros((16, 16))), ground, lambda plane: plane)
    assert (drawn[:, :8] == albedo[:, :8]).all()
    assert not np.allclose(drawn[:, 8:], 0.3, atol=1e-3)
    ground.rock_look = None
    assert cliff_layer(albedo, _scene(np.zeros((16, 16))), ground, lambda p: p) is albedo


# ---------------------------------------------------------------------- the families' tops


def test_the_forest_top_takes_its_display_target_and_the_rest_keep_their_texture():
    raw = np.tile(np.array([0.1, 0.15, 0.07], np.float32), (len(FAMILIES), 1))
    top = top_targets(raw, {"forest": "#505936"}, PAINTED_PALETTE)
    np.testing.assert_allclose(
        oklab(top[FOREST]), display_to_ground(PAINTED_PALETTE, "#505936"), atol=2e-3
    )
    keep = [k for k in range(len(FAMILIES)) if k != FOREST]
    np.testing.assert_array_equal(top[keep], raw[keep])


def test_a_target_naming_no_rock_family_is_refused_by_name():
    raw = np.zeros((len(FAMILIES), 3), np.float32)
    with pytest.raises(ValueError, match=r"calibration\.tops names 'mossy', which is not"):
        top_targets(raw, {"mossy": "#505936"}, PAINTED_PALETTE)
    lab, codes = np.zeros((4, 4, 3), np.float32), np.zeros((4, 4), np.uint8)
    with pytest.raises(ValueError, match=r"calibration\.families names 'mossy', which is not"):
        family_targets(lab, codes, {"mossy": "#505936"}, PAINTED_PALETTE, 1)


def test_a_top_of_a_paint_layers_texture_wears_that_layers_target_where_it_stands():
    tiles = "/Game/FactoryGame/World/Environment/Landscape/Texture/Tiles/"
    families = {name: {"top": [0.56, 0.45, 0.33], "top_texture": tiles + path}
                for name, path in (("sand", "Sand/TX_Sand_BC"), ("grass", "Grass/TX_Grass_Far"),
                                   ("redgrass", "GrassRed/TX_GrassRed_01_Alb"),
                                   ("forest", "Forest/TX_Forest_Far_01_Alb"))}  # fmt: skip
    desert = np.array([[0.0, 1.0], [0.0, 1.0]], np.float32)
    area = lambda keys: desert if "Area_DuneDesert" in keys else np.zeros_like(desert)
    tops = layer_tops(families, PAINTED_PALETTE, area)
    assert set(tops) == {SAND, GRASS}, "the forest has its own target, red grass none"
    planes = np.stack(tops[SAND], -1)[0]
    for k, hex_colour in ((0, "#d5cbb6"), (1, "#c4ab8b")):
        np.testing.assert_allclose(oklab(planes[k]), display_to_ground(PAINTED_PALETTE, hex_colour),
                                   atol=2e-3)  # fmt: skip


def test_the_family_tables_take_the_tops_from_the_palette():
    families = {"forest": {"top": [0.1, 0.15, 0.07]}, "grass": {"top": [0.2, 0.3, 0.1]}}
    _tint, top, has = family_tables(families, PAINTED_PALETTE)
    tops = PAINTED_PALETTE["calibration"]["tops"]
    np.testing.assert_array_equal(
        top, top_targets(family_tables(families)[1], tops, PAINTED_PALETTE)
    )
    np.testing.assert_allclose(top[GRASS], [0.2, 0.3, 0.1])
    assert has[FOREST] == has[GRASS] == 1.0


def test_a_top_target_gives_a_top_to_a_family_the_store_has_none_for():
    families = {"redjungle": {"top": None}, "cliff": {"top": None}}
    _tint, top, has = family_tables(families, PAINTED_PALETTE)
    want = display_to_ground(PAINTED_PALETTE, PAINTED_PALETTE["calibration"]["tops"]["redjungle"])
    assert has[REDJUNGLE] == 1.0 and has[CLIFF] == 0.0
    np.testing.assert_allclose(oklab(top[REDJUNGLE]), want, atol=2e-3)
    assert family_tables(families)[2][REDJUNGLE] == 0.0, "without the palette, none"
