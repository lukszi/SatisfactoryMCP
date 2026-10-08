"""The trees in the light: each species' own underside, the Titan trees' thin slab and cells of
their own, the trees' cells stored always, and the ambient occlusion.

docs/map/light-and-crowns.md section 29, "What casts as a span", "The atlas" and "Ambient
occlusion". Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.vegetation.crown_sprites import SpeciesMesh
from mapgen.lighting import horizon as hz
from mapgen.lighting import model
from mapgen.lighting.occluders import UNDER_SCALE, UNDER_TITAN, sheet_crowns
from mapgen.lighting.occlusion import ao_margin, ao_scales, occlusion, relative_occlusion
from mapgen.lighting.spans import bake as span_bake
from mapgen.lighting.spans.march import march_spans
from mapgen.lighting.undersides import (
    CROWN_UNDERSIDE,
    LEAF_LOW_SHARE,
    TITAN_SLAB_M,
    leaf_low_cm,
    species_undersides,
    stamp_undersides,
)
from mapgen.palette.lightparams import shader_light
from satisfactory_mcp.domain.spatial import heightfield as hf

SP = 0.5
HALO = hz.horizon_reach_px(SP)
EAST = 90.0


def _card(z_cm: float, half_cm: float = 100.0) -> np.ndarray:
    """A flat square leaf card at ``z_cm``, two triangles' vertices."""
    a, b = -half_cm, half_cm
    corners = [(a, a), (b, a), (b, b), (a, a), (b, b), (a, b)]
    return np.array([(x, y, z_cm) for x, y in corners], np.float32)


def _mesh(cards: list[tuple[float, float, int]]) -> SpeciesMesh:
    """A mesh of flat cards: ``(height cm, half width cm, material slot)`` each."""
    verts = np.concatenate([_card(z, h) for z, h, _slot in cards])
    tris = np.arange(len(verts), dtype=np.int64).reshape(-1, 3)
    slots = np.repeat(np.array([slot for _z, _h, slot in cards], np.int64), 2)
    return SpeciesMesh(verts, tris, slots, ["bark", "leaf"])


def test_a_species_underside_is_under_its_lowest_leaves_but_a_few():
    crown = [(800.0 + 10 * k, 300.0, 1) for k in range(40)]
    assert leaf_low_cm(_mesh(crown), ["bark", "leaf"]) == pytest.approx(800.0, abs=30.0)
    stray = crown + [(50.0, 40.0, 1)]  # one small leaf card near the ground
    assert leaf_low_cm(_mesh(stray), ["bark", "leaf"]) > 700.0, "a few low leaves do not count"
    low = crown + [(50.0, 300.0, 1) for _ in range(10)]  # a fifth of the leaves low down
    assert leaf_low_cm(_mesh(low), ["bark", "leaf"]) == pytest.approx(50.0)
    trunk = [(0.0, 50.0, 0), (400.0, 50.0, 0)]
    assert leaf_low_cm(_mesh(trunk), ["bark", "leaf"]) == pytest.approx(0.0), "bark alone"
    assert leaf_low_cm(_mesh(trunk), ["skip", "skip"]) is None
    assert 0 < LEAF_LOW_SHARE < 0.2


def test_a_species_whose_mesh_cannot_be_read_takes_the_default_share():
    species = [{"name": "a", "mesh": "/x/a", "materials": [], "radius_m": 1.0, "top_m": 5.0}]
    found = species_undersides(None, species, [500.0])
    assert found.tolist() == [pytest.approx(CROWN_UNDERSIDE)]


def _sprite(side: int, top_cm: float) -> dict[str, object]:
    cover = np.full((side, side), 255, np.uint8)
    top = np.full((side, side), top_cm, np.uint16)
    corner = -side * 12.5 / 2
    return {"cover": cover, "top_cm": top, "slot": np.zeros_like(cover), "x0_cm": corner,
            "y0_cm": corner, "offset": 0, "width": side, "height": side}  # fmt: skip


def _records(trees: list[tuple[float, float, int]]) -> np.ndarray:
    from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD

    out = np.zeros(len(trees), CROWN_RECORD)
    for k, (x, y, species) in enumerate(trees):
        out[k] = (x, y, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, species)
    return out


def test_a_texel_takes_the_underside_of_the_tree_whose_crown_is_highest_there():
    grid = {"spacing_cm": 100.0, "x0_cm": 50.0, "y0_cm": 50.0}
    sprites = [_sprite(96, 2000.0), _sprite(16, 900.0)]  # 12 m and 2 m wide
    under = np.array([0.7, 0.2], np.float32)
    records = _records([(1000.0, 1000.0, 0), (1500.0, 1000.0, 1), (1700.0, 1700.0, 1)])
    plane = stamp_undersides(records, sprites, under, (24, 24), grid)
    tall, short = round(0.7 * UNDER_SCALE), round(0.2 * UNDER_SCALE)
    assert plane[10, 10] == tall and plane[10, 15] == tall, "under the taller crown"
    assert plane[17, 17] == short and plane[2, 2] == 0
    flipped = stamp_undersides(records, sprites[::-1], under[::-1], (24, 24), grid)
    assert flipped[10, 10] == tall, "the crown highest at a texel, whichever its index"


def test_the_sheet_takes_the_underside_s_mean_over_the_crowns_it_covers():
    grid = {"x0_cm": BOUNDS_M["x_min_m"] * 100.0, "y0_cm": BOUNDS_M["y_min_m"] * 100.0,
            "spacing_cm": 100.0}  # fmt: skip
    top = np.full((32, 32), hf.NODATA, np.int16)
    top[8:16, 8:24] = 200
    under = np.zeros((32, 32), np.uint8)
    under[8:16, 8:16], under[8:16, 16:24] = 50, 150
    size = int((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / 3.0)
    out = np.empty((size, size), np.float32)
    cover, sheet_under = np.zeros((size, size), np.uint8), np.zeros((size, size), np.uint8)
    sheet_crowns(top, grid, size, out, cover, (under, sheet_under))
    assert sheet_under[3, 3] == 50 and sheet_under[3, 6] == 150
    assert 50 < sheet_under[3, 5] < 150, "a pixel over both takes their mean"
    assert not sheet_under[cover == 0].any()


def test_a_crown_spans_its_species_share_and_a_titan_tree_a_thin_slab():
    z = np.zeros((4, 4), np.float32)
    top = np.full((4, 4), 60.0, np.float32)
    under = np.full((4, 4), round(0.75 * UNDER_SCALE), np.float32)
    under[2:] = UNDER_TITAN
    rec, (c_lo, c_hi), (t_lo, t_hi) = span_bake.tree_spans(z, top, None, under)
    assert rec.tolist() == top.tolist()
    share = round(0.75 * UNDER_SCALE) / UNDER_SCALE
    assert c_lo[0, 0] == pytest.approx(share * 60.0) and c_hi[0, 0] == 60.0
    assert np.isnan(c_lo[2:]).all() and np.isnan(t_lo[:2]).all()
    assert t_lo[2, 0] == pytest.approx(60.0 - TITAN_SLAB_M) and t_hi[2, 0] == 60.0
    low = span_bake.tree_spans(z, np.full((4, 4), 8.0, np.float32), None, under)[2]
    assert low[0][2, 0] == 0.0, "a canopy under the slab's depth stands on the ground"
    _rec, (old_lo, _hi), _t = span_bake.tree_spans(z, top, None, None)
    assert old_lo[0, 0] == pytest.approx(CROWN_UNDERSIDE * 60.0), "no underside plane: the default"


def _trees(z: np.ndarray, top: np.ndarray, under: np.ndarray | None) -> span_bake.BlockSpans:
    """``z`` with trees of ``top`` and ``under`` bytes stood on it, as a block casts them."""
    rec, crowns, titans = span_bake.tree_spans(z, top, None, under)
    surfaces = span_bake.tree_surfaces(span_bake.TreePlanes(rec, *crowns, *titans), z)
    return span_bake.BlockSpans(None, *surfaces)


def test_the_titan_canopy_lets_the_sun_through_beneath_its_slab():
    n = 2 * HALO + 200
    z = np.zeros((n, n), np.float32)
    col = HALO + 150
    top = np.full((n, n), np.nan, np.float32)
    top[:, col : col + 40] = 60.0  # 20 m wide, 60 m up
    titan = _trees(z, top, np.full((n, n), UNDER_TITAN, np.float32)).titans
    old = _trees(z, top, np.full((n, n), round(0.5 * UNDER_SCALE), np.float32)).crowns
    assert titan is not None and old is not None
    el, row, d = 45.0, 40, 15.0
    at = col - round(d / SP) - HALO  # 15 m west of the canopy
    shade = {}
    for name, surface, fade in (("thin", titan, model.TITAN_FADE_M),
                                ("thick", old, hz.OCCLUDER_FADE_M)):  # fmt: skip
        bands = march_spans(surface, HALO, EAST, SP, fade)
        shade[name] = span_bake.cell_shade(bands.horizon, ((bands.lo, bands.hi),), el)[row, at]
    assert shade["thick"] == 1.0, "a 30 m slab hides the sun from beneath the canopy"
    assert shade["thin"] == 0.0, "a 12 m one lets it through"
    assert (60.0 - TITAN_SLAB_M) / (d + 20.0) > math.tan(math.radians(el + 3.0)), "beneath"


def test_a_tree_s_cell_holds_the_trees_alone_and_is_stored_inside_terrain_shade():
    n = 2 * HALO + 120
    z = np.zeros((n, n), np.float32)
    z[:, n - HALO - 30 :] = 80.0  # a cliff to the east shades the whole core at low sun
    top = np.full((n, n), np.nan, np.float32)
    top[:, HALO + 60 : HALO + 64] = 15.0
    spans = _trees(z, top, None)
    k = int(EAST / (360 / hz.HORIZON_DIRS))
    cells = {c.k: c.deg for c in span_bake.horizon_cells(z, HALO, SP, spans)}
    ground, trees = cells[k], cells[model.CROWN_CELL + k]
    row, west = 30, 40  # core columns west of the crown, under the cliff's shade
    assert ground[row, west] > 30.0, "the terrain shades it"
    assert 0 < trees[row, west] < ground[row, west], "stored, though under the terrain's"
    assert trees[row, 5] == 0.0, "far from the tree: no terrain in a tree's cell"


def test_the_page_reads_the_trees_shadows_and_their_occlusion_only_with_the_trees():
    colour = np.full((8, 8, 3), 150, np.uint8)
    nrm = np.empty((8, 8, 4), np.uint8)
    nrm[...] = (128, 128, 200, 255)
    cells = np.zeros((model.HZ_CELLS, 8, 8), np.float32)
    cells[model.TITAN_CELL : model.AO_CELL] = 60.0
    hz_u8 = hz.encode_horizon(cells)
    hz_u8[model.AO_CELL] = 128
    sun = (225.0, 30.0)
    painted, terrain = shader_light("painted"), shader_light("terrain")
    shaded = model.relight(colour, nrm, hz_u8, sun, painted)
    open_sky = model.relight(colour, nrm, None, sun, painted)
    assert shaded.mean() < open_sky.mean() - 10, "a Titan tree's shadow"
    no_trees = model.relight(colour, nrm, hz_u8, sun, terrain)
    assert no_trees.tobytes() == model.relight(colour, nrm, None, sun, terrain).tobytes()
    unshadowed = model.relight(colour, nrm, hz_u8, sun, painted, shadows=False)
    hz_u8[model.AO_CELL] = 0
    assert unshadowed.mean() < model.relight(colour, nrm, hz_u8, sun, painted, False).mean()


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
