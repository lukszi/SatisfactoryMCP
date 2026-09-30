"""``/api/plan/track`` and ``/api/plan/feeders``: one plan matched against the save.

Track is one plan version's diff and startup stages against this save, from one solve.
Feeders is the built extractors whose output reaches a running generator.
docs/planner-p4_contract.md §5 is the specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....domain.planning import track
from ....domain.planning.planlog import InvalidOp, PlanLog, UnknownPlan
from ..serial import Biomass, _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")

_KEY = re.compile(r"[0-9a-f]{8}")


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
    written_ago: str | None
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


@router.get("/plan/track", response_model=TrackResponse)
def plan_track(
    request: Request,
    key: str,
    rev: int | None = None,
    biomass: Biomass = "exclude",
    headroom: Literal["measured", "nameplate"] = "measured",
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """One plan version (the head when ``rev`` is omitted) diffed and staged against this save.
    ``headroom`` is the save's figure a plan with no stored headroom is staged against."""
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
        return track.track_view(st.game, st, state, biomass=biomass == "include", default=headroom)
    except ValueError as exc:
        return _fail(str(exc), 400)


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
