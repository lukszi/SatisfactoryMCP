"""``/api/plan/solve``, ``/api/plan/delta``, ``/api/plan/alternates``: re-solves, stores nothing.

Solve answers a request or a stored version; delta compares two versions re-solved against
this save; alternates is one item's recipes with what requiring each would change in a stored
plan. docs/planner_slice_contract.md §9 and docs/planner-p3_contract.md §5 specify them.
Handler names are operation_ids (wire rule 1 of docs/web-wire.md).
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request

from ....domain.planning import manage, summary, swaps
from ....domain.planning.planlog import InvalidOp, PlanArgs
from ....domain.planning.scenario import resolve_item
from ..serial import (
    PlanOpBody,
    check_plan_key,
    error_response,
    plan_log,
    require_plan,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class SolveRate(TypedDict):
    item: str
    per_min: float


class OverclockOption(TypedDict):
    """A row's two builds: ``machines`` with the last at ``last_clock`` for ``shards``, or
    ``spread_machines`` at ``spread_clock``. ``pinned`` is the row's own choice ("last",
    "spread") or null to follow the plan; ``applied`` says the overclocked build is the one
    listed. ``without``: no shards were left; ``unused``: it costs more than spreading."""

    pinned: str | None
    applied: bool
    machines: int
    last_clock: float
    shards: int
    extra_mw: float
    spread_machines: int
    spread_clock: float
    without: bool
    unused: bool


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
    inputs: list[SolveRate]
    outputs: list[SolveRate]
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


class PowerSource(TypedDict):
    """One source of the grid mix: running MW and its price in points per MWh."""

    source: str
    mw: float
    price: float


class OverclockRow(TypedDict):
    label: str
    building: str
    machines: int
    instead: int
    last_clock: float
    shards: int
    extra_mw: float
    pinned: str | None
    applied: bool


class RowName(TypedDict):
    label: str
    building: str


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
    exports: list[SolveRate]
    inputs: list[SolveRate]
    rows: list[SolveRow]
    graph: PlanGraph
    shards: int | None
    sloops_used: int
    power: PaybackView
    blockers: list[str]
    token: str


class SolveBody(TypedDict):
    """Exactly one of ``args`` (a request) and ``key`` (a stored plan, at ``rev`` or its head)."""

    args: NotRequired[dict | None]
    key: NotRequired[str | None]
    rev: NotRequired[int | None]


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


class DeltaResponse(TypedDict):
    """What re-solving ``from_rev`` and ``to_rev`` gives. ``comparable`` is false when either
    side is not solvable, and then only ``text`` says anything."""

    key: str
    from_rev: int
    to_rev: int
    comparable: bool
    machines: int
    mw_draw: float
    mw_net: float
    buildings: list[DeltaRow]
    inputs: list[DeltaRow]
    rows: list[RowChange]
    text: str


class AlternatesBody(TypedDict):
    key: str
    rev: NotRequired[int | None]
    item: str


class SwapOption(TypedDict):
    """One recipe for the item. ``status`` is in use, required, banned, available or locked;
    ``delta`` is null when ``solved`` is false (locked, or banned by a pattern)."""

    recipe_id: str
    name: str
    alternate: bool
    machine: str | None
    unlocked: bool | None
    spoiler: bool
    granted_by: list[str]
    status: str
    in_use: bool
    required: bool
    banned: bool
    banned_by: str | None
    solved: bool
    delta: ResultDelta | None
    require_ops: list[PlanOpBody]
    ban_ops: list[PlanOpBody]
    free_ops: list[PlanOpBody]


class PlanAlternatesResponse(TypedDict):
    """Every recipe for one item with what requiring it changes in the plan at ``rev``."""

    key: str
    rev: int
    item: str
    name: str
    head_feasible: bool
    head_machines: int
    head_mw_draw: float | None
    head_mw_net: float | None
    options: list[SwapOption]
    hidden: int
    text: str


@router.post("/plan/solve", response_model=SolveResponse)
def solve_plan(
    request: Request,
    body: Annotated[SolveBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Solve a request or a stored version against this save; nothing is written."""
    args, key = body.get("args"), body.get("key")
    if (args is None) == (key is None):
        return error_response("send exactly one of args and key", 400)
    st = require_world(request, save, world)
    if key is not None:
        check_plan_key(key)
        kwargs = require_plan(plan_log(st), key, body.get("rev")).kwargs()
    else:
        try:
            kwargs = PlanArgs.from_dict(args).kwargs()
        except InvalidOp as exc:
            return error_response(str(exc), 400)
    try:
        return summary.solve_summary(st.game, st, kwargs)
    except ValueError as exc:
        return error_response(str(exc), 400)


@router.get("/plan/delta", response_model=DeltaResponse)
def plan_delta(
    request: Request,
    key: str,
    from_rev: int,
    to_rev: int | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """The result deltas between two versions of one plan, both re-solved against this save."""
    check_plan_key(key)
    st = require_world(request, save, world)
    log = plan_log(st)
    before = require_plan(log, key, from_rev).kwargs()
    to = log.head_rev(key) if to_rev is None else to_rev
    after = require_plan(log, key, to).kwargs()
    try:
        delta = manage.result_delta(
            summary.solve_summary(st.game, st, before), summary.solve_summary(st.game, st, after)
        )
    except ValueError as exc:
        return error_response(str(exc), 400)
    return {"key": key, "from_rev": from_rev, "to_rev": to, **delta}


@router.post("/plan/alternates", response_model=PlanAlternatesResponse)
def plan_alternates(
    request: Request,
    body: Annotated[AlternatesBody, Body()],
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """Every recipe making ``item``, each with what requiring it would change in the plan."""
    key = body["key"]
    check_plan_key(key)
    st = require_world(request, save, world)
    item = resolve_item(st.game, body["item"]) if body["item"] else None
    if item is None:
        return error_response(f"no item named “{body['item']}”", 404)
    state = require_plan(plan_log(st), key, body.get("rev"))
    try:
        return swaps.swap_deltas(st.game, st, state, item, spoilers is not False)
    except ValueError as exc:
        return error_response(str(exc), 400)
