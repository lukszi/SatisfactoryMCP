"""The uncompressed header at the front of a .sav file.

It is read straight off the front with no decompression, which is why ``read_info`` is cheap
enough to call on every save in a directory just to group them by world. It is a linear
sequence with no lengths or offsets to seek by, so a field added by a future patch shifts
everything after it and the ``PACKAGE_FILE_TAG`` check at the end of the walk is what catches
that. The old header is a strict prefix of the modern one -- types 8 and 9 have an identical
field list, 10 adds ``save_identifier``, 14 adds ``save_name``, two unnamed int32s,
``save_data_hash`` and ``is_creative`` -- so this is one walk with gated fields rather than
four layouts. Absent fields read as ``None`` rather than ``0``/``False``, because a zero says
"not creative" and ``None`` says "this header has no such field".
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from .errors import ParseError
from .reader import Reader
from .versions import (
    HEADER_TYPE_SAVE_IDENTIFIER,
    HEADER_TYPE_SAVE_NAME,
    KNOWN_HEADER_TYPES,
)

__all__ = ["SaveInfo", "body_hash", "check_body_hash", "read_info", "read_info_bytes"]

#: Marks the start of the compressed body. Unreal's PACKAGE_FILE_TAG, and the check that
#: the header was walked to exactly the right place: land anywhere else and this is not it.
PACKAGE_FILE_TAG = 0x9E2A83C1


def body_hash(data: bytes, body_offset: int) -> tuple[int, int]:
    """The digest the header's ``save_data_hash`` holds, computed from the bytes.

    It is **the md5 of every byte from ``body_offset`` to the end of the file** -- the compressed
    body: all chunk preambles and all zlib blobs, and nothing of the header. Returned as the
    little-endian u64 pair the header stores, so it compares directly against
    ``SaveInfo.save_data_hash``. Absent from every pre-1.0 header.

    Reading a save never needs it. Anything that MODIFIES a body and writes it back does:
    without recomputing this, the file carries a digest of bytes it no longer contains.
    """
    digest = hashlib.md5(data[body_offset:]).digest()
    lo = int.from_bytes(digest[:8], "little")
    hi = int.from_bytes(digest[8:], "little")
    return (lo, hi)


def check_body_hash(data: bytes, info: SaveInfo) -> bool | None:
    """Does the header's stored digest match the body actually present?

    ``None`` rather than ``False`` when the header predates the field, because "this save has no
    hash to check" and "this save's hash is wrong" are different answers and a caller acting on
    the second should never be handed the first. The absence test is ``is None`` and never
    ``== (0, 0)``, so an all-zero digest on a modern save is still checked.
    """
    if info.save_data_hash is None:
        return None
    return body_hash(data, info.body_offset) == info.save_data_hash


@dataclass
class SaveInfo:
    save_header_type: int
    save_version: int
    build_version: int
    #: ``""`` on saveHeaderType 8, 9 and 10, which do not write it. Not the file name: the
    #: field is absent, and inventing it from the path would make a header report something
    #: the header does not contain.
    save_name: str
    map_name: str
    map_options: str
    session_name: str
    play_duration_s: int
    save_datetime_ticks: int
    #: One byte. On a pre-1.0 save it is the session's visibility and ``map_options`` says so
    #: too -- 0 with ``SV_Private``, 1 with ``SV_FriendsOnly``, 35 of 35. On a 1.0 save it takes
    #: five values (``0x00`` at saveVersion 60, ``0x18``/``0x58``/``0x98``/``0xD8`` at 52) whose
    #: low six bits are 24 or 0 rather than the old 0 or 1, so it is not that enum any more and
    #: what it *is* is unknown. Do not read a visibility off this on a 1.0 save.
    session_visibility: int
    editor_object_version: int
    mod_metadata: str
    is_modded: bool
    #: ``""`` on saveHeaderType 8 and 9. Where it exists it identifies the world, and it is the
    #: same value across all four years of saves on this disk.
    save_identifier: str
    #: ``None`` on every old header, where the field is absent.
    save_data_hash: tuple[int, int] | None
    #: ``None`` on every old header, where the field is absent.
    is_creative: bool | None
    #: Where the compressed body starts, so the caller need not re-walk the header.
    body_offset: int

    # The projection's `header` block reads these nine names, so they are aliases rather than
    # renamed fields: this module stays snake_case and its consumer stays unchanged.
    @property
    def saveHeaderType(self) -> int:
        return self.save_header_type

    @property
    def saveVersion(self) -> int:
        return self.save_version

    @property
    def buildVersion(self) -> int:
        return self.build_version

    @property
    def sessionName(self) -> str:
        return self.session_name

    @property
    def playDurationInSeconds(self) -> int:
        return self.play_duration_s

    @property
    def saveDateTimeInTicks(self) -> int:
        return self.save_datetime_ticks

    @property
    def saveIdentifier(self) -> str:
        return self.save_identifier

    @property
    def isModdedSave(self) -> bool:
        return self.is_modded

    @property
    def isCreativeModeEnabled(self) -> bool | None:
        return self.is_creative


def read_info_bytes(data: bytes) -> SaveInfo:
    """The header of a save already in memory; any refusal is prefixed with its two versions."""
    try:
        return _walk_header(data)
    except ParseError as exc:
        raise ParseError(f"{_header_context(data)}: {exc}") from exc


def _header_context(data: bytes) -> str:
    """The two version fields, for a failure message. Guarded: the file may be shorter."""
    if len(data) < 8:
        return f"{len(data)}-byte file, too short to hold a save header"
    r = Reader(data)
    header_type, save_version = r.i32(), r.i32()
    known = ""
    if header_type not in KNOWN_HEADER_TYPES:
        known = f" (known: {', '.join(map(str, KNOWN_HEADER_TYPES))})"
    return f"saveHeaderType {header_type}{known}, saveVersion {save_version}"


def _walk_header(data: bytes) -> SaveInfo:
    """One linear walk over all four field lists, gated on ``save_header_type``.

    A type this module has never seen is walked as the nearest layout below it and then judged
    by the tag: landing on ``PACKAGE_FILE_TAG`` at exactly the offset the walk predicts means
    the field list was right, and landing anywhere else refuses the file and names the type.
    """
    r = Reader(data)
    header_type = r.i32()
    type14_fields = header_type >= HEADER_TYPE_SAVE_NAME
    save_version = r.i32()
    build_version = r.i32()
    save_name = r.string() if type14_fields else ""
    map_name = r.string()
    map_options = r.string()
    session_name = r.string()
    play_duration_s = r.i32()
    save_datetime_ticks = r.i64()
    session_visibility = r.i8()
    editor_object_version = r.i32()
    # all zero on every old save, where only the total width can be checked: the modern
    # (str, i32) grouping sums to it on all four header types
    mod_metadata = r.string()
    is_modded = bool(r.i32())
    save_identifier = r.string() if header_type >= HEADER_TYPE_SAVE_IDENTIFIER else ""
    save_data_hash: tuple[int, int] | None = None
    is_creative: bool | None = None
    if type14_fields:
        r.i32()  # unnamed, 1 on every save seen
        r.i32()  # unnamed, 1 on every save seen
        save_data_hash = (r.u64(), r.u64())
        is_creative = bool(r.i32())
    info = SaveInfo(
        save_header_type=header_type,
        save_version=save_version,
        build_version=build_version,
        save_name=save_name,
        map_name=map_name,
        map_options=map_options,
        session_name=session_name,
        play_duration_s=play_duration_s,
        save_datetime_ticks=save_datetime_ticks,
        session_visibility=session_visibility,
        editor_object_version=editor_object_version,
        mod_metadata=mod_metadata,
        is_modded=is_modded,
        save_identifier=save_identifier,
        save_data_hash=save_data_hash,
        is_creative=is_creative,
        body_offset=r.pos,
    )

    # The tag is the proof: every field above is positional, so a wrong width anywhere lands
    # here at the wrong byte. Never skip this when fewer than four bytes are left -- that
    # throws the proof away in the one case it is most needed, a file the game has begun
    # writing and not finished, and pushes the refusal two layers down into an empty body.
    if r.remaining < 4:
        raise ParseError(
            f"the file ends at offset {r.pos}, where the compressed body should start: "
            f"{r.remaining} byte(s) left, too few for the {PACKAGE_FILE_TAG:#x} tag. The "
            "header is complete, so this is a partly-written file rather than a bad layout"
        )
    tag = Reader(data, r.pos).u32()
    if tag != PACKAGE_FILE_TAG:
        # Do not repeat the version fields here: `read_info_bytes` puts them in front of
        # every failure from this module.
        raise ParseError(
            f"header did not end at the compressed body: expected tag "
            f"{PACKAGE_FILE_TAG:#x} at offset {r.pos}, found {tag:#x}. The layout "
            f"likely changed"
        )
    return info


def read_info(path: str | os.PathLike[str]) -> SaveInfo:
    """Header only. Reads a bounded 64 KiB prefix -- far more than any header seen, about 700
    bytes with a long ``mapOptions`` -- rather than the whole file.
    """
    with open(path, "rb") as fh:
        return read_info_bytes(fh.read(65_536))
