"""Named, persisted plans.

**The request is stored, never the solution.** A solve depends on unlocked recipes, free
nodes and built buildings, so a stored solution would keep answering about a world that no
longer exists; re-solving on recall answers about the world as it is now, and a saved
``plan_id`` that no longer matches says the world moved rather than the plan. Stored per
world under ``saveIdentifier``: a plan for one world is meaningless in another.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ... import config
from ...core import atomic, filelock

__all__ = ["SCHEMA", "Plan", "PlanStore"]

SCHEMA = 1

#: Argument names a plan captures: everything build_scenario takes that changes the
#: answer, and so not `limit` (presentation) or `save`/`world` (which save was read).
PLAN_ARGS = (
    "objective",
    "target_item",
    "sources",
    "exports",
    "export_minimums",
    "only_free_nodes",
    "allow_sinks",
    "clocks",
    "extractor_clocks",
    "machine_cost_mw",
    "exclude_recipes",
    "only_recipes",
    "water_extractors",
    "sloops",
    "belt_ipm",
    "pipe_m3min",
    "recycle_once",
    "supplied",
)


@dataclass
class Plan:
    name: str
    args: dict = field(default_factory=dict)
    notes: str = ""
    #: plan_id at the moment it was saved. A different id on recall means the WORLD moved.
    plan_id: str = ""
    #: Optional factory label this plan is for, so a diff can be scoped to it.
    factory: str = ""
    created: str = ""
    #: What each source selector RESOLVED to when saved; ``provenance`` owns the shape.
    #: Empty means "not recorded", which a recall reports as such and not as "unchanged".
    provenance: dict = field(default_factory=dict)
    #: Where this plan is to STAND; ``planning.siting`` owns the shape and empty means "not
    #: sited". Untouched by ``put``: where a plan goes has its own verb.
    siting: dict = field(default_factory=dict)

    def kwargs(self) -> dict:
        """Stored arguments, filtered to those a planning call still accepts."""
        return {k: v for k, v in self.args.items() if k in PLAN_ARGS}


@dataclass
class PlanStore:
    world_id: str
    session_name: str = ""
    plans: list[Plan] = field(default_factory=list)
    #: Bumped by every write, as ``LabelStore.version`` is.
    version: int = 0

    @staticmethod
    def path_for(world_id: str) -> Path:
        safe = "".join(c for c in world_id if c.isalnum() or c in "-_") or "world"
        return config.plans_dir() / f"{safe}.json"

    @classmethod
    def load(cls, world_id: str, session_name: str = "") -> PlanStore:
        path = cls.path_for(world_id)
        if not path.is_file():
            return cls(world_id=world_id, session_name=session_name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            world_id=raw.get("world_id", world_id),
            session_name=raw.get("session_name", session_name),
            plans=[Plan(**p) for p in raw.get("plans", ())],
            version=int(raw.get("version", 0)),
        )

    @classmethod
    @contextmanager
    def editing(cls, world_id: str, session_name: str = "") -> Iterator[PlanStore]:
        """Locked read-modify-write, as ``LabelStore.editing``; written on a clean exit."""
        with filelock.held(cls.path_for(world_id)):
            store = cls.load(world_id, session_name)
            yield store
            store._write()

    def save(self) -> Path:
        """Overwrite the file with this store, whole, under the lock; ``editing`` merges."""
        with filelock.held(self.path_for(self.world_id)):
            return self._write()

    def _write(self) -> Path:
        """Atomically: a plan is a request the reader typed and nothing can reconstruct it."""
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
                    "plans": [asdict(p) for p in self.plans],
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    def find(self, name: str) -> Plan | None:
        needle = name.strip().casefold()
        for plan in self.plans:
            if plan.name.casefold() == needle:
                return plan
        hits = [p for p in self.plans if needle in p.name.casefold()]
        return hits[0] if len(hits) == 1 else None

    def put(
        self,
        name: str,
        args: dict,
        plan_id: str,
        notes: str = "",
        factory: str = "",
        when: str = "",
        provenance: dict | None = None,
    ) -> Plan:
        existing = self.find(name)
        if existing is None:
            existing = Plan(name=name.strip(), created=when)
            self.plans.append(existing)
        # Only non-defaults, so a stored plan reads as the request that was made.
        existing.args = {
            k: v for k, v in args.items() if k in PLAN_ARGS and v not in (None, [], {})
        }
        existing.plan_id = plan_id
        # Rewritten WITH the arguments: a record describing the previous `sources` would
        # report drift that is really an edit. None means the caller has none to offer.
        if provenance is not None:
            existing.provenance = provenance
        if notes:
            existing.notes = notes
        if factory:
            existing.factory = factory
        return existing

    def remove(self, name: str) -> bool:
        plan = self.find(name)
        if plan is None:
            return False
        self.plans.remove(plan)
        return True
