"""The legacy plan file and the ``Plan`` shape the rest of the code reads plans through.

**The request is stored, never the solution.** A solve depends on unlocked recipes, free
nodes and built buildings, so re-solving on recall answers about the world as it is now,
and a saved ``plan_id`` that no longer matches says the world moved rather than the plan.

Plans are written through ``planlog`` now. ``PlanStore.load`` survives only so the
migration can read ``plans/<world>.json``; docs/plan_log.md has the layout.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Protocol, TypeVar

from .... import config
from ....core import schema
from ....core.jsontypes import JsonObject, JsonValue
from .plan_args import PLAN_ARGS

__all__ = ["PLAN_ARGS", "SCHEMA", "Plan", "PlanStore", "StoredPlan", "find_by_name"]

SCHEMA = 1

_Item = TypeVar("_Item")


class StoredPlan(Protocol):
    """What a recall reads off a stored plan: a ``Plan``, or a ``planlog.PlanState``."""

    @property
    def name(self) -> str: ...

    @property
    def plan_id(self) -> str: ...

    @property
    def provenance(self) -> JsonObject: ...

    @property
    def siting(self) -> JsonObject: ...

    def kwargs(self) -> dict[str, object]: ...


def find_by_name(items: list[_Item], name_of: Callable[[_Item], str], needle: str) -> _Item | None:
    """The first item named ``needle`` (any case), else the one whose name contains it."""
    wanted = needle.strip().casefold()
    exact = [x for x in items if name_of(x).casefold() == wanted]
    if exact:
        return exact[0]
    hits = [x for x in items if wanted in name_of(x).casefold()]
    return hits[0] if len(hits) == 1 else None


@dataclass
class Plan:
    name: str
    args: dict[str, object] = field(default_factory=dict[str, object])
    notes: str = ""
    #: plan_id at the moment it was saved. A different id on recall means the WORLD moved.
    plan_id: str = ""
    #: Optional factory label this plan is for, so a diff can be scoped to it.
    factory: str = ""
    created: str = ""
    #: What each source selector RESOLVED to when saved; ``provenance`` owns the shape.
    #: Empty means "not recorded", which a recall reports as such and not as "unchanged".
    provenance: JsonObject = field(default_factory=dict[str, JsonValue])
    #: Where this plan is to STAND; ``planning.siting`` owns the shape and empty means "not
    #: sited". Untouched by a re-save: where a plan goes has its own verb.
    siting: JsonObject = field(default_factory=dict[str, JsonValue])
    key: str = ""
    rev: int = 0

    def kwargs(self) -> dict[str, object]:
        """Stored arguments, filtered to those a planning call still accepts."""
        return {k: v for k, v in self.args.items() if k in PLAN_ARGS}


_FIELDS = frozenset(f.name for f in fields(Plan))


@dataclass
class PlanStore:
    world_id: str
    session_name: str = ""
    plans: list[Plan] = field(default_factory=list[Plan])
    version: int = 0

    @staticmethod
    def path_for(world_id: str) -> Path:
        """The legacy file; ``planlog.PlanLog.dir_for`` is the same path without ``.json``."""
        return config.plans_dir() / f"{config.world_file_stem(world_id)}.json"

    @classmethod
    def load(cls, world_id: str, session_name: str = "") -> PlanStore:
        path = cls.path_for(world_id)
        if not path.is_file():
            return cls(world_id=world_id, session_name=session_name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        schema.check(raw, SCHEMA, path)
        return cls(
            world_id=raw.get("world_id", world_id),
            session_name=raw.get("session_name", session_name),
            plans=[
                Plan(**{k: v for k, v in p.items() if k in _FIELDS}) for p in raw.get("plans", ())
            ],
            version=int(raw.get("version", 0)),
        )

    def find(self, name: str) -> Plan | None:
        return find_by_name(self.plans, lambda p: p.name, name)
