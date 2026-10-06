"""``/api/progress/shards`` and ``/api/progress/sloops``: the two production boosts and who holds them.

They read the same ``WorldState`` budgets as ``power_shards`` and ``somersloops``. The
dashboard: docs/frontend_vision.md §8. Handler names are operation_ids (wire rule 1 of
docs/web-wire.md).
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata.constants import CAPABILITY_SCHEMATICS, max_clock
from ....domain.world.state import WorldState
from ..serial import ItemAmount, item_amounts, require_world, xyz_m

__all__ = ["router"]

router = APIRouter(prefix="/api")

#: Where a holder the overclock records do not place is drawn: nowhere, said as two nulls.
NO_POSITION: dict[str, float | None] = {"x_m": None, "y_m": None}


class NamedAmount(TypedDict):
    name: str
    amount: float


class PlaceRow(TypedDict):
    """``place`` is carried, storage or depot."""

    place: str
    items: list[NamedAmount]


class SlugRow(TypedDict):
    item: str
    name: str
    held: float
    each: float
    shards: float


class ShardHolder(TypedDict):
    instance: str
    name: str | None
    clock: float
    slotted: int
    needed: int
    idle: int
    x_m: float | None
    y_m: float | None


class ShardsResponse(TypedDict):
    """``measured`` false means ``committed`` is unknown rather than zero."""

    measured: bool
    free: float
    craftable: float
    potential: float
    committed: int
    owned: float
    per_shard: float
    max_clock: float
    slots_per_building: int
    idle: int
    slugs: list[SlugRow]
    by_place: list[PlaceRow]
    holders: list[ShardHolder]


def _overclock_positions(st: WorldState) -> dict[str, dict[str, float | None]]:
    """Instance leaf to map position, for every machine the overclock records carry."""
    positions = {}
    for record in st.overclock.records:
        xyz = xyz_m(record.get("pos"))
        positions[str(record.get("instance", "")).rsplit(".", 1)[-1]] = {
            "x_m": xyz["x_m"],
            "y_m": xyz["y_m"],
        }
    return positions


@router.get("/progress/shards", response_model=ShardsResponse)
def progress_shards(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """The ``power_shards`` budget: free, craftable from slugs, committed, and who holds them."""
    st = require_world(request, save, world)

    budget = st.shard_budget()
    per_shard = max(budget["shard_items"].values()) if budget["shard_items"] else 0.0
    at = _overclock_positions(st)
    return {
        "measured": budget["measured"],
        "free": float(budget["free"]),
        "craftable": float(budget["craftable"]),
        "potential": float(budget["potential"]),
        "committed": int(budget["committed"]),
        "owned": float(budget["owned"]),
        "per_shard": float(per_shard),
        "max_clock": float(max_clock(per_shard)),
        "slots_per_building": int(budget["slots_per_building"]),
        "idle": sum(int(h["idle"]) for h in budget["holders"]),
        "slugs": [
            {
                "item": s["item"],
                "name": s["name"],
                "held": float(s["held"]),
                "each": float(s["each"]),
                "shards": float(s["shards"]),
            }
            for s in budget["slugs"]
        ],
        "by_place": [
            {
                "place": place,
                "items": [{"name": k, "amount": float(v)} for k, v in sorted(held.items())],
            }
            for place, held in budget["by_place"].items()
        ],
        "holders": [
            {
                "instance": h["instance"],
                "name": st.game.building_name(h["cls"]),
                "clock": float(h["clock"]),
                "slotted": int(h["slotted"]),
                "needed": int(h["needed"]),
                "idle": int(h["idle"]),
                **at.get(h["instance"], NO_POSITION),
            }
            for h in budget["holders"]
        ],
    }


class SloopHolder(TypedDict):
    """``boost`` is the plan model's multiplier, ``boost_in_save`` the save's own."""

    instance: str
    name: str
    sloops: float
    boost: float | None
    boost_in_save: float | None
    x_m: float | None
    y_m: float | None


class SloopsResponse(TypedDict):
    """``amplifier_researched`` false means no sloop can go into a machine yet.

    ``amplifier_tree_shut`` is true while that research sits in a MAM tree not opened yet, and
    ``amplifier_spoiler`` while it is both unresearched and in that shut tree.
    """

    measured: bool
    free: float
    committed: float
    owned: float
    mercer_spheres: float
    by_place: list[NamedAmount]
    amplifier_researched: bool
    amplifier_research: str | None
    amplifier_tree_shut: bool
    amplifier_spoiler: bool
    amplifier_cost: list[ItemAmount]
    holders: list[SloopHolder]


@router.get("/progress/sloops", response_model=SloopsResponse)
def progress_sloops(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """The ``somersloops`` budget: free, slotted and owned, and which machines hold them.

    With ``spoilers=0`` a spoiler amplifier research loses its name and bill.
    """
    st = require_world(request, save, world)

    budget = st.sloop_budget()
    gate = st.research_gate("production_boost")
    shut = st.research.tree_locked(CAPABILITY_SCHEMATICS["production_boost"])
    spoiler = gate is not None and shut
    hide = spoiler and spoilers is False
    at = _overclock_positions(st)
    return {
        "measured": budget["committed_measured"],
        "free": float(budget["free"]),
        "committed": float(budget["committed"]),
        "owned": float(budget["owned"]),
        "mercer_spheres": float(budget["mercer_spheres"]),
        "by_place": [{"name": k, "amount": float(v)} for k, v in budget["by_place"].items()],
        "amplifier_researched": gate is None,
        "amplifier_research": gate["schematic_name"] if gate and not hide else None,
        "amplifier_tree_shut": shut,
        "amplifier_spoiler": spoiler,
        "amplifier_cost": item_amounts(st.game, ((r["item"], r["need"]) for r in gate["cost"]))
        if gate and not hide
        else [],
        "holders": [
            {
                "instance": h["instance"],
                "name": h["name"],
                "sloops": float(h["sloops"]),
                "boost": None if h["boost"] is None else float(h["boost"]),
                "boost_in_save": None if h["boost_in_save"] is None else float(h["boost_in_save"]),
                **at.get(h["instance"], NO_POSITION),
            }
            for h in budget["holders"]
        ],
    }
