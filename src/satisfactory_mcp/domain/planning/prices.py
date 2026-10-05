"""What a machine costs to build and what a MWh costs to run, read from one save.

docs/planner-payback-horizon_contract.md §3 is the specification. Nothing here is stored:
the prices are derived per save and handed to the solver as plain numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ..power.report import BIOMASS_BURNERS, generator_building

__all__ = [
    "AREA_POINTS_M2",
    "TIERS",
    "Prices",
    "building_points",
    "fuel_price",
    "grid_mix",
    "material_tiers",
    "nameplate_surplus",
    "prices_for",
]

TIERS = (0.25, 0.5, 1.0, 2.0, 4.0)
REFERENCE_MACHINES = 20
SURPLUS_MINUTES = 60.0
#: 1,000 points per 8 x 8 m foundation, plus that foundation's own 5 Concrete.
AREA_POINTS_M2 = 1000 / 64 + 60 / 64

#: Last tiers per world, for the hysteresis band. Process memory only.
_last_tiers: dict[str, dict[tuple[str, str], float]] = {}


@dataclass(frozen=True)
class Prices:
    points: dict[str, float] = field(default_factory=dict)
    tiers: dict[tuple[str, str], float] = field(default_factory=dict)
    price: float = 0.0
    mix: list[dict] = field(default_factory=list)


def _sink(g: GameData, item: str) -> float:
    it = g.items.get(item)
    return float(it.sink_points) if it is not None else 0.0


def nameplate_surplus(g: GameData, projection: dict) -> dict[str, float]:
    """Per-minute production minus consumption of every running machine at its clock."""
    net: dict[str, float] = {}
    for rec in projection.get("machines", ()):
        if rec.get("paused"):
            continue
        recipe = g.recipes.get(rec.get("recipe") or "")
        if recipe is None:
            continue
        clock = rec.get("clock") or 1.0
        for f in recipe.products:
            net[f.item] = net.get(f.item, 0.0) + f.per_min * clock
        for f in recipe.ingredients:
            net[f.item] = net.get(f.item, 0.0) - f.per_min * clock
    return net


def _snap(raw: float) -> float:
    return min(TIERS, key=lambda t: abs(math.log(t / raw)))


def _tier(available: float, per_machine: float, before: float | None) -> float:
    if available <= 0:
        raw = TIERS[-1]
    else:
        raw = (available / (REFERENCE_MACHINES * per_machine)) ** -0.5
        raw = min(TIERS[-1], max(TIERS[0], raw))
    if before is not None and before / 2 < raw < before * 2:
        return before
    return _snap(raw)


def material_tiers(
    g: GameData, stock: dict[str, float], surplus: dict[str, float], world: str = ""
) -> dict[tuple[str, str], float]:
    """``{(building, item): tier}`` for every build-cost line of every building."""
    before = _last_tiers.get(world, {}) if world else {}
    out: dict[tuple[str, str], float] = {}
    for cls, b in g.buildings.items():
        for f in b.build_cost:
            if f.amount <= 0:
                continue
            available = stock.get(f.item, 0.0) + SURPLUS_MINUTES * max(
                0.0, surplus.get(f.item, 0.0)
            )
            out[(cls, f.item)] = _tier(available, f.amount, before.get((cls, f.item)))
    if world:
        _last_tiers[world] = dict(out)
    return out


def building_points(g: GameData, cls: str, tiers: dict[tuple[str, str], float]) -> float:
    """Save-priced build materials plus the machine's own floor area."""
    b = g.buildings.get(cls)
    if b is None:
        return 0.0
    total = sum(f.amount * _sink(g, f.item) * tiers.get((cls, f.item), 1.0) for f in b.build_cost)
    if b.footprint is not None:
        total += b.footprint.area_m2 * AREA_POINTS_M2
    return total


def fuel_price(g: GameData, cls: str, fuel: str | None) -> float:
    """Points per MWh burnt by generator ``cls`` on ``fuel``: its sink points, untiered."""
    b = generator_building(g, cls)
    item = g.items.get(fuel or "")
    if b is None or item is None or not b.power_production_mw:
        return 0.0
    per_hour = b.fuel_rate_per_min(item) * 60
    return per_hour * _sink(g, fuel) / b.power_production_mw


def _source(g: GameData, rec: dict) -> tuple[str, float, float] | None:
    b = generator_building(g, rec.get("cls", ""))
    if b is None:
        return None
    clock = rec.get("clock") or 1.0
    mw = b.power_production_mw * clock
    if not b.power_production_mw and b.variable_power_factor:
        return "Geothermal", b.variable_power_factor * clock, 0.0
    fuel = rec.get("fuel") or (b.fuels[0].fuel_class if b.fuels else None)
    if not fuel:
        return b.name, mw, 0.0
    return g.item_name(fuel), mw, fuel_price(g, b.cls, fuel)


def grid_mix(g: GameData, projection: dict, wired, biomass: bool) -> tuple[float, list[dict]]:
    """The MW-weighted running price of the grid, and its sources, largest first."""
    rows: dict[str, dict] = {}
    for rec in projection.get("generators", ()):
        if rec.get("paused") or (not biomass and rec.get("cls") in BIOMASS_BURNERS):
            continue
        if wired is not None and rec.get("instance", "").rsplit(".", 1)[-1] not in wired:
            continue
        found = _source(g, rec)
        if found is None or found[1] <= 0:
            continue
        name, mw, price = found
        row = rows.setdefault(name, {"source": name, "mw": 0.0, "price": round(price, 1)})
        row["mw"] += mw
    total = sum(r["mw"] for r in rows.values())
    if total <= 0:
        return 0.0, []
    price = sum(r["mw"] * r["price"] for r in rows.values()) / total
    mix = sorted(rows.values(), key=lambda r: -r["mw"])
    for r in mix:
        r["mw"] = round(r["mw"], 1)
    return round(price, 1), mix


def prices_for(state, biomass: bool) -> Prices:
    """Every building's points and the grid's price for ``state``, cached per projection."""

    def build() -> Prices:
        g = state.game
        tiers = material_tiers(
            g,
            state.inventory.stock(),
            nameplate_surplus(g, state.projection),
            world=state.world_id,
        )
        points = {
            cls: building_points(g, cls, tiers)
            for cls, b in g.buildings.items()
            if b.build_cost or b.footprint is not None
        }
        price, mix = grid_mix(g, state.projection, state.power.wired, biomass)
        return Prices(points=points, tiers=tiers, price=price, mix=mix)

    return state._derived(f"prices:{bool(biomass)}", build)
