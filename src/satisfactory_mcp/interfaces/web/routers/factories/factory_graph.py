"""``/api/factories/graph`` and ``/api/factories/machines``: one factory's machines, drawn.

Both answer for a named factory or for a detected candidate (its ``proposal:N`` selector plus
the ``token`` it was detected at): the graph as recipe groups and terminals, the machines as
spots on the map. Naming, renaming and amending are ``factory_labels.py``.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from typing import NamedTuple

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....domain.factories import candidates, flowgraph
from .....domain.factories.built_graph import built_graph
from .....domain.factories.query import build_view
from .....domain.factories.select import SelectorError, select_machines
from .....domain.factories.views import BuiltGraphEdge, BuiltGraphNode
from .....domain.planning.readout.views import PlanGraphBuilding
from .....domain.world import pin
from .....domain.world.state import WorldState
from ...serial import (
    MachineSpot,
    RequestRefused,
    bbox_m,
    machine_spots,
    require_world,
    standing_anchors,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class FactoryGraphResponse(TypedDict):
    """The plan graph's shape for built machines (docs/frontend_vision.md §9.8); ``mw_draw``
    and ``mw_generated`` are nameplate at the saved clocks."""

    title: str
    token: str
    buffers: int
    nodes: list[BuiltGraphNode]
    edges: list[BuiltGraphEdge]
    buildings: list[PlanGraphBuilding]
    mw_draw: float
    mw_generated: float


class PickedMachines(NamedTuple):
    state: WorldState
    machines: list[str]
    title: str


def _picked_machines(
    request: Request,
    factory: str | None,
    candidate: str | None,
    token: str | None,
    save: str | None,
    world: str | None,
) -> PickedMachines:
    """The standing machines of a named factory, or of a candidate detected at ``token``."""
    if bool(factory) == bool(candidate):
        raise RequestRefused("pass exactly one of factory= or candidate=", 400)
    st = require_world(request, save, world)
    if factory:
        label = next((x for x in st.labels.labels if x.name == factory), None)
        if label is None:
            raise RequestRefused(f"no factory named “{factory}” in this world", 404)
        return PickedMachines(st, standing_anchors(st, label), label.name)
    assert candidate is not None  # exactly one of the two is set, and it is not ``factory``
    if not token:
        raise RequestRefused("candidate= needs the token= it was detected at", 400)
    try:
        pin.check(st.header, token)
    except pin.PinRefused as exc:
        raise RequestRefused(
            "a newer save was written since this was detected; detect again", 409
        ) from exc
    try:
        return PickedMachines(st, select_machines([candidate], st), candidate)
    except SelectorError as exc:
        raise RequestRefused(str(exc), 404) from exc


@router.get("/factories/graph", response_model=FactoryGraphResponse)
def factory_graph(
    request: Request,
    factory: str | None = None,
    candidate: str | None = None,
    token: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> FactoryGraphResponse:
    """The production graph of a named factory, or of a detected candidate, by recipe group.

    A candidate is its ``proposal:N`` selector plus the ``token`` it was detected at; a save
    written since then is refused (409), since the index may now name another cluster.
    """
    st, machines, title = _picked_machines(request, factory, candidate, token, save, world)
    view = build_view(title, machines, st.graph, st.game, st.projection)
    flow_graph = flowgraph.build(st, st.game, view)
    placed = candidates.positions(st.projection)
    graph = built_graph(
        flow_graph,
        view,
        st.game,
        "factory" if factory else "cluster",
        lambda group: bbox_m(placed, group),
    )
    return {
        "title": title,
        "token": pin.check(st.header, None),
        "buffers": len(flow_graph.buffers),
        **graph,
    }


class FactoryMachinesResponse(TypedDict):
    title: str
    token: str
    machines: list[MachineSpot]


@router.get("/factories/machines", response_model=FactoryMachinesResponse)
def factory_machines(
    request: Request,
    factory: str | None = None,
    candidate: str | None = None,
    token: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> FactoryMachinesResponse:
    """Where each standing machine of a named factory, or of a detected candidate, stands."""
    st, machines, title = _picked_machines(request, factory, candidate, token, save, world)
    return {
        "title": title,
        "token": pin.check(st.header, None),
        "machines": machine_spots(st, machines),
    }
