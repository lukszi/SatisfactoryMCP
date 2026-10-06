"""The payload shapes more than one router sends, and the one builder each shape has."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, TypedDict

from ....core.gamedata.model import GameData, pretty_class
from ....domain.factories import identity as fidentity
from ....domain.planning.planlog import Actor
from ....domain.spatial import regions as spatial_regions
from ....domain.world.state import WorldState
from .units import cm_to_m, instance_leaf, xyz_m

__all__ = [
    "ActorBody",
    "Biomass",
    "CollectibleRow",
    "Flow",
    "FoundField",
    "ItemAmount",
    "MachineSpot",
    "PlanOpBody",
    "Region",
    "TableAge",
    "actor_json",
    "collectible_json",
    "flow_json",
    "found_field_json",
    "item_amounts",
    "machine_spots",
    "region_json",
    "regions_or_none",
    "resource_name",
    "settings_json",
]


#: ``?biomass=`` on every route that returns a power ledger: whether hand-fed biomass burners
#: count as generation. Absent means exclude; see ``PowerLedger.power_report``.
Biomass = Literal["exclude", "include"]


class Region(TypedDict):
    """What ``_label_json`` sends: a region lookup that never arrives without its doubt.

    Declared here rather than in a router because ``_label_json`` builds it for two of them,
    ``/api/nodes`` and ``/api/inspect``, which must publish one schema and not two.

    ``name`` is not nullable and the field is not optional: the whole dict is ``None`` for
    ocean and off-map, which is ``_label_json``'s refusal and this layer must not soften it.
    """

    name: str
    confidence: str
    accuracy_m: int
    certain: bool
    text: str


def region_json(label: spatial_regions.Label) -> Region | None:
    """A region lookup as JSON, or ``None`` for ocean and off-map.

    ``None`` rather than a nearest-land guess: a page that printed the closest biome for a
    click in the sea would read like a measurement.
    """
    if label.name is None:
        return None
    return {
        "name": label.name,
        "confidence": label.confidence,
        "accuracy_m": label.accuracy_m,
        "certain": label.certain,
        "text": label.describe(),
    }


def regions_or_none() -> spatial_regions.RegionMap | None:
    """The region raster, or ``None`` on a machine that has none to read."""
    try:
        return spatial_regions.load_regions()
    except FileNotFoundError:
        return None


class TableAge(TypedDict):
    """Whether a shipped map table is older than the save; built by the domain's ``table_age``.

    ``moved`` and ``unjoinable`` count rows in the reply they travel with (nodes only);
    ``observed_from``/``observed_matches`` are the collectible table's (null for nodes).
    """

    table: Literal["nodes", "collectibles"]
    behind: bool
    gap: str | None
    moved: int
    unjoinable: int
    observed_from: str | None
    observed_matches: bool | None
    notes: list[str]


class CollectibleRow(TypedDict):
    """One map placement, and what this save says about it.

    Here because ``/api/collectibles`` and ``/api/inspect`` both send placements. The three
    coordinates come off the generated placement table and are never null.

    ``observed`` is the placement table's scan of every save on disk rather than of the
    loaded one, and it is null both for a row this save has collected and for a state this
    build does not know. ``distance_m`` is set only where an origin was resolved.

    ``looted`` is a pod's own ``mHasBeenLooted``, and null means one thing: no loot flag was
    read for this placement. Only ``crashed_drop_pod`` writes one, and only a save that had
    the pod loaded records it, so ``looted`` is non-null exactly on a pod whose ``observed``
    is ``"standing"``. **Null is never "not looted"** -- that is ``false``.

    ``spoiler`` is true for a category this save has never collected one of; pods and loot
    caches never are.
    """

    category: str
    name: str
    x_m: float
    y_m: float
    z_m: float
    collected: bool
    observed: str | None
    looted: bool | None
    distance_m: float | None
    spoiler: bool


def collectible_json(row: dict, spoiler: bool) -> dict:
    return {
        "category": row["category"],
        "name": row["name"],
        **xyz_m(row["pos"]),
        "collected": row["collected"],
        "observed": row["observed"],
        "looted": row["looted"],
        "distance_m": row.get("distance_m"),
        "spoiler": spoiler,
    }


class FoundField(TypedDict):
    """A cluster of nodes within 200 m of each other; ``key`` is stable across saves.

    ``free`` is untapped and reachable capacity; ``locked`` says some member no unlocked
    extractor can work, ``spoiler`` that none can. ``distance_m`` is to the nearest member
    and is null where nothing was measured from.
    """

    key: str
    selector: str
    members: list[str]
    region: str | None
    grid: str
    direction: str
    x_m: float
    y_m: float
    bbox_m: tuple[float, float, float, float]
    size: int
    purities: dict[str, int]
    resources: list[str]
    total: float
    free: float
    spread_m: float
    locked: bool
    distance_m: float | None
    spoiler: bool


def found_field_json(found_field: Any, game: GameData | None) -> FoundField:
    x0, y0, x1, y1 = found_field.bbox
    return {
        "key": found_field.key,
        "selector": found_field.selector,
        "members": [instance_leaf(member["instance"]) for member in found_field.members],
        "region": found_field.region,
        "grid": found_field.grid,
        "direction": found_field.direction,
        "x_m": cm_to_m(found_field.centroid[0]),
        "y_m": cm_to_m(found_field.centroid[1]),
        "bbox_m": (cm_to_m(x0), cm_to_m(y0), cm_to_m(x1), cm_to_m(y1)),
        "size": found_field.size,
        "purities": dict(found_field.purities),
        "resources": [resource_name(game, cls) for cls in found_field.resources],
        "total": round(found_field.total, 2),
        "free": round(found_field.free, 2),
        "spread_m": round(found_field.diameter_m, 1),
        "locked": found_field.locked,
        "distance_m": found_field.distance_m,
        "spoiler": found_field.spoiler,
    }


def resource_name(game: GameData | None, cls: str) -> str:
    """A node's resource class as the words the MCP tools use: ``Desc_OreIron_C`` ->
    ``Iron Ore``.

    ``Desc_Geyser_C`` is a placement target rather than an item, so the docs dump has no entry
    for it; ``pretty_class`` is the same last resort ``building_name`` applies, and also the
    answer with no game data at all.
    """
    if game is None or cls not in game.items:
        return pretty_class(cls) or cls
    return game.item_name(cls)


class ItemAmount(TypedDict):
    item: str
    name: str
    amount: float


def item_amounts(game: GameData, pairs: Iterable[tuple[str, float]]) -> list[ItemAmount]:
    """``(item class, amount)`` pairs as rows that also carry the item's display name."""
    return [
        {"item": item, "name": game.item_name(item), "amount": float(amount)}
        for item, amount in pairs
    ]


class PlanOpBody(TypedDict, total=False):
    """One op as the log holds it; which keys are present depends on ``op`` (contract §3)."""

    op: str
    field: str
    value: Any
    item: str
    member: Any
    name: str
    was: Any


class ActorBody(TypedDict):
    """Who wrote a plan commit or a journal entry; ``display`` is the word the page shows."""

    kind: str
    client: str
    pid: int
    display: str


def actor_json(raw: Any) -> ActorBody:
    actor = (
        raw if isinstance(raw, Actor) else Actor.from_dict(raw if isinstance(raw, dict) else None)
    )
    return {**actor.to_dict(), "display": actor.display()}


def settings_json(view: dict) -> dict:
    """``domain.settings.read()`` as ``/api/settings`` and the ``settings`` event send it."""
    by = view.get("by")
    return {**view, "by": actor_json(by) if by else None}


class Flow(TypedDict):
    """Items per minute at nameplate: made, or for an input consumed. ``to`` is where the
    output physically ends up (``storage``, ``export``, ``sink``, ``nowhere``)."""

    name: str
    per_min: float
    to: list[str]


def flow_json(flow_graph: Any, item: str, rate: float) -> Flow:
    """One item's rate in a factory's flow graph, with where that item ends up."""
    return {
        "name": item,
        "per_min": round(rate, 2),
        "to": sorted(flow_graph.destinations.get(item, ())),
    }


class MachineSpot(TypedDict):
    """One placed machine. ``factory`` is the label that holds it, if any."""

    id: str
    building: str
    x_m: float
    y_m: float
    factory: str | None


def machine_spots(st: WorldState, machines) -> list[dict]:
    placed = fidentity.positions(st.projection)
    spots = []
    for machine in sorted(machines):
        if machine not in placed:
            continue
        held = st.labels.label_for(machine)
        cls = st.graph.cls.get(machine, "")
        spots.append(
            {
                "id": machine,
                "building": st.game.building_name(cls) or cls,
                "x_m": cm_to_m(placed[machine][0]),
                "y_m": cm_to_m(placed[machine][1]),
                "factory": held.name if held else None,
            }
        )
    return spots
