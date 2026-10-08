"""Where the relief styles, the live sun, the calibrated tone and the Titan trees meet.

Synthetic fixtures throughout. docs/spatial-and-map.md sections 28 to 31.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from mapgen.cache import (
    CACHE_SIDECAR_NAME,
    DIRECT_CACHE_DIR_NAME,
    MESH_CACHE_DIR_NAME,
    MESH_CLASS_NAME,
    MESH_Z_NAME,
    TITAN_CACHE_DIR_NAME,
    TITAN_FACTOR,
    TOP_CACHE_DIR_NAME,
    mesh_stamp,
    restyle_gaps,
)
from mapgen.colour import tone as shader_tone
from mapgen.colour import untone as shader_untone
from mapgen.lighting.hillshade import SUN_ALTITUDE_DEG, sun_dot
from mapgen.lighting.model import apply_terms
from mapgen.palette.lightparams import shader_light
from mapgen.palette.painted.band import painted_ndl
from mapgen.palette.relief import FLAT_LIT, _shade
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_ROCK
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from tests.support.map_scenes import relief_ground


def test_relief_drawn_unlit_carries_no_sun_term():
    ground = relief_ground("relief")
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
    shaded = shader_tone(y, t["knee"], t["white"])
    back = shader_untone(shaded, t["knee"], t["white"])
    np.testing.assert_allclose(back, y, atol=2e-4)
    assert shader_light("relief")["tone_knee"] == 1.0


def test_a_bright_painted_pixel_survives_a_flat_relight():
    colour = np.array([[[250, 240, 200], [40, 60, 30]]], np.uint8)
    ones = np.ones((1, 2), np.float32)
    flat = np.float32(1.0)
    out = apply_terms(colour, ones, ones * flat, ones, shader_light("painted"))
    assert np.abs(out.astype(int) - colour.astype(int)).max() <= 2


def _bowl(n=81, sp=0.25, radius_px=30):
    """A coral plate 7.5 m across standing in the sea: its rim 2.25 m above its middle."""
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    r_m = np.hypot(yy - n // 2, xx - n // 2) * sp
    inside = r_m < radius_px * sp
    z = np.where(inside, 10.0 + 0.04 * r_m * r_m, -20.0).astype(np.float32)
    kept = np.where(inside, MESH_CORAL, 0).astype(np.uint8)
    return z, sp, inside.astype(np.float32), kept


def test_painted_keeps_the_default_sun_on_a_mesh_only_it_draws_in_the_water():
    z, sp, weight, kept = _bowl()
    sea = np.full(z.shape, OCEAN_LEVEL_M, np.float32)
    flat = np.float32(np.sin(np.radians(SUN_ALTITUDE_DEG)))
    np.testing.assert_array_equal(
        painted_ndl(z, sp, False, (weight, kept, sea, None)), sun_dot(z, sp)
    )
    np.testing.assert_array_equal(painted_ndl(z, sp, True, (None, None, sea, None)), flat)
    dry = np.full(z.shape, np.nan, np.float32)
    np.testing.assert_array_equal(painted_ndl(z, sp, True, (weight, kept, dry, None)), flat)
    rock = np.where(kept > 0, MESH_ROCK, 0).astype(np.uint8)
    np.testing.assert_array_equal(painted_ndl(z, sp, True, (weight, rock, sea, None)), flat)
    got = painted_ndl(z, sp, True, (weight, kept, sea, None))
    assert got[2, 2] == pytest.approx(flat), "the sea around it is the pyramid's"
    north_east, south_west = got[28, 52], got[52, 28]
    assert north_east > flat > south_west, "the noon sun in the south-west lights the far wall"


def _write_meshes(folder, stamp):
    folder.mkdir(parents=True)
    size = stamp["size"]
    (folder / CACHE_SIDECAR_NAME).write_text(json.dumps(stamp), encoding="utf-8")
    np.zeros((size, size), np.float32).tofile(folder / MESH_Z_NAME)
    np.zeros((size, size), np.uint8).tofile(folder / MESH_CLASS_NAME)


def test_a_restyle_needs_the_titan_cache_only_when_it_draws_titan_trees(tmp_path):
    gaps = restyle_gaps(tmp_path, 64, 1, "b", top=False, meshes=False, titan=True)
    assert gaps == [DIRECT_CACHE_DIR_NAME, TITAN_CACHE_DIR_NAME]
    assert restyle_gaps(tmp_path, 64, 1, "b", top=True, meshes=True, titan=False) == [
        DIRECT_CACHE_DIR_NAME,
        TOP_CACHE_DIR_NAME,
        MESH_CACHE_DIR_NAME,
    ]
    titan = mesh_stamp(64 // TITAN_FACTOR, "b", READER_VERSIONS["titan_trees"])
    _write_meshes(tmp_path / TITAN_CACHE_DIR_NAME, titan)
    assert TITAN_CACHE_DIR_NAME not in restyle_gaps(
        tmp_path, 64, 1, "b", top=False, meshes=False, titan=True
    )
    assert TITAN_CACHE_DIR_NAME in restyle_gaps(
        tmp_path, 64, 1, "other", top=False, meshes=False, titan=True
    )
