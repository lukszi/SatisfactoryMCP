"""Everything ``plan_factory`` has to LOOK UP before anything can be said about a plan.

Solving is ``prepare`` and billing is ``slice_of``; this is the third thing between them,
the world lookups a plan implies. It returns data, never presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....core.gamedata.constants import WATER_EXTRACTOR_WARN_AT
from ....core.gamedata.model import GameData
from ...world.state import WorldState
from ..solver.optimize import MW, build_processes
from ..solver.prepare import PreparedPlan, prepare
from ..solver.scenario import resolve_item
from .slice import PlanSlice, slice_of

__all__ = ["PlanFactoryReport", "build_plan_report"]


@dataclass
class PlanFactoryReport:
    """A solved plan plus every world fact needed to comment on it."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed; every field below it is derived from a solution.
    bill: PlanSlice | None = None
    water_pumps: float = 0.0
    #: ``water_volumes()`` whenever the plan pumps at all, carrying the pump footprint and
    #: its packings only once the count is large enough for the concrete to matter.
    water: dict | None = None
    #: The extractor ceiling this solve ran under, and where it came from. ``given`` means
    #: the caller measured it; otherwise it is ``WATER_EXTRACTOR_CAP_ASSUMED``.
    water_cap: int = 0
    water_cap_given: bool = False
    #: Whether that ceiling is HOLDING THE ANSWER DOWN -- the solve took every extractor it
    #: was allowed, so the plan is shaped by an assumption rather than by the world.
    water_binding: bool = False
    #: What the terrain measures at the site, when the plan was told where it stands and
    #: this machine has a field. Evidence for the assumption above; never a substitute.
    site_water: object | None = None
    shard_budget: dict | None = None
    sloop_budget: dict | None = None
    #: The Production Amplifier research, when it is still in the way of the budget asked.
    sloop_gate: dict | None = None
    #: Somersloops the request was allowed to spend, which is not what it spent.
    sloops_asked: int = 0
    flows: list = field(default_factory=list)
    #: Item ids from ``logistics_items`` that resolved AND appear in the flows.
    pins: list[str] = field(default_factory=list)
    pin_errors: list[str] = field(default_factory=list)
    #: Building classes this plan uses and this world has never built.
    needed_buildings: set[str] = field(default_factory=set)
    #: Processes above 100%, production machines first and extractors last.
    overclocked: list = field(default_factory=list)
    #: Named exports the solution exports at zero, with what the LP can say about why. An
    #: export is a whitelist and not a demand, so zero is a legal optimum, not a fault.
    zero_exports: list[dict] = field(default_factory=list)


def build_plan_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
    logistics_items: list[str] | None = None,
    *,
    objective: str = "",
    site_at: str = "",
    site_footprint: str = "",
) -> PlanFactoryReport:
    """Solve ``plan_kwargs`` and gather what this world says about the result.

    On failure the report carries ``prepared.failure`` and nothing else.
    """
    prepared = prepare(
        g,
        st,
        plan_kwargs,
        objective_label=objective,
        audit=True,
        site_at=site_at,
        site_footprint=site_footprint,
    )
    report = PlanFactoryReport(prepared=prepared)
    if prepared.failure:
        return report
    sol = prepared.solution

    # Water has no nodes, no purity and no geometry in any data this project can read, so
    # the extractor count is bounded by an ASSUMPTION rather than by the map. Pumps stand on
    # platforms built out over open water, so area and concrete are the costs, not frontage.
    report.water_pumps = sum(
        p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C"
    )
    n_water = report.water_pumps
    report.water_cap = int(
        prepared.request.scenario.extractor_nodes.get(
            ("Build_WaterPump_C", "Desc_Water_C", "normal"), 0
        )
    )
    report.water_cap_given = plan_kwargs.get("water_extractors") is not None
    # Whole machines, so equality is the test: the LP hands back a fractional count only
    # when something else binds first.
    report.water_binding = bool(report.water_cap) and n_water >= report.water_cap - 1e-6
    if n_water > 0:
        pump = g.buildings.get("Build_WaterPump_C")
        size = pump.footprint if pump else None
        heavy = n_water >= WATER_EXTRACTOR_WARN_AT and not report.water_cap_given
        # Packed, not n x footprint: that product ignores shared edges and overstates the
        # concrete by about a third.
        block = size.pack(n_water) if size and heavy else None
        pier = size.pack(n_water, columns=1) if size and heavy else None
        report.water = {
            **st.water_volumes(),
            "size": size if heavy else None,
            "block": block,
            "pier": pier,
        }
        site = prepared.request.site
        if site is not None:
            report.site_water = st.site_water(
                site.x_m, site.y_m, width_m=site.width_m, depth_m=site.depth_m
            )

    # A shard raises the MAXIMUM clock by 0.5, so a machine at 150% needs one and only a
    # machine at 250% needs three.
    report.bill = bill = slice_of(prepared, g)
    if bill.shard_rows:
        report.shard_budget = st.shard_budget()
    report.sloops_asked = int(plan_kwargs.get("sloops") or 0)
    # A sloop budget is only spendable once Production Amplifier is researched. Reported
    # rather than refused, because planning ahead of the research is legitimate.
    if report.sloops_asked:
        report.sloop_gate = st.research_gate("production_boost")
    if bill.sloop_used_rows:
        report.sloop_budget = st.sloop_budget()

    # A POWER-BLIND objective drives clocks up and hides the cost: phase 2 pins the goal and
    # minimises MACHINE COUNT, so it picks the highest clocks, and power goes as clock**1.32.
    pushed = [p for p in sol.processes if p["clock"] > 1.01 and "last_clock" not in p]
    report.overclocked = [p for p in pushed if p["kind"] != "extractor"] + [
        p for p in pushed if p["kind"] == "extractor"
    ]

    report.needed_buildings = {
        p["building_id"]
        for p in sol.processes
        if p["building_id"] and st.built(p["building_id"]) == 0
    }

    # Three causes the LP can distinguish for a named export coming out at zero: everything
    # made was eaten as an intermediate (or sunk), nothing in scope CAN make it, or nothing
    # rewarded making it -- only the objective and export_minimums give an export value.
    zero_named = [
        item
        for item in prepared.request.scenario.exports
        if item != MW and sol.exports.get(item, 0.0) <= 1e-6
    ]
    if zero_named:
        produced: dict[str, float] = {}
        for p in sol.processes:
            for item, rate in p["rates"].items():
                if rate > 0:
                    produced[item] = produced.get(item, 0.0) + rate
        can_make = {
            item
            for proc in build_processes(prepared.request.scenario)
            for item, rate in proc.rates.items()
            if rate > 0
        }
        report.zero_exports = [
            {
                "item": item,
                "name": g.item_name(item),
                "produced": produced.get(item, 0.0),
                "sunk": sol.sunk.get(item, 0.0),
                "makeable": item in can_make,
            }
            for item in zero_named
        ]

    # Rows rank by volume, so a two-item question lives in the tail; the presenter pins
    # these ids above its own limit.
    report.flows = [e for e in sol.logistics if e["rate"] > 0]
    for name in logistics_items or []:
        item_id = resolve_item(g, name)
        if item_id is None:
            report.pin_errors.append(f"logistics_items: no item matches {name!r}")
        elif not any(e["item"] == item_id for e in report.flows):
            report.pin_errors.append(
                f"logistics: nothing moves {g.item_name(item_id)} in this plan"
            )
        else:
            report.pins.append(item_id)
    return report
