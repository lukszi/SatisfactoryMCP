"""``/api/plan/solve``, ``/api/ui/focus``, ``/api/activity``, ``/api/plan/track``: the workbench's other half.

Solve re-solves a request or a stored version and stores nothing. Focus records what the
page has open, for ``ui_context``. Activity is the plan logs and the journal merged by time.
Alternates is one item's recipes with what requiring each would change in a stored plan.
Track is one plan's diff and startup stages matched against the save.
docs/planner_slice_contract.md §9 and §11, docs/planner-p3_contract.md §5 and
docs/planner-p4_contract.md §5 are the specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import re
import time
from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request

from ....domain.planning import focus, journal, manage, summary, swaps, track
from ....domain.planning.planlog import InvalidOp, PlanArgs, PlanLog, UnknownPlan
from ....domain.planning.scenario import resolve_item
from ....domain.world import pin
from ..serial import ActorBody, Biomass, PlanOpBody, TrackBuiltAt, _actor_json, _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")

_KEY = re.compile(r"[0-9a-f]{8}")


class SolveRate(TypedDict):
    item: str
    per_min: float


class SolveRow(TypedDict):
    """One build row. ``clock`` is a fraction (1.0 = 100%) and ``mw`` is signed: negative draws.

    ``id`` is the join key for the graph, pins and chat badges; ``depth`` its chain depth."""

    id: str
    depth: int
    building: str
    recipe: str
    recipe_id: str | None
    item: str | None
    machines: int
    clock: float
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
    blockers: list[str]
    token: str


class SolveBody(TypedDict):
    """Exactly one of ``args`` (a request) and ``key`` (a stored plan, at ``rev`` or its head)."""

    args: NotRequired[dict | None]
    key: NotRequired[str | None]
    rev: NotRequired[int | None]


class Selection(TypedDict):
    kind: str
    label: str
    ref: str


class FocusBody(TypedDict):
    view: str
    dash: NotRequired[str]
    plan: NotRequired[str | None]
    rev: NotRequired[int | None]
    tab: NotRequired[str]
    selection: NotRequired[Selection | None]
    follow: NotRequired[str]
    sav: NotRequired[str]


class FocusResponse(TypedDict):
    ok: bool
    heartbeat: float


class ActivityRow(TypedDict):
    """A plan commit (``source`` "plan", ``kind`` "commit") or a journal entry."""

    id: str
    ts: float
    source: str
    actor: ActorBody
    kind: str
    plan: str | None
    name: str | None
    rev: int | None
    text: str
    args: dict | None


class ActivityResponse(TypedDict):
    now: float
    entries: list[ActivityRow]


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


class TrackState(TypedDict):
    state: str
    count: int


class TrackMachine(TypedDict):
    instance: str
    x_m: float | None
    y_m: float | None


class TrackTarget(TypedDict):
    node: str
    x_m: float | None
    y_m: float | None
    m: float | None


class TrackRow(TypedDict):
    """One build job. ``verb`` is ok, unpause, setrecipe or build; ``build_max`` and ``have_min``
    are null when the count is exact; ``running`` is null when no matched machine is monitored."""

    id: str
    kind: str
    step: int
    stages: list[int]
    process: str
    building: str
    recipe_id: str | None
    item: str | None
    need: int
    have: int
    have_min: int | None
    build: int
    build_max: int | None
    verb: str
    count: int
    reuse: int
    running: int | None
    states: list[TrackState]
    new_building: bool
    note: str
    delta_mw: float
    act: list[TrackMachine]
    targets: list[TrackTarget]
    bbox_m: list[float] | None
    selectors: str


class TrackStageRow(TypedDict):
    row: str
    label: str
    building: str
    machines: int
    total: int
    built: int
    built_max: int
    running: int | None
    states: list[TrackState]
    draw_mw: float
    generation_mw: float
    to_build: int


class TrackStage(TypedDict):
    """One startup wave matched against the save; ``state`` is the server's phrase for it."""

    index: int
    machines: int
    built: int
    built_max: int
    running: int | None
    dark: int
    complete: bool
    state: str
    draw_mw: float
    generation_mw: float
    available_before: float
    available_after: float
    fill_s: float
    waits_for_fill: bool
    states: list[TrackState]
    rows: list[TrackStageRow]
    bbox_m: list[float] | None


class TrackStartup(TypedDict):
    ok: bool
    headroom_mw: float
    headroom_source: str
    plant_draw_mw: float
    plant_generation_mw: float
    minimum_slice_mw: float
    warnings: list[str]


class TrackPower(TypedDict):
    generation_mw: float
    draw_mw: float
    headroom_mw: float
    measured_headroom_mw: float
    biomass: bool


class TrackCost(TypedDict):
    item: str
    name: str
    need: float
    stock: float
    short: float
    lines: int


class TrackNeighbour(TypedDict):
    label: str
    count: int


class TrackSiteRow(TypedDict):
    name: str
    planned: int
    standing: int


class TrackSite(TypedDict):
    text: str
    planned_total: int
    standing_total: int
    rows: list[TrackSiteRow]


class TrackResponse(TypedDict):
    """One plan version's diff and startup stages against this save, from one solve.

    Not feasible is a 200 with ``feasible: false`` and empty lists; a count-as-built factory
    with no machines left is a 200 with ``scope_error`` and empty lists."""

    key: str
    rev: int
    name: str
    feasible: bool
    empty: bool
    headline: str
    cause: str
    save_id: str
    age_note: str
    plan_id: str
    scope: str
    scope_note: str
    scope_error: str
    drift_note: str
    headroom_mw: float | None
    current: int
    count: int
    partition_id: str
    stage_text: str
    to_build: int
    to_build_max: int
    actionable: int
    unpause: int
    setrecipe: int
    rows: list[TrackRow]
    stages: list[TrackStage]
    startup: TrackStartup
    power: TrackPower
    cost: list[TrackCost]
    neighbours: list[TrackNeighbour]
    site: TrackSite | None
    notes: list[str]
    caveats: list[str]
    monitored: int
    built_at: TrackBuiltAt


class Feeder(TypedDict):
    name: str
    instance: str
    x_m: float | None
    y_m: float | None
    mw: float
    region: str


class FeedersResponse(TypedDict):
    feeders: list[Feeder]
    total_mw: float
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
        return _fail("send exactly one of args and key", 400)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    if key is not None:
        if not _KEY.fullmatch(key):
            return _fail(f"no plan “{key}” in this world", 404)
        try:
            kwargs = PlanLog(st.world_id).state(key, body.get("rev")).kwargs()
        except UnknownPlan:
            return _fail(f"no plan “{key}” in this world", 404)
        except InvalidOp as exc:
            return _fail(str(exc), 404)
    else:
        try:
            kwargs = PlanArgs.from_dict(args).kwargs()
        except InvalidOp as exc:
            return _fail(str(exc), 400)
    try:
        return summary.solve_summary(st.game, st, kwargs)
    except ValueError as exc:
        return _fail(str(exc), 400)


@router.put("/ui/focus", response_model=FocusResponse)
def put_focus(
    request: Request,
    body: Annotated[FocusBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Record what the page has open, stamped with a heartbeat. The page's only focus write."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        written = focus.write(st.world_id, dict(body))
    except focus.InvalidFocus as exc:
        return _fail(str(exc), 400)
    return {"ok": True, "heartbeat": written["heartbeat"]}


def _commit_rows(log: PlanLog, since: float) -> list[ActivityRow]:
    rows: list[ActivityRow] = []
    for state in log.heads(include_forgotten=True):
        for commit in log.commits(state.key):
            if commit.ts <= since:
                continue
            rows.append(
                {
                    "id": f"{state.key}:v{commit.rev}",
                    "ts": commit.ts,
                    "source": "plan",
                    "actor": _actor_json(commit.actor),
                    "kind": "commit",
                    "plan": state.key,
                    "name": state.name,
                    "rev": commit.rev,
                    "text": commit.text(),
                    "args": None,
                }
            )
    return rows


def _entry_row(entry: dict, names: dict[str, str]) -> ActivityRow:
    plan = entry.get("plan")
    return {
        "id": str(entry.get("id") or ""),
        "ts": float(entry.get("ts") or 0.0),
        "source": "journal",
        "actor": _actor_json(entry.get("actor")),
        "kind": str(entry.get("kind") or ""),
        "plan": plan,
        "name": names.get(plan) if plan else None,
        "rev": entry.get("rev"),
        "text": str(entry.get("text") or ""),
        "args": entry.get("args"),
    }


@router.get("/activity", response_model=ActivityResponse)
def activity(
    request: Request,
    since: float = 0.0,
    limit: int = 50,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Plan commits and journal entries after ``since``, oldest first, the newest ``limit``."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    now = time.time()
    limit = max(0, min(limit, 500))
    log = PlanLog(st.world_id, st.header.get("session_name") or "")
    names = {s.key: s.name for s in log.heads(include_forgotten=True)}
    rows = _commit_rows(log, since)
    rows += [_entry_row(e, names) for e in journal.read(st.world_id, since_ts=since, limit=limit)]
    rows.sort(key=lambda r: (r["ts"], r["id"]))
    return {"now": now, "entries": rows[-limit:] if limit else []}


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
    if not _KEY.fullmatch(key):
        return _fail(f"no plan “{key}” in this world", 404)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    log = PlanLog(st.world_id)
    try:
        to = log.head_rev(key) if to_rev is None else to_rev
        before = log.state(key, from_rev).kwargs()
        after = log.state(key, to).kwargs()
    except UnknownPlan:
        return _fail(f"no plan “{key}” in this world", 404)
    except InvalidOp as exc:
        return _fail(str(exc), 404)
    try:
        delta = manage.result_delta(
            summary.solve_summary(st.game, st, before), summary.solve_summary(st.game, st, after)
        )
    except ValueError as exc:
        return _fail(str(exc), 400)
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
    if not _KEY.fullmatch(key):
        return _fail(f"no plan “{key}” in this world", 404)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    g = st.game
    item = resolve_item(g, body["item"]) if body["item"] else None
    if item is None:
        return _fail(f"no item named “{body['item']}”", 404)
    try:
        state = PlanLog(st.world_id).state(key, body.get("rev"))
    except UnknownPlan:
        return _fail(f"no plan “{key}” in this world", 404)
    except InvalidOp as exc:
        return _fail(str(exc), 404)
    try:
        return swaps.swap_deltas(g, st, state, item, spoilers is not False)
    except ValueError as exc:
        return _fail(str(exc), 400)


@router.get("/plan/track", response_model=TrackResponse)
def plan_track(
    request: Request,
    key: str,
    rev: int | None = None,
    biomass: Biomass = "exclude",
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """One plan version (the head when ``rev`` is omitted) diffed and staged against this save."""
    if not _KEY.fullmatch(key):
        return _fail(f"no plan “{key}” in this world", 404)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        state = PlanLog(st.world_id).state(key, rev)
    except UnknownPlan:
        return _fail(f"no plan “{key}” in this world", 404)
    except InvalidOp as exc:
        return _fail(str(exc), 404)
    try:
        out = track.track_view(st.game, st, state, biomass=biomass == "include")
    except ValueError as exc:
        return _fail(str(exc), 400)
    out["built_at"]["token"] = pin.check(st.header, None)
    return out


@router.get("/plan/feeders", response_model=FeedersResponse)
def plan_feeders(
    request: Request,
    biomass: Biomass = "exclude",
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Built extractors whose output reaches a running generator: what startup waves stand on."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    return track.feeders_view(st.game, st, biomass=biomass == "include")
