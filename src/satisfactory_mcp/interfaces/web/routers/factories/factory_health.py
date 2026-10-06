"""``/api/factories/health``: the ``factory_health`` sweep over every named factory, as rows.

The same ``assess`` and ``build_view`` calls the MCP tool makes per label; nothing here
classifies a machine. The panel it feeds: docs/spatial-and-map.md §21. Wire rules:
docs/web-wire.md.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from .....domain.factories import candidates
from .....domain.factories.health import ACTIONABLE, OK, STATES
from .....domain.factories.sweep import sweep
from .....domain.world.state import WorldState
from ...serial import bbox_m, cm_to_m, point_m, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")

WORST_PER_FACTORY = 8


class StateCount(TypedDict):
    state: str
    count: int


class MachineIssue(TypedDict):
    """``x_m``/``y_m`` are null for a record the projection placed nowhere."""

    instance: str
    state: str
    uptime: float | None
    what: str
    cause: list[str]
    x_m: float | None
    y_m: float | None


class FactoryHealthRow(TypedDict):
    """``review`` is ``LabelStore.review``'s status, null when every anchor still stands.
    ``worst`` is the machines not fine, paused included; ``worst_actionable`` only those in
    an ``actionable_states`` state, of which there are ``actionable`` in all."""

    name: str
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    anchors: int
    alive: int
    review: str | None
    machines: int
    uptime: float | None
    measured_mw: float
    nameplate_mw: float
    states: list[StateCount]
    actionable: int
    unwired: int
    no_generator: int
    worst: list[MachineIssue]
    worst_actionable: list[MachineIssue]
    attention: int


class FactoryHealthResponse(TypedDict):
    """``actionable_states`` is the subset of ``states`` that ``actionable`` counts, and
    ``ok_states`` the subset that ``attention`` leaves out; ``labels_version`` is what a
    rename or forget sends back as ``version``."""

    labels_version: int
    states: list[str]
    actionable_states: list[str]
    ok_states: list[str]
    factories: list[FactoryHealthRow]


def _issue_row(st: WorldState, placed: dict, machine: Any) -> dict:
    at = placed.get(machine.instance)
    return {
        "instance": machine.instance,
        "state": machine.state,
        "uptime": None if machine.uptime is None else round(machine.uptime, 3),
        "what": machine.recipe or st.game.building_name(machine.building) or machine.building,
        "cause": list(machine.cause),
        "x_m": cm_to_m(at[0]) if at else None,
        "y_m": cm_to_m(at[1]) if at else None,
    }


def _factory_row(st: WorldState, swept: Any, placed: dict, review: dict[str, str]) -> dict:
    """One named factory's sweep: its extent, uptime, states and worst machines."""
    label, standing, report, view = swept.label, swept.standing, swept.report, swept.view
    mean = report.mean_uptime
    worst = report.worst(WORST_PER_FACTORY)
    worst_actionable = swept.worst_actionable(WORST_PER_FACTORY)
    return {
        "name": label.name,
        "centroid_m": point_m(label.centroid),
        "bbox_m": bbox_m(placed, standing),
        "anchors": len(label.anchors),
        "alive": len(standing),
        "review": review.get(label.name),
        "machines": len(report.machines),
        "uptime": None if mean is None else round(mean, 3),
        "measured_mw": round(view.measured_draw_mw, 1),
        "nameplate_mw": round(view.draw_mw, 1),
        "states": [
            {"state": state, "count": report.by_state[state]}
            for state in STATES
            if report.by_state[state]
        ],
        "actionable": sum(report.by_state[state] for state in ACTIONABLE),
        "unwired": len(report.unwired),
        "no_generator": len(report.no_generator),
        "worst": [_issue_row(st, placed, machine) for machine in worst],
        "worst_actionable": [_issue_row(st, placed, machine) for machine in worst_actionable],
        "attention": sum(1 for machine in report.machines if machine.state not in OK),
    }


@router.get("/factories/health", response_model=FactoryHealthResponse)
def factory_health(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Uptime, states and the worst machines of every named factory, worst factory first."""
    st = require_world(request, save, world)

    alive_set = set(st.graph.machines())
    placed = candidates.positions(st.projection)
    review = {row["name"]: row["status"] for row in st.labels.review(alive_set)}
    rows = [_factory_row(st, swept, placed, review) for swept in sweep(st)]
    return {
        "labels_version": st.labels.version,
        "states": list(STATES),
        "actionable_states": list(ACTIONABLE),
        "ok_states": [s for s in STATES if s in OK],
        "factories": rows,
    }
