"""The Nanite decoder's arithmetic, and the checks that make a wrong decode loud.

A real Nanite page is 128 KB of bit-packed strips out of a 12 GB container, and a test that
needs one is a test that runs on one machine. What is pinned here is everything that does
not: the bit helpers whose C# semantics are easy to get subtly wrong in Python, the page
header's own refusal to be misread, and the three falsifiers -- page tiling, the identity
counts, and the boundary-edge count -- which are the whole reason this decoder can be
believed on a build nobody has verified it against by hand.

The end-to-end decode is checked against the installed game by
``tools/check_terrain_geometry.py``, which is where a 12 GB container belongs.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from satisfactory_mcp.core.gameassets import nanite as nan
from satisfactory_mcp.core.gameassets import staticmesh as sm

# ------------------------------------------------------------------ the bit helpers


def test_shift_counts_are_masked_to_five_bits_like_the_source() -> None:
    """C# masks a shift count to 5 bits and several call sites depend on it.

    Python's shifts are unbounded, so ``x >> 32`` is 0 there and ``x >> 0`` in C#. Two
    places rely on the C# behaviour -- ``FirstBitHigh`` returning ``0xFFFFFFFF`` and a
    ``foundBitIndex - 1`` of -1 -- and both would decode to plausible, wrong triangles.
    """
    assert nan.get_bits(0xDEADBEEF, 32, 32) == nan.get_bits(0xDEADBEEF, 32, 0)
    assert nan.bit_align_u32(0xFFFFFFFF, 0x0000FFFF, 32) == 0x0000FFFF


def test_get_bits_signed_sign_extends() -> None:
    assert nan.get_bits_signed(0b1, 1, 0) == -1
    assert nan.get_bits_signed(0b0, 1, 0) == 0
    assert nan.get_bits_signed(0b1000, 4, 0) == -8
    assert nan.get_bits_signed(0b0111, 4, 0) == 7


def test_bit_align_u32_joins_two_dwords() -> None:
    assert nan.bit_align_u32(0xAAAAAAAA, 0xBBBBBBBB, 0) == 0xBBBBBBBB
    assert nan.bit_align_u32(0x0000000F, 0xF0000000, 28) == 0xFF


def test_first_bit_high_of_zero_is_the_c_sharp_sentinel() -> None:
    assert nan.first_bit_high(0) == 0xFFFFFFFF
    assert nan.first_bit_high(1) == 0
    assert nan.first_bit_high(0x80000000) == 31


def test_precision_scale_is_two_to_the_minus_exponent() -> None:
    """The encoder writes a precision as a biased-exponent subtraction, not a float."""
    for exponent in (-20, -1, 0, 1, 7, 20):
        assert nan.precision_scale(exponent) == pytest.approx(2.0**-exponent)


# ------------------------------------------------------------------ the page header


def test_a_page_with_the_wrong_magic_is_refused() -> None:
    blob = struct.pack("<4H", 0x1234, 1, 0, 0) + b"\0" * 128
    with pytest.raises(nan.DecodeError, match="fixup magic"):
        nan.parse_page_headers(blob, 0)


def test_a_page_whose_two_cluster_counts_disagree_is_refused() -> None:
    """The fixup chunk and the disk header each state the cluster count. They must agree.

    Two statements about one number in two places is the cheapest structural check the
    format offers, and a misread offset breaks it immediately.
    """
    blob = struct.pack("<4H", nan.NANITE_FIXUP_MAGIC, 3, 0, 0)
    blob += struct.pack("<6I", 5, 0, 0, 0, 0, 0) + b"\0" * 256
    with pytest.raises(nan.DecodeError, match="disk header"):
        nan.parse_page_headers(blob, 0)


# ------------------------------------------------------------------ the falsifiers


def test_a_closed_tetrahedron_has_no_boundary_edges() -> None:
    closed = np.array([[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 3, 2]], np.int64)
    assert nan.boundary_edges(closed) == 0


def test_an_open_shell_reports_its_rim() -> None:
    """A single triangle is three boundary edges; a shell missing one face is that face."""
    assert nan.boundary_edges(np.array([[0, 1, 2]], np.int64)) == 3
    open_shell = np.array([[0, 1, 2], [0, 2, 3], [0, 3, 1]], np.int64)
    assert nan.boundary_edges(open_shell) == 3


def test_boundary_edges_of_nothing_is_zero() -> None:
    assert nan.boundary_edges(np.zeros((0, 3), np.int64)) == 0


def test_welding_closes_a_seam_that_the_assembly_opened() -> None:
    """A cluster carries its own copy of every shared vertex, so an unwelded decode of a
    closed mesh reads as full of holes. The falsifier has to fire on the decode, not on
    the concatenation that follows it. Measured on build 495413: unwelded, **not one** of
    the Nanite rock meshes reads as closed; welded, 77 of 120 do."""
    corners = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], np.float32
    )
    faces = np.array([[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 3, 2]], np.int64)
    # Every face in its own "cluster": four independent triples, twelve vertices.
    scattered = corners[faces].reshape(-1, 3)
    split = np.arange(12, dtype=np.int64).reshape(4, 3)
    assert nan.boundary_edges(split) == 12

    welded_positions, welded = nan.weld(scattered, split)
    assert len(welded_positions) == 4
    assert nan.boundary_edges(welded) == 0


def test_welding_keeps_the_original_positions_rather_than_the_rounded_key() -> None:
    positions = np.array([[1.00000001, 2.0, 3.0], [1.0, 2.0, 3.0]], np.float32)
    triangles = np.array([[0, 1, 0]], np.int64)
    got, _tris = nan.weld(positions, triangles)
    assert len(got) == 1
    assert got[0][0] == positions[0][0]


def test_welding_nothing_is_nothing() -> None:
    empty = np.zeros((0, 3), np.float32)
    got, tris = nan.weld(empty, np.zeros((0, 3), np.int64))
    assert len(got) == 0 and len(tris) == 0


class Resource:
    def __init__(self, clusters: int, input_triangles: int) -> None:
        self.clusters = clusters
        self.input_triangles = input_triangles


def test_identity_checks_are_silent_when_the_mesh_agrees_with_itself() -> None:
    decoded = {
        "total_clusters": 4,
        "leaf_clusters": 3,
        "triangles": np.zeros((128, 3), np.int64),
    }
    assert nan.identity_checks(Resource(4, 128), decoded) == []


def test_identity_checks_name_both_disagreements() -> None:
    decoded = {
        "total_clusters": 4,
        "leaf_clusters": 3,
        "triangles": np.zeros((127, 3), np.int64),
    }
    problems = nan.identity_checks(Resource(5, 128), decoded)
    assert len(problems) == 2
    assert "clusters" in problems[0] and "triangles" in problems[1]


# ------------------------------------------------------------------ the page table


def state(offset: int, size: int) -> tuple[int, ...]:
    return (offset, size, size, 0, 0, 0, 0)


def resource(root_pages: int, root_bytes: int, states: list[tuple[int, ...]]):
    return sm.NaniteResource(
        present=True, root_bytes=root_bytes, root_pages=root_pages, page_states=states
    )


def test_pages_that_tile_exactly_report_nothing() -> None:
    got = resource(2, 300, [state(0, 100), state(100, 200), state(0, 500), state(500, 250)])
    assert sm.page_table_problems(got, 750) == []


def test_root_pages_that_do_not_cover_rootdata_are_caught() -> None:
    got = resource(1, 300, [state(0, 100)])
    assert sm.page_table_problems(got, None) == ["root pages cover 100 of 300 RootData bytes"]


def test_a_gap_between_streaming_pages_is_caught() -> None:
    got = resource(1, 100, [state(0, 100), state(0, 500), state(600, 250)])
    problems = sm.page_table_problems(got, None)
    assert any("streaming page starts at 600, expected 500" in p for p in problems)


def test_streaming_pages_are_checked_against_the_bulk_entry() -> None:
    got = resource(1, 100, [state(0, 100), state(0, 500)])
    assert sm.page_table_problems(got, 999) == [
        "streaming pages cover 500 bytes, the .ubulk entry says 999"
    ]


def test_a_mesh_with_no_pages_is_not_a_mesh_with_broken_pages() -> None:
    """Vacuous, and it must read as vacuous: a fifth of the rock set has no Nanite."""
    assert sm.page_table_problems(sm.NaniteResource(present=False), 1234) == []


# ------------------------------------------------------------------ the cursor


def test_the_cursor_refuses_to_run_off_the_end() -> None:
    cur = sm.Cursor(b"\x01\x00\x00\x00")
    assert cur.u32() == 1
    with pytest.raises(sm.ParseError, match="want 4 bytes at 4"):
        cur.u32()


def test_a_bulk_array_skips_exactly_its_payload() -> None:
    blob = struct.pack("<ii", 12, 3) + b"\xaa" * 36 + b"END!"
    cur = sm.Cursor(blob)
    size, count, at = cur.bulk_array()
    assert (size, count, at) == (12, 3, 8)
    assert cur.take(4) == b"END!"


def test_render_data_refuses_garbage_rather_than_inventing_a_lod() -> None:
    with pytest.raises(sm.ParseError):
        sm.parse_render_data(b"\x00" * 512)
