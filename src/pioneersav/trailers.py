"""The class-specific bytes trailing an actor's property list, for seven of the eight classes
that have them; ``FGLightweightBuildableSubsystem``, the eighth, has its own module.

Every reader here is checked by exact consumption: a record read correctly ends precisely
where the object declared its trailing bytes end, and any wrong field width leaves a
remainder. Fields that stay unexplained are named ``unknown`` rather than given a plausible
name. Decoding is lazy -- ``ParsedObject.actorSpecificInfo`` decodes on first access -- so a
malformed trailer raises inside the caller rather than at the save boundary.
"""

from __future__ import annotations

from .errors import ParseError
from .reader import Reader
from .references import read_reference

__all__ = ["TRAILER_READERS", "read_trailer"]

CONVEYOR_CHAIN = "/Script/FactoryGame.FGConveyorChainActor"
POWER_LINE = "/Game/FactoryGame/Buildable/Factory/PowerLine/Build_PowerLine.Build_PowerLine_C"
CIRCUIT_SUBSYSTEM = "/Game/FactoryGame/-Shared/Blueprint/BP_CircuitSubsystem.BP_CircuitSubsystem_C"
PLAYER_STATE = "/Game/FactoryGame/Character/Player/BP_PlayerState.BP_PlayerState_C"


def _read_chain(r: Reader, end: int) -> list:
    """A conveyor chain: the belts it spans, their splines, and the items on it.

    The projection's ``belts`` key comes from the spline geometry; nothing reads the items::

        reference   the first belt in the chain
        reference   the last belt
        int32       segmentCount
        per segment:
            reference       the chain actor this segment belongs to
            reference       the belt
            int32           pointCount
            per point:      3 x (3 x double) -- location, then two tangents
            float32         the part of this segment's offset range with no spline behind
                            it -- 0 on most segments, and ~200/300/400 cm at a conveyor lift
                            junction, always at the low-offset end, where no item ever sits
            float32         where this segment starts, centimetres along the chain
            float32         where it ends
            int32           ring index of the first item on this segment, or -1
            int32           ring index of the last, or -1
            int32           the segment's own index
        float32     the chain's length in centimetres -- always the first segment's end
        int32       the item ring's capacity, DERIVED rather than independent:
                    floor(length / 120) + 2 * segmentCount + 1
        int32       ring index of the chain's first item, or -1 when the chain is empty
        int32       ring index of its last, or -1
        int32       itemCount
        per item:
            reference       the item class
            int32           the item's state, a length that is 0 on every item seen
            float32         how far along the chain it is, centimetres

    Ring buffer, direction of travel and offset bounds: savparse-notes.md, ``### The conveyor
    chain record, explained``.
    """
    first_belt, last_belt = read_reference(r), read_reference(r)
    segments = []
    for _ in range(_read_count(r, end, 24, "chain segments")):
        owner, belt = read_reference(r), read_reference(r)
        points = [
            [
                [r.f64(), r.f64(), r.f64()],
                [r.f64(), r.f64(), r.f64()],
                [r.f64(), r.f64(), r.f64()],
            ]
            for _ in range(_read_count(r, end, 72, "spline points"))
        ]
        segments.append([owner, belt, points, r.f32(), r.f32(), r.f32(), r.i32(), r.i32(), r.i32()])
    chain = [r.f32(), r.i32(), r.i32(), r.i32()]
    items = [
        [read_reference(r), r.i32(), r.f32()] for _ in range(_read_count(r, end, 12, "chain items"))
    ]
    return [first_belt, last_belt, segments, chain, items]


def _read_power_line(r: Reader, end: int) -> list:
    """The two power connections a line joins.

    Nothing reads this -- the projection's power graph comes from the connection components'
    own properties -- but decoding it is what lets ``save._attach_trailer`` treat any other
    actor with a long trailer as unexplained.
    """
    return [read_reference(r), read_reference(r)]


def _read_circuit_subsystem(r: Reader, end: int) -> list:
    """Every power circuit in the world, as ``(id, reference)`` pairs. The id is the same
    number the circuit's own ``mCircuitID`` property carries.
    """
    return [[r.i32(), read_reference(r)] for _ in range(_read_count(r, end, 12, "power circuits"))]


def _read_player_state(r: Reader, end: int) -> list:
    """The player's account id.

    A ``uint8`` of unknown meaning, then a one-byte id type, then a length-prefixed blob of 8
    bytes holding a 64-bit account id -- the name of the folder the save sits in. The declared
    length is checked against what is left, which is what makes reading an opaque blob safe.
    """
    unknown, id_type = r.i8(), r.i8()
    size = r.i32()
    if size < 0 or r.pos + size != end:
        raise ParseError(
            f"at body offset {r.pos - 4}: player id declares {size} bytes with {end - r.pos} left"
        )
    return [unknown, id_type, r.bytes(size)]


def _read_count(r: Reader, end: int, min_record_bytes: int, what: str) -> int:
    """An int32 count, refused if that many records of at least ``min_record_bytes`` cannot fit:
    a torn file makes a count anything, and two billion iterations is a hang, not an error."""
    at = r.pos
    count = r.i32()
    if count < 0 or r.pos + count * min_record_bytes > end:
        raise ParseError(
            f"at body offset {at}: {count} {what} do not fit in the {end - r.pos} bytes left"
        )
    return count


#: Which classes this module can read, by the ``typePath`` their actor header carries. The
#: ``RepSize`` variants are the same actor with another replication budget and the identical
#: record.
TRAILER_READERS = {
    CONVEYOR_CHAIN: _read_chain,
    f"{CONVEYOR_CHAIN}_RepSizeMedium": _read_chain,
    f"{CONVEYOR_CHAIN}_RepSizeLarge": _read_chain,
    f"{CONVEYOR_CHAIN}_RepSizeHuge": _read_chain,
    f"{CONVEYOR_CHAIN}_RepSizeNoCull": _read_chain,
    POWER_LINE: _read_power_line,
    CIRCUIT_SUBSYSTEM: _read_circuit_subsystem,
    PLAYER_STATE: _read_player_state,
}


def read_trailer(class_path: str, body: bytes, offset: int, length: int) -> list:
    """Decode one actor's trailing bytes. ``offset``/``length`` span the 4-byte trailer too.

    Refuses to return a short read: the reader must land exactly on the end the object
    declared. That is the only check available on a record with no separators and no
    self-describing length.
    """
    reader = TRAILER_READERS[class_path]
    end = offset + length
    r = Reader(body, offset + 4)
    out = reader(r, end)
    if r.pos != end:
        raise ParseError(
            f"at body offset {r.pos}: {class_path.rsplit('.', 1)[-1]} left "
            f"{end - r.pos} of its {length} trailing bytes unread"
        )
    return out
