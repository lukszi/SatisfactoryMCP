"""The seabed coral carpet: harvest planes, the paint-store round trip and the bed hook.

docs/spatial-and-map.md section 32. Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.colour import srgb_to_linear
from mapgen.gamedata.vegetation.carpet import (
    COVER_NAME,
    TOP_NAME,
    carpet_planes,
    footprint,
    is_carpet,
)
from mapgen.palette.painted.optics import carpet_bed, load_carpet
from mapgen.palette.painted.shapes import UnderwaterScene
from mapgen.palette.styles import PAINTED_PALETTE
from satisfactory_mcp.core.gameassets import versions
from satisfactory_mcp.domain.spatial import heightfield as hf

CARPET = {
    "colour": [108, 158, 190],
    "strength": 1.0,
    "depth_scale": 0.2,
    "blur_m": 1.0,
    "gain": 3.0,
}


def _cross(half=100.0, height=50.0):
    """Two vertical cards crossing at the origin: no plan area of their own."""
    verts = np.array(
        [[-half, 0, 0], [half, 0, 0], [half, 0, height], [-half, 0, height],
         [0, -half, 0], [0, half, 0], [0, half, height], [0, -half, height]],
        np.float64,
    )  # fmt: skip
    tris = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    return verts, tris


def _instance(x_cm, y_cm, z_cm, scale=1.0):
    m = np.eye(4)
    m[:3, :3] *= scale
    m[3, :3] = (x_cm, y_cm, z_cm)
    return m


def test_the_carpet_is_the_crater_grass_and_nothing_else():
    assert is_carpet(
        "/Game/FactoryGame/World/Environment/Foliage/Grass/Crater_Grass_01/SM_CraterGrass_01"
    )
    assert not is_carpet("/Game/FactoryGame/World/Environment/Foliage/Coral/CraterBush/CraterBush")
    assert not is_carpet(
        "/Game/FactoryGame/World/Environment/Foliage/Grass/Grass_03/SM_Grass_03_02"
    )


def test_the_footprint_is_the_plan_hull_of_upright_cards():
    verts, _tris = _cross()
    points, top = footprint(verts, step_cm=10.0)
    assert top == 50.0
    area = len(points) * 100.0
    assert area == pytest.approx(2 * 100.0**2, rel=0.15), "a diamond, not two lines"
    assert np.abs(points).sum(1).max() <= 100.0


def test_planes_hold_the_covered_share_and_the_rosette_top():
    points = np.array([[x, y] for x in (25.0, 75.0) for y in (25.0, 75.0)])
    shapes = {"m": (points, 50.0)}
    instances = {"m": np.array([_instance(200.0, 300.0, -1700.0)])}
    cover, top = carpet_planes(instances, shapes, 8, 0.0, 0.0, 100.0, 50.0)
    assert cover[3, 2] == 255 and np.count_nonzero(cover) == 1
    assert top[3, 2] == -165 and top[0, 0] == hf.NODATA
    half = {"m": np.array([_instance(150.0, 300.0, -1700.0, 0.5)])}
    cover, top = carpet_planes(half, shapes, 8, 0.0, 0.0, 100.0, 50.0)
    assert cover[3, 1] == round(0.25 * 255) and top[3, 1] == -168


def test_off_grid_instances_are_dropped():
    shapes = {"m": (np.array([[0.0, 0.0]]), 10.0)}
    far = {"m": np.array([_instance(-500.0, 50.0, 0.0), _instance(50.0, 9000.0, 0.0)])}
    cover, top = carpet_planes(far, shapes, 4, 0.0, 0.0, 100.0, 10.0)
    assert not cover.any() and (top == hf.NODATA).all()


def _store(tmp_path, cover, top):
    files = {}
    for name, grid, kind in ((COVER_NAME, cover, "u8"), (TOP_NAME, top, "i16")):
        blob = hf.encode_u8(grid) if kind == "u8" else hf.encode_i16(grid)
        (tmp_path / name).write_bytes(blob)
        files[name] = {"shape": list(grid.shape), "kind": kind}
    return {"files": files}


def test_the_store_round_trips_and_spreads_rosettes_into_patches(tmp_path):
    cover = np.zeros((16, 16), np.uint8)
    top = np.full((16, 16), hf.NODATA, np.int16)
    cover[8, 8], top[8, 8] = 255, -165
    meta = _store(tmp_path, cover, top)
    got_cover, got_top = load_carpet(tmp_path, meta, {"carpet": CARPET})
    assert got_cover[8, 8] > 0 and got_cover[8, 9] > 0, "a rosette spreads into its patch"
    assert got_cover[0, 0] == 0
    assert float(got_top[8, 9]) == pytest.approx(-16.5, abs=0.02), "gaps take a neighbour's top"
    assert float(got_top[0, 0]) <= -999.0


def test_an_old_paint_store_or_a_switched_off_style_draws_no_carpet(tmp_path):
    assert load_carpet(tmp_path, {"files": {}}, {"carpet": CARPET}) is None
    meta = _store(tmp_path, np.zeros((4, 4), np.uint8), np.zeros((4, 4), np.int16))
    assert load_carpet(tmp_path, meta, {"carpet": {**CARPET, "strength": 0.0}}) is None
    assert load_carpet(tmp_path, meta, {}) is None


def _ground(cover, top_m):
    water = PAINTED_PALETTE["water"]
    return SimpleNamespace(
        palette={"carpet": CARPET},
        carpet=(np.full((1, 4), cover, np.uint8), np.asarray(top_m, np.float16).reshape(1, 4)),
        water={
            "k": np.asarray(water["k_per_m"], np.float32),
            "body": srgb_to_linear(water["body"]),
            "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
        },
    )


def test_the_carpet_shows_through_shallow_water_and_fades_with_depth():
    ground = _ground(255, [-17.5, -18.5, -16.5, -17.5])
    scene = UnderwaterScene(
        np.full((1, 4), -18.4, np.float32),
        {"depth_m": np.array([[1.4, 1.4, 1.4, 0.0]], np.float32)},
        None,
    )
    under = np.full((1, 4, 3), 0.1, np.float32)
    out = carpet_bed(under, scene, ground, lambda plane: plane.astype(np.float32))
    colour = srgb_to_linear(CARPET["colour"])
    near = np.abs(out[0] - colour).sum(1)
    assert near[2] < near[0] < near[1], "a top above the surface is seen whole, a deep one barely"
    assert out[0, 0, 2] > out[0, 0, 0], "the shallow patch reads blue"
    np.testing.assert_array_equal(out[0, 3], under[0, 3], "dry ground is untouched")


def test_the_painted_style_carries_the_carpet_and_both_versions_moved():
    assert set(PAINTED_PALETTE["carpet"]) == {"colour", "strength", "depth_scale", "blur_m", "gain"}
    assert versions.PAINT_GENERATOR_VERSION >= 2
    assert versions.STYLES["satellite-painted"]["version"] >= 2
    json.dumps(PAINTED_PALETTE["carpet"])
