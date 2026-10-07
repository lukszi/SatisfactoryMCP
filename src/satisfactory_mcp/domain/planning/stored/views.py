"""The shapes of a stored plan: its arguments, one op, its log records, two solves compared.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import TypeAlias

from typing_extensions import TypedDict

from ....core.jsontypes import JsonObject, JsonValue

__all__ = [
    "ActorRecord",
    "CommitRecord",
    "ConflictRecord",
    "DeltaRow",
    "PlanArgsBody",
    "PlanOp",
    "PlanOpBody",
    "PlanStamp",
    "PlanStateRecord",
    "ProvenanceRecord",
    "ResultDelta",
    "RowChange",
    "SelectorRecord",
]


class PlanArgsBody(TypedDict):
    """The whole solve request, every field present at its default when unset (contract §2)."""

    objective: str
    target_item: str | None
    sources: list[str]
    exports: list[str]
    export_minimums: dict[str, float]
    only_free_nodes: bool
    allow_sinks: bool
    clocks: list[float]
    extractor_clocks: list[float]
    machine_cost_mw: float
    banned: list[str]
    required: list[str]
    only_recipes: list[str]
    water_extractors: int | None
    sloops: int
    belt_ipm: float | None
    pipe_m3min: float | None
    recycle_once: list[str]
    supplied: dict[str, float]
    logistics_items: list[str]
    payback_hours: float | None
    overclock_last: bool | None
    power_price: float | None
    row_overclock: dict[str, str]


class PlanOpBody(TypedDict, total=False):
    """One op as the log holds it; which keys are present depends on ``op`` (contract §3)."""

    op: str
    field: str
    value: JsonValue
    item: str
    member: JsonValue
    name: str
    was: JsonValue


#: One op as the log reads and writes it: JSON, of the shape ``PlanOpBody`` declares. A
#: ``create`` op also carries the plan's whole ``state``, a ``PlanStateRecord``.
PlanOp: TypeAlias = JsonObject


class SelectorRecord(TypedDict):
    """What one source selector resolved to; ``nodes`` is empty past ``LEAF_CAP``."""

    selector: str
    count: int
    hash: str
    nodes: list[str]
    bbox: list[float] | None


class ProvenanceRecord(TypedDict):
    schema: int
    selectors: list[SelectorRecord]


class PlanStamp(TypedDict, total=False):
    """What a ``planlog.Stamp`` works out for a new head; a key it leaves out is unchanged."""

    plan_id: str
    provenance: ProvenanceRecord | JsonObject


class PlanStateRecord(TypedDict, total=False):
    """A plan's state as the log stores it. The ``create`` op's ``state`` has no key or rev;
    every other key is always written, but an older writer may have left one out."""

    key: str
    rev: int
    name: str
    forgotten: bool
    notes: str
    factory: str
    created: str
    plan_id: str
    provenance: JsonObject
    siting: JsonObject
    args: PlanArgsBody
    headroom_mw: float | None


#: Who wrote a commit: ``kind``, ``client`` and ``pid``, as ``planlog.Actor.to_dict`` gives it.
ActorRecord: TypeAlias = dict[str, str | int]


class CommitRecord(TypedDict):
    """One line of a plan's ``ops.jsonl``."""

    rev: int
    base_rev: int
    ts: float
    actor: ActorRecord
    sav: str
    ops: list[PlanOp]
    merged_over: list[int]
    undoes: int | None
    note: str


class ConflictRecord(TypedDict):
    key: str
    mine: PlanOp
    theirs: PlanOp
    theirs_rev: int
    theirs_actor: ActorRecord
    text: str


class DeltaRow(TypedDict):
    """One building's machine count or one raw input's rate, before and after."""

    name: str
    before: float
    after: float
    delta: float


class RowChange(TypedDict):
    """A process row joined on ``SolveRow.id``; ``change`` is added, removed or changed."""

    id: str
    label: str
    change: str
    machines_before: int
    machines_after: int
    clock_before: float
    clock_after: float


class ResultDelta(TypedDict):
    """Two solves compared. ``comparable`` is false when either side is not solvable."""

    comparable: bool
    machines: int
    mw_draw: float
    mw_net: float
    buildings: list[DeltaRow]
    inputs: list[DeltaRow]
    rows: list[RowChange]
    text: str
