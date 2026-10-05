"""The relief styles and the palette-only restyle. docs/spatial-and-map.md section 28.

Synthetic fixtures throughout: no install, no field on disk.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.cache import (  # noqa: E402
    DIRECT_CACHE_DIR_NAME,
    DIRECT_CACHE_SIDECAR,
    DIRECT_COVERAGE_NAME,
    DIRECT_Z_NAME,
    MESH_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    direct_cache_stamp,
    mesh_stamp,
    missing_caches,
)
from mapgen.palette.painted import oklab, srgb_to_linear  # noqa: E402
from mapgen.palette.relief import (  # noqa: E402
    LUT_STEPS,
    ReliefGround,
    lch,
    ramp_lut,
    relief_colours,
    water_tint_plane,
)
from mapgen.palette.styles import LAYER_STYLES, RELIEF_PALETTES, SHORE_OPTICS  # noqa: E402
from mapgen.pipeline import BIOME_LAYERS, LAYERS  # noqa: E402
from satisfactory_mcp.core.gameassets import versions  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402


def _field(height_m: np.ndarray, water_m: np.ndarray | None = None, grades=None):
    height = (height_m * hf.DM_PER_M).astype(np.int16)
    water = None if water_m is None else (water_m * hf.DM_PER_M).astype(np.int16)
    rows, cols = height.shape
    return SimpleNamespace(
        _height_dm=height,
        _water_raster=lambda: water,
        _water_quality_raster=lambda: grades,
        spacing_cm=100.0,
        width=cols,
        height=rows,
    )


def _scene(z_m: np.ndarray) -> dict:
    zero = np.zeros_like(z_m)
    return {
        "z_m": z_m,
        "spacing_m": 1.0,
        "borrow": np.ones_like(z_m),
        "water": {"cover": zero, "depth": zero, "depth_m": zero, "ocean": zero, "edge": zero},
    }


def _ground(layer: str, biome=None, names=()) -> ReliefGround:
    ramp = np.linspace(0.0, 100.0, 64, dtype=np.float32)
    return ReliefGround(RELIEF_PALETTES[layer][0], _field(np.tile(ramp, (64, 1))), biome, list(names))


def _identity(plane):
    return plane


def test_both_relief_layers_are_registered_styles_with_a_tone():
    for layer in ("relief", "relief-dark"):
        assert layer in LAYERS
        style = versions.STYLES[LAYER_STYLES[layer]]
        assert style["layer"] == layer and style["version"] >= 1
        assert SHORE_OPTICS[layer] == RELIEF_PALETTES[layer][0]["shore"]
    assert versions.STYLES[LAYER_STYLES["relief"]]["tone"] == "light"
    assert versions.STYLES[LAYER_STYLES["relief-dark"]]["tone"] == "dark"
    assert all(row["tone"] in ("light", "dark") for row in versions.STYLES.values())
    assert "relief" in BIOME_LAYERS and "relief-dark" not in BIOME_LAYERS


def test_the_ramp_is_spaced_evenly_in_oklab():
    lut = ramp_lut(RELIEF_PALETTES["relief"][0]["ramp_lch"])
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
    for layer in ("relief", "relief-dark"):
        ground = _ground(layer)
        z = np.full((16, 16), 50.0, np.float32)
        got = relief_colours(_scene(z), ground, _identity, _identity)
        lab = oklab(srgb_to_linear(got[8, 8]))
        assert np.allclose(got, got[8, 8], atol=0.6)
        nearest = np.abs(ground.lut[:, 0] - lab[0]).argmin()
        assert np.allclose(lab, ground.lut[nearest], atol=0.01)


def test_a_slope_facing_away_from_the_sun_is_darker_and_cooler():
    ground = _ground("relief")
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
    plane = water_tint_plane(_field(height, water, grades), RELIEF_PALETTES["relief"][0]["water"])
    assert plane.dtype == np.uint8
    assert plane[20, 2] < plane[20, 37]
    assert plane[20, 18] < plane[20, 22]
    assert plane[20, 19] > plane[20, 2]


def test_water_covers_the_ground_in_the_style_s_own_colours():
    ground = _ground("relief")
    z = np.full((8, 8), 50.0, np.float32)
    scene = _scene(z)
    scene["water"]["cover"] = np.ones_like(z)
    ground.water = None
    got = relief_colours(scene, ground, _identity, _identity)
    lab = oklab(srgb_to_linear(got[4, 4]))
    assert np.allclose(lab, lch(ground.palette["water"]["shallow_lch"]), atol=0.01)


def test_biome_tints_move_the_ground_towards_the_biome():
    names = ["Area_DuneDesert", "Area_TitanForest"]
    biome = {"area": np.repeat(np.array([[0, 1]], np.uint8), 64, 0).repeat(64, 1), "width": 128}
    ground = _ground("relief", biome, names)
    assert ground.biome is not None and ground.biome.shape == (64, 128, 4)
    desert, forest = ground.biome[32, 2].astype(np.float32), ground.biome[32, 125]
    assert desert[0] > 0 > forest[0]
    assert _ground("relief-dark", biome, names).biome is None


def test_a_restyle_names_every_cache_it_cannot_use(tmp_path):
    stamp = direct_cache_stamp(64, 1, "build 1")
    meshes = mesh_stamp(64, "build 1", 1)
    assert missing_caches(tmp_path, stamp, meshes, True, True) == [
        DIRECT_CACHE_DIR_NAME,
        TOP_CACHE_DIR_NAME,
        MESH_CACHE_DIR_NAME,
    ]
    for name in (DIRECT_CACHE_DIR_NAME, TOP_CACHE_DIR_NAME):
        folder = tmp_path / name
        folder.mkdir()
        (folder / DIRECT_CACHE_SIDECAR).write_text(json.dumps(stamp), encoding="utf-8")
        np.zeros((64, 64), np.float32).tofile(folder / DIRECT_Z_NAME)
        np.zeros((64, 64), np.uint8).tofile(folder / DIRECT_COVERAGE_NAME)
    assert missing_caches(tmp_path, stamp, meshes, True, False) == []
    assert missing_caches(tmp_path, stamp, meshes, True, True) == [MESH_CACHE_DIR_NAME]
    other = direct_cache_stamp(128, 1, "build 1")
    assert missing_caches(tmp_path, other, meshes, False, False) == [DIRECT_CACHE_DIR_NAME]
