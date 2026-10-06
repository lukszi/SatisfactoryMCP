"""Loading the terrain field: its no-data rules, its layers, its bounds and its plane cache.

The raster is derived from the game's cooked assets and never committed, so every test reads a
synthetic field from ``tests.support.heightfields``; that is also the only way the absent
case is ever exercised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.heightfields import (
    FAKE_H,
    FAKE_SPACING,
    FAKE_W,
    FAKE_X0,
    FAKE_Y0,
    build_field,
    build_layered_field,
)


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


# ----------------------------------------------------------------------- the plane cache


def _meta(directory: Path) -> dict:
    return json.loads((directory / hf.META_NAME).read_text(encoding="utf-8"))


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
