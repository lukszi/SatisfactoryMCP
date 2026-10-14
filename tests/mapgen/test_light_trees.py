"""The trees in the light: a porous leaf shell under each tree's top, the trees' cells stored
always, the ground's horizons on the canopy top, the page's trees lit on their own, and the
ambient occlusion.

docs/map/light-and-crowns.md section 29, "A porous canopy" and "Ambient occlusion". Synthetic
fixtures throughout: no install, no field.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mapgen.lighting import horizon as hz
from mapgen.lighting import model
from mapgen.lighting.occlusion import ao_margin, ao_scales, occlusion, relative_occlusion
from mapgen.lighting.spans import bake as span_bake
from mapgen.lighting.spans import porous
from mapgen.lighting.spans.march import path_elevation
from mapgen.lighting.spans.trees import (
    CANOPY_PAD_CELLS,
    TreePlanes,
    canopy_cell,
    canopy_planes,
    tree_cells,
    trees_porous,
)
from mapgen.palette.lightparams import shader_light

SP = 0.5
HALO = hz.horizon_reach_px(SP)
EAST = 90.0


def test_a_cell_takes_the_cover_weighted_top_and_a_leaf_shell_under_it():
    z = np.zeros((4, 4), np.float32)
    top = np.full((4, 4), np.nan, np.float32)
    top[0, :2], top[1, :2] = (20.0, 10.0), (20.0, 10.0)
    cover = np.where(np.isfinite(top), 0.5, 0.0).astype(np.float32)
    cover[0, 0] = cover[1, 0] = 1.0
    titan = np.zeros((4, 4), bool)
    titan[:2, 2:] = True
    top[:2, 2:], cover[:2, 2:] = 60.0, 0.25
    cells = tree_cells(z, np.zeros((2, 2), np.float32), top, cover, titan)
    weighted = (2 * 1.0 * 20.0 + 2 * 0.5 * 10.0) / 3.0
    assert cells.hi[0, 0] == pytest.approx(weighted)
    assert cells.lo[0, 0] == pytest.approx(weighted - porous.LEAF_SHELL_M)
    assert cells.cover[0, 0] == pytest.approx(0.75) and cells.receivers[0, 0] == cells.hi[0, 0]
    assert cells.cover[0, 1] == pytest.approx(0.25) and cells.receivers[0, 1] == 0.0
    assert cells.tau_titans[0, 1] > 0 and cells.tau_crowns[0, 1] == 0.0
    assert cells.tau_crowns[0, 0] == pytest.approx(-math.log(1 - 0.75), rel=1e-6)
    assert np.isnan(cells.hi[1]).all() and (cells.receivers[1] == 0.0).all()


def test_a_shell_too_near_the_ground_reaches_down_to_it():
    z = np.zeros((2, 2), np.float32)
    top = np.full((2, 2), 1.5, np.float32)
    cells = tree_cells(z, np.zeros((1, 1), np.float32), top, np.ones((2, 2), np.float32), z > 1)
    assert cells.lo[0, 0] == 0.0 and cells.hi[0, 0] == 1.5
    assert cells.tau_crowns[0, 0] == pytest.approx(-math.log(1 - porous.COVER_MAX), rel=1e-6)


def _canopy(n: int, cols: slice, top_m: float, cover: float, titan: bool = False) -> TreePlanes:
    """Flat ground at 0 m under a canopy over ``cols`` of every row, at half resolution."""
    z = np.zeros((2 * n, 2 * n), np.float32)
    tops = np.full(z.shape, np.nan, np.float32)
    tops[:, 2 * cols.start : 2 * cols.stop] = top_m
    share = np.where(np.isfinite(tops), cover, 0.0).astype(np.float32)
    flag = np.full(z.shape, titan)
    return tree_cells(z, np.zeros((n, n), np.float32), tops, share, flag)


def _depth(planes: TreePlanes, az: float = EAST) -> np.ndarray:
    return porous.march_porous(trees_porous(planes), HALO, az, SP)


@pytest.mark.parametrize("cover", [0.3, 0.6, 1.0])
def test_a_ray_through_a_canopy_keeps_the_light_its_cover_lets_through(cover):
    n = 2 * HALO + 200
    col = HALO + 100
    planes = _canopy(n, slice(col, col + 60), 30.0, cover)
    depth = _depth(planes)
    el = math.radians(path_elevation(EAST))
    west = col - HALO - round(30.0 / math.tan(el) / SP) + 4  # rising through the shell
    shade = 1 - math.exp(-float(depth[40, west]))
    kept = min(cover, porous.COVER_MAX)
    assert shade == pytest.approx(kept, abs=0.05), "the light the cover lets through"
    assert depth[40, col - HALO - 120] == 0.0, "the ray passes over the canopy far west of it"


def test_the_light_passes_beneath_a_canopy_and_through_its_gaps():
    n = 2 * HALO + 200
    col = HALO + 150
    planes = _canopy(n, slice(col, col + 40), 60.0, 1.0, titan=True)
    depth = _depth(planes)
    row = 40
    under = col - HALO - round(15.0 / SP)  # 15 m west: the ray rises under the 20 m wide shell
    assert depth[row, under] == 0.0, "light passes beneath the canopy"
    through = col - HALO - round(50.0 / SP)  # 50 m west: it climbs through the shell
    assert depth[row, through] > 1.0
    gap = planes._replace(tau_titans=np.where(np.arange(n)[:, None] == HALO + row, 0.0, planes.tau_titans))
    assert _depth(gap)[row, through] < depth[row, through] / 4, "a gap in the cover lets it in"


def test_each_tree_class_fades_with_its_own_distance():
    n = 2 * HALO + 200
    col = HALO + 300
    crowns = _canopy(n, slice(col, col + 20), 60.0, 0.6)
    titans = _canopy(n, slice(col, col + 20), 60.0, 0.6, titan=True)
    at = col - HALO - round(60.0 / SP)  # the ray climbs through the shell about 60 m out
    near, far = float(_depth(crowns)[40, at]), float(_depth(titans)[40, at])
    assert 0.0 < near < far, "the crowns fade by 80 m, the Titan canopy by 150 m"
    assert near / far == pytest.approx(
        hz.fade_weight(60.0, hz.OCCLUDER_FADE_M) / hz.fade_weight(60.0, hz.FADE_M), rel=0.15
    )


def test_a_tree_cell_holds_the_trees_alone_folded_at_the_sun_path_s_elevation():
    n = 2 * HALO + 120
    z = np.zeros((n, n), np.float32)
    z[:, n - HALO - 30 :] = 80.0  # a cliff to the east shades the whole core at low sun
    col = HALO + 60
    planes = _canopy(n, slice(col, col + 16), 15.0, 0.9)
    spans = span_bake.BlockSpans(None, planes, z)
    k = int(EAST / (360 / hz.HORIZON_DIRS))
    cells = {c.k: c for c in span_bake.horizon_cells(z, HALO, SP, spans)}
    ground, trees = cells[k].deg, cells[model.CROWN_CELL + k]
    west = col - HALO - round(13.0 / SP)
    el = 45.0
    assert ground[30, west] > 30.0, "the terrain shades it"
    assert trees.deg[30, west] == pytest.approx(el + 6.0 * (0.8 - 0.5), abs=0.6), "the cover's"
    assert trees.band_in is not None and trees.band_in[30, west]
    assert trees.deg[30, 5] == 0.0, "far from the tree: no terrain in a tree's cell"


def test_a_canopy_cell_holds_the_ground_s_horizon_received_on_the_canopy_top():
    n = 2 * HALO + 120
    z = np.zeros((n, n), np.float32)
    z[:, n - HALO - 30 :] = 80.0  # a cliff to the east
    col = HALO + 40
    planes = _canopy(n, slice(col, col + 20), 60.0, 1.0)
    canopy = canopy_planes(z, planes, HALO - 1, SP)
    found = canopy_cell(canopy, HALO - 1, EAST, SP)[1:-1, 1:-1]
    ground = hz.march_horizon(z, HALO, EAST, SP)
    row, on = 30, col - HALO + 10
    assert 0.0 < found[row, on] < ground[row, on], "the cliff stands lower over the canopy"
    assert found[row, on - 10 - CANOPY_PAD_CELLS - 2] == 0.0, "no tree: no canopy cell"
    beside = found[row, on - 10 - 1]
    assert beside >= model.CANOPY_FLOOR_DEG or beside == pytest.approx(ground[row, on - 11], 2.0)


def test_the_page_reads_the_trees_shadows_and_their_occlusion_only_with_the_trees():
    colour = np.full((8, 8, 3), 150, np.uint8)
    nrm = np.empty((8, 8, 4), np.uint8)
    nrm[...] = (128, 128, 200, 255)
    cells = np.zeros((model.HZ_CELLS, 8, 8), np.float32)
    cells[model.CROWN_CELL : model.CANOPY_CELL] = 60.0
    hz_u8 = hz.encode_horizon(cells)
    hz_u8[model.AO_CELL] = 128
    sun = (225.0, 30.0)
    painted, terrain = shader_light("painted"), shader_light("terrain")
    shaded = model.relight(colour, nrm, hz_u8, sun, painted)
    open_sky = model.relight(colour, nrm, None, sun, painted)
    assert shaded.mean() < open_sky.mean() - 10, "a tree's shadow"
    no_trees = model.relight(colour, nrm, hz_u8, sun, terrain)
    assert no_trees.tobytes() == model.relight(colour, nrm, None, sun, terrain).tobytes()
    unshadowed = model.relight(colour, nrm, hz_u8, sun, painted, shadows=False)
    hz_u8[model.AO_CELL] = 0
    assert unshadowed.mean() < model.relight(colour, nrm, hz_u8, sun, painted, False).mean()


def test_the_page_lights_the_trees_under_the_canopy_s_horizon_not_the_ground_s():
    colour = np.full((8, 8, 3), 150, np.uint8)
    nrm = np.empty((8, 8, 4), np.uint8)
    nrm[...] = (128, 128, 255, 255)
    cells = np.zeros((model.HZ_CELLS, 8, 8), np.float32)
    cells[: model.CROWN_CELL] = 60.0  # the ground beneath the canopy is in a rock's shadow
    cells[model.CANOPY_CELL : model.AO_CELL] = model.CANOPY_FLOOR_DEG  # its top is not
    hz_u8 = hz.encode_horizon(cells)
    sun, painted = (225.0, 30.0), shader_light("painted")
    trees = np.dstack([colour, np.full((8, 8), 255, np.uint8)])
    lit = model.relight(colour, nrm, hz_u8, sun, painted, trees=trees)
    ground = model.relight(colour, nrm, hz_u8, sun, painted)
    assert lit.mean() > ground.mean() + 10, "the canopy stands over the rock's shadow"
    cells[model.CANOPY_CELL : model.AO_CELL] = 0.0  # no canopy cell: the ground's horizon
    held = model.relight(colour, nrm, hz.encode_horizon(cells), sun, painted, trees=trees)
    assert abs(int(held.mean()) - int(ground.mean())) <= 2
    bare = trees.copy()
    bare[..., 3] = 0
    assert model.relight(colour, nrm, hz_u8, sun, painted, trees=bare).tobytes() == ground.tobytes()


# --------------------------------------------------------------------- ambient occlusion


def _plane(n: int, m: int) -> np.ndarray:
    return np.zeros((n + 2 * m, n + 2 * m), np.float32)


@pytest.mark.parametrize("spacing", [0.229, 1.0])
def test_flat_ground_and_a_slope_take_no_occlusion_and_a_pit_does(spacing):
    m = ao_margin(spacing)
    flat = _plane(64, m)
    assert not occlusion(flat, spacing, gpu=False).any()
    yy = np.mgrid[0 : flat.shape[0], 0 : flat.shape[1]][0].astype(np.float32)
    slope = occlusion(yy * np.float32(spacing * 0.3), spacing, gpu=False)
    assert slope.max() < 1e-4, "a slope: its steps' rounding at most"
    pit = flat.copy()
    pit[m + 30 : m + 34, m + 30 : m + 34] = -3.0
    found = occlusion(pit, spacing, gpu=False)
    assert found[31, 31] > 0.1 and found.max() <= model.AO_STRENGTH
    ridge = flat.copy()
    ridge[m + 30 : m + 34, m + 30 : m + 34] = 3.0
    near = occlusion(ridge, spacing, gpu=False)
    assert near[31, 31] == 0.0 and near[31, 28] > 0.0, "a rock's top is open, its foot is not"


def test_the_occlusion_is_the_same_bits_whatever_window_reads_it():
    sp = 0.229
    m = ao_margin(sp)
    rng = np.random.default_rng(3)
    z = (rng.random((200 + 2 * m, 180 + 2 * m)) * 4).astype(np.float32)
    z[60:66, 70:90] = np.nan
    whole = occlusion(z, sp, gpu=False)
    top = occlusion(z[: 80 + 2 * m], sp, gpu=False)
    assert top.tobytes() == whole[:80].tobytes()
    assert not whole[60 - m : 66 - m, 70 - m : 90 - m].any(), "no height, no occlusion"


def test_a_coarse_sheet_keeps_only_the_scales_it_resolves():
    assert [s.radius for s in ao_scales(0.229)] == [4, 13, 35]
    assert [s.radius for s in ao_scales(3.66)] == [1, 2]
    assert ao_scales(20.0) == [] and ao_margin(20.0) == 0
    flat = np.zeros((10, 10), np.float32)
    assert occlusion(flat, 20.0, gpu=False).shape == (10, 10)


def test_the_trees_occlusion_is_what_they_add_to_the_ground_s():
    ground = np.array([0.0, 0.2, 0.2, 0.5], np.float32)
    canopy = np.array([0.3, 0.2, 0.1, 0.5], np.float32)
    found = relative_occlusion(canopy, ground)
    assert found.tolist() == pytest.approx([0.3, 0.0, 0.0, 0.0])
    assert ((1 - found) * (1 - ground)).tolist() == pytest.approx([0.7, 0.8, 0.8, 0.5])
