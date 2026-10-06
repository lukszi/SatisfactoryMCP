"""One object's whole payload: the actor's reference lists, the property list, the trailer."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import expect
from ..objects import ObjectSlice
from ..reader import Reader
from ..references import ObjectReference, read_reference, read_references
from ..versions import FIRST_MODERN_BODY, FIRST_UE5_OBJECT_VERSION
from .decoder import PropertyDecoder

__all__ = ["PLAIN_TRAILER", "ParsedObject", "read_object"]

#: Bytes between the property list's ``"None"`` terminator and the end of an object's payload
#: when its class writes nothing of its own. A component always leaves one of these; an actor
#: with class-specific data leaves more. Which of the two a given object gets is not established.
PLAIN_TRAILER = (4, 8)


@dataclass(slots=True)
class ParsedObject:
    """One object's property block, decoded.

    ``properties`` holds the ``[name, value]`` pairs the projection consumes;
    ``property_types`` the parallel types, the only record of what a value *was*.
    """

    version: int
    #: Actors only: the object this one hangs off, and its component children.
    parent_reference: ObjectReference | None = None
    child_references: list[ObjectReference] = field(default_factory=list)
    properties: list[list] = field(default_factory=list)
    property_types: list[list] = field(default_factory=list)
    #: Absolute span of everything after the terminator: the plain trailer, plus class-specific
    #: data on some actors, which ``pioneersav.save`` arranges to decode.
    extra_offset: int = 0
    extra_length: int = 0
    #: The trailing class-specific bytes, decoded on first access to ``actorSpecificInfo``.
    actor_specific_info: list | None = None
    #: Zero-argument decoder for the trailing bytes, set where the class is known; else ``None``.
    decode_trailer: object | None = None
    #: Anything skipped rather than understood, as ``(offset, what)``.
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def actorSpecificInfo(self) -> list | None:
        """The trailing class-specific bytes, decoded on first access.

        ``None``, never ``[]``, when no reader knows the class: an empty list would pass for a
        decoded blob holding nothing.
        """
        if self.actor_specific_info is None and self.decode_trailer is not None:
            self.actor_specific_info = self.decode_trailer()
        return self.actor_specific_info


def read_object(
    body: bytes,
    slot: ObjectSlice,
    *,
    actor: bool,
    save_version: int = FIRST_MODERN_BODY,
) -> ParsedObject:
    """Decode one object's property block, the ``slot`` of ``body`` that ``read_body`` found.

    ``actor`` comes from the object's header, since only an actor's payload opens with
    reference lists and the payload does not say which it is. ``save_version`` is the file's,
    and picks the float width of ``FVector``/``FQuat``/``FBox``.
    """
    r = Reader(body, slot.offset)
    end = slot.end
    parsed = ParsedObject(version=slot.version)

    if actor:
        parsed.parent_reference = read_reference(r)
        parsed.child_references = read_references(r, end)
    if slot.version >= FIRST_UE5_OBJECT_VERSION:
        r.i8()  # the object-reference migration byte

    decoder = PropertyDecoder(r, slot.version, parsed.warnings, save_version=save_version)
    parsed.properties, parsed.property_types = decoder.property_list(end)
    parsed.extra_offset = r.pos
    parsed.extra_length = end - r.pos

    # a list ending inside its trailer means a name was read as None
    expect(
        parsed.extra_length >= PLAIN_TRAILER[0],
        r.pos,
        f"the property list of {'an actor' if actor else 'a component'} ended "
        f"{parsed.extra_length} bytes before its {slot.length}-byte payload does, and no "
        f"object's trailer is shorter than {PLAIN_TRAILER[0]}. A property name was "
        "read as the list terminator, so the properties after it are missing",
    )
    # actor trailers stay unbounded: a patch may add a ninth class
    if not actor:
        expect(
            parsed.extra_length in PLAIN_TRAILER,
            r.pos,
            f"a component's property list left {parsed.extra_length} bytes of its "
            f"{slot.length}-byte payload unread, and a component's trailer is exactly "
            f"{' or '.join(map(str, PLAIN_TRAILER))} bytes, so the list terminated early "
            "and the properties after that point are missing",
        )
    return parsed
