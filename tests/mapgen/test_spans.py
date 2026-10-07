"""Spans in the light: arches, overhangs and crowns cast where the sun's ray meets them.

docs/map/light-and-crowns.md section 29, "Arches as spans". Synthetic surfaces throughout;
the numba kernels' bits are held by test_kernels.py.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mapgen.lighting import horizon as hz
from mapgen.lighting import span_bake, spans
from mapgen.lighting.light_tiles import downsample
from mapgen.lighting.slabs import SlabPlanes, SlabStore
from mapgen.lighting.sun import DEFAULT_SUN

SP = 0.5
HALO = hz.horizon_reach_px(SP)
N = 2 * HALO + 120
EAST = 90.0


def _deck(lo_m, hi_m, cols, rows=slice(None)):
    """Flat ground at 0 m with a span from ``lo_m`` to ``hi_m`` over ``cols`` of the sheet."""
    z = np.zeros((N, N), np.float32)
    lo = np.full((N, N), np.nan, np.float32)
    lo[rows, cols] = lo_m
    hi = np.where(np.isfinite(lo), np.float32(hi_m), np.nan).astype(np.float32)
    return spans.span_surface(np.where(np.isfinite(hi), hi, z), z, lo, hi), z


def _core_col(distance_m, span_col):
    """The core column ``distance_m`` west of the span's first sheet column."""
    return span_col - round(distance_m / SP) - HALO


def _shade(bands, el):
    return span_bake.cell_shade(bands.horizon, ((bands.lo, bands.hi),), el)


def test_a_span_casts_where_the_ray_meets_it_and_light_passes_beneath():
    col = HALO + 100
    surface, _z = _deck(30.0, 34.0, slice(col, col + 4))
    bands = spans.march_spans(surface, HALO, EAST, SP)
    el = 62.25
    row = 60
    near_lo, near_hi = 30.0 / math.tan(math.radians(el)), 34.0 / math.tan(math.radians(el))
    assert bands.horizon[row].max() == 0.0, "nothing reaches down to the ground"
    for d in (3.0, 8.0, near_hi + 4.0):
        assert _shade(bands, el)[row, _core_col(d, col)] == 0.0, d
    middle = (near_lo + near_hi) / 2
    assert _shade(bands, el)[row, _core_col(middle, col)] > 0.5, "a 4 m deck hides most of it"


def test_a_span_whose_foot_stands_on_the_ground_merges_into_the_horizon():
    col = HALO + 100
    surface, _z = _deck(0.0, 12.0, slice(col, col + 4))
    bands = spans.march_spans(surface, HALO, EAST, SP)
    at = (60, _core_col(5.0, col))
    assert bands.horizon[at] > 60.0 and np.isnan(bands.lo[at])


def test_two_arches_in_line_keep_the_sky_between_them():
    col = HALO + 100
    z = np.zeros((N, N), np.float32)
    lo = np.full((N, N), np.nan, np.float32)
    hi = np.full((N, N), np.nan, np.float32)
    far = col + round(10.0 / SP)
    lo[:, col : col + 2], hi[:, col : col + 2] = 10.0, 12.0
    lo[:, far : far + 2], hi[:, far : far + 2] = 40.0, 42.0
    surface = spans.span_surface(np.fmax(z, np.nan_to_num(hi)), z, lo, hi)
    bands = spans.march_spans(surface, HALO, EAST, SP)
    at = (60, _core_col(10.0, col))
    shade = span_bake.cell_shade(bands.horizon, ((bands.lo, bands.hi),), 56.0)
    assert shade[at] == 0.0, "the sun between the two bands shines through"
    assert span_bake.cell_shade(bands.horizon, ((bands.lo, bands.hi),), 47.5)[at] == 1.0


def test_a_receiver_on_an_arch_sees_its_own_top_as_a_plain_march_does():
    col = HALO + 60
    surface, _z = _deck(20.0, 23.0, slice(col, col + 40))
    bands = spans.march_spans(surface, HALO, EAST, SP)
    plain = hz.march_horizon(surface.z, HALO, EAST, SP)
    deck = (slice(None), slice(col - HALO + 2, col - HALO + 38))
    assert bands.horizon[deck].tobytes() == plain[deck].tobytes()


def test_without_spans_the_march_and_the_sky_view_are_the_plain_ones():
    z = np.random.default_rng(3).random((N, N), dtype=np.float32) * 8
    nothing = np.full((N, N), np.nan, np.float32)
    surface = spans.span_surface(z, z, nothing, nothing)
    for az in (0.0, 135.0, 270.0):
        bands = spans.march_spans(surface, HALO, az, SP)
        assert bands.horizon.tobytes() == hz.march_horizon(z, HALO, az, SP).tobytes()
        assert not np.isfinite(bands.lo).any() and not bands.seen.any()
    sky = int(np.ceil(hz.SKY_RADIUS_M / SP)) + 2
    assert spans.sky_view_spans(surface, sky, SP).tobytes() == hz.sky_view(z, sky, SP).tobytes()


def test_the_sky_beside_a_span_is_open_and_beside_a_wall_it_is_not():
    col = HALO + 100
    surface, z = _deck(30.0, 34.0, slice(col, col + 8))
    sky = int(np.ceil(hz.SKY_RADIUS_M / SP)) + 2
    beside = (60, col - HALO - 2)
    assert spans.sky_view_spans(surface, HALO, SP)[beside] >= 0.95
    wall = z.copy()
    wall[:, col : col + 8] = 34.0
    assert hz.sky_view(wall, HALO, SP)[beside] <= 0.6
    assert sky < HALO


def test_a_band_is_folded_into_the_atlas_at_the_sun_path_s_elevation():
    hz_deg = np.zeros((1, 3), np.float32)
    lo = np.array([[50.0, 58.0, np.nan]], np.float32)
    hi = np.array([[58.0, 70.0, np.nan]], np.float32)
    bands = spans.Bands(hz_deg, lo, hi, np.ones((1, 3), bool))
    folded = span_bake.path_horizon(bands, 62.25)
    assert folded[0, 0] == 0.0, "a band under the sun's disc there leaves the horizon"
    assert folded[0, 1] == pytest.approx(62.25 + 3.0), "one over it shades it whole"
    assert folded[0, 2] == 0.0
    assert spans.path_elevation(DEFAULT_SUN[0]) == pytest.approx(DEFAULT_SUN[1], abs=0.1)
    assert spans.path_elevation(0.0) == spans.OFF_PATH_EL_DEG


def test_two_bands_hide_their_union_of_the_sun_disc():
    hz_deg = np.zeros((1, 1), np.float32)
    a = (np.array([[59.0]], np.float32), np.array([[63.0]], np.float32))
    b = (np.array([[61.0]], np.float32), np.array([[70.0]], np.float32))
    cover = span_bake.band_cover(hz_deg, (a, b), 62.0)
    assert cover[0, 0] == pytest.approx((65.0 - 59.0) / 6.0)
    assert span_bake.band_cover(hz_deg, (a,), 62.0)[0, 0] == pytest.approx(4.0 / 6.0)


def test_the_default_sun_s_shade_is_filtered_only_where_a_span_was_in_reach():
    col = HALO + 60
    surface, _z = _deck(30.0, 34.0, slice(col, col + 4), rows=slice(HALO + 40, HALO + 60))
    marched = {
        k: spans.march_spans(surface, HALO, k * 360.0 / hz.HORIZON_DIRS, SP) for k in (20, 21)
    }
    found = span_bake.default_shade(marched, DEFAULT_SUN, crowns=False)
    assert found is not None
    use, shade = found
    assert use[:40].any() and not use[-40:].any(), "rows the rays from it never meet keep it"
    assert shade.dtype == np.float32 and ((shade >= 0) & (shade <= 1)).all()
    plain = {k: span_bake.plain_bands(b.horizon) for k, b in marched.items()}
    assert span_bake.default_shade(plain, DEFAULT_SUN, crowns=True) is None


# ---------------------------------------------------------------------- the slab store


def test_the_slab_store_keeps_only_tiles_with_a_slab_and_reads_them_back(tmp_path):
    size = 512
    z = np.random.default_rng(1).random((256, size), dtype=np.float32)
    lo = np.full(z.shape, np.nan, np.float32)
    lo[10:20, 300:310] = 5.0
    slabs = SlabPlanes(z - 1, lo, lo + 2)
    store = SlabStore(tmp_path / "slabs")
    store.put(256, 0, slabs)
    assert [p.name for p in (tmp_path / "slabs").glob("*.npy")] == ["256_256.npy"]
    window = (200, 600, -40, 560)
    z_window = np.zeros((400, 600), np.float32)
    z_window[56:312, 40:552] = z
    half = store.half(window, downsample(z_window))
    assert half is not None
    cells = (slice((256 + 10 - 200) // 2, (256 + 20 - 200) // 2), slice(170, 175))
    assert (half.lo[cells] == 5.0).all() and (half.hi[cells] == 7.0).all()
    assert np.isfinite(half.lo).sum() == 25
    floating = np.isfinite(half.lo)
    stood = z_window.copy()
    stood[56:312, 40:552] = z - 1
    assert (half.solid[floating] == downsample(stood)[floating]).all()
    assert (half.solid[~floating] == downsample(z_window)[~floating]).all()
    solid, floats = store.full(window, z_window)
    assert floats.sum() == 100 and (solid[floats] == (z - 1)[10:20, 300:310].ravel()).all()
    assert store.half((0, 128, 0, 128), np.zeros((64, 64), np.float32)) is None
