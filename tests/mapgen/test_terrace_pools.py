"""The hot-spring terraces: their own texture, and their pools rasterised apart and drawn as the
terrace under the hot-spring water. docs/map/painted.md section 30, "The hot-spring terraces and
their pools". Synthetic meshes and textures; the install test skips without the game.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.colour import oklab
from mapgen.gamedata.rocks.looks import TERRACE_CUT, TERRACE_FADE, TERRACE_TILE_M
from mapgen.palette.painted.pools import POOL_DEPTH_M, pool_colour
from mapgen.palette.painted.rock_look.atlas import ALBEDO_TERRACE, rock_look, seamless_cut
from mapgen.palette.painted.surfaces import mesh_surface
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.terrain.render_meshes import (
    MESH_CLASS_MASK,
    MESH_FAMILY_SHIFT,
    MESH_TERRACE,
    MeshGroup,
    PreparedMeshes,
    rasterise_mesh_band,
)
from mapgen.terrain.terrace_pools import POOL_SUFFIX, TERRACE_POOL, split_pools
from tests.support.rock_textures import SIDE, rock_textures

TERRACE = "/Game/FactoryGame/World/Environment/HotSpring/Mesh/Hotspring_Plateau_02"
CREAM = np.array([0.6, 0.52, 0.4], np.float32)


def _terrace() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A 4 m square terrace, 1 m high, whose inner 2 m square is a pool 5 cm under its rim:
    verts (cm), triangles, and which triangles are the pool."""
    verts = np.array(
        [[-200, -200, 100], [200, -200, 100], [200, 200, 100], [-200, 200, 100],
         [-100, -100, 95], [100, -100, 95], [100, 100, 95], [-100, 100, 95]],
        np.float32,
    )  # fmt: skip
    rim = [[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    tris = np.array([[4, 5, 6], [4, 6, 7], *rim], np.int64)
    pool = np.zeros(len(tris), bool)
    pool[:2] = True
    return verts, tris, pool


def _prepared() -> PreparedMeshes:
    verts, tris, pool = _terrace()
    mats = np.eye(4, dtype=np.float32)[None].copy()
    mats[0, 3, :3] = (300.0, 300.0, 0.0)
    group = MeshGroup(np.array([MESH_TERRACE], np.uint16), mats, np.array([0.0]), np.array([600.0]))
    items, shapes = {TERRACE: group}, {TERRACE: (verts, tris)}
    counts = split_pools(items, shapes, {TERRACE: pool}, MESH_FAMILY_SHIFT)
    assert counts == {"Hotspring_Plateau_02": 2}
    return PreparedMeshes(items, shapes, families=True)


def test_the_pools_are_rasterised_as_the_terrace_with_the_pool_above_its_class():
    prepared = _prepared()
    assert (
        len(prepared.shapes[TERRACE][1]) == 8
        and len(prepared.shapes[TERRACE + POOL_SUFFIX][1]) == 2
    )
    z, code = rasterise_mesh_band(prepared, 0.0, 0.0, 25.0, 24, 24)
    cls, above = code & MESH_CLASS_MASK, code >> MESH_FAMILY_SHIFT
    assert (cls[np.isfinite(z)] == MESH_TERRACE).all(), "pool and rim are one class"
    centre, rim = (12, 12), (12, 5)
    assert above[centre] == TERRACE_POOL and z[centre] == pytest.approx(95.0)
    assert above[rim] == 0 and z[rim] > 95.0
    pool_texels = int((above == TERRACE_POOL).sum())
    assert 40 <= pool_texels <= 80, "the inner 2 m square at 25 cm a texel"


def test_a_pool_is_the_terrace_under_the_hot_spring_water():
    row = PAINTED_PALETTE["water_classes"]["hot_spring"]
    bed = np.broadcast_to(CREAM, (2, 2, 3))
    pool = pool_colour(bed, PAINTED_PALETTE)
    lab_pool, lab_bed = oklab(pool[0, 0]), oklab(CREAM)
    assert lab_pool[1] < 0 < lab_bed[1], "the cream turns turquoise (green over red)"
    assert lab_pool[0] < lab_bed[0], "the water darkens it"
    dark = pool_colour(np.zeros((1, 3), np.float32), PAINTED_PALETTE)
    light = pool_colour(np.ones((1, 3), np.float32), PAINTED_PALETTE)
    k = np.asarray(row["k_per_m"], np.float32)
    seen = (1 - row["turbidity"]) * np.exp(-k * POOL_DEPTH_M) * PAINTED_PALETTE["water"]["bed_wet"]
    far = 1 - np.exp(-POOL_DEPTH_M / row["deep_tau_m"])
    np.testing.assert_allclose(light - dark, seen * np.asarray(row["bed_tint"]) * (1 - far)
                               * np.ones((1, 3)), rtol=1e-5)  # fmt: skip
    plain = {key: value for key, value in PAINTED_PALETTE.items() if key != "water_classes"}
    np.testing.assert_array_equal(pool_colour(bed, plain), bed, err_msg="no row, no water")


def _ground(look=None) -> SimpleNamespace:
    return SimpleNamespace(
        palette=PAINTED_PALETTE, rock_look=look, mesh_rgb={MESH_TERRACE: CREAM},
        shell_family_rgb={}, coral_rgb={},
    )  # fmt: skip


def _scene(family: list[int]) -> dict[str, object]:
    shape = (4, len(family))
    return {
        "z_m": np.zeros(shape, np.float32), "grid": (slice(0, 4), 0, 4, 0, len(family), 2.0),
        "mesh_weight": np.ones(shape, np.float32),
        "mesh_class": np.full(shape, MESH_TERRACE, np.uint8),
        "mesh_family": np.tile(np.array([family], np.uint8), (4, 1)),
        "water": {"cover": np.zeros(shape, np.float32)},
    }  # fmt: skip


def test_the_terrace_wears_its_texture_and_its_pools_the_water():
    g = np.zeros((4, 64, 3), np.float32)
    scene = _scene([0] * 48 + [TERRACE_POOL] * 16)
    same = lambda plane: plane
    flat = mesh_surface(g, g + 0.25, scene, _ground(), same)
    np.testing.assert_allclose(flat[:, :48], np.broadcast_to(CREAM, (4, 48, 3)), err_msg="rim")
    np.testing.assert_allclose(flat[:, 48:], pool_colour(np.broadcast_to(CREAM, (4, 16, 3)),
                               PAINTED_PALETTE), rtol=1e-6, err_msg="pool")  # fmt: skip
    textured = mesh_surface(g, g + 0.25, scene, _ground(rock_look(rock_textures(), 0.25)), same)
    rim = textured[:, :48].reshape(-1, 3)
    assert rim.std(axis=0).min() > 0.01, "the rim is textured, not flat"
    even = rock_look(rock_textures(albedo=(150, 130, 100), normals=(128, 128)), 0.25)
    np.testing.assert_allclose(mesh_surface(g, g + 0.25, scene, _ground(even), same)[:, :48],
                               flat[:, :48], rtol=5e-3, err_msg="the texture keeps the colour")  # fmt: skip
    pool = textured[:, 48:]
    assert pool.reshape(-1, 3).std(axis=0).max() < rim.std(axis=0).min(), "the water hides it"
    assert np.median(oklab(pool)[..., 1]) < 0, "turquoise"


def test_the_terrace_texture_is_a_cut_of_its_material_s_albedo():
    textures = rock_textures()
    own = textures.textures["terrace_albedo"][..., :3].astype(np.float32) / 255
    cut = seamless_cut(own, TERRACE_CUT, TERRACE_FADE)
    n = round(TERRACE_CUT[2] * SIDE)
    r0, c0, m = (
        round(TERRACE_CUT[1] * SIDE),
        round(TERRACE_CUT[0] * SIDE),
        round(TERRACE_FADE * SIDE),
    )
    assert cut.shape == (n, n, 3) and r0 + n + m <= SIDE and c0 + n + m <= SIDE
    look = rock_look(textures, 0.25)
    assert look.albedo_tiles_m[ALBEDO_TERRACE] == TERRACE_TILE_M
    assert 6.0 < TERRACE_TILE_M < 8.0, "a quarter of a 27 m UV unit"


@pytest.mark.integration
def test_the_terrace_meshes_pools_read_from_the_installed_game():
    from mapgen.common import DEFAULT_GAME
    from mapgen.gamedata.install import missing_container, open_game
    from mapgen.terrain.render_meshes import read_shape
    from mapgen.terrain.terrace_pools import pool_masks

    if missing_container(DEFAULT_GAME) is not None:
        pytest.skip(f"no game install at {DEFAULT_GAME}")
    game = open_game(DEFAULT_GAME)
    leaves = ("Plateau_01", "Plateau_02", "Plateau_03", "Origin_01", "Staligtite_01")
    meshes = [
        f"/Game/FactoryGame/World/Environment/HotSpring/Mesh/Hotspring_{leaf}" for leaf in leaves
    ]
    shapes = {}
    for mesh in meshes:
        shape, _source = read_shape(game.store, game.scripts, game.index, mesh)
        assert shape is not None
        shapes[mesh] = shape
    masks = pool_masks(game.store, game.scripts, game.index, shapes)
    counts = {
        mesh.rsplit("/", 1)[-1].removeprefix("Hotspring_"): int(mask.sum())
        for mesh, mask in masks.items()
    }
    assert counts == {"Plateau_01": 224, "Plateau_02": 195, "Plateau_03": 78, "Origin_01": 69}
    verts, tris = shapes[meshes[1]]
    pool_z = verts[tris[masks[meshes[1]]]][..., 2]
    assert np.ptp(pool_z) < 1e-3, "a pool is a level plane"
