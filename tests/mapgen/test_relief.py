"""The relief style and the palette-only restyle. docs/spatial-and-map.md section 28.

Synthetic fixtures throughout: no install, no field on disk.
"""

from __future__ import annotations

import json

import numpy as np

from mapgen.cache import (
    CACHE_SIDECAR_NAME,
    DIRECT_CACHE_DIR_NAME,
    DIRECT_COVERAGE_NAME,
    DIRECT_Z_NAME,
    MESH_CACHE_DIR_NAME,
    PLANE_DTYPES,
    TOP_CACHE_DIR_NAME,
    TOP_PLANE_NAMES,
    mesh_stamp,
    missing_caches,
    raster_cache_stamp,
)
from mapgen.colour import oklab, srgb_to_linear
from mapgen.commands.renders import BIOME_LAYERS, LAYERS
from mapgen.palette.relief import (
    LUT_STEPS,
    oklab_from_lch,
    ramp_lut,
    relief_colours,
    water_tint_plane,
)
from mapgen.palette.styles import LAYER_STYLES, RELIEF_PALETTES, SHORE_OPTICS
from satisfactory_mcp.core.gameassets import versions
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.map_scenes import relief_ground, stub_field


def _scene(z_m: np.ndarray) -> dict:
    zero = np.zeros_like(z_m)
    return {
        "z_m": z_m,
        "spacing_m": 1.0,
        "borrow": np.ones_like(z_m),
        "water": {"cover": zero, "depth": zero, "depth_m": zero, "ocean": zero, "edge": zero},
    }


def _identity(plane):
    return plane


def test_the_dark_relief_is_the_one_relief_layer_and_has_a_dark_tone():
    assert "relief-dark" in LAYERS and "relief" not in LAYERS
    assert set(RELIEF_PALETTES) == {"relief-dark"}
    style = versions.STYLES[LAYER_STYLES["relief-dark"]]
    assert style["layer"] == "relief-dark" and style["version"] >= 1
    assert style["tone"] == "dark"
    assert SHORE_OPTICS["relief-dark"] == RELIEF_PALETTES["relief-dark"][0]["shore"]
    assert all(row["tone"] in ("light", "dark") for row in versions.STYLES.values())
    assert "relief-dark" not in BIOME_LAYERS


def test_the_ramp_is_spaced_evenly_in_oklab():
    lut = ramp_lut(RELIEF_PALETTES["relief-dark"][0]["ramp_lch"])
    steps = np.linalg.norm(np.diff(lut, axis=0), axis=1)
    assert lut.shape == (LUT_STEPS, 3)
    assert steps.max() / steps.mean() < 1.05
    assert np.all(np.diff(lut[:, 0]) > 0)


def test_the_dark_ramp_ends_warm_rather_than_mauve():
    stops = RELIEF_PALETTES["relief-dark"][0]["ramp_lch"]
    for lightness, chroma, hue in stops[-2:]:
        assert 60 <= hue <= 100 and chroma >= 0.04, (lightness, chroma, hue)


def test_flat_dry_ground_keeps_its_ramp_colour():
    """The shade term is measured from flat ground, so flat ground is the ramp itself."""
    ground = relief_ground("relief-dark")
    z = np.full((16, 16), 50.0, np.float32)
    got = relief_colours(_scene(z), ground, _identity, _identity)
    lab = oklab(srgb_to_linear(got[8, 8]))
    assert np.allclose(got, got[8, 8], atol=0.6)
    nearest = np.abs(ground.lut[:, 0] - lab[0]).argmin()
    assert np.allclose(lab, ground.lut[nearest], atol=0.01)


def test_a_slope_facing_away_from_the_sun_is_darker_and_cooler():
    ground = relief_ground("relief-dark")
    rows = np.arange(32, dtype=np.float32)[:, None] * np.ones((1, 32), np.float32)
    flat = relief_colours(_scene(np.full((32, 32), 50.0, np.float32)), ground, _identity, _identity)
    away = relief_colours(_scene(50.0 + 0.4 * (16.0 - rows)), ground, _identity, _identity)
    lab_flat = oklab(srgb_to_linear(flat[16, 16]))
    lab_away = oklab(srgb_to_linear(away[16, 16]))
    assert lab_away[0] < lab_flat[0]
    assert lab_away[2] < lab_flat[2]


def test_water_is_tinted_by_depth_and_blurred_over_the_shore():
    height = np.full((40, 40), -10.0, np.float32)
    height[:, :20] = -1.0
    grades = np.full((40, 40), hf.WATER_MEASURED, np.uint8)
    water = np.zeros((40, 40), np.float32)
    plane = water_tint_plane(
        stub_field(height, water, grades), RELIEF_PALETTES["relief-dark"][0]["water"]
    )
    assert plane.dtype == np.uint8
    assert plane[20, 2] < plane[20, 37]
    assert plane[20, 18] < plane[20, 22]
    assert plane[20, 19] > plane[20, 2]


def test_water_covers_the_ground_in_the_style_s_own_colours():
    ground = relief_ground("relief-dark")
    z = np.full((8, 8), 50.0, np.float32)
    scene = _scene(z)
    scene["water"]["cover"] = np.ones_like(z)
    ground.water = None
    got = relief_colours(scene, ground, _identity, _identity)
    lab = oklab(srgb_to_linear(got[4, 4]))
    assert np.allclose(lab, oklab_from_lch(ground.palette["water"]["shallow_lch"]), atol=0.01)


def test_biome_tints_move_the_ground_towards_the_biome():
    """A palette's tints move each area's ground; the dark relief's palette holds none."""
    names = ["Area_DuneDesert", "Area_TitanForest"]
    biome = {"area": np.repeat(np.array([[0, 1]], np.uint8), 64, 0).repeat(64, 1), "width": 128}
    tints = {"Area_DuneDesert": [0.04, 0.05, 78, 0.85], "Area_TitanForest": [-0.08, 0.05, 135, 0.8]}
    tinted = {**RELIEF_PALETTES["relief-dark"][0], "biome_tints": tints}
    ground = relief_ground("relief-dark", biome, names, tinted)
    assert ground.biome is not None and ground.biome.shape == (64, 128, 4)
    desert, forest = ground.biome[32, 2].astype(np.float32), ground.biome[32, 125]
    assert desert[0] > 0 > forest[0]
    assert relief_ground("relief-dark", biome, names).biome is None


def test_a_restyle_names_every_cache_it_cannot_use(tmp_path):
    stamp = raster_cache_stamp(64, 1, "build 1")
    meshes = mesh_stamp(64, "build 1", 1)
    assert missing_caches(tmp_path, stamp, meshes, top=True, meshes=True) == [
        DIRECT_CACHE_DIR_NAME,
        TOP_CACHE_DIR_NAME,
        MESH_CACHE_DIR_NAME,
    ]
    for name in (DIRECT_CACHE_DIR_NAME, TOP_CACHE_DIR_NAME):
        folder = tmp_path / name
        folder.mkdir()
        (folder / CACHE_SIDECAR_NAME).write_text(json.dumps(stamp), encoding="utf-8")
        planes = TOP_PLANE_NAMES if name == TOP_CACHE_DIR_NAME else (DIRECT_Z_NAME,
                                                                     DIRECT_COVERAGE_NAME)  # fmt: skip
        for plane in planes:
            np.zeros((64, 64), PLANE_DTYPES[plane]).tofile(folder / plane)
    assert missing_caches(tmp_path, stamp, meshes, top=True, meshes=False) == []
    assert missing_caches(tmp_path, stamp, meshes, top=True, meshes=True) == [MESH_CACHE_DIR_NAME]
    other = raster_cache_stamp(128, 1, "build 1")
    assert missing_caches(tmp_path, other, meshes, top=False, meshes=False) == [
        DIRECT_CACHE_DIR_NAME
    ]
