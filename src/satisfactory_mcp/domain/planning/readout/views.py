"""The wire shapes a solve reads out as: ``SolveResponse`` and every part of it.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from ..solver.views import OverclockOption, OverclockRow, PowerSource, RowName

__all__ = [
    "BuildAmount",
    "ItemRate",
    "OverclockView",
    "PaybackStop",
    "PaybackView",
    "PlanGraph",
    "PlanGraphEdge",
    "PlanGraphNode",
    "SolveResponse",
    "SolveRow",
]


class ItemRate(TypedDict):
    item: str
    per_min: float


class SolveRow(TypedDict):
    """One build row. ``clock`` is a fraction (1.0 = 100%) and ``mw`` is signed: negative draws.
    ``last_clock`` is set when every machine but the last runs at 100% (overclock-last), and
    ``overclock_option`` on every row that could run that way.

    ``id`` is the join key for the graph, pins and chat badges; ``depth`` its chain depth."""

    id: str
    depth: int
    building: str
    recipe: str
    recipe_id: str | None
    item: str | None
    machines: int
    clock: float
    last_clock: float | None
    overclock_option: OverclockOption | None
    mw: float
    inputs: list[ItemRate]
    outputs: list[ItemRate]
    required: bool


class PlanGraphNode(TypedDict):
    """``kind`` is process, input or export; ``item`` is a class id, null for power."""

    id: str
    kind: str
    label: str
    detail: str
    rank: int
    row: str | None
    item: str | None


class PlanGraphEdge(TypedDict):
    source: str
    target: str
    item: str
    per_min: float
    text: str | None


class PlanGraph(TypedDict):
    nodes: list[PlanGraphNode]
    edges: list[PlanGraphEdge]


class BuildAmount(TypedDict):
    item: str
    amount: float


class PaybackStop(TypedDict):
    """The plan at one payback horizon, same recipes. ``extra_machines``, ``saved_mw``,
    ``cost``, ``area_m2`` and ``points`` are against the plain build (0 h, no overclock);
    ``cost`` is what the extra machines take to build and ``points`` their build points.
    ``average_payback_h`` is null when nothing is saved."""

    hours: float
    machines: int
    mw_draw: float
    extra_machines: int
    saved_mw: float
    cost: list[BuildAmount]
    area_m2: float
    points: int
    average_payback_h: float | None
    shards: int


class OverclockView(TypedDict):
    """The overclock-last pick at the plan's horizon, made whether or not ``on``; the totals
    are what the switch builds when on. ``without`` ran short of shards; ``unused`` would cost
    more than spreading. ``pinned_last``/``pinned_spread`` count rows with their own choice."""

    on: bool
    inherited: bool
    rows: list[OverclockRow]
    shards: int
    machines_saved: int
    extra_mw: float
    without: list[RowName]
    unused: list[RowName]
    pinned_last: int
    pinned_spread: int
    shards_free: float | None
    shards_craftable: float | None


class PaybackView(TypedDict):
    """``hours`` is the plan's horizon, ``inherited`` when it follows the shared default.
    ``price`` is points per MWh from ``price_source`` (grid mix or plan). ``splits`` is false
    when no stop changes a row, with ``reason``; ``stops`` is empty when nothing solved."""

    hours: float
    inherited: bool
    default_hours: float
    price: float
    price_source: str
    mix: list[PowerSource]
    splits: bool
    reason: str
    stops: list[PaybackStop]
    overclock: OverclockView


class SolveResponse(TypedDict):
    """A solve's facts. Infeasible is a 200 with ``feasible: false`` and a player ``cause``."""

    feasible: bool
    headline: str
    cause: str
    plan_id: str
    notes: list[str]
    warnings: list[str]
    machines: int
    processes: int
    mw_draw: float | None
    mw_generated: float | None
    mw_net: float | None
    grid_import: bool
    exports: list[ItemRate]
    inputs: list[ItemRate]
    rows: list[SolveRow]
    graph: PlanGraph
    shards: int | None
    sloops_used: int
    power: PaybackView
    blockers: list[str]
    token: str
