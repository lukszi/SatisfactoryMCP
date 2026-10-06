"""``/api/stock``: the ``stock``, ``storage`` and ``crates`` tools' one domain object as rows.

Per-item piles come from ``Inventory.breakdown`` and per-place rows from
``Inventory.holdings``, the calls the three MCP tools make. The dashboard's Inventory section
reads it: docs/frontend_vision.md §10. Wire rules: docs/web-wire.md.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from math import hypot
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from .....domain.world.inventory import CRATE_KIND_TEXT, Holding
from ...serial import Region, cm_to_m, region_json, regions_or_none, require_world, xyz_m

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


def _place(st, region_map, player_xy, holding: Holding) -> StockPlace:
    game = st.game
    region = None
    distance = None
    pos = holding.pos
    if pos is not None:
        if region_map is not None:
            region = region_json(region_map.label_for(pos[0], pos[1]))
        if player_xy is not None:
            distance = cm_to_m(hypot(pos[0] - player_xy[0], pos[1] - player_xy[1]))
    return {
        "source": holding.source,
        "kind": holding.kind,
        "instance_leaf": holding.instance,
        "cls": holding.cls,
        "name": game.building_name(holding.cls) or holding.cls,
        **xyz_m(pos),
        "region": region,
        "distance_m": distance,
        "items": [
            {"item": item, "name": game.item_name(item), "amount": amount}
            for item, amount in holding.items
        ],
        "total": holding.total,
        "slots": holding.slots,
        "slots_used": holding.slots_used,
        "fill": None if holding.fill is None else round(holding.fill, 4),
        "capacity_m3": holding.capacity_m3,
        "crate_kind": holding.crate_kind,
        "crate_kind_text": (
            CRATE_KIND_TEXT.get(holding.crate_kind) if holding.crate_kind else None
        ),
    }


def _pile_row(game, item: str, piles: dict) -> StockPile:
    return {
        "item": item,
        "name": game.item_name(item),
        "fluid": bool(getattr(game.items.get(item), "is_fluid", False)),
        "spendable": piles["spendable"],
        "carried": piles["player"],
        "storage": piles["storage"],
        "depot": piles["depot"],
        "buffers": piles["machine"],
        "crates": piles["crate"],
    }


@router.get("/stock", response_model=StockResponse)
def stock(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every item held, split into piles, and every place holding something, biggest first."""
    st = require_world(request, save, world)

    inventory = st.inventory
    ordered = sorted(
        inventory.breakdown().items(), key=lambda kv: (-kv[1]["spendable"], -kv[1]["machine"])
    )
    items = [_pile_row(st.game, item, piles) for item, piles in ordered]

    region_map = regions_or_none()
    player_xy = st.player_position()
    holdings = inventory.holdings()
    places = [_place(st, region_map, player_xy, holding) for holding in holdings]
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
        "player": xyz_m(player_xy),
    }
