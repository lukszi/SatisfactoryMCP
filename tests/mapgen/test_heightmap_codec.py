"""The heightfield's on-disk codec, and the generator's half of the format it shares.

The raster is never committed, so every test builds its own; the generator imports the codec
from ``domain.spatial.heightfield`` rather than carrying a copy.
"""

from __future__ import annotations

import numpy as np
import pytest

import mapgen.commands.heightmap
from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM, sample_grid
from mapgen.gamedata.level.fill_raster import (
    FILL_FLOOR_CM,
    FILL_RASTER_OFFSET_CM,
    FILL_RASTER_SCALE_CM_PER_RAW,
    decode_fill_raster,
)
from mapgen.gamedata.maxz_raster import MaxZRaster
from satisfactory_mcp.domain.spatial import heightfield as hf


def test_the_codec_round_trips_a_raster_exactly_including_the_wrap():
    """Row-delta plus zlib has to be lossless, and the lossy case is the interesting one.

    A delta between two int16 values does not fit in an int16 -- a cliff top beside a
    no-data texel is a swing of 65,535 -- so the format leans on two's-complement wrapping
    rather than a wider type. That is exact, and it is exact only if both halves wrap the
    same way, which no amount of ordinary data would ever reveal. The raster here therefore
    puts the extremes side by side on purpose, and a random middle everywhere else so the
    test is not just three special values.
    """
    rng = np.random.default_rng(20260731)
    grid = rng.integers(-32768, 32768, size=(37, 61)).astype(np.int16)
    grid[0, 0] = hf.NODATA
    grid[5, 10:14] = [32767, hf.NODATA, 32767, hf.NODATA]
    grid[6, :] = 0

    blob = hf.encode_i16(grid)
    assert np.array_equal(hf.decode_i16(blob, 37, 61), grid), "the round trip is not exact"

    prov = rng.integers(0, 5, size=(37, 61)).astype(np.uint8)
    assert np.array_equal(hf.decode_u8(hf.encode_u8(prov), 37, 61), prov)

    # The water quality raster rides the same uint8 codec rather than a second one, which
    # is the whole reason it is a uint8 raster: three values in long flat runs is what
    # zlib is already good at, and a fourth encoder would be a fourth thing to get wrong.
    grade = rng.choice(
        [hf.WATER_DRY, hf.WATER_MEASURED, hf.WATER_LEVEL_ONLY], size=(37, 61)
    ).astype(np.uint8)
    assert np.array_equal(hf.decode_u8(hf.encode_u8(grade), 37, 61), grade)


def test_the_int16_accumulator_decodes_exactly_what_the_int32_one_did():
    """The decoder sums in int16 to halve its peak; the wrap must land on the same raster."""
    rng = np.random.default_rng(20261005)
    grid = rng.integers(-32768, 32768, size=(23, 41)).astype(np.int16)
    grid[3, ::2] = hf.NODATA
    grid[3, 1::2] = 32767
    blob = hf.encode_i16(grid)
    import zlib

    delta = np.frombuffer(zlib.decompress(blob), dtype="<i2").reshape(23, 41)
    reference = np.cumsum(delta.astype(np.int32), axis=1).astype(np.int16)
    decoded = hf.decode_i16(blob, 23, 41)
    assert decoded.dtype == np.int16
    assert np.array_equal(decoded, reference)
    assert np.array_equal(decoded, grid)


def test_the_delta_is_what_makes_the_raster_small():
    """Not decoration: on the real field it is 16.45 MB against 26.72 MB, 38% of the file.

    Asserted on a synthetic surface with the property the real one has -- it is smooth, so
    neighbouring texels differ by a little where the absolute values differ by a lot. A
    codec that dropped the delta would pass every other test in this file and quietly cost
    ten megabytes, which is exactly the kind of regression nothing else here would catch.
    """
    import zlib

    y, x = np.mgrid[0:300, 0:500].astype(np.float64)
    surface = 800 * np.sin(x / 97) * np.cos(y / 61) + 400 * np.sin(x / 23 + y / 29)
    grid = surface.astype(np.int16)

    delta = len(hf.encode_i16(grid))
    plain = len(zlib.compress(grid.tobytes(), hf.ZLIB_LEVEL))
    assert delta < plain, f"the delta cost bytes instead of saving them: {delta} vs {plain}"
    assert np.array_equal(hf.decode_i16(hf.encode_i16(grid), 300, 500), grid)


def test_decoding_does_not_widen_the_raster():
    """The real field decodes in every xdist worker; a wider accumulator ran them out of memory."""
    import tracemalloc

    grid = np.zeros((1000, 2000), np.int16)
    blob = hf.encode_i16(grid)
    tracemalloc.start()
    try:
        hf.decode_i16(blob, 1000, 2000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= 3 * grid.nbytes, f"decode peaked at {peak / grid.nbytes:.1f}x the raster"


def test_the_codec_refuses_a_raster_that_does_not_match_its_sidecar():
    """A shape mismatch is a mismatched pair, not a raster to reshape into whatever fits."""
    blob = hf.encode_i16(np.zeros((4, 5), np.int16))
    with pytest.raises(ValueError, match="not from one run"):
        hf.decode_i16(blob, 5, 5)
    with pytest.raises(ValueError, match="not from one run"):
        hf.decode_u8(hf.encode_u8(np.zeros((4, 5), np.uint8)), 5, 5)


def test_the_codec_refuses_the_wrong_dtype():
    """int16 in, int16 out. A float raster silently truncated is a field that is wrong."""
    with pytest.raises(TypeError):
        hf.encode_i16(np.zeros((2, 2), np.float32))
    with pytest.raises(TypeError):
        hf.encode_u8(np.zeros((2, 2), np.int16))


def test_the_fill_layers_no_data_test_is_on_the_decoded_height_not_the_raw_value():
    """The single easiest thing in the generator to get wrong, pinned.

    ``HeightData_Test``'s blank value is ``raw == 0``, and the calibration that makes it a
    height maps that to about **-522 m** -- not to zero. So ``raw > 0`` and
    ``decoded > FILL_FLOOR_CM`` look like the same test and are not, and the naive one
    admits every blank texel as a false sea floor at the bottom of the map. On the real
    2048 px raster that is 138,481 texels.

    Both halves are asserted: that the blank really does decode that low, and that the real
    ocean shelf -- which is also a small raw value, just not zero -- survives the rule that
    rejects it. A test that only checked the first would pass on a rule that rejected
    everything.
    """
    blank = np.zeros((2, 2), np.float32)
    z_cm, valid = decode_fill_raster(blank)
    assert z_cm[0, 0] / 100.0 == pytest.approx(-522.8, abs=0.5), "the blank is not near -522 m"
    assert not valid.any(), "the blank value was admitted into the fill"
    assert (blank > 0).sum() == valid.sum() == 0

    # A raw value that is small but real: the ocean shelf sits just above the blank, and it
    # is exactly what the naive `raw > 0` test and the right one disagree about keeping.
    shelf = np.full(
        (2, 2),
        (FILL_FLOOR_CM - FILL_RASTER_OFFSET_CM) / FILL_RASTER_SCALE_CM_PER_RAW + 0.01,
        np.float32,
    )
    _z, shelf_valid = decode_fill_raster(shelf)
    assert shelf_valid.all(), "real low ground was rejected along with the blank"

    # And the world's own floor is above the cut, so nothing real is ever near it.
    assert FILL_FLOOR_CM < -25500.0 < 0.0


def test_the_generator_and_the_loader_agree_on_the_file_names_and_the_grid():
    """One format, and the two halves of it must not drift apart.

    The generator imports the codec from the loader's own module rather than carrying a
    copy, which is what makes that true; this asserts the rest of the agreement -- the
    georeference the sidecar promises and the constants the generator writes it from.
    """
    grid_px = GRID_PX
    origin_x, origin_y = ORIGIN_X_CM, ORIGIN_Y_CM
    assert grid_px == 7500
    assert (origin_x, origin_y, SPACING_CM) == (-324700.0, -375000.0, 100.0)
    assert mapgen.commands.heightmap.hf is hf, (
        "the generator must use the shipped codec, not a copy"
    )

    # The sampler the run validates on has to be the sampler the server reads with, or the
    # validation measures something nobody ships.
    height = np.array([[10, 20, hf.NODATA]], np.int16)
    big = np.full((grid_px, grid_px), hf.NODATA, np.int16)
    big[0, 0:3] = height
    got = sample_grid(
        big,
        np.array([origin_x, origin_x + 100.0, origin_x + 200.0]),
        np.array([origin_y] * 3),
    )
    assert got[0] == 1.0 and got[1] == 2.0 and np.isnan(got[2])


def test_the_u16_codec_round_trips_every_value():
    grid = np.array([[0, 1, 65535, 32768, 0, 65535]], np.uint16)
    assert np.array_equal(hf.decode_u16(hf.encode_u16(grid), 1, 6), grid)


def test_the_rock_raster_samples_at_the_vertex_the_reader_reads():
    # One triangle whose plane is z = x, far wider than the 1 m grid.
    tri = np.array([[[0.0, 0.0, 0.0], [1000.0, 0.0, 1000.0], [0.0, 1000.0, 0.0]]])
    at_vertex = MaxZRaster(10, 10, 0.0, 0.0, 100.0)
    at_vertex.add(tri, 1)
    z, _src, _density = at_vertex.result()
    assert z[2, 3] == pytest.approx(300.0) and z[0, 0] == pytest.approx(0.0)
    at_centre = MaxZRaster(10, 10, 0.0, 0.0, 100.0, sample=0.5)
    at_centre.add(tri, 1)
    assert at_centre.result()[0][2, 3] == pytest.approx(350.0)


def test_a_source_vertex_counts_for_the_texel_whose_sample_is_nearest():
    points = np.array([[240.0, 160.0, 0.0], [260.0, 140.0, 0.0]])
    raster = MaxZRaster(10, 10, 0.0, 0.0, 100.0)
    raster.count_samples(points)
    density = raster.result()[2]
    assert density[2, 2] == 1 and density[1, 3] == 1
    centred = MaxZRaster(10, 10, 0.0, 0.0, 100.0, sample=0.5)
    centred.count_samples(points)
    assert centred.result()[2][1, 2] == 2
