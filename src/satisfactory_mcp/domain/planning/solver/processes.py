"""The matrix columns: one process per recipe mode, extractor mode and generator fuel."""

from __future__ import annotations

from .model import Process, Scenario
from .overclock import best_clock


def recipe_processes(sc: Scenario) -> list[Process]:
    g = sc.game
    out: list[Process] = []
    for rid in sc.recipes:
        r = g.recipes.get(rid)
        if r is None or r.kind != "part":
            continue
        b = g.machine(r)
        if b is None:
            continue
        if sc.buildings_available is not None and b.cls not in sc.buildings_available:
            continue
        sloop_options = [0]
        if sc.sloop_budget and b.can_boost:
            # EVERY count, not just full or empty. Output is linear in sloops
            # (base + n*mult) while power goes as boost**2, so the marginal output per
            # sloop is constant and the marginal power cost rises. Under a binding
            # budget, spreading therefore strictly dominates: one sloop in each of four
            # Blenders buys 4 x 1.25 output for 4 x 1.56 power, where four in one buys
            # 2.0 for 4.0. Offering only 0-or-full made the solver pay the worst rate on
            # the scarcest resource in the game.
            sloop_options = list(range(b.sloop_slots + 1))
        for mode in sc.clocks:
            for sloops in sloop_options:
                clock = mode
                if mode == 1.0 and not sloops:
                    clock = best_clock(sc, b, g.recipe_power_mw(r, 1.0, 0))
                boost = b.boost_for(sloops)
                rates: dict[str, float] = {}
                for f in r.ingredients:
                    rates[f.item] = rates.get(f.item, 0.0) - f.per_min * clock
                for f in r.products:
                    rates[f.item] = rates.get(f.item, 0.0) + f.per_min * clock * boost
                mw = -g.recipe_power_mw(r, clock, sloops)
                suffix = ""
                if mode != 1.0:
                    suffix += f"@{mode:g}"
                if sloops:
                    suffix += f"+{sloops}sl"
                out.append(
                    Process(
                        # The resource must be in the pid. A pid built from
                        # building+purity alone silently merged coal and sulfur
                        # miners into one column that produced both.
                        pid=f"r:{rid}{suffix}",
                        kind="recipe",
                        label=f"{r.name}{' ' + suffix if suffix else ''}",
                        rates=rates,
                        mw=mw,
                        mw_at_full=-g.recipe_power_mw(r, 1.0, sloops),
                        power_exponent=b.power_exponent,
                        building=b.cls,
                        recipe=rid,
                        clock=clock,
                        sloops=sloops,
                    )
                )
    return out


def extractor_processes(sc: Scenario) -> list[Process]:
    g = sc.game
    out: list[Process] = []
    for (building, resource, purity), count in sc.extractor_nodes.items():
        b = g.buildings.get(building)
        if b is None or not b.base_extract_rate or count <= 0:
            continue
        if sc.buildings_available is not None and building not in sc.buildings_available:
            continue
        for clock in sc.extractor_clocks or sc.clocks:
            if clock > b.max_clock + 1e-9:
                continue  # beyond what power shards can reach for this building
            rate = b.extract_rate(purity, clock)
            out.append(
                Process(
                    group=f"x:{building}:{resource}:{purity}",
                    pid=f"x:{building}:{resource}:{purity}@{clock:g}",
                    kind="extractor",
                    label=f"{b.name} on {purity} {g.item_name(resource)}",
                    rates={resource: rate},
                    mw=-b.power_at(clock),
                    mw_at_full=-b.power_at(1.0),
                    power_exponent=b.power_exponent,
                    building=building,
                    purity=purity,
                    clock=clock,
                    max_count=count,
                )
            )
    return out


def generator_processes(sc: Scenario) -> list[Process]:
    g = sc.game
    out: list[Process] = []
    for cls, b in g.buildings.items():
        if not b.is_generator or not b.power_production_mw:
            continue
        if sc.buildings_available is not None and cls not in sc.buildings_available:
            continue
        for fuel in b.fuels:
            item = g.items.get(fuel.fuel_class)
            if item is None or not item.energy_mj:
                continue
            rates = {fuel.fuel_class: -b.fuel_rate_per_min(item)}
            if b.requires_supplemental:
                supp = fuel.supplemental_class or "Desc_Water_C"
                rates[supp] = rates.get(supp, 0.0) - b.supplemental_m3_min()
            if fuel.byproduct_class and fuel.byproduct_amount:
                burn_s = item.energy_mj / b.power_production_mw
                rates[fuel.byproduct_class] = (
                    rates.get(fuel.byproduct_class, 0.0) + fuel.byproduct_amount * 60 / burn_s
                )
            out.append(
                Process(
                    pid=f"g:{cls}:{fuel.fuel_class}",
                    kind="generator",
                    label=f"{b.name} on {item.name}",
                    rates=rates,
                    mw=b.power_production_mw,
                    mw_at_full=b.power_production_mw,
                    # Generators are energy-conserving: output and fuel draw are both
                    # linear in clock, so no exponent applies.
                    power_exponent=1.0,
                    building=cls,
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
            # Guard, not a nicety: a duplicate pid merges two columns and produces a
            # plausible, mass-balanced, WRONG answer.
            raise AssertionError(f"duplicate process id {p.pid!r}")
        seen[p.pid] = p
    return procs
