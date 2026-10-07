"""The arches and the rock overhangs as the rasters see them: the arch top and underside, the
hole fill, the culls, the overhangs' underside and floor, and the rasteriser's ceiling.

docs/map/light-and-crowns.md section 29, "Arches as spans". Synthetic geometry throughout.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.terrain import rasters
from mapgen.terrain.archfill import fill_arch_holes
from mapgen.terrain.overhangs import overhang_rasters, reduce_floor, reduce_under
from mapgen.terrain.rasters import PreparedPlacement, TopItems, top_items
from mapgen.terrain.render_meshes import InstanceSpans
from mapgen.terrain.top_raster import FILL_HALO_M, rasterise_top_band

STEP_CM = 750000.0 / 32768
SPACING_M = STEP_CM / 100.0


def _box(x0, x1, y0, y1, z0, z1, top=True, bottom=True):
    """A box in cm, its faces wound outward: the top faces up, the bottom down."""
    v = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], np.float32)
    faces = []
    if bottom:
        faces += [[0, 2, 1], [1, 2, 3]]
    if top:
        faces += [[4, 5, 6], [5, 7, 6]]
    faces += [
        [0, 1, 5],
        [0, 5, 4],
        [2, 6, 7],
        [2, 7, 3],
        [0, 4, 6],
        [0, 6, 2],
        [1, 3, 7],
        [1, 7, 5],
    ]
    return v, np.array(faces, np.int64)


def _join(*parts):
    verts, tris, offset = [], [], 0
    for v, t in parts:
        verts.append(v)
        tris.append(t + offset)
        offset += len(v)
    return np.concatenate(verts), np.concatenate(tris)


def _placement(mesh, verts, facing=1.0):
    return PreparedPlacement(mesh, 0, np.eye(3, dtype=np.float32), np.ones(3, np.float32),
                             np.zeros(3, np.float32), facing, float(verts[:, 1].min()),
                             float(verts[:, 1].max()))  # fmt: skip


def _overhangs(geometry, facing=1.0, rows=64, cols=64):
    prepared = [_placement(mesh, verts, facing) for mesh, (verts, _t) in geometry.items()]
    grid = (0.0, 0.0, STEP_CM)
    top = rasters.rasterise_direct_band(prepared, geometry, 0.0, 0.0, STEP_CM, rows, cols, 1)
    return top, *overhang_rasters(prepared, geometry, top, grid)


def _px(m):
    return int(m * 100 / STEP_CM)


def test_a_cap_over_a_stem_floats_at_its_rim_and_rests_on_the_stem():
    cap = _box(0, 1400, 0, 1400, 1000, 1200)
    stem = _box(500, 900, 500, 900, 0, 1000)
    top, under, floor = _overhangs({"Mushroom": _join(cap, stem)})
    rim, middle = (_px(2), _px(2)), (_px(7), _px(7))
    assert top[rim] == pytest.approx(1200.0) and under[rim] == pytest.approx(1000.0)
    assert np.isnan(floor[rim]), "nothing under the rim but the ground"
    assert under[middle] == pytest.approx(1000.0) and floor[middle] == pytest.approx(1000.0)


def test_a_rock_resting_on_another_and_an_open_shell_do_not_float():
    lower, upper = _box(0, 1400, 0, 1400, 0, 500), _box(0, 1400, 0, 1400, 500, 800)
    top, under, floor = _overhangs({"Stack": _join(lower, upper)})
    at = (_px(5), _px(5))
    assert top[at] == pytest.approx(800.0) and under[at] == pytest.approx(floor[at])
    shell = _box(0, 1400, 0, 1400, 300, 1200, bottom=False)
    _top, under, _floor = _overhangs({"Shell": shell})
    assert np.isnan(under).all(), "no face down: an open shell is solid"
    _top, under, _floor = _overhangs({"Cap": _box(0, 1400, 0, 1400, 1000, 1200)}, facing=0.0)
    assert np.isnan(under).all(), "a mesh whose winding is unknown is solid"


def test_an_underside_only_counts_well_above_the_placement_s_own_foot():
    slab = _box(0, 1400, 0, 1400, 0, 150)
    _top, under, _floor = _overhangs({"Slab": slab})
    assert np.isnan(under).all(), "its bottom is its foot"


def test_overhang_planes_fold_conservatively():
    sub = np.array([[1.0, 2.0], [np.nan, 4.0]], np.float32)
    assert np.isnan(reduce_under(sub, 1, 1, 2)[0, 0]), "a pixel half over the ground is closed"
    assert reduce_floor(sub, 1, 1, 2)[0, 0] == 4.0
    full = np.array([[1.0, 2.0], [3.0, 4.0]], np.float32)
    assert reduce_under(full, 1, 1, 2)[0, 0] == 1.0


def test_the_ceiling_keeps_only_what_lies_below_it():
    flat = lambda z: np.array([[[0, 0, z], [500, 0, z], [0, 500, z]]], np.float32)
    ceiling = np.full((4, 4), 300.0, np.float32)
    raster = MaxZRaster(4, 4, 0.0, 0.0, 100.0, sample=0.5, ceiling=ceiling)
    raster.add(flat(400.0), 1)
    raster.add(flat(250.0), 1)
    z = raster.result()[0]
    assert z[0, 0] == pytest.approx(250.0)


# ---------------------------------------------------------------------- the top raster


def _items(arch_parts, boulder=None):
    verts, tris = _join(*arch_parts)
    arch = PreparedPlacement("Arc", 0, np.eye(3, dtype=np.float32), np.ones(3, np.float32),
                             np.zeros(3, np.float32), 0.0, float(verts[:, 1].min()),
                             float(verts[:, 1].max()))  # fmt: skip
    shapes = {"Arc": (verts, tris)}
    boulders = {}
    if boulder is not None:
        shapes["Boulder"] = boulder
        mats = np.eye(4, dtype=np.float32)[None]
        y = boulder[0][:, 1]
        boulders["Boulder"] = InstanceSpans(mats, np.array([y.min()]), np.array([y.max()]))
    return TopItems([arch], boulders, shapes)


def test_the_top_raster_keeps_the_arch_s_underside_and_the_boulders_apart():
    deck = _box(0, 1000, 0, 1000, 800, 1000)
    rock = _box(1200, 1500, 0, 1000, 0, 300)
    band = rasterise_top_band(_items([deck], rock), 0.0, 0.0, STEP_CM, 32, 80, 1)
    on_deck, on_rock = (_px(5), _px(5)), (_px(5), _px(13))
    assert band.top[on_deck] == pytest.approx(1000.0) and band.under[on_deck] == pytest.approx(
        800.0
    )
    assert np.isnan(band.solid[on_deck]), "the boulders alone stand there: none"
    assert band.top[on_rock] == band.solid[on_rock] == pytest.approx(300.0)
    assert np.isnan(band.under[on_rock])


def _deck(hole):
    top = np.full((40, 80), 1000.0, np.float32)
    top[hole] = np.nan
    return top, top - np.float32(200.0)


def test_slits_and_specks_close_and_wide_holes_stay_open():
    for hole, closed in (
        ((slice(10, 30), slice(20, 23)), True),  # a 3 px slit
        ((slice(18, 22), slice(40, 44)), True),  # a 1 m speck
        ((slice(10, 30), slice(50, 56)), False),  # 6 px across
        ((slice(10, 19), slice(60, 69)), False),  # 2 m across
    ):
        top, under = _deck(hole)
        filled = fill_arch_holes(top, under, SPACING_M)
        assert np.isfinite(filled.top[hole]).all() == closed, hole
        if closed:
            assert (filled.top[hole] == 1000.0).all() and (filled.under[hole] == 800.0).all()


def test_a_notch_in_an_arch_s_outline_stays_open_and_a_narrow_cut_closes():
    top = np.full((40, 80), np.nan, np.float32)
    top[10:30, 10:70] = 1000.0
    top[10:20, 30:36] = np.nan  # a notch 6 px wide cut into the edge
    top[10:20, 50:53] = np.nan  # a cut 3 px wide: a slit open at one end
    filled = fill_arch_holes(top, top - 200, SPACING_M)
    assert np.isnan(filled.top[12, 33]) and np.isfinite(filled.top[10:20, 50:53]).all()
    assert not filled.filled[:, :9].any() and not filled.filled[:9].any(), "outside: nothing"


def test_a_coarse_grid_fills_nothing():
    top, under = _deck((slice(10, 30), slice(20, 23)))
    filled = fill_arch_holes(top, under, 3.66)
    assert not filled.filled.any() and np.isnan(filled.top[15, 21])


def test_a_band_reads_past_its_edges_to_fill_and_is_the_plain_raster_elsewhere():
    """A slit the band's edge cuts closes on both sides of it; unfilled, a band's arches are
    the arches rasterised on the band's own grid, as the top raster always was."""
    rows = 64
    gap = rows * STEP_CM + 0.2 * STEP_CM  # one row open, the band's first
    items = _items(
        [_box(0, 2000, 0, gap, 900, 1000), _box(0, 2000, gap + STEP_CM, 6000, 900, 1000)]
    )
    first = rasterise_top_band(items, 0.0, 0.0, STEP_CM, rows, 64, 1)
    second = rasterise_top_band(items, 0.0, rows * STEP_CM, STEP_CM, rows, 64, 1)
    assert np.isfinite(first.top[:, :80]).all() and np.isfinite(second.top[:, :80]).all()
    arches = MaxZRaster(64, rows, 0.0, rows * STEP_CM, STEP_CM, sample=0.5)
    rasters.add_placements(arches, items.arches, items.shapes, rows * STEP_CM, 2 * rows * STEP_CM)
    plain = arches.result()[0]
    kept = np.isfinite(plain)
    assert np.array_equal(second.top[kept], plain[kept]) and FILL_HALO_M / SPACING_M > 30


# ---------------------------------------------------------------------- the culls


class _Store:
    """``read_mesh_geometry``'s and ``read_hull``'s stand-ins: no finer source, no hull."""


def test_top_items_drop_oversized_arches_and_arches_off_the_raster(monkeypatch):
    verts, tris = _box(0, 1000, 0, 1000, 0, 500)
    monkeypatch.setattr(
        rasters, "read_mesh_geometry", lambda *a, **k: {"geometry": {}, "sources": {}}
    )
    monkeypatch.setattr(rasters, "read_hull", lambda *a, **k: None)
    x0, y0 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["y_min_m"] * 100
    row = lambda x, s: (0, 0, x, y0 + 1000, 0, 0, 0, 0, s, s, s)
    sweep = {
        "meshes": ["/Rock/Arc_A"],
        "owners": ["RockActor_C"],
        "placements": np.array(
            [row(x0 + 1000, 1.0), row(x0 + 2000, 100.0), row(x0 - 50000, 1.0)], np.float64
        ),
        "foliage": {},
    }
    items, meta = top_items(_Store(), None, None, sweep, {"/Rock/Arc_A": (verts, tris)})
    assert len(items.arches) == 1 and meta["arch_placements"] == 1
    assert meta["arches_dropped"] == {"oversize": 1, "off_raster": 1}
