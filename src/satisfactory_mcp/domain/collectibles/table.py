"""The map's own collectible placements, and the names a save can offer instead."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import cast

from ... import config
from ...core.jsontypes import JsonObject, JsonValue
from ...core.saveio.records import instance_leaf
from .views import MapPlacement

__all__ = [
    "COLLECTIBLES_FILE",
    "CollectibleTable",
    "CollectiblesUnreadable",
    "load_collectibles",
    "name_stem",
    "removed_actor_class",
]


def removed_actor_class(leaf: str) -> str:
    """Class of a removed actor from its instance name, for the `other` bucket only.

    Mirrors the sidecar's `_removed_class`, duplicated because the sidecar runs as a separate
    process. Only ever used to LABEL an unmatched class, never to decide a group.
    """
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    if len(parts) >= 2 and parts[-2] == "UAID":
        parts = parts[:-2]
    if parts and parts[-1] == "C":
        parts.pop()
    return "_".join(parts) or leaf


#: A placement counter glued onto a name (``BP_SporeFlower369``), stripped only after a
#: LETTER so the real digits of ``BP_DebrisActor_02`` survive.
_GLUED_INDEX = re.compile(r"(?<=[A-Za-z])\d+$")


def name_stem(leaf: str) -> str:
    """A label for a removed actor the map table has no row for. NOT a class.

    Only the map can name a class -- ``BP_WAT133`` is a somersloop -- so this is a display
    string and never a decision; without it 89 spore flowers read as 40 one-row entries.
    """
    return _GLUED_INDEX.sub("", removed_actor_class(leaf))


def as_object(value: JsonValue) -> JsonObject:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


@dataclass
class CollectibleTable:
    """The map's own collectible placements: ``data/world_collectibles.json``.

    Read from the installed game's cooked packages, so ``placed`` is the map's own count
    rather than a count of sightings. The save is never a source of position here and this
    table is never a source of state -- that split is what makes both halves honest.
    """

    rows: list[MapPlacement]
    meta: JsonObject
    by_key: dict[tuple[str, str], MapPlacement] = field(
        default_factory=dict[tuple[str, str], MapPlacement], repr=False
    )
    by_category: dict[str, list[MapPlacement]] = field(
        default_factory=dict[str, list[MapPlacement]], repr=False
    )

    def __post_init__(self) -> None:
        for row in self.rows:
            # ``(cell, name)``, never the bare name: auto-numbered placements reuse names
            # across cells, while the pair is unique over all 69,364 map actors.
            self.by_key[(row["cell"], instance_leaf(row["instance"]))] = row
            self.by_category.setdefault(row["category"], []).append(row)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def categories(self) -> list[str]:
        """Category names, most-placed first."""
        return sorted(self.by_category, key=lambda c: (-len(self.by_category[c]), c))

    def info(self, category: str) -> JsonObject:
        totals = as_object(self.meta.get("totals"))
        return as_object(as_object(totals.get("by_category")).get(category))

    def cls_of(self, category: str) -> str:
        rows = self.by_category.get(category) or []
        return rows[0]["class"] if rows else str(self.info(category).get("class") or "?")

    def note_for(self, category: str) -> str:
        return str(self.info(category).get("note") or "")

    def state_tracked(self, category: str) -> bool:
        """Whether a save records anything at all about this class.

        ``rows_any_save_mentions`` counts the placements some save on disk names, live or
        gone; where it is 0 the class is not save-serialised. That is the difference between
        a ``remaining`` figure and a fabricated one: with no record of a collection,
        ``placed - collected`` equals ``placed`` whether or not the player took every one.
        """
        return bool(self.info(category).get("rows_any_save_mentions"))

    def pedestal_of(self, category: str) -> str | None:
        """The category this one is the base of, where it is one.

        A shrine is a second row about one find, not a second find: the map's own
        AttachParent pairs all 298 Mercer shrines 1:1 with a sphere. Summing categories
        therefore over-counts artifacts by the number of shrines.
        """
        pedestals = as_object(as_object(self.meta.get("totals")).get("pedestals"))
        parents = as_object(as_object(pedestals.get(category)).get("parent_category"))
        return next(iter(parents), None)

    def excluded_reason(self, stem: str) -> str | None:
        """Why the map table has no row for a class, in the table's own words.

        Falls back to naming the excluded classes a stem could belong to, without picking
        one: ``BP_DebrisActor`` is the stem of three, and the counter glued onto a name is
        not evidence about which. Naming all three still answers "is this a collectible".
        """
        excluded = as_object(self.meta.get("excluded"))
        entry = excluded.get(f"{stem}_C")
        if isinstance(entry, dict):
            return str(entry.get("why"))
        siblings = sorted(k for k in excluded if k.startswith(stem))
        if siblings:
            return "the map excludes " + ", ".join(siblings) + " -- a name does not say which"
        return None

    @property
    def build(self) -> str:
        source = as_object(self.meta.get("source"))
        return str(as_object(source.get("placements")).get("game_build") or "?")


COLLECTIBLES_FILE = "world_collectibles.json"


#: Keyed by the file and its mtime, so a table regenerated against a newer game build is
#: picked up without a restart. A miss is never cached: an absent or unreadable file is a
#: state the reader fixes by running the generator, so the next call looks again.
_TABLE: dict[tuple[str, int], CollectibleTable] = {}


class CollectiblesUnreadable(Exception):
    """The table is THERE and will not parse -- a different fact from "not generated".

    Absent is a checkout that lost the committed file, and the answer is "run the generator".
    Corrupt is a half-written file or an interrupted run, and the answer is "delete it and
    run the generator", which nobody can act on if the two arrive as one.
    """


def load_collectibles(*, strict: bool = False) -> CollectibleTable | None:
    """The map's placement table, or ``None`` when it has not been generated.

    ``None`` rather than an exception: the file is committed, but a checkout without it
    still works, since every caller degrades to the save-only census instead of failing. What
    is lost without it is everything the save cannot know by itself -- how many of each
    kind exist, where they are, and therefore what remains.

    ``strict=True`` raises :class:`CollectiblesUnreadable` for a file that exists and cannot
    be read, and still returns ``None`` for one that is not there. Off by default, since the
    degrading callers are right to degrade; on for a caller that wants to tell the reader
    "you never ran it" apart from "what it wrote is broken".
    """
    path = config.data_dir() / COLLECTIBLES_FILE
    if not path.is_file():
        return None
    try:
        key = (str(path), path.stat().st_mtime_ns)
        hit = _TABLE.get(key)
        if hit is not None:
            return hit
        payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ``ValueError`` covers ``JSONDecodeError`` and the numeric parse errors a truncated
        # file produces. Named rather than caught broadly, so a bug here still raises.
        if strict:
            raise CollectiblesUnreadable(f"{path} exists but will not read: {exc}") from exc
        return None
    # A JSON file that is not an object at all is corrupt, not empty, and this function is
    # only allowed to answer ``None`` or raise ``CollectiblesUnreadable``.
    document = as_object(payload)
    rows = document.get("collectibles") or []
    if not rows:
        if strict:
            raise CollectiblesUnreadable(f"{path} exists but lists no collectibles")
        return None
    table = CollectibleTable(
        rows=cast(list[MapPlacement], rows), meta=as_object(document.get("_meta"))
    )
    _TABLE.clear()
    _TABLE[key] = table
    return table
