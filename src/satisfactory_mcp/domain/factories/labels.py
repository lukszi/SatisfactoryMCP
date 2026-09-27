"""Persistent factory names, anchored to machines rather than to positions or ids.

A label stores the SET of machine instance names it was created from. Instance names
were verified stable across saves -- 365 of 365 machines kept the same id and position
between two different save files -- so a set of them is a durable handle.

Matching uses **recall**, not Jaccard::

    recall = |anchors ∩ candidate| / |anchors|

That is what survives the edits a player actually makes:

* moving a machine       -- no effect at all, the id does not change
* adding a wing to it    -- recall stays 1.0; new machines are not in the denominator
* removing a few         -- recall dips slightly, still far above threshold
* rebuilding half of it  -- recall ~0.5, flagged for confirmation rather than lost

Jaccard would *punish growth*, which is exactly backwards: extending a factory is the
most common thing that happens to one. On a confirmed match the label re-anchors to
the current membership, so gradual rebuilding never accumulates drift.

Labels deliberately do not attach to a base or a line. Measured against a player's own
list, a real factory can be several material components (one Christmas factory is a
Tree Branch line plus a Candy Cane line) or part of one (a steel site and a tier 1&2
site inside a single belt-connected mass). Only an arbitrary machine set covers both.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ... import config
from ...core import atomic, filelock, schema

__all__ = [
    "MATCH_THRESHOLD",
    "NAMED_SHARE",
    "NAME_MAX",
    "REANCHOR_THRESHOLD",
    "BadName",
    "Label",
    "LabelError",
    "LabelStore",
    "NameClash",
    "StaleStore",
    "UnknownLabel",
    "stamp",
]

#: Above this share of a machine set covered by labels, the set is something the player has
#: already named rather than something to offer them. The clusterer runs over the whole
#: world and so rediscovers every named factory; the two obvious alternatives both misfire
#: on that. "Any anchor" hides a genuinely new cluster that happens to have swallowed one
#: neighbouring machine, and "every anchor" re-offers a factory the player named all but one
#: machine of. A majority is the only rule that survives both edits.
NAMED_SHARE = 0.5

#: Below this a label is not considered present in a candidate at all.
MATCH_THRESHOLD = 0.5

#: At or above this, and with no competing label, anchors refresh automatically.
#: Lower would risk a label silently swallowing a factory merged into it.
REANCHOR_THRESHOLD = 0.8

SCHEMA = 1

#: The longest factory name a write accepts, in characters.
NAME_MAX = 60


class LabelError(ValueError):
    """An edit a label cannot take, with the thing that refused it named."""


class BadName(LabelError):
    """A name no label may carry: blank, too long, or with a ``/`` in it."""


class NameClash(LabelError):
    """Another label in this world already holds the name, or its slug."""


class UnknownLabel(LabelError):
    """No label in this world has the name asked for."""


class StaleStore(RuntimeError):
    """The file was written by someone else after the caller read the version it expects."""

    def __init__(self, expected: int, found: int) -> None:
        super().__init__(
            "the factory names changed elsewhere (chat, or another tab); reload and try again"
        )
        self.expected, self.found = expected, found


@dataclass
class Label:
    id: str
    name: str
    anchors: list[str] = field(default_factory=list)
    notes: str = ""
    centroid: tuple[float, float] = (0.0, 0.0)
    #: Building-class counts when last anchored. A fallback hint only, used to
    #: SUGGEST a re-match after a full rebuild, never to match automatically.
    signature: dict[str, int] = field(default_factory=dict)
    created: str = ""
    last_matched: str = ""

    def recall(self, machines: set[str]) -> float:
        if not self.anchors:
            return 0.0
        return len(set(self.anchors) & machines) / len(self.anchors)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "anchors": self.anchors,
            "notes": self.notes,
            "centroid": list(self.centroid),
            "signature": self.signature,
            "created": self.created,
            "last_matched": self.last_matched,
        }

    @staticmethod
    def from_json(raw: dict) -> Label:
        centroid = raw.get("centroid") or [0.0, 0.0]
        return Label(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            anchors=list(raw.get("anchors", ())),
            notes=raw.get("notes", ""),
            centroid=(float(centroid[0]), float(centroid[1])),
            signature=dict(raw.get("signature", {})),
            created=raw.get("created", ""),
            last_matched=raw.get("last_matched", ""),
        )


def stamp(header: dict) -> str:
    """The save a label edit is dated by."""
    return str(header.get("save_datetime") or header.get("filename") or "")


def slugify(name: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "factory"


@dataclass
class LabelStore:
    """Labels for one world, keyed by the save header's ``save_identifier``.

    Keyed by world rather than by file so labels follow the world across autosave
    rotation, manual saves and renames.
    """

    world_id: str
    session_name: str = ""
    labels: list[Label] = field(default_factory=list)
    #: Bumped by every write, so a writer can tell the file moved since it read it.
    version: int = 0

    @staticmethod
    def path_for(world_id: str) -> Path:
        safe = "".join(c for c in world_id if c.isalnum() or c in "-_") or "world"
        return config.labels_dir() / f"{safe}.json"

    @classmethod
    def load(cls, world_id: str, session_name: str = "") -> LabelStore:
        path = cls.path_for(world_id)
        if not path.is_file():
            return cls(world_id=world_id, session_name=session_name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        schema.check(raw, SCHEMA, path)
        return cls(
            world_id=raw.get("world_id", world_id),
            session_name=raw.get("session_name", session_name),
            labels=[Label.from_json(x) for x in raw.get("labels", ())],
            version=int(raw.get("version", 0)),
        )

    @classmethod
    @contextmanager
    def editing(
        cls, world_id: str, session_name: str = "", expect: int | None = None
    ) -> Iterator[LabelStore]:
        """The one safe read-modify-write: locked, re-read inside the lock, written on exit.

        ``expect`` refuses with ``StaleStore`` when the file is no longer that version.
        Nothing is written when the block raises.
        """
        with filelock.held(cls.path_for(world_id)):
            store = cls.load(world_id, session_name)
            if expect is not None and store.version != expect:
                raise StaleStore(expect, store.version)
            yield store
            store._write()

    def _write(self) -> Path:
        path = self.path_for(self.world_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.version += 1
        return atomic.write_text(
            path,
            json.dumps(
                {
                    "schema": SCHEMA,
                    "version": self.version,
                    "world_id": self.world_id,
                    "session_name": self.session_name,
                    "labels": [x.to_json() for x in self.labels],
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    # ---- mutation ------------------------------------------------------

    def find(self, name: str) -> Label | None:
        needle = name.strip().casefold()
        for label in self.labels:
            if label.name.casefold() == needle or label.id == slugify(name):
                return label
        hits = [x for x in self.labels if needle in x.name.casefold()]
        return hits[0] if len(hits) == 1 else None

    def put(
        self, name: str, machines: list[str], notes: str = "", when: str = "", create: bool = False
    ) -> Label:
        existing = None if create else self.find(name)
        if existing is None:
            existing = Label(id=slugify(name), name=name.strip(), created=when)
            self.labels.append(existing)
        existing.anchors = sorted(set(machines))
        existing.last_matched = when
        if notes:
            existing.notes = notes
        return existing

    def remove(self, name: str) -> bool:
        label = self.find(name)
        if label is None:
            return False
        self.labels.remove(label)
        return True

    def rename(self, label: Label, to: str) -> str:
        """Give a label a new name and change nothing else about it. Returns the old name.

        The id moves with the name because ``find`` matches a slug as well as a name: a
        label left holding its old slug would go on answering to the name it was renamed
        away from, and the rename would be a rename in the listing only.
        """
        wanted = self._free(to, besides=label)
        was = label.name
        label.name, label.id = wanted, slugify(wanted)
        return was

    def _free(self, name: str, besides: Label | None = None) -> str:
        """``name`` stripped, or ``BadName`` / ``NameClash`` saying why it cannot be used."""
        wanted = name.strip()
        if not wanted:
            raise BadName("a factory name cannot be blank")
        if "/" in wanted:
            raise BadName("a factory name cannot contain '/'")
        if len(wanted) > NAME_MAX:
            raise BadName(f"a factory name is at most {NAME_MAX} characters, not {len(wanted)}")
        slug = slugify(wanted)
        for other in self.labels:
            if other is besides:
                continue
            if other.name.casefold() == wanted.casefold():
                raise NameClash(f"this world already has a factory named {other.name!r}")
            if other.id == slug:
                raise NameClash(
                    f"{wanted!r} and the existing {other.name!r} both slug to {slug!r}, "
                    "which find() cannot tell apart"
                )
        return wanted

    def name(self, name: str, cand, notes: str = "", when: str = "", create: bool = False) -> Label:
        """What naming a factory writes: ``put`` plus the candidate's centroid and signature.

        ``create`` refuses a name this world already holds instead of re-anchoring it. A name
        no label answers to is checked as a new one either way.
        """
        if create or self.find(name) is None:
            name = self._free(name)
        label = self.put(name, cand.machines, notes=notes, when=when, create=create)
        label.centroid = cand.centroid
        label.signature = dict(cand.buildings)
        return label

    def overlaps(self, machines: Iterable[str], name: str) -> dict[str, int]:
        """Machines per OTHER label among ``machines``."""
        stolen: dict[str, int] = {}
        for machine in machines:
            other = self.label_for(machine)
            if other and other.name.casefold() != name.strip().casefold():
                stolen[other.name] = stolen.get(other.name, 0) + 1
        return dict(sorted(stolen.items()))

    def attach(self, label: Label, machines: Iterable[str]) -> list[str]:
        """Add machines to one label, leaving every other anchor of it alone.

        Returns the ones that were not already on it. Nothing else re-anchors, which is
        the whole difference from ``put``.
        """
        held = set(label.anchors)
        added = sorted(set(machines) - held)
        label.anchors = sorted(held | set(added))
        return added

    def detach(self, label: Label, machines: Iterable[str]) -> list[str]:
        """Drop machines from one label. Returns the ones it actually held.

        Emptying a label is refused rather than done: the name and its notes are the only
        things in this file the player typed, and ``remove`` is how they say those go.
        """
        held = set(label.anchors)
        dropped = sorted(held & set(machines))
        if dropped and len(dropped) == len(held):
            raise LabelError(
                f"that would leave {label.name!r} with no machines at all; "
                "forget_factory deletes a label, and dropping the last one will not"
            )
        label.anchors = sorted(held - set(dropped))
        return dropped

    # ---- matching ------------------------------------------------------

    def assigned(self) -> set[str]:
        return {m for label in self.labels for m in label.anchors}

    def covers(self, machines, share: float = NAMED_SHARE) -> bool:
        """Whether the player has already named this machine set.

        The one home for that question. The map, ``propose_factories`` and ``factory_map``
        each grew their own version -- majority, any, all -- so the same cluster was a
        proposal on one surface and not on the other, and neither said which it was.
        """
        held = list(machines)
        if not held:
            return False
        assigned = self.assigned()
        return sum(1 for m in held if m in assigned) > share * len(held)

    def match(self, machines: set[str]) -> list[tuple[Label, float]]:
        """Labels present in a machine set, best recall first."""
        scored = [(x, x.recall(machines)) for x in self.labels]
        return sorted([(x, r) for x, r in scored if r >= MATCH_THRESHOLD], key=lambda p: -p[1])

    def label_for(self, machine: str) -> Label | None:
        for label in self.labels:
            if machine in label.anchors:
                return label
        return None

    def review(self, present: set[str]) -> list[dict]:
        """What changed since each label was anchored.

        Reports rather than acts: a label whose machines are half gone might be a
        rebuild in progress or a dismantled factory, and only the player knows which.
        """
        out = []
        for label in self.labels:
            anchors = set(label.anchors)
            alive = anchors & present
            recall = len(alive) / len(anchors) if anchors else 0.0
            if recall >= 0.999:
                continue
            if not alive:
                status = "gone"
            elif recall < MATCH_THRESHOLD:
                status = "needs confirmation"
            else:
                status = "shrunk"
            out.append(
                {
                    "name": label.name,
                    "recall": round(recall, 3),
                    "missing": len(anchors - present),
                    "status": status,
                }
            )
        return sorted(out, key=lambda d: d["recall"])
