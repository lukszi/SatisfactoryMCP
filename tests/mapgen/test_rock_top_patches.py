"""The moss on the rock tops of the game-painted style: in patches anchored to the world,
more of it on flatter faces, and the forest top in its own display colour.

docs/spatial-and-map.md sections 30 and 31. Synthetic fixtures: no install.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.colour import oklab
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.palette.painted.calibration import display_to_ground
from mapgen.palette.painted.surfaces import family_tables, rock_surface, top_cover, top_targets
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.terrain.sample import patch_noise

RULE = PAINTED_PALETTE["rock_top"]
PATCHES = RULE["patches"]
FOREST = FAMILIES.index("forest")
GRASS = FAMILIES.index("grass")
MOSS = (0.05, 0.08, 0.03)


def _ground(rule=RULE):
    n = len(FAMILIES)
    top = np.zeros((n, 3), np.float32)
    has = np.zeros(n, np.float32)
    top[FOREST], has[FOREST] = MOSS, 1.0
    return SimpleNamespace(
        rock_family=None, family_rock={}, family_tint=np.ones((n, 3), np.float32),
        family_top=top, family_has_top=has, palette={"rock_top": rule},
    )  # fmt: skip


def _cover(z, lo=0, c0=0, spacing=0.25, rule=RULE):
    rows, cols = z.shape
    scene = {"z_m": np.asarray(z, np.float32),
             "grid": (slice(0, rows), lo, lo + rows, c0, c0 + cols, spacing)}  # fmt: skip
    return top_cover(scene, _ground(rule), np.full(z.shape, FOREST, np.uint8))


def _relief(rows, cols, spacing=0.25):
    y, x = np.mgrid[0:rows, 0:cols] * spacing
    return 1.2 * np.sin(x / 3.0) + 0.9 * np.cos(y / 2.3) + 0.3 * np.sin((x + y) / 1.1)


def test_the_palette_draws_the_tops_in_patches_and_the_forest_top_dark():
    assert RULE["up"] == [0.6, 0.85]
    assert {"seed", "octaves_m", "level", "soft", "flat", "flat_gain"} <= set(PATCHES)
    assert PAINTED_PALETTE["calibration"]["tops"] == {"forest": "#505936"}


def test_the_mask_is_the_same_on_every_call_and_moves_with_the_seed():
    x, y = np.linspace(0, 500, 4001), np.linspace(-20, 900, 4001)
    first = patch_noise(x, y, PATCHES["octaves_m"], PATCHES["seed"])
    np.testing.assert_array_equal(first, patch_noise(x, y, PATCHES["octaves_m"], PATCHES["seed"]))
    other = patch_noise(x, y, PATCHES["octaves_m"], PATCHES["seed"] + 1)
    assert np.abs(first - other).mean() > 0.05
    assert 0.0 <= first.min() and first.max() <= 1.0


def test_the_mask_has_no_jump_at_a_lattice_line():
    x = np.arange(0.0, 200.0, 0.05)
    noise = patch_noise(x, np.full_like(x, 37.3), PATCHES["octaves_m"], PATCHES["seed"])
    assert np.abs(np.diff(noise)).max() < 0.03


def test_bands_and_tiles_drawn_apart_agree_with_the_whole():
    z = _relief(96, 128)
    whole = _cover(z)
    assert 0.0 < whole.mean() < 1.0
    halo = 8
    for top, bottom in ((0, 40), (40, 96)):
        lo, hi = max(top - halo, 0), min(bottom + halo, 96)
        band = _cover(z[lo:hi], lo=lo)
        np.testing.assert_array_equal(band[top - lo : bottom - lo], whole[top:bottom])
    for left, right in ((0, 64), (64, 128)):
        c0, c1 = max(left - halo, 0), min(right + halo, 128)
        tile = _cover(z[:, c0:c1], c0=c0)
        np.testing.assert_array_equal(tile[:, left - c0 : right - c0], whole[:, left:right])


def test_a_world_point_draws_the_same_at_any_size():
    fine = _cover(np.zeros((90, 120)), spacing=0.25)
    coarse = _cover(np.zeros((30, 40)), spacing=0.75)
    np.testing.assert_array_equal(fine[1::3, 1::3], coarse)
    shifted = _cover(np.zeros((30, 40)), lo=10, c0=20, spacing=0.75)
    np.testing.assert_array_equal(shifted[:20, :20], coarse[10:, 20:])


def test_flatter_faces_carry_more_moss_and_steep_faces_none():
    flat = _cover(np.zeros((400, 400)), spacing=0.5)
    for nz, less in ((0.95, True), (0.88, True)):
        slope = np.sqrt(1.0 / (nz * nz) - 1.0)
        tilted = _cover(np.tile(np.arange(400) * 0.5 * slope, (400, 1)), spacing=0.5)
        assert tilted[2:-2, 2:-2].mean() < flat.mean() - 0.03
    wall = _cover(np.tile(np.arange(400) * 0.5 * 3.0, (400, 1)), spacing=0.5)
    assert wall[2:-2, 2:-2].max() == 0.0


def test_flat_rock_is_mossy_in_patches_at_the_screenshots_share():
    cover = _cover(np.zeros((600, 600)), spacing=0.5)
    moss = cover >= 0.5
    assert 0.35 <= moss.mean() <= 0.55
    boxes = moss.reshape(10, 60, 10, 60).mean(axis=(1, 3))
    assert 0.05 <= np.quantile(boxes, 0.1) and np.quantile(boxes, 0.9) <= 0.8
    assert ((cover > 0.0) & (cover < 1.0)).mean() < 0.2, "patches with soft edges, not a haze"


def test_without_patches_a_flat_top_is_moss_all_over():
    whole = {"up": RULE["up"]}
    np.testing.assert_array_equal(_cover(np.zeros((8, 8)), rule=whole), 1.0)


def test_between_the_patches_the_rock_stays_bare():
    rock = np.full((64, 64, 3), 0.4, np.float32)
    z = np.zeros((64, 64), np.float32)
    scene = {"z_m": z, "grid": (slice(0, 64), 0, 64, 0, 64, 0.5)}
    code = np.full((64, 64), FOREST, np.uint8)
    out = rock_surface(rock, scene, _ground(), None, code)
    cover = top_cover(scene, _ground(), code)
    bare, moss = cover == 0.0, cover == 1.0
    assert bare.any() and moss.any()
    np.testing.assert_allclose(out[bare], 0.4)
    np.testing.assert_allclose(out[moss], np.tile(MOSS, (int(moss.sum()), 1)), atol=1e-6)
    grass = np.full((64, 64), GRASS, np.uint8)
    np.testing.assert_allclose(rock_surface(rock, scene, _ground(), None, grass), 0.4)


def test_the_forest_top_takes_its_display_target_and_the_rest_keep_their_texture():
    raw = np.tile(np.array([0.1, 0.15, 0.07], np.float32), (len(FAMILIES), 1))
    top = top_targets(raw, {"forest": "#505936"}, PAINTED_PALETTE)
    np.testing.assert_allclose(
        oklab(top[FOREST]), display_to_ground(PAINTED_PALETTE, "#505936"), atol=2e-3
    )
    keep = [k for k in range(len(FAMILIES)) if k != FOREST]
    np.testing.assert_array_equal(top[keep], raw[keep])
    np.testing.assert_array_equal(top_targets(raw, {}, PAINTED_PALETTE), raw)


def test_the_family_tables_take_the_tops_from_the_palette():
    families = {"forest": {"top": [0.1, 0.15, 0.07]}, "grass": {"top": [0.2, 0.3, 0.1]}}
    _tint, top, has = family_tables(families, PAINTED_PALETTE)
    np.testing.assert_array_equal(
        top, top_targets(family_tables(families)[1], {"forest": "#505936"}, PAINTED_PALETTE)
    )
    np.testing.assert_allclose(top[GRASS], [0.2, 0.3, 0.1])
    assert has[FOREST] == has[GRASS] == 1.0
