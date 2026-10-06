"""The buildables that exist without an actor: FGLightweightBuildableSubsystem's blob.

Foundations, walls, ramps, catwalks and pillars are not saved as actors. One subsystem actor
carries every one of them in the class-specific bytes trailing its empty property list -- 3.10
MB and 8,347 pieces on the reference save -- and nothing else in the save mentions them, so
this is the only source for the projection's ``structures`` and ``lightweight_counts``. The
instance count and both class paths are length-prefixed, so a record read one byte short
desynchronises the walk and the next class path fails its own length check; the blob also has
to be consumed to its last byte.

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

**The int32 before the blocks is a count, not a constant.** It is 0 for foundations, walls,
catwalks and railings, which is every class on a save built without beams -- so it read as
"0 everywhere" for as long as no beam had been placed. A beam carries one block holding its
`BeamLength`, because a beam's length is chosen per piece and there is nowhere else to keep
it. Reading it as a constant leaves the walk 116 bytes short on the first beam and the next
class path fails its own length check thousands of records later.

`RECORD_BYTES` is therefore a **minimum** per instance rather than the whole of one, which is
what it always was -- the two reference paths were never in it either. It is used only to
bound a claimed instance count, and a bound that is too generous is still a bound.
"""

from __future__ import annotations

from .errors import ParseError
from .reader import Reader
from .references import read_reference

__all__ = ["LIGHTWEIGHT_SUBSYSTEM", "VERSION", "read_lightweight"]

#: The one actor whose trailing bytes this module reads.
LIGHTWEIGHT_SUBSYSTEM = "/Script/FactoryGame.FGLightweightBuildableSubsystem"

#: Fixed bytes per instance record, by blob version: everything but the two length-prefixed
#: reference paths. Blob version 2 goes with saveVersion 52 and version 4 with saveVersion 60,
#: and the two differ by exactly the trailing ``(uint8, int32)`` pair. A version this does not
#: know is refused rather than read as the nearest one, because the walk would then
#: desynchronise and report a class path that is not one, thousands of records later.
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


def _type_data(r: Reader, count: int, end: int) -> list:
    """The optional type-specific data blocks: ``[[typeReference, rawBytes], ...]``.

    Held as raw bytes rather than decoded. The block is a tagged property list and this module
    reads no tags -- ``trailers.py`` next door takes the same line -- but it is *size-prefixed*,
    so consuming it exactly needs no tag reader at all, which is the whole requirement here.
    Decoding it would put ``BeamLength`` within reach and is worth doing separately.

    Returned as a nested list on purpose. ``extract._placed`` decides whether a record is a
    real piece or a stale slot by scanning an instance's top-level fields for a non-empty
    string or a ``pathName``, so a bare type path added at that level would make every stale
    slot look placed. A list is neither and is passed over.
    """
    if not 0 <= count <= MAX_DATA_BLOCKS:
        raise ParseError(
            f"at body offset {r.pos - 4}: an instance claims {count} type-specific data "
            f"blocks, and no instance carries more than {MAX_DATA_BLOCKS} -- the walk is "
            "out of step"
        )
    out = []
    for _ in range(count):
        type_ref = read_reference(r)
        at = r.pos
        size = r.i32()
        if not 0 <= size <= end - r.pos:
            raise ParseError(
                f"at body offset {at}: type-specific data for {type_ref.path_name!r} claims "
                f"{size} bytes with {end - r.pos} left in the blob"
            )
        out.append([type_ref, r.bytes(size)])
    return out


def _instance(r: Reader, version: int, end: int) -> list:
    """One buildable. Field order is the file's, and load-bearing: the projection reads
    ``inst[1]`` for the position and walks the rest generically.

    The type-specific data list is **appended** rather than slotted in beside the count that
    introduces it, so that every index this record already had keeps its meaning -- the version-4
    ``(uint8, int32)`` player handle stays at 13 and 14. It is empty for every class that
    carries no such data, which is most of them.
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
    data = _type_data(r, out[12], end)
    if version >= 4:
        out += [r.i8(), r.i32()]
    out.append(data)
    return out


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
    fixed = RECORD_BYTES[version]
    class_count = r.i32()
    if not 0 <= class_count <= 100_000:
        raise ParseError(
            f"at body offset {r.pos - 4}: {class_count} buildable classes is implausible"
        )

    out: list = [version]
    for k in range(class_count):
        at = r.pos
        # The class arrives as a reference, not a bare string: an empty level name and then the
        # path. Both lengths are checked BEFORE being consumed, because a walk that has lost
        # its place is reading a quaternion here, whose second int32 is 0x80000000 -- a
        # negative length, and so a four-gigabyte UTF-16 read.
        if r.remaining < 8:
            raise ParseError(
                f"at body offset {at}: expected buildable class {k + 1} of {class_count}, "
                f"found only {r.remaining} bytes left"
            )
        level_len, path_len = Reader(body, at).i32(), Reader(body, at + 4).i32()
        if level_len != 0 or not 0 < path_len < MAX_PATH:
            raise ParseError(
                f"at body offset {at}: expected buildable class {k + 1} of {class_count}, "
                f"found string lengths {level_len} and {path_len} -- the walk is out of "
                "step. Every class here has an empty level name and a path under 512 bytes"
            )
        path = read_reference(r).path_name
        if not path.startswith("/"):
            raise ParseError(
                f"at body offset {at}: expected buildable class {k + 1} of {class_count}, "
                f"found {path[:40]!r} -- the walk is out of step"
            )
        instance_count = r.i32()
        if instance_count < 0 or r.pos + instance_count * fixed > end:
            raise ParseError(
                f"at body offset {r.pos - 4}: {path} claims {instance_count} instances, "
                f"which does not fit in the {end - r.pos} bytes left"
            )
        out.append([path, [_instance(r, version, end) for _ in range(instance_count)]])

    if r.pos != end:
        raise ParseError(
            f"at body offset {r.pos}: {class_count} buildable classes ended "
            f"{end - r.pos} bytes short of the object's {length}"
        )
    return out
