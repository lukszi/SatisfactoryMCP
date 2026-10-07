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
from .....domain.factories.query import build_view
from .....domain.factories.select import SelectorError, select_machines
from .....domain.world import pin
from .....domain.world.state import WorldState
from ...serial import (
    Flow,
    FlowEdge,
    MachineSpot,
    Placed,
    RequestRefused,
    bbox_m,
    flow_edges_json,
    flow_group_json,
    flow_json,
    machine_spots,
    require_world,
    standing_anchors,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class GraphNode(TypedDict):
    """``kind`` is ``group`` (machines on one recipe), ``input``, or a terminal: ``storage``,
    ``export``, ``sink``, ``nowhere``. Counts, ``states`` (machines per health state) and ``bbox_m``
    are for groups only."""

    id: str
    kind: str
    label: str
    detail: str
    machines: int
    clock: float | None
    makes: list[Flow]
    running: int
    blocked: int
    stopped: int
    states: dict[str, int]
    bbox_m: tuple[float, float, float, float] | None


class FactoryGraphResponse(TypedDict):
    title: str
    token: str
    buffers: int
    nodes: list[GraphNode]
    edges: list[FlowEdge]


TERMINAL_LABELS = {
    "storage": "to storage",
    "export": "leaves the {}",
    "sink": "AWESOME Sink",
    "nowhere": "goes nowhere",
}


def _plain_node(key: str, kind: str, label: str, detail: str) -> GraphNode:
    """An input or terminal node: a label and a line, and none of a group's counts."""
    return {
        "id": key,
        "kind": kind,
        "label": label,
        "detail": detail,
        "machines": 0,
        "clock": None,
        "makes": [],
        "running": 0,
        "blocked": 0,
        "stopped": 0,
        "states": {},
        "bbox_m": None,
    }


def _group_node(
    flow_graph: flowgraph.FlowGraph, group: flowgraph.Group, placed: Placed
) -> GraphNode:
    """One recipe group: its machines' mean clock, what it makes, its states and its box."""
    clocks = group.clocks
    return {
        **flow_group_json(group),
        "kind": "group",
        "clock": round(sum(clocks) / len(clocks), 3) if clocks else None,
        "makes": [
            flow_json(flow_graph, item, rate)
            for item, rate in sorted(group.makes.items(), key=lambda kv: -kv[1])
        ],
        "states": dict(sorted(group.health.items())),
        "bbox_m": bbox_m(placed, group.machines),
    }


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
    """The recipe-group production graph of a named factory, or of a detected candidate.

    A candidate is its ``proposal:N`` selector plus the ``token`` it was detected at; a save
    written since then is refused (409), since the index may now name another cluster.
    """
    st, machines, title = _picked_machines(request, factory, candidate, token, save, world)
    view = build_view(title, machines, st.graph, st.game, st.projection)
    flow_graph = flowgraph.build(st, st.game, view)
    whole = "factory" if factory else "cluster"
    placed = candidates.positions(st.projection)
    nodes = [_group_node(flow_graph, group, placed) for group in flow_graph.groups.values()]
    used = {e.source for e in flow_graph.edges} | {e.target for e in flow_graph.edges}
    for key in sorted(k for k in used if k.startswith("in:")):
        nodes.append(_plain_node(key, "input", key[3:], f"enters the {whole}"))
    for kind, text in TERMINAL_LABELS.items():
        if kind in used:
            reached = f"{flow_graph.terminals.get(kind, 0)} reached"
            nodes.append(_plain_node(kind, kind, text.format(whole), reached))
    return {
        "title": title,
        "token": pin.check(st.header, None),
        "buffers": len(flow_graph.buffers),
        "nodes": nodes,
        "edges": flow_edges_json(flow_graph),
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
