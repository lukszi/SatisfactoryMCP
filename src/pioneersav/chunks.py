"""Decompressing the body, which the game writes as a run of zlib chunks.

Everything after the header is a sequence of independently-compressed blocks, each with a
49-byte preamble; concatenating the inflated blocks gives the one flat byte stream the level
and object walk reads. The sizes are written twice and the first copy is verified against the
second, which is the only integrity check this format offers. The tag is checked per chunk
rather than once, so a save torn mid-write fails on the chunk holding the tear instead of
inflating garbage into the object walk::

    int64  tag                 0x222222229E2A83C1
    int64  max chunk size      131072 on every save seen
    uint8  compressor          3
    int64  compressed size     \\
    int64  uncompressed size    | written twice, identically
    int64  compressed size      |
    int64  uncompressed size   /

A pre-1.0 save (saveVersion 25 to 36) writes a 48-byte preamble: the same fields without the
compressor byte, and with the tag's high half zero, so its tag is ``0x9E2A83C1``.
"""

from __future__ import annotations

import zlib

from .errors import ParseError
from .reader import Reader

__all__ = [
    "CHUNK_TAG",
    "OLD_CHUNK_TAG",
    "OLD_PREAMBLE_BYTES",
    "PREAMBLE_BYTES",
    "ZLIB",
    "decompress_body",
]

#: PACKAGE_FILE_TAG, as the game writes it into each chunk preamble: Unreal's
#: 0x9E2A83C1 in the low half, 0x22222222 in the high.
CHUNK_TAG = 0x222222229E2A83C1

#: The same field on a pre-1.0 save: the bare tag, with the high half left zero.
OLD_CHUNK_TAG = 0x9E2A83C1

#: The only compressor seen. Named rather than assumed so an unexpected one is refused with
#: its value instead of surfacing as a zlib error.
ZLIB = 3

#: The loop needs these only to decide whether a whole preamble is left to read.
PREAMBLE_BYTES = 49
OLD_PREAMBLE_BYTES = 48


def decompress_body(data: bytes, offset: int, *, old: bool = False) -> bytes:
    """Inflate every chunk from ``offset`` to the end of ``data``.

    ``old`` selects the 48-byte preamble of a pre-1.0 save. It is a parameter rather than a
    sniff because the caller already has the header's ``save_version``, and the tag check
    below then confirms the choice on every chunk.
    """
    r = Reader(data, offset)
    tag_wanted = OLD_CHUNK_TAG if old else CHUNK_TAG
    preamble = OLD_PREAMBLE_BYTES if old else PREAMBLE_BYTES
    out: list[bytes] = []
    while r.remaining >= preamble:
        start = r.pos
        tag = r.u64()
        if tag != tag_wanted:
            raise ParseError(
                f"chunk at {start} has tag {tag:#x}, expected {tag_wanted:#x} -- the body "
                "is not a chunk stream here, which usually means the file was still "
                "being written"
            )
        max_plain = r.i64()
        compressor = ZLIB if old else r.i8()
        if compressor != ZLIB:
            raise ParseError(
                f"chunk at {start} uses compressor {compressor}, only {ZLIB} (zlib) is known"
            )
        first_compressed, first_plain = r.i64(), r.i64()
        compressed, plain = r.i64(), r.i64()
        if (first_compressed, first_plain) != (compressed, plain):
            raise ParseError(
                f"chunk at {start} disagrees with itself: {first_compressed}/{first_plain} "
                f"then {compressed}/{plain}"
            )

        # Where a torn save lands: the last chunk's preamble survives the cut and its blob
        # does not. Checked here rather than left to `Reader._take`, whose message names
        # neither the chunk nor the shortfall. A negative size is folded into the same guard.
        if not 0 <= compressed <= r.remaining:
            raise ParseError(
                f"chunk at {start} declares {compressed} compressed bytes but "
                f"{r.remaining} are left in the file, a shortfall of "
                f"{compressed - r.remaining} -- the file is almost certainly still being "
                "written, since the game rewrites a save in place every few minutes"
            )
        # The maximum bounds the uncompressed size beside it, not 131072: a different block
        # size is a writer's choice, not a format change. The compressed size is not bounded,
        # since zlib output of incompressible data is larger than its input.
        if not 0 < max_plain or plain > max_plain:
            raise ParseError(
                f"chunk at {start} declares a maximum of {max_plain} bytes but holds "
                f"{compressed} compressed / {plain} uncompressed. The preamble contradicts "
                "itself, so this is not a chunk header"
            )
        blob = r.bytes(compressed)
        try:
            chunk = zlib.decompress(blob)
        except zlib.error as exc:
            raise ParseError(f"chunk at {start} failed to inflate: {exc}") from exc
        if len(chunk) != plain:
            raise ParseError(
                f"chunk at {start} inflated to {len(chunk)} bytes, header said {plain}"
            )
        out.append(chunk)

    if r.remaining:
        raise ParseError(f"{r.remaining} trailing byte(s) after the last chunk at {r.pos}")
    if not out:
        # Reachable, not defensive: a save truncated to exactly its header length leaves no
        # chunks at all, and an empty body would push the refusal down to the level walk.
        raise ParseError(f"no chunk at {offset}: nothing follows the header")
    return b"".join(out)
