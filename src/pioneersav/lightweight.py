"""The buildables that exist without an actor: FGLightweightBuildableSubsystem's blob.

Foundations, walls, ramps, catwalks and pillars are not saved as actors. One subsystem actor
carries every one of them in the class-specific bytes trailing its empty property list, and
nothing else in the save mentions them, so this is the only source for the projection's
``structures`` and ``lightweight_counts``. Class paths are length-prefixed, so a record read one
byte short fails the next class path's length check, and the blob must be consumed to its last
byte. Derivation and the beam blocks: docs/savparse-notes.md, ``### The lightweight buildables``.

Layout::

    int32   0                    the object's own trailer, read by the caller
    int32   2 or 4               version of what follows
    int32   classCount
    per class:
        reference   buildable class: an empty level name, then the class path
        int32       instanceCount
        per instance, 162 fixed bytes (157 at version 2) plus two reference paths:
            4 x double   rotation quaternion
            3 x double   position, world centimetres
            3 x double   scale
            reference    swatch: the paint slot
            reference    x3     empty on all 224,530 instances
            2 x 4 float  override colours, primary and secondary
            reference           empty everywhere
            uint8               0 everywhere
            reference    recipe: what the piece was built from
            reference           the blueprint proxy this piece was placed as part of, empty
                                when it was placed by hand
            int32               COUNT of the type-specific data blocks that follow -- see
                                below. 0 on most classes, which is why it read as a constant
            per block:
                reference       the data struct's type, e.g.
                                `/Script/FactoryGame.BuildableBeamLightweightData`
                int32           byte size of the property list that follows
                property list   tagged and `None`-terminated, held here as raw bytes
            uint8               version 4 only, and the two are ONE field: an
            int32               FPlayerInfoHandle naming who placed the piece. `06 00 00 00 00`
                                is a set handle and `00 ff ff ff ff` the unset one, so (6, 0)
                                on a piece this player placed and (0, -1) on everything
                                migrated from a version-2 save
"""

from __future__ import annotations

from .errors import ParseError
from .reader import Reader
from .references import read_reference

__all__ = ["LIGHTWEIGHT_SUBSYSTEM", "VERSION", "read_lightweight"]

#: The one actor whose trailing bytes this module reads.
LIGHTWEIGHT_SUBSYSTEM = "/Script/FactoryGame.FGLightweightBuildableSubsystem"

#: Fixed bytes per instance record, by blob version: a MINIMUM, used only to bound a claimed
#: instance count, since the reference paths and type-data blocks come on top. Versions 2 and
#: 4 differ by the trailing ``(uint8, int32)``; an unknown version is refused, not guessed.
RECORD_BYTES = {2: 157, 4: 162}

#: The blob version the current game writes.
VERSION = 4

#: Longest class path seen is 112 bytes; the cap is a bound on a length that a desynchronised
#: walk would otherwise turn into a multi-megabyte read.
MAX_PATH = 512


#: Most type-specific data blocks a single instance may carry. Every instance seen carries 0
#: or 1; the cap exists so that a walk that has lost its place cannot read a quaternion's
#: mantissa as a block count and then loop on it.
MAX_DATA_BLOCKS = 8


def _read_type_data(r: Reader, count: int, end: int) -> list:
    """The type-specific data blocks: ``[[typeReference, rawBytes], ...]``.

    Raw bytes, because each block is size-prefixed: consuming it exactly needs no tag reader.
    """
    if not 0 <= count <= MAX_DATA_BLOCKS:
        raise ParseError(
            f"at body offset {r.pos - 4}: an instance claims {count} type-specific data "
            f"blocks, and no instance carries more than {MAX_DATA_BLOCKS} -- the walk is "
            "out of step"
        )
    # nested list on purpose: extract._placed scans only top-level fields
    blocks = []
    for _ in range(count):
        type_ref = read_reference(r)
        at = r.pos
        size = r.i32()
        if not 0 <= size <= end - r.pos:
            raise ParseError(
                f"at body offset {at}: type-specific data for {type_ref.path_name!r} claims "
                f"{size} bytes with {end - r.pos} left in the blob"
            )
        blocks.append([type_ref, r.bytes(size)])
    return blocks


def _read_instance(r: Reader, version: int, end: int) -> list:
    """One buildable, fields in the file's order: the projection reads ``inst[1]`` for the
    position. The type-data list is appended last so every older index keeps its meaning.
    """
    out = [
        [r.f64(), r.f64(), r.f64(), r.f64()],
        [r.f64(), r.f64(), r.f64()],
        [r.f64(), r.f64(), r.f64()],
        read_reference(r),
        read_reference(r),
        read_reference(r),
        read_reference(r),
        [[r.f32(), r.f32(), r.f32(), r.f32()], [r.f32(), r.f32(), r.f32(), r.f32()]],
        read_reference(r),
        r.i8(),
        read_reference(r),
        read_reference(r),
        r.i32(),
    ]
    data = _read_type_data(r, out[12], end)
    if version >= 4:
        out += [r.i8(), r.i32()]
    out.append(data)
    return out


def _read_class_path(r: Reader, number: int, class_count: int) -> str:
    """Buildable class ``number``'s path, from a reference with an empty level name."""
    at = r.pos
    # lengths checked before they are consumed: a walk out of step reads a quaternion here,
    # whose 0x80000000 would be a negative length and a four-gigabyte UTF-16 read
    if r.remaining < 8:
        raise ParseError(
            f"at body offset {at}: expected buildable class {number} of {class_count}, "
            f"found only {r.remaining} bytes left"
        )
    level_len, path_len = Reader(r.data, at).i32(), Reader(r.data, at + 4).i32()
    if level_len != 0 or not 0 < path_len < MAX_PATH:
        raise ParseError(
            f"at body offset {at}: expected buildable class {number} of {class_count}, "
            f"found string lengths {level_len} and {path_len} -- the walk is out of "
            "step. Every class here has an empty level name and a path under 512 bytes"
        )
    path = read_reference(r).path_name
    if not path.startswith("/"):
        raise ParseError(
            f"at body offset {at}: expected buildable class {number} of {class_count}, "
            f"found {path[:40]!r} -- the walk is out of step"
        )
    return path


def read_lightweight(body: bytes, offset: int, length: int) -> list:
    """Decode the subsystem's trailing bytes into ``[version, [classPath, [instance, ...]], ...]``.

    ``offset``/``length`` are the object's ``extra`` span including its 4-byte trailer, so the
    version is read at ``offset + 4``.
    """
    r = Reader(body, offset + 4)
    end = offset + length

    version = r.i32()
    if version not in RECORD_BYTES:
        raise ParseError(
            f"at body offset {r.pos - 4}: lightweight buildables are version {version}, "
            f"this reads {sorted(RECORD_BYTES)} -- the instance record has probably changed"
        )
    min_record_bytes = RECORD_BYTES[version]
    class_count = r.i32()
    if not 0 <= class_count <= 100_000:
        raise ParseError(
            f"at body offset {r.pos - 4}: {class_count} buildable classes is implausible"
        )

    decoded: list = [version]
    for class_index in range(class_count):
        path = _read_class_path(r, class_index + 1, class_count)
        instance_count = r.i32()
        if instance_count < 0 or r.pos + instance_count * min_record_bytes > end:
            raise ParseError(
                f"at body offset {r.pos - 4}: {path} claims {instance_count} instances, "
                f"which does not fit in the {end - r.pos} bytes left"
            )
        decoded.append([path, [_read_instance(r, version, end) for _ in range(instance_count)]])

    if r.pos != end:
        raise ParseError(
            f"at body offset {r.pos}: {class_count} buildable classes ended "
            f"{end - r.pos} bytes short of the object's {length}"
        )
    return decoded
