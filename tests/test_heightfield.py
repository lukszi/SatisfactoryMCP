"""The extracted terrain field: its codec, its no-data rules, and who is allowed to see it.

The raster itself is 18 MB derived from Coffee Stain's cooked assets, so it is gitignored
and no test may need one. Everything here therefore runs on a **synthetic** field built in
``tmp_path`` -- three tiny rasters and a sidecar in the real format -- which is also the
only way to assert what the absent case does, since the machine that generated a real field
would otherwise never exercise it.

Five things are pinned, and each is a place a plausible-looking mistake would ship quietly:

* the codec round-trips exactly, including the wrap the row-delta relies on;
* the fill layer's no-data test is on the DECODED height, not on ``raw > 0``;
* a missing or broken field is ``None`` and never an exception;
* an elevation probe prefers the field where it has an answer, keeps the sampled
  populations intact, and says nothing where the field says nothing;
* the endpoint's JSON carries which layer answered and how good that layer is.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from satisfactory_mcp.domain.spatial import elevation
from satisfactory_mcp.domain.spatial import heightfield as hf
from tools import gen_world_heightmap

# --------------------------------------------------------------------------------------
# A field small enough to build in a test, in exactly the format the generator writes.
# --------------------------------------------------------------------------------------

#: The synthetic field's grid. Small, and deliberately NOT square: a width/height swap in
#: the codec or the sampler survives a square raster and nothing else.
FAKE_W, FAKE_H = 7, 6
FAKE_X0, FAKE_Y0, FAKE_SPACING = -300.0, -200.0, 100.0


def build_field(
    tmp_path: Path, *, water: bool = True, quality: bool = True, density: bool = False
) -> Path:
    """Write a whole synthetic field and return its directory.

    Row 0 is landscape, row 1 cliff, row 2 fill, row 3 no data, row 4 landscape under
    water whose depth was measured, row 5 fill under water whose depth was not -- one row
    per thing a reading can be, so a single field exercises all of them.

    Row 5 is the case the whole quality byte exists for and it is built to be nasty on
    purpose: the water surface stands **below** the recorded ground, exactly as the open
    ocean does over the 3.9 m fill raster. Anything that decides submersion by comparing
    the two numbers calls it dry, which is the bug.
    """
    directory = tmp_path / hf.DIR_NAME
    directory.mkdir(parents=True)
    height = np.zeros((FAKE_H, FAKE_W), np.int16)
    prov = np.zeros((FAKE_H, FAKE_W), np.uint8)
    for row, (layer, value) in enumerate(
        [
            (hf.PROV_LANDSCAPE, 123),
            (hf.PROV_CLIFF, 2456),
            (hf.PROV_FILL, -78),
            (hf.PROV_NODATA, hf.NODATA),
            (hf.PROV_LANDSCAPE, -150),
            (hf.PROV_FILL, -150),
        ]
    ):
        height[row, :] = value
        prov[row, :] = layer
    wet = np.full((FAKE_H, FAKE_W), hf.NODATA, np.int16)
    grade = np.full((FAKE_H, FAKE_W), hf.WATER_DRY, np.uint8)
    wet[4, :] = 20  # 2.0 m of water over ground at -15.0 m
    grade[4, :] = hf.WATER_MEASURED
    wet[5, :] = -170  # a sea surface at -17.0 m over a fill "ground" of -15.0 m
    grade[5, :] = hf.WATER_LEVEL_ONLY

    samples = np.zeros((FAKE_H, FAKE_W), np.uint8)
    if density:
        # Half of the cliff row is a texel a source vertex landed in and half is not,
        # which is the only shape that exercises the split rather than a constant.
        samples[1, : FAKE_W // 2] = 3
        prov[1, : FAKE_W // 2] = hf.PROV_CLIFF_DIRECT

    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(height))
    (directory / hf.PROV_NAME).write_bytes(hf.encode_u8(prov))
    if density:
        (directory / hf.DENSITY_NAME).write_bytes(hf.encode_u8(samples))
    if water:
        (directory / hf.WATER_NAME).write_bytes(hf.encode_i16(wet))
        if quality:
            (directory / hf.WATER_QUALITY_NAME).write_bytes(hf.encode_u8(grade))
    (directory / hf.META_NAME).write_text(
        json.dumps(
            {
                "grid": {
                    "width": FAKE_W,
                    "height": FAKE_H,
                    "spacing_cm": FAKE_SPACING,
                    "x0_cm": FAKE_X0,
                    "y0_cm": FAKE_Y0,
                },
                "nodata": hf.NODATA,
                "provenance": {
                    "0": {"name": "no data", "accuracy_m": None},
                    "1": {"name": "landscape", "accuracy_m": 0.205},
                    "3": {"name": "fill", "accuracy_m": 3.897},
                    "4": {"name": "cliff", "accuracy_m": 0.21},
                    "5": {"name": "cliff, direct", "accuracy_m": 0.21},
                },
                "sources": {"game": {"game_version_pinned": "buildVersion 495413, a test"}},
            }
        ),
        encoding="utf-8",
    )
    return directory


# --------------------------------------------------------------------------------------
# The codec.
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# The generator's no-data rule for the fill layer.
# --------------------------------------------------------------------------------------


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
    z_cm, valid = gen_world_heightmap.decode_baseline(blank)
    assert z_cm[0, 0] / 100.0 == pytest.approx(-522.8, abs=0.5), "the blank is not near -522 m"
    assert not valid.any(), "the blank value was admitted into the fill"
    assert (blank > 0).sum() == valid.sum() == 0

    # A raw value that is small but real: the ocean shelf sits just above the blank, and it
    # is exactly what the naive `raw > 0` test and the right one disagree about keeping.
    shelf = np.full(
        (2, 2),
        (gen_world_heightmap.FILL_FLOOR_CM - gen_world_heightmap.BASELINE_OFFSET_CM)
        / gen_world_heightmap.BASELINE_SCALE_CM_PER_RAW
        + 0.01,
        np.float32,
    )
    _z, shelf_valid = gen_world_heightmap.decode_baseline(shelf)
    assert shelf_valid.all(), "real low ground was rejected along with the blank"

    # And the world's own floor is above the cut, so nothing real is ever near it.
    assert gen_world_heightmap.FILL_FLOOR_CM < -25500.0 < 0.0


# --------------------------------------------------------------------------------------
# Loading.
# --------------------------------------------------------------------------------------


def test_a_missing_field_is_none_and_never_an_error(tmp_path):
    """The default case on almost every machine, and it must cost the caller nothing."""
    assert hf.load_field(tmp_path / "nothing-here") is None


def test_a_broken_field_is_also_none_rather_than_an_exception(tmp_path):
    """A loader and only a loader: the generator diagnoses a broken field, not the server."""
    directory = build_field(tmp_path)
    (directory / hf.HEIGHT_NAME).write_bytes(b"not zlib at all")
    assert hf.load_field(directory) is None

    other = build_field(tmp_path / "second")
    (other / hf.META_NAME).write_text("{ this is not json", encoding="utf-8")
    assert hf.load_field(other) is None


def test_the_field_answers_with_the_layer_that_answered_and_its_measured_accuracy(tmp_path):
    """A reading carries its own uncertainty, because the field's layers are not alike."""
    field = hf.load_field(build_field(tmp_path))
    assert field is not None
    assert (field.width, field.height) == (FAKE_W, FAKE_H)
    assert field.build == "buildVersion 495413, a test"

    landscape = field.at(FAKE_X0, FAKE_Y0)
    assert (landscape.z_m, landscape.source, landscape.accuracy_m) == (12.3, "landscape", 0.205)
    cliff = field.at(FAKE_X0 + 3 * FAKE_SPACING, FAKE_Y0 + FAKE_SPACING)
    assert (cliff.z_m, cliff.source, cliff.accuracy_m) == (245.6, "cliff", 0.21)
    fill = field.at(FAKE_X0, FAKE_Y0 + 2 * FAKE_SPACING)
    assert (fill.z_m, fill.source, fill.accuracy_m) == (-7.8, "fill", 3.897)


def test_the_two_cliff_values_are_one_layer_split_by_how_the_texel_was_answered(tmp_path):
    """5 is additive: it is still the cliff layer and it is still as accurate at 1 m.

    What it adds is the only thing a renderer drawing finer than 1 m needs to know -- that
    a source vertex actually landed in this texel -- and ``density.u8.z`` is the count
    behind it.
    """
    field = hf.load_field(build_field(tmp_path, density=True))
    interpolated = field.at(FAKE_X0 + 6 * FAKE_SPACING, FAKE_Y0 + FAKE_SPACING)
    direct = field.at(FAKE_X0, FAKE_Y0 + FAKE_SPACING)
    assert interpolated.provenance == hf.PROV_CLIFF
    assert direct.provenance == hf.PROV_CLIFF_DIRECT
    assert direct.source == "cliff, direct"
    assert (interpolated.z_m, direct.z_m) == (245.6, 245.6)
    assert (interpolated.accuracy_m, direct.accuracy_m) == (0.21, 0.21)

    samples = field.density_raster()
    assert samples is not None
    assert samples[1, 0] == 3, "the direct half of the cliff row carries its sample count"
    assert samples[1, FAKE_W - 1] == 0, "the interpolated half carries none"
    assert samples[0].max() == 0, "the landscape is a lattice and has no sample count"


def test_a_field_written_before_the_density_plane_says_nothing_rather_than_zero(tmp_path):
    """Absent is not "no samples anywhere", and reading it as that would be a claim."""
    field = hf.load_field(build_field(tmp_path))
    assert field.density_raster() is None


def test_a_no_data_texel_and_an_off_grid_point_are_both_silence(tmp_path):
    """Two reasons to say nothing, and the caller is entitled to the same nothing for both.

    Zero is sea level and a real answer, so a no-data texel must never come back as one --
    which is the whole reason the sentinel is -32768 rather than 0.
    """
    field = hf.load_field(build_field(tmp_path))
    assert field.at(FAKE_X0, FAKE_Y0 + 3 * FAKE_SPACING) is None, "no-data read as a height"
    assert field.at(FAKE_X0 - 10 * FAKE_SPACING, FAKE_Y0) is None, "off the west edge"
    assert field.at(FAKE_X0, FAKE_Y0 + 40 * FAKE_SPACING) is None, "off the south edge"


def test_the_last_column_answers_and_the_one_past_it_does_not(tmp_path):
    """The east edge, which is the bound nothing else in this file touches.

    Both off-grid assertions above overshoot by ten and forty texels, so they pass whether the
    guard reads ``col < width`` or ``col <= width`` -- and the ``<=`` version is a real
    mistake to make, because a vertex-aligned grid genuinely has ``width`` vertices numbered 0
    to ``width - 1`` and the fencepost is one character. Mutating the comparison leaves the
    whole module green today.

    What it would cost is not an exception. ``_height_dm`` is a numpy array and ``[row, width]``
    raises, but the FIRST thing an out-of-range column does on a C-ordered raster is nothing
    visible at all in the row direction, and the failure a reader would see is the map's east
    edge answering with the west edge of the row below. So the pair is asserted directly: the
    last real column is a measurement, and one spacing further east is silence.

    Row 0 is the landscape row, so the answer is the one texel value it was built with.
    """
    field = hf.load_field(build_field(tmp_path))
    east = FAKE_X0 + (FAKE_W - 1) * FAKE_SPACING

    assert field.texel(east, FAKE_Y0) == (0, FAKE_W - 1), "the last column is not addressable"
    last = field.at(east, FAKE_Y0)
    assert last is not None, "the last column reads as off the grid"
    assert (last.z_m, last.source) == (12.3, "landscape")

    assert field.texel(east + FAKE_SPACING, FAKE_Y0) is None, "one column past the east edge"
    assert field.at(east + FAKE_SPACING, FAKE_Y0) is None, "off the east edge read as a height"

    # ...and the same fencepost on the other axis, for the same reason: the two bounds are one
    # ``and`` apart and a test that pins only one of them pins neither against a copy-paste.
    south = FAKE_Y0 + (FAKE_H - 1) * FAKE_SPACING
    assert field.texel(FAKE_X0, south) == (FAKE_H - 1, 0), "the last row is not addressable"
    assert field.texel(FAKE_X0, south + FAKE_SPACING) is None, "one row past the south edge"


def test_the_grid_is_vertex_aligned_so_a_reading_snaps_to_the_nearest_measurement(tmp_path):
    """Rounded, not floored. A texel IS the point ``x0 + col*spacing``, not a cell round it.

    Flooring would answer with the vertex up to a metre south-west of the question, which
    on a cliff edge is a different cliff -- and it would do it without ever looking wrong.
    """
    field = hf.load_field(build_field(tmp_path))
    assert field.texel(FAKE_X0 + 0.4 * FAKE_SPACING, FAKE_Y0) == (0, 0)
    assert field.texel(FAKE_X0 + 0.6 * FAKE_SPACING, FAKE_Y0) == (0, 1)
    assert field.texel(FAKE_X0 - 0.4 * FAKE_SPACING, FAKE_Y0) == (0, 0)


def test_water_is_a_second_surface_and_never_a_correction_to_the_ground(tmp_path):
    """The channel says a lake stands here; the ground stays exactly where the field put it.

    Measured, not stylistic: the generator's own docstring records that gating terrain on
    this channel made the field worse (nodes trim90 0.93 against 0.77), so a reading under
    water reports both numbers and moves neither.
    """
    field = hf.load_field(build_field(tmp_path))
    under = field.at(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert under.z_m == -15.0, "the ground was moved to the water surface"
    assert under.water_m == 2.0
    assert under.submerged is True
    assert under.water_depth_m == 17.0
    assert field.at(FAKE_X0, FAKE_Y0).submerged is False


def test_a_depth_the_field_cannot_measure_is_None_and_never_a_zero(tmp_path):
    """The open ocean, which is where every plausible version of this goes wrong.

    Row 5 is water at -17.0 m over a ground the FILL layer puts at -15.0 m -- the real
    arrangement over most of this world's sea, where a 3.9 m-quantised raster rounds above
    a surface it is nowhere near. Two things must hold and neither is automatic. The texel
    is submerged, which ``water_m > z_m`` denies. And its depth is ``None``, because
    ``-17.0 - -15.0`` is a number nobody measured and ``max(..., 0)`` would print it as a
    perfectly reasonable-looking 0.0 m of water.
    """
    field = hf.load_field(build_field(tmp_path))
    sea = field.at(FAKE_X0, FAKE_Y0 + 5 * FAKE_SPACING)
    assert sea.water_m == -17.0
    assert sea.z_m == -15.0, "the ground was moved to meet the water"
    assert sea.water_quality == hf.WATER_LEVEL_ONLY
    assert sea.submerged is True, "a sea surface below the fill raster read as dry land"
    assert sea.water_depth_m is None, "an unmeasured depth was reported as a number"
    assert sea.depth_known is False


def test_a_field_written_before_the_quality_byte_reads_exactly_as_it_used_to(tmp_path):
    """Missing is not dry. Without ``waterq.u8.z`` the old comparison is all there is.

    So the lake still reads as water and the sea still reads as land -- which is the bug
    that raster was added to fix, and is the honest behaviour for a field that predates it.
    """
    field = hf.load_field(build_field(tmp_path, quality=False))
    lake = field.at(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert lake.water_m == 2.0 and lake.submerged is True
    assert lake.water_depth_m == 17.0
    sea = field.at(FAKE_X0, FAKE_Y0 + 5 * FAKE_SPACING)
    assert sea.water_m == -17.0 and sea.submerged is False


def test_a_field_without_a_water_channel_still_answers(tmp_path):
    """Water is decoded separately and only if asked, so its absence costs one attribute."""
    field = hf.load_field(build_field(tmp_path, water=False))
    reading = field.at(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert reading.z_m == -15.0
    assert reading.water_m is None and reading.submerged is False


# --------------------------------------------------------------------------------------
# Reading an AREA. A build decision is about a pad, not a point, and the two readers must
# not be able to hold different opinions about the same texels.
# --------------------------------------------------------------------------------------


def build_shaped_field(tmp_path: Path, height_m: np.ndarray) -> Path:
    """A field of exactly the given shape, all landscape, no water.

    Separate from ``build_field`` because that one is one row per *kind of reading*, which
    is what a point reader needs and is useless for slope: every row is constant, so a
    ramp, a staircase and a boulder field are indistinguishable in it.
    """
    directory = tmp_path / hf.DIR_NAME
    directory.mkdir(parents=True)
    rows, cols = height_m.shape
    (directory / hf.HEIGHT_NAME).write_bytes(
        hf.encode_i16(np.rint(height_m * hf.DM_PER_M).astype(np.int16))
    )
    (directory / hf.PROV_NAME).write_bytes(
        hf.encode_u8(np.full((rows, cols), hf.PROV_LANDSCAPE, np.uint8))
    )
    (directory / hf.META_NAME).write_text(
        json.dumps(
            {
                "grid": {
                    "width": cols,
                    "height": rows,
                    "spacing_cm": 100.0,
                    "x0_cm": 0.0,
                    "y0_cm": 0.0,
                },
                "nodata": hf.NODATA,
                "provenance": {"1": {"name": "landscape", "accuracy_m": 0.205}},
            }
        ),
        encoding="utf-8",
    )
    return directory


def _whole_field(field: hf.Field) -> hf.Area:
    last_x = field.x0_cm + (field.width - 1) * field.spacing_cm
    last_y = field.y0_cm + (field.height - 1) * field.spacing_cm
    return field.window(field.x0_cm, field.y0_cm, last_x, last_y)


def test_an_area_agrees_with_the_point_reader_texel_for_texel(tmp_path):
    """The seam. Two readers over one raster is two chances to be wrong about no-data or
    submersion, and the area reader vectorises rules the point reader spells out."""
    field = hf.load_field(build_field(tmp_path))
    area = _whole_field(field)

    zs, wet, blind = [], 0, 0
    for row in range(field.height):
        for col in range(field.width):
            reading = field.at(
                field.x0_cm + col * field.spacing_cm, field.y0_cm + row * field.spacing_cm
            )
            if reading is None:
                blind += 1
                continue
            zs.append(reading.z_m)
            wet += reading.submerged

    total = field.width * field.height
    assert area.texels == len(zs)
    assert area.nodata_pct == pytest.approx(100.0 * blind / total, abs=0.05)
    assert area.submerged_pct == pytest.approx(100.0 * wet / total, abs=0.05)
    assert area.z_min_m == pytest.approx(min(zs))
    assert area.z_max_m == pytest.approx(max(zs))


def test_a_rectangle_off_the_grid_is_all_no_data_and_never_None(tmp_path):
    """Silence about a pad is an answer. ``None`` would make "nothing is known there" and
    "you asked wrong" the same result, and a caller cannot tell those apart afterwards."""
    field = hf.load_field(build_field(tmp_path))
    area = field.window(500_000.0, 500_000.0, 500_100.0, 500_100.0)
    assert area.nodata_pct == 100.0
    assert area.texels == 0
    assert area.z_range_m is None and area.roughness_m is None


def test_a_pad_hanging_off_the_edge_reports_the_part_nobody_measured(tmp_path):
    """The percentages are over the REQUESTED rectangle. Over the clipped one instead, a
    pad 90% off the map would report a confident 0% no-data about its last strip."""
    field = hf.load_field(build_field(tmp_path))
    last_x = FAKE_X0 + (FAKE_W - 1) * FAKE_SPACING
    area = field.window(last_x - FAKE_SPACING, FAKE_Y0, last_x + 8 * FAKE_SPACING, FAKE_Y0)
    assert area.requested_texels == 10
    assert area.texels == 2
    assert area.nodata_pct == pytest.approx(80.0)


def test_slope_and_roughness_are_different_questions(tmp_path):
    """A clean ramp is steep and perfectly buildable; a lumpy flat is neither steep nor
    pleasant. One "flatness" number would call them the same, which is the whole reason
    the two are published apart."""
    ramp = np.tile(np.arange(64, dtype=np.float64) * 0.5, (64, 1))
    field = hf.load_field(build_shaped_field(tmp_path / "ramp", ramp))
    smooth = _whole_field(field)
    assert smooth.slope_mean_deg == pytest.approx(26.6, abs=0.2)
    assert smooth.roughness_m == pytest.approx(0.0, abs=0.02)

    rng = np.random.default_rng(20260805)
    lumpy = rng.normal(0.0, 3.0, size=(64, 64))
    field = hf.load_field(build_shaped_field(tmp_path / "lumpy", lumpy))
    rough = _whole_field(field)
    assert rough.roughness_m == pytest.approx(3.0, abs=0.3)
    assert rough.slope_mean_deg > smooth.slope_mean_deg


def test_a_big_window_decimates_and_says_so(tmp_path):
    """Decimation cannot see detail finer than its new spacing, so a caller comparing two
    areas has to be able to see that one of them was subsampled."""
    field = hf.load_field(build_shaped_field(tmp_path, np.zeros((400, 400))))
    whole = field.window(0.0, 0.0, 39_900.0, 39_900.0, max_texels=10_000)
    assert whole.stride == 4
    assert whole.requested_texels == 400 * 400
    assert whole.nodata_pct == pytest.approx(0.0, abs=0.5)


def test_water_below_the_ground_is_measured_against_the_DRY_ground(tmp_path):
    """A pond 15 m below a plateau rim, against the pad median, would read as a drop of
    nearly nothing -- because on a mostly-flooded pad the pad median IS the pond bed."""
    field = hf.load_field(build_field(tmp_path))
    area = _whole_field(field)
    # The dry rows are 12.3, 245.6 and -7.8 m, median 12.3; every row median is -7.8,
    # because two of the five valid rows are the flooded ones. Measured against the pad
    # median the drop would be -0.3 m -- a plateau rim reported as level with the water.
    assert area.water_below_ground_m == pytest.approx(12.3 - (-7.5), abs=0.05)
    # And the limit the same field exposes: two water bodies at 2.0 m and -17.0 m give a
    # median between them that is neither. One rectangle, one lake, is the supported case.
    assert area.water_level_m == pytest.approx(-7.5)


def test_an_area_states_which_layer_answered_so_a_reader_can_distrust_it(tmp_path):
    """The fill layer is quantised to 3.9 m. A roughness of 2 m over mostly-fill ground is
    below its own error bar, and only the provenance mix says so."""
    field = hf.load_field(build_field(tmp_path))
    area = _whole_field(field)
    assert area.provenance_pct[hf.PROV_LANDSCAPE] == pytest.approx(100.0 * 2 / 6, abs=0.05)
    assert area.coarse_pct == pytest.approx(100.0 * 2 / 6, abs=0.05)


# --------------------------------------------------------------------------------------
# Finding the nearest water: a distance and a level, and never a capacity.
# --------------------------------------------------------------------------------------


def test_the_nearest_water_is_a_distance_and_a_surface(tmp_path):
    """Row 4 is 4 m north of row 0 and its surface is 2.0 m, both read off the raster."""
    field = hf.load_field(build_field(tmp_path))
    near = field.nearest_water(0.0, FAKE_Y0, 500.0)
    assert near.distance_m == pytest.approx(4.0)
    assert near.level_m == pytest.approx(2.0)
    assert near.quality == hf.WATER_MEASURED
    assert near.covered_pct == pytest.approx(100.0 * 6 * 7 / (11 * 11), abs=0.1)


def test_no_water_in_range_says_how_much_of_the_range_it_actually_saw(tmp_path):
    """ "None within 300 m" over a box that ran off the grid is a weaker claim than it
    sounds, and only ``covered_pct`` carries the difference."""
    field = hf.load_field(build_field(tmp_path))
    near = field.nearest_water(0.0, FAKE_Y0, 300.0)
    assert near.distance_m is None
    assert near.covered_pct < 100.0


def test_a_field_with_no_water_plane_answers_nothing_rather_than_nowhere(tmp_path):
    """Two different silences: this field cannot speak about water at all, which must not
    read as "there is none nearby"."""
    field = hf.load_field(build_field(tmp_path, water=False))
    assert field.nearest_water(0.0, FAKE_Y0, 500.0) is None


def test_without_a_quality_plane_the_ocean_row_reads_as_dry(tmp_path):
    """The fallback's known blind spot, pinned where it is visible: row 5's sea surface
    stands BELOW the fill raster's ground, so a comparison-only field walks past it to the
    pond a metre away instead."""
    graded = hf.load_field(build_field(tmp_path / "graded"))
    assert graded.nearest_water(0.0, FAKE_Y0 + 5 * FAKE_SPACING, 500.0).level_m == pytest.approx(
        -17.0
    )
    blind = hf.load_field(build_field(tmp_path / "blind", quality=False))
    walked_past = blind.nearest_water(0.0, FAKE_Y0 + 5 * FAKE_SPACING, 500.0)
    assert walked_past.distance_m == pytest.approx(1.0)
    assert walked_past.level_m == pytest.approx(2.0)


# --------------------------------------------------------------------------------------
# The elevation probe: a fourth source, beside the populations rather than inside them.
# --------------------------------------------------------------------------------------


def _samples() -> list[elevation.Sample]:
    """Three nodes and a foundation, all within the probe radius of the field's origin."""
    return [
        elevation.Sample("node", FAKE_X0, FAKE_Y0, 1000.0),
        elevation.Sample("node", FAKE_X0 + 100.0, FAKE_Y0, 1200.0),
        elevation.Sample("node", FAKE_X0, FAKE_Y0 + 100.0, 1400.0),
        elevation.Sample("structure", FAKE_X0, FAKE_Y0, 3000.0),
    ]


def test_a_probe_without_a_field_is_the_probe_it_always_was(tmp_path):
    """The default, and the case on almost every machine. Nothing may change for it."""
    near = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0)
    assert near.terrain is None and near.terrain_m is None
    assert near.ground == [10.0, 12.0, 14.0]
    assert near.built == [30.0]
    assert near.fill_m == 18.0


def test_the_field_is_a_fourth_answer_and_does_not_touch_the_sampled_populations(tmp_path):
    """The whole design in one assertion: a texel read is reported, never averaged in.

    The field says 12.3 m at this coordinate and the three nodes nearby median to 12.0 m.
    Folding the reading into ``ground`` would move that median, put a 0.2 m measurement in
    with points up to 200 m away, and cost the caller the ability to tell them apart. So
    ``ground``, ``built`` and ``fill_m`` come out bit for bit what they were without a
    field, and the reading arrives beside them with its own provenance.
    """
    field = hf.load_field(build_field(tmp_path))
    without = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0)
    near = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0, terrain_field=field)

    assert near.terrain_m == 12.3
    assert near.terrain.source == "landscape"
    assert near.terrain.accuracy_m == 0.205
    assert (near.ground, near.built, near.fill_m) == (without.ground, without.built, without.fill_m)
    assert near.counts == without.counts == {"node": 3, "structure": 1}


def test_a_field_that_knows_nothing_here_leaves_the_probe_saying_nothing(tmp_path):
    """No-data must not become a number, and it must not disturb the samples either."""
    field = hf.load_field(build_field(tmp_path))
    near = elevation.probe(
        FAKE_X0, FAKE_Y0 + 3 * FAKE_SPACING, _samples(), radius_m=500.0, terrain_field=field
    )
    assert near.terrain is None and near.terrain_m is None
    assert near.ground == [10.0, 12.0, 14.0]


def test_the_field_does_not_lower_the_refusal_to_invent_a_ground_level(tmp_path):
    """``MIN_GROUND_SAMPLES`` survives the heightmap arriving, and that is deliberate.

    A terrain reading is not a ground sample. One node plus a field is still one node, and
    a fill depth quoted from it would be the invented number this module exists to refuse
    -- so ``fill_m`` stays ``None`` however good the terrain is.
    """
    field = hf.load_field(build_field(tmp_path))
    thin = [
        elevation.Sample("node", FAKE_X0, FAKE_Y0, 1000.0),
        elevation.Sample("structure", FAKE_X0, FAKE_Y0, 3000.0),
    ]
    near = elevation.probe(FAKE_X0, FAKE_Y0, thin, radius_m=200.0, terrain_field=field)
    assert near.terrain_m == 12.3, "the field answered"
    assert len(near.ground) < elevation.MIN_GROUND_SAMPLES
    assert near.fill_m is None, "one node became a ground level because a field turned up"


# --------------------------------------------------------------------------------------
# The endpoint.
# --------------------------------------------------------------------------------------


def test_the_inspect_endpoint_says_which_source_answered(tmp_path, monkeypatch):
    """The popup's whole claim, over the wire: a number, its layer, and that layer's error.

    The loader is replaced rather than the data directory pointed elsewhere, so this runs
    identically on a machine that has a real field and on one that has never had one.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web import terrain as web_terrain
    from satisfactory_mcp.interfaces.web.app import create_app

    field = hf.load_field(build_field(tmp_path))
    monkeypatch.setattr(web_terrain, "field", lambda: field)
    # The synthetic field is pinned at the map's south-west corner, so ask about a point
    # inside it in metres -- which is the unit the endpoint takes and the popup prints.
    x_m, y_m = FAKE_X0 / 100.0, FAKE_Y0 / 100.0

    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: None)
    with TestClient(app) as client:
        body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()

    e = body["elevation"]
    assert e["terrain_m"] == 12.3
    assert e["terrain_source"] == "landscape"
    assert e["terrain_accuracy_m"] == 0.205
    assert e["terrain_note"] is None
    assert e["terrain_water_m"] is None
    assert e["terrain_water_depth_m"] is None and e["terrain_water_note"] is None
    # And the populations are still there, still separate, still labelled.
    assert "ground_m" in e and "ground_count" in e and "fill_note" in e


def test_the_endpoint_sends_a_water_level_without_a_depth_where_it_has_no_depth(
    tmp_path, monkeypatch
):
    """The ocean over the wire: a surface height, a null depth, and the reason for the null.

    The two water rows are asked about in one test because the contrast is the claim. Over
    1 m terrain the panel gets both numbers; over the fill layer it gets the level, no
    depth, and a sentence naming the layer that cannot supply one. A 0.0 there would read
    as a measurement of nothing, which is the failure ``fill_note`` already argued about.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web import terrain as web_terrain
    from satisfactory_mcp.interfaces.web.app import create_app

    field = hf.load_field(build_field(tmp_path))
    monkeypatch.setattr(web_terrain, "field", lambda: field)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: None)
    with TestClient(app) as client:
        asked = {}
        for label, row in (("lake", 4), ("sea", 5)):
            params = {"x_m": FAKE_X0 / 100.0, "y_m": (FAKE_Y0 + row * FAKE_SPACING) / 100.0}
            asked[label] = client.get("/api/inspect", params=params).json()["elevation"]

    assert asked["lake"]["terrain_water_m"] == 2.0
    assert asked["lake"]["terrain_water_depth_m"] == 17.0
    assert asked["lake"]["terrain_water_note"] is None

    assert asked["sea"]["terrain_water_m"] == -17.0, "the sea surface was dropped as dry"
    assert asked["sea"]["terrain_water_depth_m"] is None, "an unmeasured depth was sent"
    assert "fill" in (asked["sea"]["terrain_water_note"] or ""), "the null carries no reason"


def test_the_endpoint_says_WHY_there_is_no_terrain_rather_than_leaving_a_null(monkeypatch):
    """A null with no reason beside it reads as a bug, exactly as ``fill_note`` decided.

    Two causes, and they call for different actions from the reader: no field on this
    machine means "run the generator", and no data at this point means "there is nothing
    there". So they are different sentences.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web import terrain as web_terrain
    from satisfactory_mcp.interfaces.web.app import create_app

    monkeypatch.setattr(web_terrain, "field", lambda: None)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: None)
    with TestClient(app) as client:
        body = client.get("/api/inspect", params={"x_m": 0.0, "y_m": 0.0}).json()

    e = body["elevation"]
    assert e["terrain_m"] is None and e["terrain_source"] is None
    assert "no terrain field on this machine" in e["terrain_note"]
    assert "gen_world_heightmap.py" in e["terrain_note"]


def test_the_generator_and_the_loader_agree_on_the_file_names_and_the_grid():
    """One format, and the two halves of it must not drift apart.

    The generator imports the codec from the loader's own module rather than carrying a
    copy, which is what makes that true; this asserts the rest of the agreement -- the
    georeference the sidecar promises and the constants the generator writes it from.
    """
    grid_px = gen_world_heightmap.GRID_PX
    origin_x, origin_y = gen_world_heightmap.ORIGIN_X_CM, gen_world_heightmap.ORIGIN_Y_CM
    assert grid_px == 7500
    assert (origin_x, origin_y, gen_world_heightmap.SPACING_CM) == (-324700.0, -375000.0, 100.0)
    assert gen_world_heightmap.hf is hf, "the generator must use the shipped codec, not a copy"

    # The sampler the run validates on has to be the sampler the server reads with, or the
    # validation measures something nobody ships.
    height = np.array([[10, 20, hf.NODATA]], np.int16)
    big = np.full((grid_px, grid_px), hf.NODATA, np.int16)
    big[0, 0:3] = height
    got = gen_world_heightmap.sample_grid(
        big,
        np.array([origin_x, origin_x + 100.0, origin_x + 200.0]),
        np.array([origin_y] * 3),
    )
    assert got[0] == 1.0 and got[1] == 2.0 and np.isnan(got[2])


# --------------------------------------------------------------------------------------
# Surfaces, bilinear reads and the plane cache.
# --------------------------------------------------------------------------------------

#: The terrain plane stores z_m = (raw - ZERO) / UNITS + OFFSET, as the cook does.
T_ZERO, T_UNITS, T_OFFSET = 32768.0, 128.0, 1.0


def _terrain_raw(z_m: np.ndarray) -> np.ndarray:
    return np.rint((z_m - T_OFFSET) * T_UNITS + T_ZERO).astype(np.uint16)


def build_layered_field(tmp_path: Path) -> Path:
    """A 7x6 ramp (z = col metres) with one rock texel standing on it.

    The terrain plane sits one column east of the main grid, as the real one is offset by
    whole texels, and covers columns 1..6. At row 2, col 3 the ground is a 50 m rock top,
    the bare terrain under it 3 m and the top plane a 60 m arch over both.
    """
    directory = build_shaped_field(tmp_path, np.tile(np.arange(7, dtype=float), (6, 1)))
    ground = np.tile(np.arange(7, dtype=np.int16) * 10, (6, 1))
    ground[2, 3] = 500
    prov = np.full((6, 7), hf.PROV_LANDSCAPE, np.uint8)
    prov[2, 3] = hf.PROV_CLIFF_DIRECT
    top = ground.copy()
    top[2, 3] = 600
    terrain = _terrain_raw(np.tile(np.arange(1, 7, dtype=float), (6, 1)))
    terrain[5, 5] = 0  # a hole
    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(ground))
    (directory / hf.PROV_NAME).write_bytes(hf.encode_u8(prov))
    (directory / hf.TOP_NAME).write_bytes(hf.encode_i16(top))
    (directory / hf.TERRAIN_NAME).write_bytes(hf.encode_u16(terrain))
    meta = json.loads((directory / hf.META_NAME).read_text(encoding="utf-8"))
    meta["provenance"]["5"] = {"name": "cliff, direct", "accuracy_m": 0.17}
    meta["generator_version"] = 4
    meta["terrain_grid"] = {
        "width": 6,
        "height": 6,
        "spacing_cm": 100.0,
        "x0_cm": 100.0,
        "y0_cm": 0.0,
        "zero": T_ZERO,
        "units_per_m": T_UNITS,
        "offset_m": T_OFFSET,
    }
    (directory / hf.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return directory


def _meta(directory: Path) -> dict:
    return json.loads((directory / hf.META_NAME).read_text(encoding="utf-8"))


def test_the_u16_codec_round_trips_every_value():
    grid = np.array([[0, 1, 65535, 32768, 0, 65535]], np.uint16)
    assert np.array_equal(hf.decode_u16(hf.encode_u16(grid), 1, 6), grid)


def test_a_bilinear_read_on_a_ramp_lands_between_the_vertices(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    reading = field.z(125.0, 100.0)
    assert reading.z_m == pytest.approx(1.25)
    assert reading.surface == "ground"
    assert field.z(125.0, 100.0, bilinear=False).z_m == pytest.approx(1.0)
    assert field.at(125.0, 100.0).z_m == pytest.approx(1.0)
    assert field.z(125.0, 100.0, surface="terrain").z_m == pytest.approx(1.25, abs=0.01)


def test_a_bilinear_read_beside_no_data_uses_the_nearest_valid_vertex(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    # Between the hole at x 600 and its western neighbour at x 500.
    assert field.z(560.0, 500.0, surface="terrain").z_m == pytest.approx(5.0, abs=0.01)
    assert field.z(540.0, 500.0, surface="terrain").z_m == pytest.approx(5.0, abs=0.01)
    assert field.z(600.0, 500.0, surface="terrain") is None
    assert field.z(0.0, 0.0, surface="terrain") is None, "west of the terrain frame"
    assert field.z(-500.0, 0.0) is None, "off the grid"


def test_a_rock_texel_is_ambiguous_and_carries_the_terrain_under_it(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    rock = field.z(300.0, 200.0)
    assert rock.z_m == pytest.approx(50.0)
    assert rock.terrain_z_m == pytest.approx(3.0, abs=0.01)
    assert rock.ambiguous
    assert not field.z(100.0, 100.0).ambiguous
    surfaces = field.surfaces(300.0, 200.0)
    assert (surfaces.ground_m, surfaces.top_m) == (50.0, 60.0)


def test_a_hint_picks_the_surface_at_or_below_it(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    on_floor = field.z(300.0, 200.0, hint_z_cm=400.0)
    assert (on_floor.surface, on_floor.provenance) == ("terrain", hf.PROV_LANDSCAPE)
    assert on_floor.z_m == pytest.approx(3.0, abs=0.01)
    on_rock = field.z(300.0, 200.0, hint_z_cm=5100.0)
    assert (on_rock.surface, on_rock.z_m) == ("ground", 50.0)
    # 58.5 m is within the 2 m slack below the arch at 60 m, so the arch is the floor.
    assert field.z(300.0, 200.0, hint_z_cm=5850.0).surface == "top"
    # Above everything: the highest surface below the hint, not the nearest roof.
    assert field.z(300.0, 200.0, hint_z_cm=9000.0).surface == "top"


def test_a_window_reads_the_surface_it_is_asked_for(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    ground = field.window(200.0, 100.0, 400.0, 300.0)
    terrain = field.window(200.0, 100.0, 400.0, 300.0, surface="terrain")
    assert ground.z_max_m == 50.0 and terrain.z_max_m == pytest.approx(4.0, abs=0.05)
    assert ground.ambiguous_pct == pytest.approx(100.0 / 9, abs=0.1)
    assert terrain.provenance_pct == {hf.PROV_LANDSCAPE: 100.0}
    edge = field.window(0.0, 0.0, 100.0, 0.0, surface="terrain")
    assert edge.nodata_pct == 50.0, "column 0 lies west of the terrain frame"
    with pytest.raises(ValueError):
        hf.load_field(build_field(tmp_path / "old")).window(0, 0, 100, 100, surface="terrain")


def test_without_a_terrain_plane_every_cliff_texel_is_ambiguous(tmp_path):
    field = hf.load_field(build_field(tmp_path))
    assert field.z(FAKE_X0, FAKE_Y0 + FAKE_SPACING).ambiguous
    assert not field.z(FAKE_X0, FAKE_Y0).ambiguous
    assert field.z(FAKE_X0, FAKE_Y0).terrain_z_m is None


def test_the_mapped_cache_matches_the_in_memory_decode(tmp_path):
    directory = build_layered_field(tmp_path)
    fresh = hf.Field(_meta(directory), directory)
    assert fresh.cache_events[hf.HEIGHT_NAME] == "written"
    mapped = hf.Field(_meta(directory), directory)
    plain = hf.Field(_meta(directory), directory, cache=False)
    assert mapped.cache_events[hf.HEIGHT_NAME] == "mapped"
    assert isinstance(mapped._height_dm, np.memmap)
    for name in (hf.HEIGHT_NAME, hf.PROV_NAME, hf.TERRAIN_NAME, hf.TOP_NAME):
        assert np.array_equal(mapped._plane(name), plain._plane(name)), name
    assert mapped.z(125.0, 100.0) == plain.z(125.0, 100.0)


def _bump_mtime(path: Path) -> None:
    import os

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))


def test_a_changed_source_invalidates_the_cache(tmp_path):
    directory = build_layered_field(tmp_path)
    hf.Field(_meta(directory), directory)
    changed = np.full((6, 7), 70, np.int16)
    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(changed))
    _bump_mtime(directory / hf.HEIGHT_NAME)
    again = hf.Field(_meta(directory), directory)
    assert again.cache_events[hf.HEIGHT_NAME] == "written"
    assert np.array_equal(again._height_dm, changed)


def test_a_new_generator_version_invalidates_the_cache(tmp_path):
    directory = build_layered_field(tmp_path)
    hf.Field(_meta(directory), directory)
    meta = {**_meta(directory), "generator_version": 5}
    assert hf.Field(meta, directory).cache_events[hf.HEIGHT_NAME] == "written"


def test_an_untouched_source_with_a_new_mtime_is_still_mapped(tmp_path):
    directory = build_layered_field(tmp_path)
    hf.Field(_meta(directory), directory)
    _bump_mtime(directory / hf.HEIGHT_NAME)
    assert hf.Field(_meta(directory), directory).cache_events[hf.HEIGHT_NAME] == "mapped"


def test_a_cache_that_cannot_be_written_falls_back_to_decoding(tmp_path):
    directory = build_layered_field(tmp_path)
    (directory / hf.CACHE_DIR_NAME).write_text("not a directory", encoding="utf-8")
    field = hf.Field(_meta(directory), directory)
    assert field.cache_events[hf.HEIGHT_NAME] == "failed, decoded"
    assert field.z(125.0, 100.0).z_m == pytest.approx(1.25)


def test_a_cliff_edge_reads_the_nearest_vertex_rather_than_a_blend(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    assert field.z(240.0, 200.0).z_m == pytest.approx(2.0)
    assert field.z(260.0, 200.0).z_m == pytest.approx(50.0)
    assert field.z(225.0, 100.0).z_m == pytest.approx(2.25), "a gentle slope still blends"


def test_a_landscape_reading_takes_the_terrain_planes_finer_value(tmp_path):
    directory = build_layered_field(tmp_path)
    terrain = _terrain_raw(np.tile(np.arange(1, 7, dtype=float), (6, 1)))
    terrain[0, 0] = _terrain_raw(np.array([1.04]))[0]
    (directory / hf.TERRAIN_NAME).write_bytes(hf.encode_u16(terrain))
    field = hf.load_field(directory)
    assert field.z(100.0, 0.0).z_m == pytest.approx(1.04, abs=0.008)
    assert field.at(100.0, 0.0).z_m == pytest.approx(1.0)
