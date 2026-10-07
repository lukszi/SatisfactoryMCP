"""The shapes this package builds: machine states counted, tallies, labels and reviews.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = [
    "BalanceRow",
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
