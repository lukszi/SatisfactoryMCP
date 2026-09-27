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
from ....domain.planning.planlog import PlanLog
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
