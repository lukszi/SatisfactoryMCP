"""``/api/factories/health``: the ``factory_health`` sweep over every named factory, as rows.

The same ``assess`` and ``build_view`` calls the MCP tool makes per label; nothing here
classifies a machine. The panel it feeds: docs/spatial-and-map.md §21. Wire rules:
docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import candidates
from ....domain.factories.health import ACTIONABLE, OK, STATES
from ....domain.factories.sweep import sweep
from ....domain.spatial import geo
from ..serial import _fail, _m, _state

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


@router.get("/factories/health", response_model=FactoryHealthResponse)
def factory_health(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Uptime, states and the worst machines of every named factory, worst factory first."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    alive_set = set(st.graph.machines())
    placed = candidates.positions(st.projection)
    review = {row["name"]: row["status"] for row in st.labels.review(alive_set)}

    def issue(m) -> dict:
        at = placed.get(m.instance)
        return {
            "instance": m.instance,
            "state": m.state,
            "uptime": None if m.uptime is None else round(m.uptime, 3),
            "what": m.recipe or st.game.building_name(m.building) or m.building,
            "cause": list(m.cause),
            "x_m": _m(at[0]) if at else None,
            "y_m": _m(at[1]) if at else None,
        }

    rows = []
    for swept in sweep(st):
        label, standing, report, view = swept.label, swept.standing, swept.report, swept.view
        box = geo.bbox([placed[m][:2] for m in standing if m in placed])
        mean = report.mean_uptime
        rows.append(
            {
                "name": label.name,
                "centroid_m": [_m(label.centroid[0]), _m(label.centroid[1])],
                "bbox_m": None if box is None else [_m(v) for v in box],
                "anchors": len(label.anchors),
                "alive": len(standing),
                "review": review.get(label.name),
                "machines": len(report.machines),
                "uptime": None if mean is None else round(mean, 3),
                "measured_mw": round(view.measured_draw_mw, 1),
                "nameplate_mw": round(view.draw_mw, 1),
                "states": [
                    {"state": s, "count": report.by_state[s]} for s in STATES if report.by_state[s]
                ],
                "actionable": sum(report.by_state[s] for s in ACTIONABLE),
                "unwired": len(report.unwired),
                "no_generator": len(report.no_generator),
                "worst": [issue(m) for m in report.worst(WORST_PER_FACTORY)],
                "worst_actionable": [issue(m) for m in swept.worst_actionable(WORST_PER_FACTORY)],
                "attention": sum(1 for m in report.machines if m.state not in OK),
            }
        )
    return {
        "labels_version": st.labels.version,
        "states": list(STATES),
        "actionable_states": list(ACTIONABLE),
        "ok_states": [s for s in STATES if s in OK],
        "factories": rows,
    }
