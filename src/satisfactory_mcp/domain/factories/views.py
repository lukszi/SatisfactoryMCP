"""The shapes this package builds: machine states counted, tallies, labels and reviews.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict

from ..planning.readout.views import PlanGraphBuilding

__all__ = [
    "BalanceRow",
    "BuiltGraph",
    "BuiltGraphEdge",
    "BuiltGraphNode",
    "FloorCounts",
    "GraphSummary",
    "LabelDoc",
    "LabelReview",
    "StateCount",
    "StructureSummary",
]


class StateCount(TypedDict):
    state: str
    count: int


class FloorCounts(TypedDict):
    """The shape of the answer before the rows. Nested; see ``FloorReport.counts``."""

    platforms: int
    bands: int
    runs: int
    violations: int
    #: Keyed by ``ffloors.GROUPS`` and ``ffloors.MEMBERSHIPS``: open maps, so the domain's
    #: two vocabularies are not restated here.
    placements: dict[str, int]
    membership: dict[str, int]


class GraphSummary(TypedDict):
    """``FactoryGraph.summary``: how big the graph is, layer by layer."""

    actors: int
    machines: int
    material_edges: int
    power_edges: int
    transport_edges: int
    hyper_edges: int
    towers: int


class StructureSummary(TypedDict):
    """``Structures.summary``: the slabs, their tiles, and the machines standing on them."""

    slabs: int
    tiles: int
    machines_on_slabs: int


class LabelDoc(TypedDict):
    """One label as the label file stores it; ``centroid`` is ``[x, y]`` in centimetres."""

    id: str
    name: str
    anchors: list[str]
    notes: str
    centroid: list[float]
    signature: dict[str, int]
    created: str
    last_matched: str
    icon: NotRequired[str]


class LabelReview(TypedDict):
    """A label whose machines are not all standing; ``status`` is ``gone``, ``needs
    confirmation`` or ``shrunk``."""

    name: str
    recall: float
    missing: int
    status: str


class BalanceRow(TypedDict):
    """One item of ``FactoryView.balance``; ``measured_net`` is ``None`` where nothing
    readable touches the item: unknown, not zero."""

    item: str
    made: float
    used: float
    net: float
    measured_net: float | None
    verdict: str


class BuiltGraphNode(TypedDict):
    """The plan graph's node (planning ``PlanGraphNode``) for a built factory. ``kind`` is
    process, input, export, leftover or sink; ``note`` qualifies an item node's rate.
    ``productivity`` is the measured mean over the ``monitored`` running machines, ``states``
    machines per health state; ``bbox_m`` is a process's machines on the map."""

    id: str
    kind: str
    label: str
    detail: str
    rank: int
    item_id: str | None
    item_name: str | None
    recipe_id: str | None
    recipe_name: str | None
    building: str | None
    building_icon: str | None
    built: int
    machines: float
    clock: float | None
    mw: float
    utilisation: float
    per_min: float | None
    unit: str
    note: str | None
    productivity: float | None
    monitored: int
    states: dict[str, int]
    bbox_m: tuple[float, float, float, float] | None


class BuiltGraphEdge(TypedDict):
    """One apportioned nameplate flow; ``text`` names an overflow path with no surplus."""

    id: str
    source: str
    target: str
    item_id: str
    item_name: str
    item_group: str
    per_min: float
    unit: str
    text: str | None


class BuiltGraph(TypedDict):
    nodes: list[BuiltGraphNode]
    edges: list[BuiltGraphEdge]
    buildings: list[PlanGraphBuilding]
    mw_draw: float
    mw_generated: float
