"""The painted layer drawn apart at its trees: the ground without them for ``unlit/``, the
crowns and Titan trees as one sparse RGBA tree, and the picture the two make unchanged.

docs/map/light-and-crowns.md section 36, "Trees apart". Synthetic fixtures: no install.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from mapgen.colour import by_luminance, linear_to_srgb_unit, srgb_unit_to_linear, tone, untone
from mapgen.palette.painted.band import painted_colours, painted_parts
from mapgen.palette.painted.trees import folded_trees, lay_over
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.render.draw.light import TREES_DIR_NAME, UNLIT_DIR_NAME, LightingRun
from mapgen.render.draw.painting import TreeSplit
from mapgen.render.draw.stream import RenderStream
from mapgen.terrain.render_meshes import TITAN_LEAVES, TITAN_TRUNK
from mapgen.tiles.cutter import TileStream
from mapgen.tiles.formats import GROUND_TILES, TREES_TILES
from mapgen.tiles.levels import SheetRows
from tests.support.cut import cut_whole
from tests.support.map_scenes import painted_ground_stub, water_scene

Image = pytest.importorskip("PIL.Image")

N = 6
LEAF = (0.06, 0.12, 0.035)


def _same(plane):
    return plane


def _band():
    """Six pixels: bare, a crown, half a crown, a crown over the water, one sunk under it,
    and a Titan trunk (the raster draws the trunks; the canopy comes from its sprites)."""
    ground = painted_ground_stub(N)
    titan_cls = np.zeros((2, N + 1), np.uint8)
    titan_cls[:, 5] = TITAN_TRUNK
    ground.titan = (np.full((2, N + 1), 5000, np.int32), titan_cls, 1, 0, 0)
    ground.titan_rgb = {TITAN_LEAVES: np.zeros(3), TITAN_TRUNK: np.array(LEAF, np.float32)}
    scene = water_scene(N)
    scene["water"]["cover"] = np.array([[0, 0, 0, 1, 1, 0]], np.float32)
    scene["water"]["depth_m"] = np.array([[0, 0, 0, 0.3, 3.0, 0]], np.float32)
    cover = np.array([[0, 1, 0.5, 1, 1, 0]], np.float32)
    top_m = np.array([[0, 20, 20, 20, 6, 0]], np.float32)
    scene["crowns"] = {
        "cover": cover,
        "rgb": cover[..., None] * np.array(LEAF, np.float32),
        "top_cm": top_m * 100,
        "ndl": np.ones((1, N), np.float32),
        "dome_m": np.zeros((1, N), np.float32),
    }
    scene["grid"] = (None, 0, 1, 0, N, 1.0)
    scene["unlit"] = True
    return scene, ground


def test_the_trees_fold_into_one_layer_that_lays_as_they_do_in_turn():
    rng = np.random.default_rng(3)
    shape = (5, 7, 3)
    under = rng.random(shape, dtype=np.float32)
    layers = []
    for _ in range(2):
        alpha = rng.random(shape[:2], dtype=np.float32)
        alpha[0] = 0.0
        layers.append((alpha, rng.random(shape, dtype=np.float32)))
    alpha, colour = folded_trees(shape, layers)
    in_turn = lay_over(lay_over(under, layers[0]), layers[1])
    np.testing.assert_allclose(lay_over(under, (alpha, colour)), in_turn, atol=2e-6)
    np.testing.assert_allclose(alpha, 1 - (1 - layers[0][0]) * (1 - layers[1][0]), atol=1e-6)
    assert (alpha[0] == 0).all() and (colour[0] == 0).all(), "none laid: nothing"


def test_the_band_apart_is_the_picture_its_ground_and_its_trees():
    scene, ground = _band()
    parts = painted_parts(scene, ground, _same, _same)
    whole = painted_colours(scene, ground, _same, _same)
    np.testing.assert_array_equal(parts.colour, whole)
    bare = parts.alpha == 0
    assert bare[0].tolist() == [True, False, False, False, True, False]
    np.testing.assert_array_equal(parts.ground[bare], whole[bare])
    assert parts.alpha[0, 5] == pytest.approx(PAINTED_PALETTE["titan_trees"]["opacity"])
    assert np.abs(parts.ground[0, 4] - whole[0, 4]).max() == 0, "the sunk crown is the bed's"
    assert np.abs(parts.ground[0, 1] - whole[0, 1]).max() > 10, "a crown hides its ground"


def test_the_page_lays_the_cut_trees_back_within_a_level_of_the_picture():
    scene, ground = _band()
    parts = painted_parts(scene, ground, _same, _same)
    knee, white = PAINTED_PALETTE["tone"]["knee"], PAINTED_PALETTE["tone"]["white"]

    def linear(srgb):
        cut = np.clip(srgb, 0, 255).astype(np.uint8).astype(np.float32) / 255
        return by_luminance(srgb_unit_to_linear(cut), untone, knee, white)

    alpha = (np.rint(parts.alpha * 255) / 255)[..., None]
    mixed = linear(parts.ground) * (1 - alpha) + linear(parts.trees) * alpha
    again = linear_to_srgb_unit(by_luminance(mixed, tone, knee, white)) * 255
    picture = np.clip(parts.colour, 0, 255).astype(np.uint8)
    assert np.abs(np.rint(again) - picture).max() <= 1


def test_coarser_levels_of_the_trees_are_resampled_premultiplied():
    sheet = np.zeros((512, 512, 4), np.uint8)
    sheet[:, :257] = (200, 30, 30, 255)
    sheet[:, 257:] = (0, 255, 0, 0)  # what lies under alpha 0 must not bleed in
    rows = SheetRows(Image, 512, [256], channels=4)
    level = np.concatenate([out for side, out in rows.add(sheet) if side == 256])
    edge = level[:, 124:133].astype(int)
    half = (edge[..., 3] > 64) & (edge[..., 3] < 192)
    assert half.any(), "the edge is half covered"
    assert np.abs(edge[half][:, :3] - (200, 30, 30)).max() <= 4, "and keeps its colour"
    assert edge[edge[..., 3] > 32][:, 1].max() <= 40, "no green from under alpha 0"


def _digests(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.webp"))
    }


def test_a_layer_drawn_apart_cuts_its_ground_and_its_trees_sparse(tmp_path):
    size = 512
    run = LightingRun(tmp_path / "cache", size)
    z = np.zeros((size, size), np.float32)
    land = np.ones((size, size), np.float32)
    rng = np.random.default_rng(5)
    ground = rng.integers(0, 256, (size, size, 3), np.uint8)
    trees = np.zeros((size, size, 4), np.uint8)
    trees[20:200, 30:220] = (40, 90, 20, 255)
    trees[200:210, 30:220, 3] = 100
    picture = np.full((size, size, 3), 77, np.uint8)
    out = tmp_path / "out"
    with TileStream(Image, 2, threads=2) as cutter:
        split = ("painted",)
        stream = RenderStream(cutter, ("painted",), (out, "r"), size, 6, run, split=split)
        with pytest.raises(ValueError, match="apart"):
            stream.put(0, {"painted": picture[:0]})
        for top in range(0, size, 128):
            rows = slice(top, top + 128)
            run.surface.put(top, z[rows], land[rows])
            stream.put(
                top, {"painted": picture[rows]}, {"painted": TreeSplit(ground[rows], trees[rows])}
            )
        stream.finish()
        installed = stream.install("painted")
    run.close()
    layer = out / "r" / "painted"
    assert installed.trees is not None and installed.unlit is not None
    assert installed.trees["count"] == 2 and installed.trees["sparse"] is True
    assert installed.trees["layout"] == "trees/{z}/{x}_{y}.webp"
    assert installed.trees["alpha"] == "straight" and installed.unlit["count"] == 5
    assert sorted(_digests(layer / TREES_DIR_NAME)) == ["0/0_0.webp", "1/0_0.webp"]
    cut_whole(trees, tmp_path / "ref" / "trees", TREES_TILES)
    cut_whole(ground, tmp_path / "ref" / "unlit", GROUND_TILES)
    assert _digests(layer / TREES_DIR_NAME) == _digests(tmp_path / "ref" / "trees")
    assert _digests(layer / UNLIT_DIR_NAME) == _digests(tmp_path / "ref" / "unlit")
    tile = np.asarray(Image.open(layer / TREES_DIR_NAME / "1" / "0_0.webp"))
    shown = trees[:256, :256, 3] > 0
    assert tile.shape == (256, 256, 4) and (tile[shown] == trees[:256, :256][shown]).all()
    assert (tile[..., 3] == trees[:256, :256, 3]).all(), "lossless, alpha and all"
    unlit = np.asarray(Image.open(layer / UNLIT_DIR_NAME / "1" / "1_1.webp"))
    assert (unlit == ground[256:, 256:]).all(), "the ground, not the picture"
    sidecar = {"_meta": {"provenance": {}}}
    run.decorate(sidecar, "painted", installed.unlit, installed.trees)
    light = sidecar["_meta"]["light"]
    assert light["trees_dir"] == TREES_DIR_NAME and light["trees_tiles"]["max_z"] == 1
    assert "trees/" in light["role"] and json.dumps(sidecar)
