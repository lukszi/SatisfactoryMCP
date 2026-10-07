"""``/api/plan/solve``, ``/api/plan/delta``, ``/api/plan/alternates``: re-solves, stores nothing.

Solve answers a request or a stored version; delta compares two versions re-solved against
this save; alternates is one item's recipes with what requiring each would change in a stored
plan. docs/planner_slice_contract.md §9 and docs/planner-p3_contract.md §5 specify them.
Handler names are operation_ids (wire rule 1 of docs/web-wire.md).
"""

from __future__ import annotations

from typing import Annotated, NotRequired, cast

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.gamedata.search import resolve_item
from .....domain.planning.analysis import swaps
from .....domain.planning.analysis.views import PlanAlternatesResponse
from .....domain.planning.readout import summary
from .....domain.planning.readout.views import SolveResponse
from .....domain.planning.stored import manage
from .....domain.planning.stored.plan_args import InvalidOp, PlanArgs
from .....domain.planning.stored.views import DeltaRow, RowChange
from ...serial import (
    check_plan_key,
    error_response,
    plan_log,
    require_plan,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class SolveBody(TypedDict):
    """Exactly one of ``args`` (a request) and ``key`` (a stored plan, at ``rev`` or its head)."""

    args: NotRequired[dict[str, object] | None]
    key: NotRequired[str | None]
    rev: NotRequired[int | None]


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


@router.post("/plan/solve", response_model=SolveResponse)
def solve_plan(
    request: Request,
    body: Annotated[SolveBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> SolveResponse | JSONResponse:
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
        solved = summary.solve_summary(st.game, st, kwargs)
    except ValueError as exc:
        return error_response(str(exc), 400)
    # ``solve_summary`` documents its plain dict as this shape.
    return cast(SolveResponse, solved)


@router.get("/plan/delta", response_model=DeltaResponse)
def plan_delta(
    request: Request,
    key: str,
    from_rev: int,
    to_rev: int | None = None,
    save: str | None = None,
    world: str | None = None,
) -> DeltaResponse | JSONResponse:
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
) -> PlanAlternatesResponse | JSONResponse:
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
