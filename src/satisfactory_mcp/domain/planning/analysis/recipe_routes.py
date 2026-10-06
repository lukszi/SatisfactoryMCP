"""Rank whole ROUTES to an item, with the consequences of each computed.

``alternates_for_item`` answers "which recipes make this". This module answers the
harder question: given the recipes this save has unlocked, what does each way of
making the thing actually COST, end to end -- raw per unit, buildings, power,
byproducts that need an outlet, and machine types the player owns but has never
placed.

Enumeration: ONE PINNED PRODUCER PER ROUTE
------------------------------------------
A route is not a recipe, it is a sub-chain: the interesting comparison is
``Crude -> Alt HOR -> Diluted Fuel`` against ``Crude -> Fuel``, not one recipe
against another. So a route is defined by pinning the recipe that makes the final
item and deleting its RIVALS from the recipe set, then letting the LP choose
everything upstream. Deleting the rivals is what makes it a route rather than a
blend: with them present the LP simply mixes producers and there is nothing to
compare.

Why not expand a tree: Recycled Plastic and Recycled Rubber form a genuine 2-cycle
and this save has both, so there is no correct depth limit -- and the LP exploits
that cycle in the real answer (the best Plastic route runs through it).

Why not read shadow prices off one solve: the required outputs -- machine counts,
byproduct outlets, which building is unlocked but unbuilt -- are not dual
quantities at all. Duals would also be non-unique here, because min_raw LPs over
this recipe set are degenerate, and a reduced cost prices one marginal unit at the
current basis rather than the cost of committing to a chain that is not in the
basis.

What this method CANNOT answer, all consequences of the pinning:

* It cannot compare two chains that end in the same recipe. ``Residual Fuel`` fed
  by Alt HOR and ``Residual Fuel`` fed by the base Plastic recipe are one route;
  the LP picks the cheaper and the readout names it, but the runner-up is gone.
* It cannot price a deliberate BLEND of two producers, which real factories run.
* Deleting a rival also deletes it as a source of its OTHER products, so a route
  can be charged for replacing a by-product it never wanted to lose.
* Raw materials are abstract. Nothing here knows whether a reachable node of that
  resource exists, which is ``plan_factory``'s job.
* It ranks on ONE resource, because a per-unit column whose denominator changes
  between rows is not a comparison. A route that spends a different resource
  entirely therefore cannot be placed in the order at all, and a route that spends
  several is ranked on the one the most routes share -- the rest are named in the
  notes, never priced. ``per_resource`` re-runs the whole table on another one.

Three solves per route, because one objective cannot answer the question
------------------------------------------------------------------------
1. ``min_machines`` at the requested rate -- the fewest-buildings floor, and how
   the raw bill looks when buildings are what you are short of.
2. ``max_item`` with the primary resource capped at ``PROBE_RATE`` -- the headline.
   This is the "60 crude -> 160 Fuel" number and it is scale-free.
3. ``min_raw`` at the requested rate with the primary resource pinned to what
   solve 2 proved possible -- the buildable plan, and a documented lexicographic
   tie-break for the degeneracy the design spec warns about: minimise the scarce
   resource first, then total raw.

Step 3 is needed because a bare ``min_raw`` sums every resource with weight one and
therefore trades crude against water. Measured on this save: Recycled Plastic came
back at 0.94 m3 crude per Plastic with zero water, when 0.33 crude was available for
the price of some water. Water is effectively unlimited on this map, so that trade
is always the wrong way round.

Extraction is deliberately NOT modelled. A route comparison is about the chain, and
charging oil pumps here would make the per-unit economics depend on which nodes the
save happens to have free. That also makes the figures reproduce the design spec's
oil finding exactly: 60 crude -> 160 Fuel at 30.00 net MW per m3/min crude via
Alt HOR + Diluted Fuel, against 40 Fuel and 7.58 MW for the base Fuel recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ....core.gamedata.model import GameData
from ....core.gamedata.search import resolve_item
from ...world.state import WorldState
from ..solver.model import Scenario, Solution
from ..solver.optimize import solve
from ..solver.scenario import build_scenario

__all__ = [
    "PROBE_RATE",
    "Route",
    "RouteComparison",
    "compare_routes",
]

#: The primary resource is capped at this rate for the headline solve, so the yield
#: reads as a sentence a player can check in game: "60 crude in, 160 Fuel out".
PROBE_RATE = 60.0

#: Stand-in for "unlimited". Every resource gets a cap because ``min_raw`` only
#: optimises resources that HAVE a cap; a resource left out has no column at all and
#: the balance is unsatisfiable.
_UNLIMITED = 1e7

#: Above this, a resource is at its stand-in cap rather than at a real optimum.
_AT_CAP = _UNLIMITED * 0.5

#: Water is excluded from "which resource is this route really spending".
#: The design spec models water as unlimited on this map, so it is a pipe-count
#: burden, not a scarcity, and ranking routes by it would invert the answer.
_WATER = "Desc_Water_C"

_EPS = 1e-7


@dataclass
class Byproduct:
    item: str
    name: str
    rate: float
    #: sink | export. A fluid can never be "sink" -- the AWESOME Sink's input is a
    #: conveyor -- so a fluid byproduct always needs a real customer.
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
    #: Fewest whole buildings for the same output, ignoring raw. Reported only when
    #: it is strictly better, because "you can have it in 6 instead of 9" is a real
    #: decision and burying it in a second table is not.
    machines_floor: int = 0
    floor_per_unit: float = 0.0
    mw: float = 0.0  # exact draw at the derived clock, negative
    #: Net MW per unit of the primary resource once the output is burnt in the best
    #: generator this save has unlocked. None when the target is not a fuel.
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


# ------------------------------------------------------------------ helpers


def _short(name: str) -> str:
    """``Alternate: Diluted Fuel`` -> ``Alt Diluted Fuel``.

    Every alternate carries the same 11-character prefix, which at 25 rows is pure
    repetition in a response whose binding constraint is length.
    """
    return "Alt " + name[11:] if name.startswith("Alternate: ") else name


def _best_generator(
    game: GameData, state: WorldState, item: str
) -> tuple[str | None, float, float]:
    """The unlocked generator that pays most per unit of ``item``, if any.

    Restricted to unlocked buildings on purpose: a route's power value has to be
    power this player could actually collect.
    """
    it = game.items.get(item)
    if it is None or not it.energy_mj:
        return None, 0.0, 0.0
    best: tuple[str | None, float, float] = (None, 0.0, 0.0)
    for cls, b in game.buildings.items():
        if not b.is_generator or cls not in state.unlocked_building_ids:
            continue
        if not any(f.fuel_class == item for f in b.fuels):
            continue
        per_min = b.fuel_rate_per_min(it)
        if per_min <= 0:
            continue
        mw = b.power_production_mw / per_min
        if mw > best[2]:
            water = b.supplemental_m3_min() / per_min if b.requires_supplemental else 0.0
            best = (cls, water, mw)
    return best[0], best[2], best[1]


def _pick_primary(solutions: list[Solution], fallback: str = _WATER) -> str:
    """The resource the routes are really spending.

    Chosen once for the whole comparison, never per route: a table whose per-unit
    column silently changes denominator between rows is not a comparison.
    """
    counts: dict[str, int] = {}
    mass: dict[str, float] = {}
    for sol in solutions:
        if not sol.ok:
            continue
        for item, used in sol.raw_used.items():
            if item == _WATER or used <= _EPS:
                continue
            counts[item] = counts.get(item, 0) + 1
            mass[item] = mass.get(item, 0.0) + used
    if not counts:
        return fallback
    return max(counts, key=lambda k: (counts[k], mass[k]))


def _unreachable_inputs(
    game: GameData, recipe_ids: list[str], raws: set[str], pinned_id: str
) -> list[str]:
    """Ingredients of the pinned recipe that nothing in its route can supply.

    A least fixpoint from the raw resources outward, NOT a tree walk: a recipe
    cycle with no external entry simply never enters the set, which is the right
    answer and the one a depth-limited expansion cannot give. It is a necessary
    condition only -- an input can be reachable and the route still stall on a
    byproduct -- so it is used to explain infeasibility, never to predict it.
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
        # The solve rounds to 4dp, so a residual 1e-5 would otherwise render as a
        # byproduct of "0" and read as a real thing needing a belt.
        if rate <= 1e-4:
            continue
        it = game.items.get(item)
        out.append(Byproduct(item, game.item_name(item), rate, outlet, bool(it and it.is_fluid)))
    out.sort(key=lambda b: -b.rate)
    return out


# ------------------------------------------------------------------- solving


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

    # build_scenario is the one construction path from tool arguments to a Scenario,
    # so the route variants are derived FROM it rather than assembled by hand: that
    # keeps the recipe set, the building set and the item resolution identical to
    # what plan_factory would solve.
    base = build_scenario(game, state, objective="min_raw", exports=["MW"]).scenario
    raw_caps = {cls: _UNLIMITED for cls, it in game.items.items() if it.is_resource}
    # A target that is itself a raw resource must be MADE by the pinned recipe.
    # Left in, every route to Water scores "1 water per water" from zero machines.
    raw_caps.pop(target, None)

    outlet_ids = tuple(resolve_item(game, o) or o for o in (outlets or []))
    exports = (target, *(o for o in outlet_ids if o != target))

    producers = [
        r
        for r in game.producers_of(target, "part")
        if r.cls in state.available_recipe_ids
        and (r.machine is None or r.machine in base.buildings_available)
    ]
    producers.sort(key=lambda r: r.name)
    truncated = len(producers) > max_routes
    producers = producers[:max_routes]
    rivals = {r.cls for r in producers}

    gen_cls, gen_mw, gen_water = _best_generator(game, state, target)
    comparison = RouteComparison(
        item=target,
        item_name=game.item_name(target),
        rate=rate,
        primary=_WATER,
        primary_name=game.item_name(_WATER),
        primary_unit="m3/min",
        routes=[],
        generator=game.buildings[gen_cls].name if gen_cls in game.buildings else None,
        generator_mw_per_unit=gen_mw,
        generator_water_m3_min=gen_water,
        allow_sinks=allow_sinks,
        outlets=outlet_ids,
    )
    if not producers:
        comparison.notes.append(
            f"no unlocked recipe makes {comparison.item_name} -- "
            "alternates_for_item lists the locked ones"
        )
        return comparison
    if truncated:
        comparison.notes.append(f"only the first {max_routes} producers were compared")

    def scenario_for(recipe_id: str, **kwargs) -> Scenario:
        kwargs.setdefault("raw_caps", raw_caps)
        return replace(
            base,
            # The pin: every rival producer of the target is deleted, so the LP
            # cannot blend two routes into one un-comparable answer.
            recipes=[r for r in base.recipes if r == recipe_id or r not in rivals],
            target_item=target,
            exports=exports,
            allow_sinks=allow_sinks,
            # A route's cost is the chain's cost. Charging extractors here would
            # make the per-unit economics depend on which nodes this save has free,
            # which is a different question and plan_factory's.
            extractor_nodes={},
            grid_import_mw=_UNLIMITED,
            **kwargs,
        )

    solves = 0
    floors: dict[str, Solution] = {}
    for p in producers:
        floors[p.cls] = solve(
            scenario_for(p.cls, objective="min_machines", export_minimums={target: rate})
        )
        solves += 1

    primary = per_resource and (resolve_item(game, per_resource) or per_resource)
    if primary and primary not in raw_caps:
        # Adding a non-resource here would hand the LP a free source of a
        # manufactured part, and every route would then look absurdly cheap.
        raise ValueError(f"{per_resource!r} is not a raw resource for {comparison.item_name}")
    primary = primary or _pick_primary(list(floors.values()))
    comparison.primary = primary
    comparison.primary_name = game.item_name(primary)
    pit = game.items.get(primary)
    comparison.primary_unit = "m3/min" if pit and pit.is_fluid else "/min"

    for p in producers:
        floor = floors[p.cls]
        route = Route(recipe=p.cls, name=p.name, status="infeasible")
        if not floor.ok:
            missing = _unreachable_inputs(game, scenario_for(p.cls).recipes, set(raw_caps), p.cls)
            route.note = (
                f"nothing left to make {', '.join(missing)}"
                if missing
                else "a byproduct has no consumer and no legal sink"
            )
            comparison.routes.append(route)
            continue

        probe_caps = {**raw_caps, primary: PROBE_RATE}
        probe = solve(scenario_for(p.cls, objective="max_item", raw_caps=probe_caps))
        solves += 1
        at_cap = any(v >= _AT_CAP for k, v in probe.raw_used.items() if k != primary)
        if not probe.ok or probe.objective_value <= _EPS or at_cap:
            route.status = "unbounded"
            route.note = f"does not consume {comparison.primary_name}"
            route.machines = int(floor.machines_total)
            comparison.routes.append(route)
            continue

        route.yield_per_probe = probe.objective_value
        route.per_unit = PROBE_RATE / probe.objective_value
        # Pin the scarce resource to what solve 2 proved reachable, then minimise
        # total raw underneath it. Without the pin, min_raw would buy crude back
        # with water.
        #
        # The headroom is not decoration. ``Solution.objective_value`` is rounded to
        # 4dp, so the probe yield can read HIGH by up to 5e-5 and a cap derived from
        # it then sits just BELOW what the route actually needs -- which reported
        # Motor, Battery and both Heavy Modular Frame routes, all buildable, as
        # having no route at all. Widen by exactly that rounding, plus slack for the
        # MILP's own 1e-6 optimum.
        reachable = rate * PROBE_RATE / max(probe.objective_value - 5e-5, _EPS)
        pinned = {**raw_caps, primary: reachable * (1 + 1e-6) + 1e-9}
        best = solve(
            scenario_for(
                p.cls,
                objective="min_raw",
                raw_caps=pinned,
                export_minimums={target: rate},
            )
        )
        solves += 1
        if not best.ok:
            route.note = "no plan at the best-case resource draw"
            comparison.routes.append(route)
            continue

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
            if row["recipe"] != p.cls
        ]
        route.upstream = [_short(row["label"]) for row in rest]
        route.upstream_ids = [row["recipe"] for row in rest if row["recipe"]]
        route.build_first = sorted(
            {
                game.buildings[row["building_id"]].name
                for row in best.processes
                if row["building_id"] in game.buildings and state.built(row["building_id"]) == 0
            }
        )
        if gen_mw:
            # best.net_mw is the LP's linear draw, which is what makes this figure
            # scale-free: the exact per-machine draw depends on how the requested
            # rate happens to round up to whole buildings.
            route.power_yield = (rate * gen_mw + best.net_mw) / (rate * route.per_unit)
        comparison.routes.append(route)

    comparison.routes.sort(
        key=lambda r: (not r.ok, r.per_unit if r.ok else 0.0, r.machines if r.ok else 0)
    )
    comparison.solves = solves
    comparison.notes.extend(_notes(game, comparison))
    return comparison


def _notes(game: GameData, cmp: RouteComparison) -> list[str]:
    """Warnings first, because that is where the decision is."""
    out: list[str] = []
    build = {b: r.name for r in cmp.feasible for b in r.build_first}
    for building, route in sorted(build.items()):
        out.append(f"{_short(route)} needs a {building}: unlocked, 0 built")

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
    # The headline reads "60 crude -> 160 Fuel", which is the whole story for oil and
    # is NOT for a Computer: that chain also eats copper, caterium and limestone at
    # scale. One denominator can only price one resource, so the others are named
    # rather than left to be inferred from a column that does not mention them.
    others = sorted(
        {
            game.item_name(i)
            for r in cmp.feasible
            for i in r.raw_per_unit
            if i not in (cmp.primary, _WATER)
        }
    )
    if others:
        more = f" and {len(others) - 4} more" if len(others) > 4 else ""
        out.append(
            f"ranked on {cmp.primary_name} alone; these routes also consume "
            + ", ".join(others[:4])
            + more
        )

    # A route that never touches the ranking resource is not broken and must not be
    # explained as a chain that cannot close -- it is a route this table has no
    # denominator for, and per_resource is the way to see it.
    off_scale = [_short(r.name) for r in cmp.routes if r.status == "unbounded"]
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
