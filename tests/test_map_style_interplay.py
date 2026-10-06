"""Where the relief styles, the live sun, the calibrated tone and the Titan trees meet.

Synthetic fixtures throughout. docs/spatial-and-map.md sections 28 to 31.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.cache import (  # noqa: E402
    DIRECT_CACHE_DIR_NAME,
    MESH_CACHE_DIR_NAME,
    MESH_CACHE_SIDECAR,
    MESH_CLASS_NAME,
    MESH_Z_NAME,
    TITAN_CACHE_DIR_NAME,
    TITAN_FACTOR,
    TOP_CACHE_DIR_NAME,
    mesh_stamp,
    restyle_gaps,
)
from mapgen.lighting.model import _tone, _untone, apply_terms  # noqa: E402
from mapgen.palette.lightparams import shader_light  # noqa: E402
from mapgen.palette.painted import tone  # noqa: E402
from mapgen.palette.relief import FLAT_LIT, _shade  # noqa: E402
from mapgen.palette.styles import PAINTED_PALETTE  # noqa: E402
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS  # noqa: E402
from tests.test_map_relief import _ground  # noqa: E402


def test_relief_drawn_unlit_carries_no_sun_term():
    ground = _ground("relief")
    rows = np.arange(16, dtype=np.float32)[:, None] * np.ones((1, 16), np.float32)
    slope = 50.0 + 0.6 * (8.0 - rows)
    lab = np.full((16, 16, 3), 0.6, np.float32)
    lab[..., 1:] = 0.01
    lit_lab, lit = _shade(lab.copy(), slope, 1.0, ground)
    flat_lab, flat = _shade(lab.copy(), slope, 1.0, ground, unlit=True)
    assert not np.allclose(lit, FLAT_LIT)
    np.testing.assert_allclose(flat, FLAT_LIT)
    np.testing.assert_allclose(flat_lab, lab, atol=1e-6)
    assert not np.allclose(lit_lab, lab)


def test_the_shader_tone_is_the_painted_style_s_own_and_inverts():
    params = shader_light("painted")
    t = PAINTED_PALETTE["tone"]
    assert (params["tone_knee"], params["tone_white"]) == (t["knee"], t["white"])
    y = np.linspace(0.0, 1.5, 61, dtype=np.float32)
    np.testing.assert_allclose(_tone(y, t["knee"], t["white"]), tone(y, t["knee"], t["white"]),
                               atol=1e-6)  # fmt: skip
    back = _untone(_tone(y, t["knee"], t["white"]), t["knee"], t["white"])
    np.testing.assert_allclose(back, y, atol=2e-4)
    assert shader_light("relief")["tone_knee"] == 1.0


def test_a_bright_painted_pixel_survives_a_flat_relight():
    colour = np.array([[[250, 240, 200], [40, 60, 30]]], np.uint8)
    ones = np.ones((1, 2), np.float32)
    flat = np.float32(1.0)
    out = apply_terms(colour, ones, ones * flat, ones, shader_light("painted"))
    assert np.abs(out.astype(int) - colour.astype(int)).max() <= 2


def _write_meshes(folder, stamp):
    folder.mkdir(parents=True)
    size = stamp["size"]
    (folder / MESH_CACHE_SIDECAR).write_text(json.dumps(stamp), encoding="utf-8")
    np.zeros((size, size), np.float32).tofile(folder / MESH_Z_NAME)
    np.zeros((size, size), np.uint8).tofile(folder / MESH_CLASS_NAME)


def test_a_restyle_needs_the_titan_cache_only_when_it_draws_titan_trees(tmp_path):
    gaps = restyle_gaps(tmp_path, 64, 1, "b", False, False, True)
    assert gaps == [DIRECT_CACHE_DIR_NAME, TITAN_CACHE_DIR_NAME]
    assert restyle_gaps(tmp_path, 64, 1, "b", True, True, False) == [
        DIRECT_CACHE_DIR_NAME,
        TOP_CACHE_DIR_NAME,
        MESH_CACHE_DIR_NAME,
    ]
    titan = mesh_stamp(64 // TITAN_FACTOR, "b", READER_VERSIONS["titan_trees"])
    _write_meshes(tmp_path / TITAN_CACHE_DIR_NAME, titan)
    assert TITAN_CACHE_DIR_NAME not in restyle_gaps(tmp_path, 64, 1, "b", False, False, True)
    assert TITAN_CACHE_DIR_NAME in restyle_gaps(tmp_path, 64, 1, "other", False, False, True)
