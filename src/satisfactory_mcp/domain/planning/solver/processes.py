"""The matrix columns: one process per recipe mode, extractor mode and generator fuel."""

from __future__ import annotations

import math

from ....core.gamedata.constants import WATER
from .model import Process, Scenario
from .overclock import best_clock

#: The clock mode that runs a machine as built, and the one ``best_clock`` may lower.
FULL_CLOCK = 1.0


def recipe_processes(sc: Scenario) -> list[Process]:
    game = sc.game
    out: list[Process] = []
    for rid in sc.recipes:
        recipe = game.recipes.get(rid)
        if recipe is None or recipe.kind != "part":
            continue
        building = game.machine(recipe)
        if building is None:
            continue
        if sc.buildings_available is not None and building.cls not in sc.buildings_available:
            continue
        sloop_options = [0]
        if sc.sloop_budget and building.can_boost:
            # Every count, not just full or empty: spreading sloops dominates (§8.2f).
            sloop_options = list(range(building.sloop_slots + 1))
        for mode in sc.clocks:
            at_full = math.isclose(mode, FULL_CLOCK)
            for sloops in sloop_options:
                clock = mode
                if at_full and not sloops:
                    clock = best_clock(sc, building, game.recipe_power_mw(recipe, 1.0, 0))
                boost = building.boost_for(sloops)
                rates: dict[str, float] = {}
                for flow in recipe.ingredients:
                    rates[flow.item] = rates.get(flow.item, 0.0) - flow.per_min * clock
                for flow in recipe.products:
                    rates[flow.item] = rates.get(flow.item, 0.0) + flow.per_min * clock * boost
                mw = -game.recipe_power_mw(recipe, clock, sloops)
                suffix = ""
                if not at_full:
                    suffix += f"@{mode:g}"
                if sloops:
                    suffix += f"+{sloops}sl"
                out.append(
                    Process(
                        pid=f"r:{rid}{suffix}",
                        kind="recipe",
                        label=f"{recipe.name}{' ' + suffix if suffix else ''}",
                        rates=rates,
                        mw=mw,
                        mw_at_full=-game.recipe_power_mw(recipe, 1.0, sloops),
                        power_exponent=building.power_exponent,
                        building=building.cls,
                        recipe=rid,
                        clock=clock,
                        sloops=sloops,
                    )
                )
    return out


def extractor_processes(sc: Scenario) -> list[Process]:
    game = sc.game
    out: list[Process] = []
    for (building_cls, resource, purity), count in sc.extractor_nodes.items():
        building = game.buildings.get(building_cls)
        if building is None or not building.base_extract_rate or count <= 0:
            continue
        if sc.buildings_available is not None and building_cls not in sc.buildings_available:
            continue
        for clock in sc.extractor_clocks or sc.clocks:
            if clock > building.max_clock + 1e-9:
                continue  # beyond what power shards can reach for this building
            rate = building.extract_rate(purity, clock)
            out.append(
                Process(
                    group=f"x:{building_cls}:{resource}:{purity}",
                    # The resource is in the pid, or coal and sulfur miners merge (§8.3).
                    pid=f"x:{building_cls}:{resource}:{purity}@{clock:g}",
                    kind="extractor",
                    label=f"{building.name} on {purity} {game.item_name(resource)}",
                    rates={resource: rate},
                    mw=-building.power_at(clock),
                    mw_at_full=-building.power_at(1.0),
                    power_exponent=building.power_exponent,
                    building=building_cls,
                    purity=purity,
                    clock=clock,
                    max_count=count,
                )
            )
    return out


def generator_processes(sc: Scenario) -> list[Process]:
    game = sc.game
    out: list[Process] = []
    for building_cls, building in game.buildings.items():
        if not building.is_generator or not building.power_production_mw:
            continue
        if sc.buildings_available is not None and building_cls not in sc.buildings_available:
            continue
        for fuel in building.fuels:
            item = game.items.get(fuel.fuel_class)
            if item is None or not item.energy_mj:
                continue
            rates = {fuel.fuel_class: -building.fuel_rate_per_min(item)}
            if building.requires_supplemental:
                supplemental = fuel.supplemental_class or WATER
                rates[supplemental] = rates.get(supplemental, 0.0) - building.supplemental_m3_min()
            if fuel.byproduct_class and fuel.byproduct_amount:
                burn_s = item.energy_mj / building.power_production_mw
                rates[fuel.byproduct_class] = (
                    rates.get(fuel.byproduct_class, 0.0) + fuel.byproduct_amount * 60 / burn_s
                )
            out.append(
                Process(
                    pid=f"g:{building_cls}:{fuel.fuel_class}",
                    kind="generator",
                    label=f"{building.name} on {item.name}",
                    rates=rates,
                    mw=building.power_production_mw,
                    mw_at_full=building.power_production_mw,
                    # Output and fuel draw are both linear in clock, so no exponent applies.
                    power_exponent=1.0,
                    building=building_cls,
                )
            )
    return out


def build_processes(sc: Scenario) -> list[Process]:
    procs = [*recipe_processes(sc), *extractor_processes(sc), *generator_processes(sc)]
    if sc.excluded_pids:
        procs = [p for p in procs if p.pid not in sc.excluded_pids]
    seen: dict[str, Process] = {}
    for p in procs:
        if p.pid in seen:
            # A duplicate pid merges two columns into a plausible, WRONG answer (§8.3).
            raise AssertionError(f"duplicate process id {p.pid!r}")
        seen[p.pid] = p
    return procs
