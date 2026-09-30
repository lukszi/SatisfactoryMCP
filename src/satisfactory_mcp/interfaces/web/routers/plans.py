"""``/api/plans``: where each stored plan is to STAND, and the index of live plans.

``plans`` is the siting the map draws; ``index`` is one row per live plan at its head, for
the Planner's list (docs/planner_slice_contract.md §11.1). A plan's contents are a request,
never a solve; ``/api/plan/solve`` re-solves one.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.planning import manage
from ....domain.planning import siting as planning_siting
from ....domain.planning.diff_service import plan_progress
from ....domain.planning.planlog import PlanLog
from ....domain.world import pin
from ..serial import ActorBody, _actor_json, _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------------ plans


class PlanSiting(TypedDict):
    """One stored plan's pad: centre, facing and extent, all in metres on save axes.

    NOT centimetres, and this is the one payload on this surface where that is not a bug.
    ``Siting`` records metres because a player typed them, so ``serial._m`` has nothing to
    do here -- see ``domain/planning/siting.py``.

    ``z_m`` is null wherever the origin was named by something with no height (a factory
    centroid, a bare ``x,y``); the pad is still a rectangle on the ground. ``source`` is
    ``"given"`` for a footprint the player measured and ``"layout"`` for the square
    ``plan_layout`` budgeted, which is the difference between a pad and an estimate.
    """

    key: str
    name: str
    x_m: float
    y_m: float
    z_m: float | None
    yaw_deg: float
    width_m: float
    depth_m: float
    source: str
    origin_label: str
    factory: str


class PlanLast(TypedDict):
    """The plan's newest commit: who, when, and the words ``describe_commit`` gives it."""

    rev: int
    ts: float
    actor: ActorBody
    text: str


class PlanIndexRow(TypedDict):
    """One live plan at its head. ``rates`` is ``export_minimums``, item name to per minute.

    ``status`` is what moved under it (``manage.plan_status``): "world moved", "field N->M",
    "broken: ..."; empty with ``recorded`` false means the field was never checked.
    """

    key: str
    name: str
    rev: int
    objective: str
    target_item: str | None
    exports: list[str]
    rates: dict[str, float]
    sited: bool
    factory: str
    plan_id: str
    last: PlanLast
    status: list[str]
    recorded: bool


class PlansResponse(TypedDict):
    """What ``/api/plans`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    plans: list[PlanSiting]
    stored: int
    index: list[PlanIndexRow]


def _index(log: PlanLog, st) -> list[PlanIndexRow]:
    rows: list[PlanIndexRow] = []
    for state in log.heads():
        status = manage.plan_status(st, state)
        newest = log.commits(state.key, since=state.rev - 1)[-1]
        rows.append(
            {
                "key": state.key,
                "name": state.name,
                "rev": state.rev,
                "objective": state.args.objective,
                "target_item": state.args.target_item,
                "exports": list(state.args.exports),
                "rates": dict(state.args.export_minimums),
                "sited": bool(state.siting),
                "factory": state.factory,
                "plan_id": state.plan_id,
                "last": {
                    "rev": newest.rev,
                    "ts": newest.ts,
                    "actor": _actor_json(newest.actor),
                    "text": newest.text(),
                },
                "status": status.flags,
                "recorded": status.recorded,
            }
        )
    return rows


@router.get("/plans", response_model=PlansResponse)
def plans(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every stored plan that has been sited, as the rectangle it claims.

    A world with plans and no sitings answers ``{"plans": [], "stored": 3}``, which is why
    ``stored`` is here: an empty layer over three stored plans means "none of them has been
    sited yet", and an empty layer over no plans at all means the feature is unused.

    A siting with no footprint is NOT sent. ``site_plan`` always records one -- given or
    derived from the layout -- so a footprintless record is a hand-edited file, and an
    origin alone bounds nothing this layer could draw.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    rows = []
    for plan in st.plans.plans:
        sit = planning_siting.parse(plan)
        if sit is None or not sit.has_footprint:
            continue
        rows.append(
            {
                "key": getattr(plan, "key", ""),
                "name": plan.name,
                "x_m": sit.x_m,
                "y_m": sit.y_m,
                "z_m": sit.z_m,
                "yaw_deg": sit.yaw_deg,
                "width_m": sit.width_m,
                "depth_m": sit.depth_m,
                "source": sit.source,
                "origin_label": sit.origin_label,
                "factory": plan.factory,
            }
        )
    log = PlanLog(st.world_id, st.header.get("session_name") or "")
    return {"plans": rows, "stored": len(st.plans.plans), "index": _index(log, st)}


# ------------------------------------------------------------------ progress


class PlanBuiltRow(TypedDict):
    """One live plan's progress at its head: ``built_at`` without the startup order.

    ``figure`` is ``12 / 16``, ``12–16 / 20`` (unsure), ``–`` (not placed) or ``?`` (the
    plan does not solve or builds nothing)."""

    key: str
    rev: int
    mode: str
    confidence: str
    built: int | None
    built_max: int | None
    total: int
    percent: float | None
    percent_max: float | None
    figure: str
    text: str


class PlansBuiltResponse(TypedDict):
    rows: list[PlanBuiltRow]


#: (world, plan key, rev, save token, labels version) -> row. Every part of the answer's
#: input is in the key, so an entry is never stale, only unused.
_BUILT: dict[tuple, PlanBuiltRow] = {}
_BUILT_MAX = 256


def _built_row(st, state) -> PlanBuiltRow:
    found = plan_progress(st.game, st, state)
    if found is None:
        return {
            "key": state.key,
            "rev": state.rev,
            "mode": "",
            "confidence": "",
            "built": None,
            "built_max": None,
            "total": 0,
            "percent": None,
            "percent_max": None,
            "figure": "?",
            "text": "the plan does not solve today",
        }
    return {
        "key": state.key,
        "rev": state.rev,
        "mode": found.mode,
        "confidence": found.confidence,
        "built": found.built,
        "built_max": found.built_max,
        "total": found.total,
        "percent": found.percent,
        "percent_max": found.percent_max,
        "figure": "?" if found.confidence == "unsure" else found.figure(),
        "text": found.where(),
    }


@router.get("/plan/built", response_model=PlansBuiltResponse)
def plans_built(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every live plan's built progress, for the Planner's list: a solve per plan, cached
    per plan version, save and factory names."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    token = pin.check(st.header, None)
    log = PlanLog(st.world_id, st.header.get("session_name") or "")
    rows = []
    for state in log.heads():
        key = (st.world_id, state.key, state.rev, token, st.labels.version)
        row = _BUILT.get(key)
        if row is None:
            if len(_BUILT) >= _BUILT_MAX:
                _BUILT.clear()
            row = _BUILT[key] = _built_row(st, state)
        rows.append(row)
    return {"rows": rows}
