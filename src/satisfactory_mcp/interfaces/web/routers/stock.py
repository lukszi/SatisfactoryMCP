"""``/api/stock``: the ``stock``, ``storage`` and ``crates`` tools' one domain object as rows.

Per-item piles come from ``Inventory.breakdown`` and per-place rows from
``Inventory.holdings``, the calls the three MCP tools make. The dashboard's Inventory section
reads it: docs/frontend_vision.md §10. Wire rules: docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from math import hypot
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.spatial import regions as spatial_regions
from ....domain.world.inventory import CRATE_KIND_TEXT, Holding
from ..serial import Region, cm_to_m, error_response, region_json, world_state, xyz_m

__all__ = ["router"]

router = APIRouter(prefix="/api")


class StockPile(TypedDict):
    """One item's piles. ``spendable`` is carried + storage + depot; fluids are m3."""

    item: str
    name: str
    fluid: bool
    spendable: float
    carried: float
    storage: float
    depot: float
    buffers: float
    crates: float


class PlaceItem(TypedDict):
    item: str
    name: str
    amount: float


class StockPlace(TypedDict):
    """A container, fluid buffer or crate. ``source`` is ``storage`` or ``crate``."""

    source: str
    kind: str
    instance_leaf: str
    cls: str
    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    region: Region | None
    distance_m: float | None
    items: list[PlaceItem]
    total: float
    slots: int | None
    slots_used: int | None
    fill: float | None
    capacity_m3: float | None
    crate_kind: str | None
    crate_kind_text: str | None


class StockCensus(TypedDict):
    containers: int
    solid: int
    fluid: int
    filled: int
    crates: int
    deaths: int


class StockPlayer(TypedDict):
    x_m: float | None
    y_m: float | None
    z_m: float | None


class StockResponse(TypedDict):
    items: list[StockPile]
    places: list[StockPlace]
    census: StockCensus
    player: StockPlayer


def _place(st, rmap, me, h: Holding) -> StockPlace:
    g = st.game
    region = None
    distance = None
    if h.pos is not None:
        if rmap is not None:
            region = region_json(rmap.label_for(h.pos[0], h.pos[1]))
        if me is not None:
            distance = cm_to_m(hypot(h.pos[0] - me[0], h.pos[1] - me[1]))
    return {
        "source": h.source,
        "kind": h.kind,
        "instance_leaf": h.instance,
        "cls": h.cls,
        "name": g.building_name(h.cls) or h.cls,
        **xyz_m(h.pos),
        "region": region,
        "distance_m": distance,
        "items": [{"item": i, "name": g.item_name(i), "amount": n} for i, n in h.items],
        "total": h.total,
        "slots": h.slots,
        "slots_used": h.slots_used,
        "fill": None if h.fill is None else round(h.fill, 4),
        "capacity_m3": h.capacity_m3,
        "crate_kind": h.crate_kind,
        "crate_kind_text": CRATE_KIND_TEXT.get(h.crate_kind) if h.crate_kind else None,
    }


@router.get("/stock", response_model=StockResponse)
def stock(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every item held, split into piles, and every place holding something, biggest first."""
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)

    g = st.game
    inv = st.inventory
    breakdown = inv.breakdown()
    ordered = sorted(breakdown.items(), key=lambda kv: (-kv[1]["spendable"], -kv[1]["machine"]))
    items = [
        {
            "item": i,
            "name": g.item_name(i),
            "fluid": bool(getattr(g.items.get(i), "is_fluid", False)),
            "spendable": v["spendable"],
            "carried": v["player"],
            "storage": v["storage"],
            "depot": v["depot"],
            "buffers": v["machine"],
            "crates": v["crate"],
        }
        for i, v in ordered
    ]

    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError:
        rmap = None
    me = st.player_position()
    holdings = inv.holdings()
    places = [_place(st, rmap, me, h) for h in holdings]
    containers = [h for h in holdings if h.source == "storage"]
    solids = [h for h in containers if h.kind == "solid"]
    crates = [h for h in holdings if h.source == "crate"]
    return {
        "items": items,
        "places": places,
        "census": {
            "containers": len(containers),
            "solid": len(solids),
            "fluid": len(containers) - len(solids),
            "filled": sum(1 for h in solids if h.total),
            "crates": len(crates),
            "deaths": sum(1 for h in crates if h.crate_kind == "death"),
        },
        "player": xyz_m(me),
    }
