"""``/api/factories/plan``: a new stored plan that mirrors a named factory as built.

The same ``plan_from_factory`` the MCP tool calls, so a plan made from the page and one made
from chat are one shape; the page opens the plan it gets back in the planner.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.filelock import LockTimeout
from .....domain.factories.select import SelectorError
from .....domain.planning.progress.as_built import plan_from_factory
from .....domain.planning.stored.plan_args import PlanLogError
from .....domain.world import pin
from ...serial import busy_response, error_response, page_actor, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


class PlanFromFactoryBody(TypedDict):
    factory: str


class PlanFromFactoryResponse(TypedDict):
    """``differences`` is how a solve of the plan departs from the build; empty when none."""

    key: str
    name: str
    rev: int
    factory: str
    feasible: bool
    machines: int
    mw_draw: float | None
    built_mw: float
    differences: list[str]
    notes: str


@router.post(
    "/factories/plan",
    status_code=201,
    response_model=PlanFromFactoryResponse,
    responses={404: {"description": "no factory by that name"}},
)
def plan_from_built_factory(
    request: Request,
    body: Annotated[PlanFromFactoryBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> PlanFromFactoryResponse | JSONResponse:
    """Store a new plan at v1 that reproduces one named factory; every call makes a new one."""
    st = require_world(request, save, world)
    try:
        sav = pin.check(st.header, None)
    except Exception:
        sav = ""
    try:
        made = plan_from_factory(st, body["factory"], actor=page_actor(), sav=sav)
    except SelectorError as exc:
        return error_response(str(exc), 404)
    except LockTimeout as exc:
        return busy_response("plans", exc)
    except PlanLogError as exc:
        return error_response(str(exc), 409)
    plan, solved = made.pushed.state, made.solve
    return {
        "key": plan.key,
        "name": plan.name,
        "rev": plan.rev,
        "factory": made.built.factory,
        "feasible": solved["feasible"],
        "machines": solved["machines"],
        "mw_draw": solved["mw_draw"],
        "built_mw": round(made.built.draw_mw, 2),
        "differences": made.differences,
        "notes": plan.notes,
    }
