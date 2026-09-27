"""The legacy plan file and the ``Plan`` shape the rest of the code reads plans through.

**The request is stored, never the solution.** A solve depends on unlocked recipes, free
nodes and built buildings, so re-solving on recall answers about the world as it is now,
and a saved ``plan_id`` that no longer matches says the world moved rather than the plan.

Plans are written through ``planlog`` now. ``PlanStore.load`` survives only so the
migration can read ``plans/<world>.json``; docs/plan_log.md has the layout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

from ... import config

__all__ = ["PLAN_ARGS", "SCHEMA", "Plan", "PlanStore"]

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
    "required",
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
    #: sited". Untouched by a re-save: where a plan goes has its own verb.
    siting: dict = field(default_factory=dict)
    key: str = ""
    rev: int = 0

    def kwargs(self) -> dict:
        """Stored arguments, filtered to those a planning call still accepts."""
        return {k: v for k, v in self.args.items() if k in PLAN_ARGS}


_FIELDS = frozenset(f.name for f in fields(Plan))


@dataclass
class PlanStore:
    world_id: str
    session_name: str = ""
    plans: list[Plan] = field(default_factory=list)
    version: int = 0

    @staticmethod
    def path_for(world_id: str) -> Path:
        """The legacy file; ``planlog.PlanLog.dir_for`` is the same path without ``.json``."""
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
            plans=[
                Plan(**{k: v for k, v in p.items() if k in _FIELDS}) for p in raw.get("plans", ())
            ],
            version=int(raw.get("version", 0)),
        )

    def find(self, name: str) -> Plan | None:
        needle = name.strip().casefold()
        for plan in self.plans:
            if plan.name.casefold() == needle:
                return plan
        hits = [p for p in self.plans if needle in p.name.casefold()]
        return hits[0] if len(hits) == 1 else None
