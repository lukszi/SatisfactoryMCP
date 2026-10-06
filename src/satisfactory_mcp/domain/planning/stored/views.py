"""The wire shapes of a stored plan: its arguments, one op, and two solves compared.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from ....core.jsontypes import JsonValue

__all__ = ["DeltaRow", "PlanArgsBody", "PlanOpBody", "ResultDelta", "RowChange"]


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
