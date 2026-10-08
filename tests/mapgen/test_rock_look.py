"""The rock look: the install's rock textures at the run's mip level, read through the texel
kernel, and the CUDA twin's bits.

docs/map/painted.md section 30, "Rock textures". Synthetic textures throughout; the kernel tests skip, saying
why, on a machine without CuPy or a CUDA device, and the install test without the game.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mapgen import jit
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.gamedata.rocks.looks import (
    ARCH_TILE_M,
    CLIFF_ALBEDO_TILE_M,
    DESERT_TILE_M,
    LOOK_TEXTURES,
)
from mapgen.palette.painted.rock_look.atlas import (
    ALBEDO_ARCH,
    ALBEDO_CLIFF,
    ALBEDO_DESERT,
    RockLook,
    mip_level,
    rock_look,
)
from mapgen.palette.painted.rock_look.reference import (
    KIND_ARCH,
    KIND_CLIFF,
    KIND_DESERT,
    KIND_LAYER,
    LookPixels,
    look_texels,
)
from mapgen.terrain.texels import sample_texture
from tests.support.rock_textures import rock_textures

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

SAND = FAMILIES.index("sand")


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _look(**overrides: np.ndarray) -> RockLook:
    return rock_look(rock_textures(**overrides), 0.25)


def _pixels(n: int, kind: int | np.ndarray, seed: int = 1, top: int = -1) -> LookPixels:
    rng = np.random.default_rng(seed)
    normal = rng.normal(size=(n, 3)).astype(np.float32)
    normal[:, 2] = np.abs(normal[:, 2]) * 2
    normal /= np.linalg.norm(normal, axis=1, keepdims=True)
    kinds = np.broadcast_to(np.asarray(kind, np.uint8), (n,)).copy()
    return LookPixels(
        x_m=rng.uniform(0, 7500, n),
        y_m=rng.uniform(0, 7500, n),
        normal=normal,
        kind=kinds,
        top=np.full(n, top, np.int32),
    )


def _tile(look: RockLook, tile: int) -> np.ndarray:
    x0, y0, w, h = look.albedo.tiles[tile]
    return look.albedo.texels[y0 : y0 + h, x0 : x0 + w]


def _plain(look: RockLook, tile: int, px: LookPixels) -> np.ndarray:
    """The tile read flat at each pixel's position, over its median."""
    texels, span = _tile(look, tile), look.albedo_tiles_m[tile]
    side = texels.shape[0]
    u = ((px.x_m / span * side) % side).astype(np.float32)
    v = ((px.y_m / span * side) % side).astype(np.float32)
    return sample_texture(texels, u, v) / look.albedo_median[tile]


# ---------------------------------------------------------------------- the levels


def test_a_texture_is_read_at_the_level_of_the_pixel():
    assert mip_level(1024, 20.0, 0.2289) == 4, "64 texels a tile, 0.31 m each"
    assert mip_level(1024, 20.0, 0.01) == 0, "never finer than the texture"
    assert mip_level(1024, 20.0, 3.66) == 8, "the coarsest kept level: four texels a side"


def test_the_look_names_the_install_s_textures_and_the_material_spans():
    assert set(LOOK_TEXTURES) == {
        "cliff_albedo", "cliff_normal", "cliff_detail", "cells", "arch_albedo",
        "arch_normal", "desert_albedo", "desert_normal",
    }  # fmt: skip
    assert CLIFF_ALBEDO_TILE_M == 20.0 and math.isclose(ARCH_TILE_M, 8.93, abs_tol=0.01)
    look = _look()
    assert look.tops == {SAND: 4} and look.rules[SAND].tile_m == 50.0
    assert look.record["tiles_m"]["desert"] == DESERT_TILE_M


# ---------------------------------------------------------------------- the reference


def test_without_a_cell_the_body_is_its_texel_over_the_texture_s_median():
    look = _look(cells_alpha=0)
    px = _pixels(500, KIND_CLIFF)
    want = _plain(look, ALBEDO_CLIFF, px)
    np.testing.assert_allclose(look_texels(look, px).body, want, rtol=1e-5)


def test_a_cell_reads_the_body_turned_shifted_and_scaled():
    look = _look(cells_alpha=255, cells_rgb=(255, 0, 64))
    px = _pixels(400, KIND_CLIFF, seed=2)
    tile = _tile(look, ALBEDO_CLIFF)
    side, span = tile.shape[0], CLIFF_ALBEDO_TILE_M
    turn = 2 * math.pi * 64 / 255
    ua, vb = (px.x_m / span + 1.0) * 0.9, (px.y_m / span - 1.0) * 0.9
    u = (((math.cos(turn) * ua - math.sin(turn) * vb) * side) % side).astype(np.float32)
    v = (((math.sin(turn) * ua + math.cos(turn) * vb) * side) % side).astype(np.float32)
    want = sample_texture(tile, u, v) / look.albedo_median[ALBEDO_CLIFF]
    np.testing.assert_allclose(look_texels(look, px).body, want, rtol=1e-4, atol=1e-5)


def test_arches_and_desert_rock_wear_their_own_texture_without_the_cells():
    turned, plain = _look(cells_alpha=255), _look(cells_alpha=0)
    for kind, tile in ((KIND_ARCH, ALBEDO_ARCH), (KIND_DESERT, ALBEDO_DESERT)):
        px = _pixels(300, kind, seed=kind)
        got = look_texels(turned, px).body
        assert got.tobytes() == look_texels(plain, px).body.tobytes()
        np.testing.assert_allclose(got, _plain(plain, tile, px), rtol=1e-5)


def test_the_cliff_layer_takes_only_the_detail_finer_than_its_1m_colour():
    flat = _look(albedo=(90, 80, 70))
    np.testing.assert_allclose(look_texels(flat, _pixels(200, KIND_LAYER)).body, 1.0, rtol=1e-6)
    busy = look_texels(_look(), _pixels(4000, KIND_LAYER)).body
    assert 0.9 < float(np.median(busy)) < 1.1 and busy.std() > 0.01


def test_flat_normal_maps_leave_the_surface_s_normal_and_others_tilt_it():
    flat = _look(normals=(128, 128))
    px = _pixels(300, KIND_CLIFF, seed=3)
    np.testing.assert_allclose(look_texels(flat, px).normal, px.normal, atol=0.02)
    tilted = look_texels(_look(), px).normal
    np.testing.assert_allclose(np.linalg.norm(tilted, axis=1), 1.0, atol=1e-5)
    assert np.abs(tilted - px.normal).max() > 0.05


def test_a_top_layer_reads_its_own_texture_and_none_reads_one():
    look = _look()
    px = _pixels(500, KIND_CLIFF, top=look.tops[SAND])
    np.testing.assert_allclose(
        look_texels(look, px).top, _plain(look, look.tops[SAND], px), rtol=1e-5
    )
    np.testing.assert_array_equal(look_texels(look, _pixels(50, KIND_CLIFF)).top, 1.0)


# ---------------------------------------------------------------------- on the GPU


@pytest.mark.usefixtures("device")
def test_the_texel_kernel_on_the_device_gives_the_reference_s_bits():
    from mapgen.palette.painted.rock_look import gpu

    look = _look()
    kinds = np.random.default_rng(9).integers(1, 5, 20000)
    px = _pixels(20000, kinds, seed=4)
    top = np.where(np.arange(20000) % 3 == 0, look.tops[SAND], -1).astype(np.int32)
    px = px._replace(top=top)
    want, got = look_texels(look, px), gpu.look_texels(look, px)
    for name in ("body", "top", "normal"):
        assert getattr(got, name).tobytes() == getattr(want, name).tobytes(), name
    empty = _pixels(0, KIND_CLIFF)
    assert gpu.look_texels(look, empty).body.shape == (0, 3)


# ---------------------------------------------------------------------- the install


@pytest.mark.integration
def test_the_rock_textures_read_from_the_installed_game():
    from mapgen.common import DEFAULT_GAME
    from mapgen.gamedata.install import missing_container
    from mapgen.gamedata.rocks.looks import read_rock_textures
    from satisfactory_mcp.core.gameassets.container import open_container, paks_dir
    from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
    from satisfactory_mcp.core.gameassets.packages import ScriptObjects

    if missing_container(DEFAULT_GAME):
        pytest.skip(f"needs the installed game at {DEFAULT_GAME}")
    store = open_container(DEFAULT_GAME)
    scripts = ScriptObjects(paks_dir(DEFAULT_GAME), oodle_decompress)
    tiles = "/Game/FactoryGame/World/Environment/Landscape/Texture/Tiles/"
    tops = {"grass": tiles + "Grass/TX_Grass_Far_01_Alb", "sand": tiles + "Sand/TX_Sand_BC"}
    found = read_rock_textures(store, scripts, tops)
    assert set(found.textures) == set(LOOK_TEXTURES)
    assert all(t.ndim == 3 and t.shape[2] == 4 for t in found.textures.values())
    grass, sand = (found.tops[FAMILIES.index(name)][0] for name in ("grass", "sand"))
    assert grass.tile_m == pytest.approx(83.33, abs=0.01), "Cliff_Grass's own far tiling"
    assert sand.tile_m == 50.0 and (sand.power, sand.contrast) == (1.5, 1.2)
