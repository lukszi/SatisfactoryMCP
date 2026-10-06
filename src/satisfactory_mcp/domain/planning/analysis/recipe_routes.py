"""Rank whole ROUTES to an item, with the consequences of each computed.

A route pins the producer of the final item, deletes its rivals and lets the LP choose
everything upstream; three solves per route give the fewest-buildings floor, the headline
yield and the buildable plan. docs/planning.md §8.10 has the method and what it cannot answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import NamedTuple

from ....core.gamedata.constants import UNLIMITED_RATE, WATER
from ....core.gamedata.model import GameData, Recipe
from ....core.gamedata.search import resolve_item
from ...world.state import WorldState
from ..solver.model import Scenario, Solution
from ..solver.optimize import solve
from ..solver.scenario import ChainScenario, chain_scenario

__all__ = [
    "PROBE_RATE",
    "Route",
    "RouteComparison",
    "compare_routes",
    "short_recipe_name",
]

#: The primary resource's rate in the headline solve, so the yield reads as a sentence a
#: player can check in game: "60 crude in, 160 Fuel out".
PROBE_RATE = 60.0

#: Above this, a resource is at its stand-in cap rather than at a real optimum.
_AT_CAP = UNLIMITED_RATE * 0.5

_ALTERNATE_PREFIX = "Alternate: "

_EPS = 1e-7


@dataclass
class Byproduct:
    item: str
    name: str
    rate: float
    #: sink | export. A fluid is never "sink": the AWESOME Sink takes a conveyor only.
    outlet: str
    is_fluid: bool


@dataclass
class Route:
    recipe: str
    name: str
    status: str  # optimal | infeasible | unbounded
    #: Target output for PROBE_RATE of the primary resource. The headline.
    yield_per_probe: float = 0.0
    per_unit: float = 0.0  # primary resource consumed per unit of target
    raw_per_unit: dict[str, float] = field(default_factory=dict)
    machines: int = 0
    #: Fewest whole buildings for the same output, ignoring raw.
    machines_floor: int = 0
    floor_per_unit: float = 0.0
    mw: float = 0.0  # exact draw at the derived clock, negative
    #: Net MW per unit of the primary resource once the output is burnt in the best
    #: unlocked generator. None when the target is not a fuel.
    power_yield: float | None = None
    byproducts: list[Byproduct] = field(default_factory=list)
    build_first: list[str] = field(default_factory=list)
    upstream: list[str] = field(default_factory=list)
    upstream_ids: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


@dataclass
class RouteComparison:
    item: str
    item_name: str
    rate: float
    primary: str
    primary_name: str
    primary_unit: str
    routes: list[Route]
    generator: str | None = None
    generator_mw_per_unit: float = 0.0
    generator_water_m3_min: float = 0.0
    allow_sinks: bool = True
    outlets: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    solves: int = 0

    @property
    def feasible(self) -> list[Route]:
        return [r for r in self.routes if r.ok]


class _Generator(NamedTuple):
    cls: str | None
    mw_per_unit: float
    water_per_unit: float


def short_recipe_name(name: str) -> str:
    """``Alternate: Diluted Fuel`` -> ``Alt Diluted Fuel``: the shared prefix is pure length."""
    if name.startswith(_ALTERNATE_PREFIX):
        return "Alt " + name[len(_ALTERNATE_PREFIX) :]
    return name


def _best_generator(game: GameData, state: WorldState, item: str) -> _Generator:
    """The unlocked generator that pays most per unit of ``item``: power the player could
    actually collect."""
    fuel = game.items.get(item)
    best = _Generator(None, 0.0, 0.0)
    if fuel is None or not fuel.energy_mj:
        return best
    for building_cls, building in game.buildings.items():
        if not building.is_generator or building_cls not in state.unlocked_building_ids:
            continue
        if not any(f.fuel_class == item for f in building.fuels):
            continue
        per_min = building.fuel_rate_per_min(fuel)
        if per_min <= 0:
            continue
        mw = building.power_production_mw / per_min
        if mw > best.mw_per_unit:
            water = (
                building.supplemental_m3_min() / per_min if building.requires_supplemental else 0.0
            )
            best = _Generator(building_cls, mw, water)
    return best


def _unlocked_producers(
    game: GameData, state: WorldState, target: str, scenario: Scenario, max_routes: int
) -> tuple[list[Recipe], bool]:
    """Part recipes making ``target`` this save can run, by name, and whether more were cut."""
    producers = [
        r
        for r in game.producers_of(target, "part")
        if r.cls in state.available_recipe_ids
        and (r.machine is None or r.machine in scenario.buildings_available)
    ]
    producers.sort(key=lambda r: r.name)
    return producers[:max_routes], len(producers) > max_routes


def _route_scenario(
    chain: ChainScenario, recipe_id: str, rivals: set[str], **overrides
) -> Scenario:
    """The chain with every rival producer deleted but ``recipe_id``: one route, never a blend."""
    return replace(
        chain.scenario,
        recipes=[r for r in chain.scenario.recipes if r == recipe_id or r not in rivals],
        **overrides,
    )


def _pick_primary(solutions: list[Solution], fallback: str = WATER) -> str:
    """The resource most routes spend, water aside; one denominator for the whole table."""
    counts: dict[str, int] = {}
    mass: dict[str, float] = {}
    for sol in solutions:
        if not sol.ok:
            continue
        for item, used in sol.raw_used.items():
            if item == WATER or used <= _EPS:
                continue
            counts[item] = counts.get(item, 0) + 1
            mass[item] = mass.get(item, 0.0) + used
    if not counts:
        return fallback
    return max(counts, key=lambda k: (counts[k], mass[k]))


def _choose_primary(
    game: GameData,
    per_resource: str | None,
    raw_caps: dict[str, float],
    floors: dict[str, Solution],
    item_name: str,
) -> str:
    """``per_resource`` when it names a raw resource, else the one the routes share."""
    primary = per_resource and (resolve_item(game, per_resource) or per_resource)
    if primary and primary not in raw_caps:
        # A manufactured part as a free raw input would make every route look absurdly cheap.
        raise ValueError(f"{per_resource!r} is not a raw resource for {item_name}")
    return primary or _pick_primary(list(floors.values()))


def _unreachable_inputs(
    game: GameData, recipe_ids: list[str], raws: set[str], pinned_id: str
) -> list[str]:
    """Ingredients of the pinned recipe that nothing in its route can supply.

    A least fixpoint from the raws outward, so a cycle with no entry never enters. Necessary
    only, so it explains an infeasible route and never predicts one.
    """
    recipes = [r for r in (game.recipes.get(i) for i in recipe_ids) if r and r.kind == "part"]
    have = set(raws)
    changed = True
    while changed:
        changed = False
        for r in recipes:
            if all(f.item in have for f in r.ingredients):
                for f in r.products:
                    if f.item not in have:
                        have.add(f.item)
                        changed = True
    pinned = game.recipes[pinned_id]
    return [game.item_name(f.item) for f in pinned.ingredients if f.item not in have]


def _byproducts(game: GameData, sol: Solution, target: str) -> list[Byproduct]:
    out: list[Byproduct] = []
    flows = [(i, r, "sink") for i, r in sol.sunk.items()]
    flows += [(i, r, "export") for i, r in sol.exports.items() if i != target]
    for item, rate, outlet in flows:
        # A 4 dp residue would print as a byproduct of "0" needing a belt.
        if rate <= 1e-4:
            continue
        known = game.items.get(item)
        out.append(
            Byproduct(item, game.item_name(item), rate, outlet, bool(known and known.is_fluid))
        )
    out.sort(key=lambda b: -b.rate)
    return out


def _solve_route(
    game: GameData,
    state: WorldState,
    chain: ChainScenario,
    producer: Recipe,
    rivals: set[str],
    floor: Solution,
    primary: str,
    primary_name: str,
    rate: float,
    generator: _Generator,
) -> tuple[Route, int]:
    """One route from its floor solve: the headline probe, then the buildable plan pinned
    under it. Returns the route and how many solves it took."""
    target = chain.scenario.target_item
    route = Route(recipe=producer.cls, name=producer.name, status="infeasible")
    if not floor.ok:
        missing = _unreachable_inputs(
            game,
            _route_scenario(chain, producer.cls, rivals).recipes,
            set(chain.raw_caps),
            producer.cls,
        )
        route.note = (
            f"nothing left to make {', '.join(missing)}"
            if missing
            else "a byproduct has no consumer and no legal sink"
        )
        return route, 0

    probe_caps = {**chain.raw_caps, primary: PROBE_RATE}
    probe = solve(
        _route_scenario(chain, producer.cls, rivals, objective="max_item", raw_caps=probe_caps)
    )
    at_cap = any(v >= _AT_CAP for k, v in probe.raw_used.items() if k != primary)
    if not probe.ok or probe.objective_value <= _EPS or at_cap:
        route.status = "unbounded"
        route.note = f"does not consume {primary_name}"
        route.machines = int(floor.machines_total)
        return route, 1

    route.yield_per_probe = probe.objective_value
    route.per_unit = PROBE_RATE / probe.objective_value
    # Pin the primary at what the probe reached, widened by its 4 dp rounding (§8.10).
    reachable = rate * PROBE_RATE / max(probe.objective_value - 5e-5, _EPS)
    pinned = {**chain.raw_caps, primary: reachable * (1 + 1e-6) + 1e-9}
    best = solve(
        _route_scenario(
            chain,
            producer.cls,
            rivals,
            objective="min_raw",
            raw_caps=pinned,
            export_minimums={target: rate},
        )
    )
    if not best.ok:
        route.note = "no plan at the best-case resource draw"
        return route, 2

    route.status = "optimal"
    route.raw_per_unit = {k: v / rate for k, v in best.raw_used.items() if v > _EPS}
    route.machines = int(best.machines_total)
    route.machines_floor = int(floor.machines_total)
    route.floor_per_unit = floor.raw_used.get(primary, 0.0) / rate
    route.mw = sum(row["mw"] for row in best.processes)
    route.byproducts = _byproducts(game, best, target)
    rest = [
        row
        for row in sorted(best.processes, key=lambda d: -d["machine_equivalents"])
        if row["recipe"] != producer.cls
    ]
    route.upstream = [short_recipe_name(row["label"]) for row in rest]
    route.upstream_ids = [row["recipe"] for row in rest if row["recipe"]]
    route.build_first = sorted(
        {
            game.buildings[row["building_id"]].name
            for row in best.processes
            if row["building_id"] in game.buildings and state.built(row["building_id"]) == 0
        }
    )
    if generator.mw_per_unit:
        # The LP's linear draw keeps this scale-free; whole machines would round with rate.
        route.power_yield = (rate * generator.mw_per_unit + best.net_mw) / (rate * route.per_unit)
    return route, 2


def compare_routes(
    game: GameData,
    state: WorldState,
    item: str,
    rate: float = 100.0,
    allow_sinks: bool = True,
    outlets: list[str] | None = None,
    per_resource: str | None = None,
    max_routes: int = 12,
) -> RouteComparison:
    """Compare every unlocked way of making ``item`` at ``rate`` per minute."""
    target = resolve_item(game, item)
    if target is None:
        raise ValueError(f"no item matching {item!r}")
    if rate <= 0:
        raise ValueError("rate must be positive")

    chain = chain_scenario(game, state, target, outlets=outlets or [], allow_sinks=allow_sinks)
    producers, truncated = _unlocked_producers(game, state, target, chain.scenario, max_routes)
    generator = _best_generator(game, state, target)
    comparison = RouteComparison(
        item=target,
        item_name=game.item_name(target),
        rate=rate,
        primary=WATER,
        primary_name=game.item_name(WATER),
        primary_unit="m3/min",
        routes=[],
        generator=game.buildings[generator.cls].name if generator.cls in game.buildings else None,
        generator_mw_per_unit=generator.mw_per_unit,
        generator_water_m3_min=generator.water_per_unit,
        allow_sinks=allow_sinks,
        outlets=chain.outlets,
    )
    if not producers:
        comparison.notes.append(
            f"no unlocked recipe makes {comparison.item_name} -- "
            "alternates_for_item lists the locked ones"
        )
        return comparison
    if truncated:
        comparison.notes.append(f"only the first {max_routes} producers were compared")

    rivals = {r.cls for r in producers}
    floors = {
        p.cls: solve(
            _route_scenario(
                chain, p.cls, rivals, objective="min_machines", export_minimums={target: rate}
            )
        )
        for p in producers
    }
    solves = len(producers)
    primary = _choose_primary(game, per_resource, chain.raw_caps, floors, comparison.item_name)
    comparison.primary = primary
    comparison.primary_name = game.item_name(primary)
    primary_item = game.items.get(primary)
    comparison.primary_unit = "m3/min" if primary_item and primary_item.is_fluid else "/min"

    for producer in producers:
        route, used = _solve_route(
            game,
            state,
            chain,
            producer,
            rivals,
            floors[producer.cls],
            primary,
            comparison.primary_name,
            rate,
            generator,
        )
        solves += used
        comparison.routes.append(route)

    comparison.routes.sort(
        key=lambda r: (not r.ok, r.per_unit if r.ok else 0.0, r.machines if r.ok else 0)
    )
    comparison.solves = solves
    comparison.notes.extend(_route_warnings(game, comparison))
    return comparison


def _route_warnings(game: GameData, cmp: RouteComparison) -> list[str]:
    """Warnings first, because that is where the decision is."""
    out: list[str] = []
    build = {b: r.name for r in cmp.feasible for b in r.build_first}
    for building, route in sorted(build.items()):
        out.append(f"{short_recipe_name(route)} needs a {building}: unlocked, 0 built")

    fluid = {b.name for r in cmp.feasible for b in r.byproducts if b.is_fluid}
    if fluid:
        out.append(
            f"fluid byproduct leaving the plant: {', '.join(sorted(fluid))} -- "
            "a fluid cannot be sunk, so it needs a real consumer or the line stalls"
        )
    sunk = {b.name for r in cmp.feasible for b in r.byproducts if b.outlet == "sink"}
    if sunk:
        out.append(
            f"belted to an AWESOME Sink for want of a consumer: {', '.join(sorted(sunk))} -- "
            "pass outlets=[...] to make it a product instead"
        )
    # One denominator prices one resource; the others a route eats are named (§8.10).
    others = sorted(
        {
            game.item_name(i)
            for r in cmp.feasible
            for i in r.raw_per_unit
            if i not in (cmp.primary, WATER)
        }
    )
    if others:
        more = f" and {len(others) - 4} more" if len(others) > 4 else ""
        out.append(
            f"ranked on {cmp.primary_name} alone; these routes also consume "
            + ", ".join(others[:4])
            + more
        )

    # Buildable, with no denominator here: never explained as a chain that cannot close.
    off_scale = [short_recipe_name(r.name) for r in cmp.routes if r.status == "unbounded"]
    if off_scale:
        out.append(
            f"{', '.join(off_scale)} consumes no {cmp.primary_name}, so it cannot be "
            f"ranked here -- it is buildable; re-run with per_resource=<its own input>"
        )
    if any(r.status == "infeasible" for r in cmp.routes):
        out.append(
            "pinning one producer also removes its rivals as suppliers, so an "
            "infeasible route means that chain alone cannot close -- every item "
            "balance is an equality, so a stranded byproduct stalls the line"
        )
    return out
