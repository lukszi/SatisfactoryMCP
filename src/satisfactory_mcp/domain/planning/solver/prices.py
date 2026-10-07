"""What a machine costs to build and what a MWh costs to run, read from one save.

docs/planner-payback-horizon_contract.md §3 is the specification. The prices are derived per
save and handed to the solver as plain numbers; only the last scarcity tiers are stored.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ....core import atomic, filelock, schema
from ....core.gamedata.model import GameData
from ....core.jsontypes import JsonObject, JsonValue
from ....core.saveio.records import instance_leaf
from ....core.saveio.schema import GeneratorRecord, Projection
from ...power.report import BIOMASS_BURNERS, generator_building
from ..stored.store import PlanStore
from .views import PowerSource

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState

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

TIER_SCHEMA = 1

_log = logging.getLogger(__name__)


def tiers_path(world: str) -> Path:
    """The last tiers of ``world``, beside its plan log, shared by every process (contract §3.1)."""
    return PlanStore.path_for(world).with_suffix("") / "tiers.json"


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, not {type(value).__name__}")
    return value


def _number(value: JsonValue) -> float:
    """``float(value)``, refusing what ``float`` refuses with the same ``TypeError``."""
    if not isinstance(value, str | int | float):
        raise TypeError(f"expected a number, not {type(value).__name__}")
    return float(value)


def _read_tiers(path: Path) -> dict[tuple[str, str], float]:
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
        schema.check(raw, TIER_SCHEMA, path)
        lines = _object(raw).get("tiers") or {}
        tiers: dict[tuple[str, str], float] = {}
        for key, tier in _object(lines).items():
            if "|" in key and _number(tier) in TIERS:
                building, item = key.split("|", 1)
                tiers[(building, item)] = _number(tier)
        return tiers
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, AttributeError):
        _log.warning("could not read %s; scarcity tiers start afresh", path, exc_info=True)
        return {}


def _write_tiers(path: Path, tiers: dict[tuple[str, str], float]) -> None:
    payload = {
        "schema": TIER_SCHEMA,
        "tiers": {f"{b}|{i}": t for (b, i), t in sorted(tiers.items())},
    }
    atomic.write_text(path, json.dumps(payload, ensure_ascii=False))


@dataclass(frozen=True)
class Prices:
    """One save's prices: build points per building, scarcity tiers, and the grid's price."""

    build_points: dict[str, float] = field(default_factory=dict[str, float])
    tiers: dict[tuple[str, str], float] = field(default_factory=dict[tuple[str, str], float])
    #: Points per MWh, MW-weighted over the grid's running generators.
    power_price: float = 0.0
    grid_mix: list[PowerSource] = field(default_factory=list[PowerSource])


def _sink(g: GameData, item: str) -> float:
    it = g.items.get(item)
    return float(it.sink_points) if it is not None else 0.0


def nameplate_surplus(g: GameData, projection: Projection) -> dict[str, float]:
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


def _nearest_tier(raw: float) -> float:
    return min(TIERS, key=lambda t: abs(math.log(t / raw)))


def _scarcity_tier(available: float, per_machine: float, before: float | None) -> float:
    """One build-cost line's tier; ``before`` holds within a factor of 2 (contract §3.1)."""
    if available <= 0:
        raw = TIERS[-1]
    else:
        raw = (available / (REFERENCE_MACHINES * per_machine)) ** -0.5
        raw = min(TIERS[-1], max(TIERS[0], raw))
    if before is not None and before / 2 < raw < before * 2:
        return before
    return _nearest_tier(raw)


def material_tiers(
    g: GameData, stock: dict[str, float], surplus: dict[str, float], world: str = ""
) -> dict[tuple[str, str], float]:
    """``{(building, item): tier}`` for every build-cost line of every building.

    With ``world`` the last tiers are read from and written back to ``tiers_path`` under its
    lock, so the web server and the MCP server hold the same band."""
    if not world:
        return _all_tiers(g, stock, surplus, {})
    path = tiers_path(world)
    try:
        with filelock.held(path):
            before = _read_tiers(path)
            out = _all_tiers(g, stock, surplus, before)
            if out != before:
                _write_tiers(path, out)
            return out
    except schema.NewerSchema:
        _log.warning("%s is from a newer version; scarcity tiers are not stored", path)
        return _all_tiers(g, stock, surplus, {})
    except OSError:
        _log.warning("scarcity tiers for %s not stored", world, exc_info=True)
        return _all_tiers(g, stock, surplus, _read_tiers(path))


def _all_tiers(
    g: GameData,
    stock: dict[str, float],
    surplus: dict[str, float],
    before: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for cls, b in g.buildings.items():
        for f in b.build_cost:
            if f.amount <= 0:
                continue
            available = stock.get(f.item, 0.0) + SURPLUS_MINUTES * max(
                0.0, surplus.get(f.item, 0.0)
            )
            out[(cls, f.item)] = _scarcity_tier(available, f.amount, before.get((cls, f.item)))
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
    return per_hour * float(item.sink_points) / b.power_production_mw


def _generator_source(g: GameData, rec: GeneratorRecord) -> tuple[str, float, float] | None:
    """``(source name, MW, points per MWh)`` for one generator record, None if not one."""
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


def grid_mix(
    g: GameData, projection: Projection, wired: frozenset[str] | None, biomass: bool
) -> tuple[float, list[PowerSource]]:
    """The MW-weighted running price of the grid, and its sources, largest first."""
    rows: dict[str, PowerSource] = {}
    for rec in projection.get("generators", ()):
        if rec.get("paused") or (not biomass and rec.get("cls") in BIOMASS_BURNERS):
            continue
        if wired is not None and instance_leaf(rec.get("instance", "")) not in wired:
            continue
        found = _generator_source(g, rec)
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


def prices_for(state: WorldState, biomass: bool) -> Prices:
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
        return Prices(build_points=points, tiers=tiers, power_price=price, grid_mix=mix)

    return state.derived(f"prices:{bool(biomass)}", build)
