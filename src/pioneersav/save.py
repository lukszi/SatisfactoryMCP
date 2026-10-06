"""One call that reads a whole .sav, composing the four layers below into the shape the
projection consumes: ``save.levels[i].actorAndComponentObjectHeaders`` parallel to
``save.levels[i].objects``, each object carrying ``properties`` as ``[name, value]`` pairs.

**The body has to stay alive for the whole parse**, because every ``ObjectSlice`` and every
``ParsedObject.extra_offset`` is an absolute index into it and nothing is copied. This module
is also where an actor's class is matched to a trailing-bytes reader, which
``ParsedObject.actorSpecificInfo`` calls on first access rather than during the parse.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import partial

from .chunks import decompress_body
from .header import SaveInfo, read_info_bytes
from .lightweight import LIGHTWEIGHT_SUBSYSTEM, read_lightweight
from .objects import ActorHeader, ComponentHeader, read_body
from .properties import PLAIN_TRAILER, ParsedObject, read_object
from .trailers import TRAILER_READERS, read_trailer
from .versions import FIRST_MODERN_BODY

__all__ = [
    "UNDECODED_TRAILER_CLASSES",
    "ParsedLevel",
    "ParsedSave",
    "read_full_save",
    "read_full_save_bytes",
]


@dataclass
class ParsedLevel:
    """One level's headers and decoded objects, as two parallel lists.

    ``header[i]`` describes ``object[i]``. They are separate length-prefixed blocks in the
    file, so the pairing is a fact about the format, and ``objects`` is decoded in header
    order to keep the two aligned.
    """

    name: str
    headers: list[ActorHeader | ComponentHeader]
    objects: list[ParsedObject]

    @property
    def actorAndComponentObjectHeaders(self) -> list[ActorHeader | ComponentHeader]:
        """The spelling ``extract_save.iter_objects`` reads."""
        return self.headers


@dataclass
class ParsedSave:
    """A whole save: its header, its levels, and the bytes they point into.

    ``body`` is retained because ``ParsedObject.extra_offset``/``extra_length`` are absolute
    indices into it, and it is the only copy -- the slices are views.
    """

    info: SaveInfo
    body: bytes
    levels: list[ParsedLevel]
    #: Every actor the save records as gone, as ``(level cell, actor path)``. The world's
    #: collectibles -- slugs, mushrooms, Mercer spheres, somersloops, looted drop pods -- are
    #: placed by the map and not saved, so this negative record is the only thing that says
    #: which of them the player has taken. Merged from the three lists the save keeps; see
    #: ``objects.SaveBody.destroyed_actors``.
    destroyed_actors: list[tuple[str, str]] = field(default_factory=list)
    #: Everything skipped rather than understood, as ``(body offset, what)``, merged from the
    #: level walk and every object's property list. Diagnostics, not projection data: nothing
    #: above reads the fields they name, and the sidecar prints them to stderr rather than into
    #: the projection.
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return sum(len(lv.objects) for lv in self.levels)


#: Class paths that carry their own bytes after the property list on a save below
#: ``FIRST_MODERN_BODY``, and that nothing here decodes. Pre-1.0 there is no
#: ``FGConveyorChainActor``: **every belt and lift carries the items on it itself**.
#:
#: Being on this list means "known to write class-specific bytes, not decoded" --
#: ``actorSpecificInfo`` stays ``None``. The list exists so that ``_attach_trailer``'s check
#: still says something on an old save instead of warning about tens of thousands of belts.
#: ``ConveyorBeltMk4``/``Mk5`` and their lifts are absent because no old save on this disk has
#: one, so a save that does should warn rather than pass silently.
UNDECODED_TRAILER_CLASSES = frozenset(
    {
        "/Game/FactoryGame/Buildable/Factory/PowerLine/Build_PowerLine.Build_PowerLine_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk1/Build_ConveyorBeltMk1.Build_ConveyorBeltMk1_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk2/Build_ConveyorBeltMk2.Build_ConveyorBeltMk2_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk3/Build_ConveyorBeltMk3.Build_ConveyorBeltMk3_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorLiftMk1/Build_ConveyorLiftMk1.Build_ConveyorLiftMk1_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorLiftMk2/Build_ConveyorLiftMk2.Build_ConveyorLiftMk2_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorLiftMk3/Build_ConveyorLiftMk3.Build_ConveyorLiftMk3_C",
        "/Game/FactoryGame/Character/Player/BP_PlayerState.BP_PlayerState_C",
        "/Game/FactoryGame/-Shared/Blueprint/BP_CircuitSubsystem.BP_CircuitSubsystem_C",
        "/Game/FactoryGame/-Shared/Blueprint/BP_GameState.BP_GameState_C",
        "/Game/FactoryGame/-Shared/Blueprint/BP_GameMode.BP_GameMode_C",
    }
)


def _attach_trailer(
    body: bytes,
    header: ActorHeader,
    obj: ParsedObject,
    warnings: list[tuple[int, str]],
    save_version: int = FIRST_MODERN_BODY,
) -> None:
    """Attach a decoder for this actor's trailing class-specific bytes, which
    ``ParsedObject.actorSpecificInfo`` calls on first access, and warn about bytes nothing can
    account for.

    An actor with no reader and more than a plain trailer looks like a property list that
    stopped early. It warns rather than refuses, because a modded or future class carrying its
    own data looks the same.
    """
    class_path = getattr(header, "typePath", None)
    if save_version < FIRST_MODERN_BODY:
        # no reader is verified on pre-1.0 bytes, though Build_PowerLine_C kept its class path;
        # leaving decode_trailer unset keeps actorSpecificInfo None, "not decoded"
        unexplained = (
            class_path is not None
            and obj.extra_length not in PLAIN_TRAILER
            and class_path not in UNDECODED_TRAILER_CLASSES
        )
        if unexplained:
            what = (
                f"{class_path.rsplit('.', 1)[-1]} left {obj.extra_length} trailing bytes on a "
                f"saveVersion {save_version} save, and no class is known to"
            )
            warnings.append((obj.extra_offset, what))
        return
    if class_path == LIGHTWEIGHT_SUBSYSTEM:
        obj.decode_trailer = partial(read_lightweight, body, obj.extra_offset, obj.extra_length)
    elif class_path in TRAILER_READERS:
        obj.decode_trailer = partial(
            read_trailer, class_path, body, obj.extra_offset, obj.extra_length
        )
    elif class_path is not None and obj.extra_length not in PLAIN_TRAILER:
        plain = " or ".join(map(str, PLAIN_TRAILER))
        what = (
            f"{class_path.rsplit('.', 1)[-1]} left {obj.extra_length} trailing bytes; "
            f"no reader knows this class and a plain actor leaves {plain}"
        )
        warnings.append((obj.extra_offset, what))


def read_full_save_bytes(data: bytes) -> ParsedSave:
    """Parse a complete .sav already in memory.

    Split out from ``read_full_save`` so the tests can assemble a file from the committed
    fixtures and exercise the whole composition with no game install. Every raise below is a
    ``ParseError``; anything else escaping here is a bug in this parser rather than a problem
    with the file.
    """
    info = read_info_bytes(data)
    # every layer below is gated on this one header-read version, so no two can disagree
    old = info.save_version < FIRST_MODERN_BODY
    body = decompress_body(data, info.body_offset, old=old)
    parsed = read_body(body, info.save_version, info.build_version)
    warnings = list(parsed.warnings)
    levels = []
    for level in parsed.levels:
        objects = []
        for header, slot in zip(level.headers, level.objects, strict=True):
            actor = isinstance(header, ActorHeader)
            obj = read_object(body, slot, actor=actor, save_version=info.save_version)
            if obj.warnings:
                warnings.extend(obj.warnings)
            if actor:
                _attach_trailer(body, header, obj, warnings, info.save_version)
            objects.append(obj)
        levels.append(ParsedLevel(name=level.name, headers=level.headers, objects=objects))

    return ParsedSave(
        info=info,
        body=body,
        levels=levels,
        warnings=warnings,
        destroyed_actors=parsed.destroyed_actors,
    )


def read_full_save(path: str | os.PathLike[str]) -> ParsedSave:
    """Read and fully parse the save at ``path``.

    The whole file is read at once rather than streamed: it is the closest thing available to
    an atomic snapshot of a file the running game rewrites every few minutes.

    An unreadable *path* stays an ``OSError`` and is not turned into a ``ParseError``. A file
    that is missing or locked is not a save that cannot be parsed, and callers report the two
    differently.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    return read_full_save_bytes(data)
