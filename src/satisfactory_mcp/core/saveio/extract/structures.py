"""The lightweight buildables, and the map-placed actors the save records as gone.

Row layouts are in ``docs/save-projection.md`` §6.16.
"""

from __future__ import annotations

from .census import Drops
from .interning import Interner
from .readers import ref_class, ref_path, yaw_of

__all__ = ["lightweight", "placed", "removed", "removed_class", "structures"]


def placed(record) -> bool:
    """Is this lightweight record a piece that exists, or a stale slot?

    A real piece names a swatch and a recipe and a stale slot names neither; emitting a stale
    slot invents floor that ``domain/factories/structure.py`` and
    ``domain/spatial/elevation.py`` would build on. Not positional: the field indices differ
    between parsers, and "does any field name an asset" is true of a piece under either.
    """
    for field in record if isinstance(record, list) else ():
        if getattr(field, "pathName", None):
            return True
        if isinstance(field, str) and field:
            return True
    return False


def lightweight(obj) -> dict:
    """Build_* class -> count, from FGLightweightBuildableSubsystem's ``actorSpecificInfo``.

    These appear in NO actor header, so a header-only census undercounts what is built.
    """
    out: dict[str, int] = {}

    def walk(node) -> None:
        if not isinstance(node, list):
            return
        for position, child in enumerate(node):
            if isinstance(child, str) and "Build_" in child and child.endswith("_C"):
                cls = ref_class(child)
                following = node[position + 1] if position + 1 < len(node) else None
                # Stale slots are skipped, not counted -- see `placed`.
                count = (
                    sum(1 for record in following if placed(record))
                    if isinstance(following, list)
                    else 1
                )
                if cls:
                    out[cls] = out.get(cls, 0) + count
            else:
                walk(child)

    walk(getattr(obj, "actorSpecificInfo", None))
    return out


def structures(obj, drops: Drops) -> dict:
    """Transforms of every lightweight buildable -- foundations, ramps, walls, catwalks."""
    classes = Interner()
    instances: list[list] = []

    for entry in getattr(obj, "actorSpecificInfo", None) or []:
        # NOT a drop: the blob leads with the record format's version word on every save.
        if not isinstance(entry, list):
            continue
        if len(entry) != 2:
            drops["lightweight class block(s) skipped: not a [class, instances] pair"] += 1
            continue
        class_path, records = entry
        cls = ref_class(class_path) or str(class_path).rsplit(".", 1)[-1]
        if not isinstance(records, list):
            drops[f"lightweight class block(s) skipped: {cls} lists no instances"] += 1
            continue
        class_index = classes.intern(cls)
        for record in records:
            if not (isinstance(record, list) and len(record) >= 2):
                drops["lightweight piece(s) dropped: no [rotation, position] to read"] += 1
                continue
            # NOT a drop: a stale slot is a record of nothing. See `placed`.
            if not placed(record):
                continue
            position = record[1]
            try:
                instances.append(
                    [
                        class_index,
                        int(position[0]),
                        int(position[1]),
                        int(position[2]),
                        yaw_of(record[0]),
                    ]
                )
            except (TypeError, ValueError, IndexError):
                drops["lightweight piece(s) dropped: position would not read as numbers"] += 1
                continue

    return {"classes": classes.names(), "instances": instances}


def removed(save) -> dict:
    """Map-placed actors the save records as GONE -- the only record of what was collected.

    ``pioneersav`` merges the format's three lists into ``destroyed_actors``; the fallback
    reads them separately. See ``docs/save-projection.md`` §6.11 and §6.16.
    """
    refs = getattr(save, "destroyed_actors", None)
    if refs is None:
        # ref_path, not str(): an ObjectReference's __str__ renders the whole object.
        pairs: list[tuple[str, str]] = []
        for level in getattr(save, "levels", None) or []:
            for which in ("collectables1", "collectables2"):
                for ref in getattr(level, which, None) or []:
                    pairs.append((str(getattr(ref, "levelName", "")), ref_path(ref) or ""))
        for which in ("dropPodObjectReferenceList", "extraObjectReferenceList"):
            for ref in getattr(save, which, None) or []:
                pairs.append((str(getattr(ref, "levelName", "")), ref_path(ref) or ""))
        refs = list(dict.fromkeys(pairs))

    cells = Interner()
    instances: list[list] = []
    counts: dict[str, int] = {}
    # Sorted: the order is an artefact of which list was walked first, and this is a cache key.
    for cell, path in sorted(refs, key=lambda pair: (pair[0], pair[1])):
        leaf = path.rsplit(".", 1)[-1]
        if not leaf:
            continue
        instances.append([cells.intern(cell), leaf])
        counts[removed_class(leaf)] = counts.get(removed_class(leaf), 0) + 1
    return {
        "cells": cells.names(),
        "instances": instances,
        "counts": dict(sorted(counts.items())),
    }


def removed_class(leaf: str) -> str:
    """Class of a destroyed actor, from its instance name alone, stripped from the right.

    Approximate: ``BP_Crystal2_228`` cannot be told from a class named ``BP_Crystal2``, so
    callers wanting slugs match a prefix, as ``domain/collectibles/removed.py`` does.
    """
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    if len(parts) >= 2 and parts[-2] == "UAID":
        parts = parts[:-2]
    if parts and parts[-1] == "C":
        parts.pop()
    return "_".join(parts) or leaf
