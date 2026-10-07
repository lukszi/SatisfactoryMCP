"""What a node yields and who taps it: rates, occupancy, reachability and free capacity."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, cast

from typing_extensions import TypedDict

from ....core.gamedata.model import Building, GameData
from ....core.saveio.records import instance_leaf
from ....core.saveio.schema import ExtractorRecord, Position, SaveHeader
from .. import geo
from . import skew as node_skew
from . import table as node_table
from .table import EXTRACTOR_FOR_KIND, GEYSER_CONSUMER, SUPPORT_BUILDINGS_FOR_KIND, NodeRecord

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = [
    "AnnotatedNode",
    "MeasuredNode",
    "Occupant",
    "UnresolvedExtractor",
    "annotate",
    "annotate_for_save",
    "blocking_buildings",
    "can_extract",
    "extractor_records",
    "measure_from",
    "node_rate",
    "occupancy_by_node",
    "reachable",
    "unresolved_extractors",
    "untapped_rate",
]


class Occupant(TypedDict):
    """The extractor standing on a node, as the save records it."""

    extractor: str
    instance: str | None
    clock: float
    paused: bool
    pos: Position | None


class AnnotatedNode(NodeRecord):
    """A node row with what it yields, its grid cell, its occupant and its reachability.

    ``tapped_*`` are null on a free node.
    """

    rate: float
    grid: str
    tapped: bool
    tapped_by: str | None
    tapped_clock: float | None
    tapped_paused: bool | None
    tapped_instance: str | None
    tapped_pos: Position | None
    reachable: bool


class MeasuredNode(AnnotatedNode):
    """An annotated node row and its distance from the place it was measured from."""

    distance_m: float


class UnresolvedExtractor(ExtractorRecord):
    """An extractor the node join could not place, and why."""

    reason: str


def extractor_records(projection: Mapping[str, object]) -> list[ExtractorRecord]:
    """The projection's extractors. ``Mapping`` because ``WorldState.projection`` is not a
    ``Projection`` yet; the sidecar read has checked the schema."""
    return cast("list[ExtractorRecord]", projection.get("extractors", []))


def can_extract(building: Building | None, resource_id: str, game: GameData) -> bool:
    """Whether this extractor could tap this resource, unlocks aside.

    ``mAllowedResources`` decides where it is populated, else ``mAllowedResourceForms``
    (RF_SOLID on miners, RF_LIQUID on pumps); a building stating neither takes solids only.
    """
    if building is None or not building.base_extract_rate:
        return False
    if building.allowed_resources:
        return resource_id in building.allowed_resources
    item = game.items.get(resource_id)
    if building.allowed_forms:
        return item is not None and item.form in building.allowed_forms
    return item is not None and not item.is_fluid


def node_rate(node: NodeRecord, game: GameData | None, extractor_cls: str | None = None) -> float:
    """Extraction rate for one node at 100% clock, in items/min or m3/min.

    Uses the best extractor the player could place unless one is named. Geysers have
    no extractor -- they are consumed by a Geothermal Generator instead.
    """
    kind = node["kind"]
    if kind == "geyser" or game is None:
        return 0.0
    candidates = (extractor_cls,) if extractor_cls else EXTRACTOR_FOR_KIND.get(kind, ())
    best = 0.0
    for cls in candidates:
        building = game.buildings.get(cls)
        if building is not None and can_extract(building, node["resource"], game):
            best = max(best, building.extract_rate(node["purity"]))
    return best


def occupancy_by_node(projection: Mapping[str, object]) -> dict[str, Occupant]:
    """node instanceName -> the extractor sitting on it.

    Resolution is PARTIAL: water pumps point at FGWaterVolume objects, which are not node
    keys, and some miners carry no mExtractableResource property at all -- only 40 of the
    reference save's 66 extractors resolve. An absent link is UNKNOWN, never free.
    """
    out: dict[str, Occupant] = {}
    for extractor in extractor_records(projection):
        node = extractor.get("node")
        if not node:
            continue
        out[node] = {
            "extractor": extractor["cls"],
            "instance": extractor.get("instance"),
            "clock": extractor.get("clock", 1.0),
            "paused": extractor.get("paused", False),
            "pos": extractor.get("pos"),
        }
    return out


def unresolved_extractors(projection: Mapping[str, object]) -> list[UnresolvedExtractor]:
    """Extractors whose node could not be resolved, so capacity is uncertain."""
    table = node_table.load_nodes().by_instance()
    # A node the newer build renamed lands here too, and "target not a node" is the wrong
    # diagnosis: the target IS a node, it is this table that is behind.
    skew = node_skew.skew_for_save(cast("SaveHeader | None", projection.get("header")))
    was = {instance_leaf(new): old for old, new in (skew.renamed_to.items() if skew else ())}
    out: list[UnresolvedExtractor] = []
    for extractor in extractor_records(projection):
        node = extractor.get("node")
        if node is None:
            out.append({**extractor, "reason": "no mExtractableResource property"})
        elif node not in table:
            leaf = instance_leaf(node)
            old = was.get(leaf)
            reason = (
                f"node renamed by a game update after this table was cut "
                f"(this table calls it {instance_leaf(old)})"
                if old
                else f"target not a node ({leaf})"
            )
            out.append({**extractor, "reason": reason})
    return out


def reachable(node: NodeRecord, unlocked_buildings: set[str] | None) -> bool:
    """Whether the player can actually exploit this node yet.

    Without it a node table counts resource-well satellites needing a Pressurizer the player
    has not unlocked, which overstates crude on the reference save by 1,080 of 5,040 m3/min.
    """
    if unlocked_buildings is None:
        return True
    kind = node["kind"]
    if kind == "geyser":
        return GEYSER_CONSUMER in unlocked_buildings
    if not set(SUPPORT_BUILDINGS_FOR_KIND.get(kind, ())) <= unlocked_buildings:
        return False
    return any(cls in unlocked_buildings for cls in EXTRACTOR_FOR_KIND.get(kind, ()))


def blocking_buildings(
    node: NodeRecord, game: GameData, unlocked_buildings: set[str] | None
) -> tuple[str, ...]:
    """Building classes that must be unlocked before this node yields anything.

    Empty when nothing is in the way. Sharper than `reachable`, which asks only whether an
    extractor of the right KIND is unlocked: an unlocked Miner Mk2 makes a crude oil node
    read as reachable while nothing on the map can pump it.
    """
    if unlocked_buildings is None:
        return ()
    kind = node["kind"]
    if kind == "geyser":
        return () if GEYSER_CONSUMER in unlocked_buildings else (GEYSER_CONSUMER,)
    options = tuple(
        cls
        for cls in EXTRACTOR_FOR_KIND.get(kind, ())
        if can_extract(game.buildings.get(cls), node["resource"], game)
    )
    missing = () if any(cls in unlocked_buildings for cls in options) else options
    support = tuple(
        c for c in SUPPORT_BUILDINGS_FOR_KIND.get(kind, ()) if c not in unlocked_buildings
    )
    return missing + support


def annotate(
    nodes: Iterable[NodeRecord],
    game: GameData,
    projection: Mapping[str, object] | None = None,
    unlocked_buildings: set[str] | None = None,
) -> list[AnnotatedNode]:
    """Attach rate, grid cell, occupancy and reachability to node rows.

    The whole occupancy record travels, not a boolean: which extractor stands there, at
    what clock, whether it is switched off and where it is are what "is this node worth
    reclaiming" is answered from, and ``occupancy_by_node`` computes all of it anyway.
    """
    occupied: dict[str, Occupant] = occupancy_by_node(projection) if projection else {}
    out: list[AnnotatedNode] = []
    for node in nodes:
        taken = occupied.get(node["instance"])
        out.append(
            {
                **node,
                "rate": node_rate(node, game),
                "grid": geo.grid_cell(node["x"], node["y"]),
                "tapped": taken is not None,
                "tapped_by": taken["extractor"] if taken else None,
                "tapped_clock": taken["clock"] if taken else None,
                "tapped_paused": taken["paused"] if taken else None,
                "tapped_instance": taken["instance"] if taken else None,
                "tapped_pos": taken["pos"] if taken else None,
                "reachable": reachable(node, unlocked_buildings),
            }
        )
    return out


def annotate_for_save(
    nodes: Iterable[NodeRecord], game: GameData, st: WorldState | None
) -> list[AnnotatedNode]:
    """``annotate`` against a world state, or as all free and reachable with ``st`` None."""
    return annotate(
        nodes,
        game,
        st.projection if st else None,
        st.unlocked_building_ids if st else None,
    )


def measure_from(rows: Iterable[AnnotatedNode], origin: tuple[float, float]) -> list[MeasuredNode]:
    """``rows``, each with its planar ``distance_m`` from ``origin`` (cm)."""
    return [{**row, "distance_m": geo.distance_m((row["x"], row["y"]), origin)} for row in rows]


def untapped_rate(rows: Iterable[AnnotatedNode]) -> float:
    """Total rate of the rows no extractor stands on and the player can reach: unreachable
    capacity is not a plan."""
    return sum(
        row.get("rate", 0.0) for row in rows if not row.get("tapped") and row.get("reachable", True)
    )
