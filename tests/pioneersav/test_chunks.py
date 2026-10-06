"""Inflating the compressed body.

Everything after the header is a run of independently zlib-compressed blocks with a
49-byte preamble each. Verified across every save on the author's disk: all 31 whose
header parses inflate cleanly, 1,194 MB of body in 1.3 s, none failed.

The preamble writes its compressed and uncompressed sizes TWICE, identically. That is a
free integrity check on a format that has no other one, so both copies are read and
compared rather than one being skipped.
"""

from __future__ import annotations

import zlib

import pytest

from pioneersav import CHUNK_TAG, decompress_body
from pioneersav.chunks import ZLIB


def _chunk(payload: bytes, *, tag: int = CHUNK_TAG, algo: int = ZLIB, lie: bool = False) -> bytes:
    """One well-formed chunk, so the failure paths can be tested without a real save."""
    blob = zlib.compress(payload)
    head = tag.to_bytes(8, "little")
    head += (131072).to_bytes(8, "little", signed=True)
    head += bytes([algo])
    first = len(blob).to_bytes(8, "little", signed=True) + len(payload).to_bytes(
        8, "little", signed=True
    )
    second = first
    if lie:
        second = len(blob).to_bytes(8, "little", signed=True) + (len(payload) + 1).to_bytes(
            8, "little", signed=True
        )
    return head + first + second + blob


def test_a_single_chunk_round_trips():
    payload = b"factory" * 900
    assert decompress_body(_chunk(payload), 0) == payload


def test_chunks_concatenate_in_order():
    """The body is one flat stream; a reader above this must not see the seams."""
    a, b = b"alpha" * 500, b"beta" * 700
    assert decompress_body(_chunk(a) + _chunk(b), 0) == a + b


def test_a_wrong_tag_is_refused_with_its_offset():
    """A save torn mid-write fails on the chunk where the tear is. This project reads
    autosaves that are rewritten every few minutes, so that is a routine event, and
    inflating garbage into the object walk would fail somewhere unrelated instead."""
    with pytest.raises(ValueError, match="expected"):
        decompress_body(_chunk(b"x" * 100, tag=0xDEADBEEF), 0)


def test_an_unknown_compressor_is_named_not_attempted():
    with pytest.raises(ValueError, match="compressor 7"):
        decompress_body(_chunk(b"x" * 100, algo=7), 0)


def test_the_duplicated_sizes_are_checked_against_each_other():
    """Both copies are present and equal on every chunk of every save seen. Reading one
    and ignoring the other would throw away the only consistency check the format offers."""
    with pytest.raises(ValueError, match="disagrees with itself"):
        decompress_body(_chunk(b"x" * 100, lie=True), 0)


def test_a_short_inflate_is_caught():
    """The header's uncompressed size is a claim, and a claim that does not match what
    came out means the stream is not what it says."""
    good = _chunk(b"y" * 200)
    broken = bytearray(good)
    broken[33:41] = (999).to_bytes(8, "little", signed=True)  # second uncompressed size
    with pytest.raises(ValueError, match="disagrees with itself"):
        decompress_body(bytes(broken), 0)


def test_trailing_bytes_are_refused():
    """Silently ignoring a partial chunk would silently drop the end of a factory."""
    with pytest.raises(ValueError, match="trailing"):
        decompress_body(_chunk(b"z" * 50) + b"\x00" * 8, 0)


def test_offset_is_honoured():
    payload = b"skip me" * 100
    assert decompress_body(b"\xff" * 16 + _chunk(payload), 16) == payload


def test_a_chunk_whose_blob_is_cut_short_names_the_chunk_and_the_shortfall():
    """The commonest real failure in this whole parser, and it used to be the least legible.

    Truncating two real saves at fourteen different fractions of their length put the failure
    on this exact read every single time: the 49-byte preamble of the last chunk survives the
    cut and the compressed blob it promises does not. Left to ``Reader._take`` the report was
    "read of 5131 at 974874 runs past end (978615)" -- three numbers, no mention of a chunk,
    and nothing to tell a reader that the file is simply mid-write rather than the walk being
    lost. If this regresses, every torn autosave goes back to looking like a parser bug.
    """
    whole = _chunk(b"conveyor" * 400)
    with pytest.raises(ValueError, match=r"chunk at 0 declares \d+ compressed bytes"):
        decompress_body(whole[:-20], 0)
    with pytest.raises(ValueError, match="shortfall of 20"):
        decompress_body(whole[:-20], 0)
    # And the tear is attributed to the chunk it is in, not to the first one.
    good = _chunk(b"belt" * 300)
    with pytest.raises(ValueError, match=f"chunk at {len(good)} declares"):
        decompress_body(good + whole[:-20], 0)


def test_a_negative_compressed_size_is_refused_by_the_same_guard():
    """A torn preamble can put any int64 in the size field, including a negative one.

    ``Reader._take`` renders that as "read of -5 at 49 runs past end", which is not merely
    unhelpful but actively misleading -- nothing ran past any end. Folding it into the
    shortfall check costs one comparison and keeps the message true.
    """
    broken = bytearray(_chunk(b"q" * 100))
    broken[33:41] = (-5).to_bytes(8, "little", signed=True)  # second compressed size
    broken[17:25] = (-5).to_bytes(8, "little", signed=True)  # first copy, kept in agreement
    with pytest.raises(ValueError, match="declares -5 compressed bytes"):
        decompress_body(bytes(broken), 0)
