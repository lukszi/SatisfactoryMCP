"""The serialisation vocabulary every JSON endpoint speaks, and the three conventions.

* **Metres, one decimal.** The save stores centimetres; every coordinate that leaves this
  layer has been divided by 100 and rounded, exactly as the text presenters do.
* **``?save=`` and ``?world=`` wherever a state is read**, so a page can pin itself to one
  save while the game keeps autosaving over another.
* **An error is ``{"error": "..."}`` with a 4xx**, never a 200 with an empty list: a browser
  that cannot tell "no nodes" from "no save" draws an empty map and says nothing.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import Request
from fastapi.responses import JSONResponse

from ...core.gamedata.model import GameData, pretty_class
from ...domain.planning.planlog import Actor
from ...domain.spatial import regions as spatial_regions
from ...domain.world.state import WorldState

__all__ = [
    "ActorBody",
    "Biomass",
    "CollectibleRow",
    "FoundField",
    "Region",
    "TableAge",
    "_actor_json",
    "_fail",
    "_field_json",
    "_label_json",
    "_m",
    "_pickup_json",
    "_resource_name",
    "_state",
    "_xyz",
    "_yaw",
]


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


def _pickup_json(row: dict, spoiler: bool) -> dict:
    return {
        "category": row["category"],
        "name": row["name"],
        **_xyz(row["pos"]),
        "collected": row["collected"],
        "observed": row["observed"],
        "looted": row["looted"],
        "distance_m": round(row["distance_m"], 1) if row.get("distance_m") is not None else None,
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


def _field_json(f: Any, game: GameData | None) -> FoundField:
    x0, y0, x1, y1 = f.bbox
    return {
        "key": f.key,
        "selector": f.selector,
        "members": [str(m["instance"]).rsplit(".", 1)[-1] for m in f.members],
        "region": f.region,
        "grid": f.grid,
        "direction": f.direction,
        "x_m": _m(f.centroid[0]),
        "y_m": _m(f.centroid[1]),
        "bbox_m": (_m(x0), _m(y0), _m(x1), _m(y1)),
        "size": f.size,
        "purities": dict(f.purities),
        "resources": [_resource_name(game, r) for r in f.resources],
        "total": round(f.total, 2),
        "free": round(f.free, 2),
        "spread_m": round(f.diameter_m, 1),
        "locked": f.locked,
        "distance_m": None if f.distance_m is None else round(f.distance_m, 1),
        "spoiler": f.spoiler,
    }


class ActorBody(TypedDict):
    """Who wrote a plan commit or a journal entry; ``display`` is the word the page shows."""

    kind: str
    client: str
    pid: int
    display: str


def _actor_json(raw: Any) -> ActorBody:
    actor = (
        raw if isinstance(raw, Actor) else Actor.from_dict(raw if isinstance(raw, dict) else None)
    )
    return {**actor.to_dict(), "display": actor.display()}


def _m(value: float | None) -> float | None:
    """Centimetres to metres, one decimal. The unit rule, in one place."""
    return None if value is None else round(float(value) / 100.0, 1)


def _xyz(pos: Any) -> dict[str, float | None]:
    """A projection ``pos`` triple as named metre fields."""
    if not pos:
        return {"x_m": None, "y_m": None, "z_m": None}
    p = list(pos) + [None, None, None]
    return {"x_m": _m(p[0]), "y_m": _m(p[1]), "z_m": _m(p[2])}


def _yaw(value: Any) -> float | None:
    """A placement's rotation about world Z, degrees, one decimal.

    Positive turns +X towards +Y, so it is directly comparable with ``atan2(dy, dx)`` over
    two ``pos`` values.

    ``None``, never 0.0, when the projection carries no yaw: schema 12 added the field, and
    an absent one means "this projection predates it", which is a different claim from "this
    thing is axis-aligned".
    """
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


#: ``?biomass=`` on every route that returns a power ledger: whether hand-fed biomass burners
#: count as generation. Absent means exclude; see ``PowerLedger.power_report``.
Biomass = Literal["exclude", "include"]


def _fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _state(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about. Raises whatever the loader raises."""
    return request.app.state.load_state(save, world)


def _resource_name(game: GameData | None, cls: str) -> str:
    """A node's resource class as the words the MCP tools use: ``Desc_OreIron_C`` ->
    ``Iron Ore``.

    Here rather than in a router because ``/api/nodes`` and ``/api/inspect`` both name a
    resource, and two spellings for one fact is the page contradicting itself at two clicks.

    ``Desc_Geyser_C`` is a placement target rather than an item, so the docs dump has no entry
    for it and ``item_name`` would hand the class id back; ``pretty_class`` is the same last
    resort ``building_name`` already applies -- and it is also the answer with no game data
    at all, so a machine without the install still gets a readable word rather than a 500.
    """
    if game is None or cls not in game.items:
        return pretty_class(cls) or cls
    return game.item_name(cls)


def _label_json(label: spatial_regions.Label) -> Region | None:
    """A region lookup as JSON, or ``None`` for ocean and off-map.

    ``None`` rather than a nearest-land guess: that is ``label_for``'s own refusal, and a
    page that printed the closest biome for a click in the sea would read like a measurement.

    The confidence word travels with the name because the raster is 256 m per cell, so
    "Northern Forest, boundary" and "Northern Forest, interior" are different claims.
    ``certain`` is the domain's own reading of that word, so the page need not know the four
    codes.
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
