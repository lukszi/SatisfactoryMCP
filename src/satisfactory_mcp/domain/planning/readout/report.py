"""Everything ``plan_factory`` has to LOOK UP before anything can be said about a plan.

Solving is ``prepare`` and billing is ``slice_of``; this is the third thing between them,
the world lookups a plan implies. It returns data, never presentation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from typing_extensions import TypedDict

from ....core.gamedata.constants import WATER_EXTRACTOR_KEY, WATER_EXTRACTOR_WARN_AT, WATER_PUMP
from ....core.gamedata.footprint import Footprint, Packed
from ....core.gamedata.model import GameData
from ....core.gamedata.search import resolve_item
from ...progression.views import ResearchGate, ShardBudget, SloopBudget
from ...world.state import WorldState
from ...world.water import SiteWater
from ..solver.model import MW, LogisticsRow, ProcessRow, Solution
from ..solver.prepare import PreparedPlan, prepare
from ..solver.processes import build_processes
from .slice import PlanSlice, slice_of

__all__ = ["PlanFactoryReport", "WaterFacts", "ZeroExport", "build_plan_report"]


class WaterFacts(TypedDict):
    """The save's pumps by water body and its sea level, with the pump footprint and its
    packings only once the count is large enough for the concrete to matter."""

    volumes: dict[str, int]
    pumps: int
    sea_level_m: float | None
    sea_level_span_m: float | None
    size: Footprint | None
    block: Packed | None
    pier: Packed | None


class ZeroExport(TypedDict):
    """A named export leaving at zero, and what the LP can say about why."""

    item: str
    name: str
    produced: float
    sunk: float
    makeable: bool


@dataclass
class PlanFactoryReport:
    """A solved plan plus every world fact needed to comment on it."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed; every field below it is derived from a solution.
    bill: PlanSlice | None = None
    water_pumps: int = 0
    #: Whenever the plan pumps at all.
    water: WaterFacts | None = None
    #: The extractor ceiling this solve ran under; ``given`` when the caller measured it,
    #: otherwise ``WATER_EXTRACTOR_CAP_ASSUMED``.
    water_cap: int = 0
    water_cap_given: bool = False
    #: The solve took every extractor allowed: the plan is shaped by an assumption.
    water_binding: bool = False
    #: What the terrain measures at the site: evidence for the assumption, never a substitute.
    site_water: SiteWater | None = None
    shard_budget: ShardBudget | None = None
    sloop_budget: SloopBudget | None = None
    #: The Production Amplifier research, when it is still in the way of the budget asked.
    sloop_gate: ResearchGate | None = None
    #: Somersloops the request was allowed to spend, which is not what it spent.
    sloops_asked: int = 0
    flows: list[LogisticsRow] = field(default_factory=list[LogisticsRow])
    #: Item ids from ``logistics_items`` that resolved AND appear in the flows.
    logistics_item_ids: list[str] = field(default_factory=list[str])
    logistics_item_errors: list[str] = field(default_factory=list[str])
    #: Building classes this plan uses and this world has never built.
    needed_buildings: set[str] = field(default_factory=set[str])
    #: Processes above 100%, production machines first and extractors last.
    overclocked: list[ProcessRow] = field(default_factory=list[ProcessRow])
    #: Zero is a legal optimum, since an export is a whitelist and not a demand.
    zero_exports: list[ZeroExport] = field(default_factory=list[ZeroExport])


def _water_facts(
    report: PlanFactoryReport,
    g: GameData,
    st: WorldState,
    sol: Solution,
    plan_kwargs: Mapping[str, object],
) -> None:
    """Pump count, its assumed cap and whether it binds, and what the site measures.

    Water has no nodes, so the count is bounded by an assumption (docs/planning.md §8.2c).
    """
    request = report.prepared.request
    report.water_pumps = sum(
        p["machines"] for p in sol.processes if p.get("building_id") == WATER_PUMP
    )
    report.water_cap = int(request.scenario.extractor_nodes.get(WATER_EXTRACTOR_KEY, 0))
    report.water_cap_given = plan_kwargs.get("water_extractors") is not None
    # Whole machines, so equality is the test.
    report.water_binding = bool(report.water_cap) and report.water_pumps >= report.water_cap - 1e-6
    if report.water_pumps <= 0:
        return
    pump = g.buildings.get(WATER_PUMP)
    size = pump.footprint if pump else None
    heavy = report.water_pumps >= WATER_EXTRACTOR_WARN_AT and not report.water_cap_given
    # Packed, never n x footprint, which ignores shared edges (docs/planning.md §8.5g).
    block = size.pack(report.water_pumps) if size and heavy else None
    pier = size.pack(report.water_pumps, columns=1) if size and heavy else None
    volumes = st.water_volumes()
    report.water = {
        "volumes": volumes["volumes"],
        "pumps": volumes["pumps"],
        "sea_level_m": volumes["sea_level_m"],
        "sea_level_span_m": volumes["sea_level_span_m"],
        "size": size if heavy else None,
        "block": block,
        "pier": pier,
    }
    site = request.site
    if site is not None:
        report.site_water = st.site_water(
            site.x_m, site.y_m, width_m=site.width_m, depth_m=site.depth_m
        )


def _overclocked(sol: Solution) -> list[ProcessRow]:
    """Rows above 100% that are not overclock-last, production machines first."""
    pushed = [p for p in sol.processes if p["clock"] > 1.01 and "last_clock" not in p]
    return [p for p in pushed if p["kind"] != "extractor"] + [
        p for p in pushed if p["kind"] == "extractor"
    ]


def _zero_exports(g: GameData, prepared: PreparedPlan, sol: Solution) -> list[ZeroExport]:
    """Named exports at zero: eaten or sunk, not makeable in scope, or simply unrewarded."""
    scenario = prepared.request.scenario
    zero_named = [
        item for item in scenario.exports if item != MW and sol.exports.get(item, 0.0) <= 1e-6
    ]
    if not zero_named:
        return []
    produced: dict[str, float] = {}
    for p in sol.processes:
        for item, rate in p["rates"].items():
            if rate > 0:
                produced[item] = produced.get(item, 0.0) + rate
    can_make = {
        item for proc in build_processes(scenario) for item, rate in proc.rates.items() if rate > 0
    }
    return [
        {
            "item": item,
            "name": g.item_name(item),
            "produced": produced.get(item, 0.0),
            "sunk": sol.sunk.get(item, 0.0),
            "makeable": item in can_make,
        }
        for item in zero_named
    ]


def _logistics_item_ids(
    g: GameData, flows: list[LogisticsRow], logistics_items: list[str] | None
) -> tuple[list[str], list[str]]:
    """The named items that move in this plan, and why each other name was dropped.

    The presenter shows these above its own limit, since flows rank by volume.
    """
    item_ids: list[str] = []
    errors: list[str] = []
    for name in logistics_items or []:
        item_id = resolve_item(g, name)
        if item_id is None:
            errors.append(f"logistics_items: no item matches {name!r}")
        elif not any(e["item"] == item_id for e in flows):
            errors.append(f"logistics: nothing moves {g.item_name(item_id)} in this plan")
        else:
            item_ids.append(item_id)
    return item_ids, errors


def build_plan_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: Mapping[str, object],
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
    sol = prepared.solution
    if sol is None:
        return report
    _water_facts(report, g, st, sol, plan_kwargs)

    report.bill = bill = slice_of(prepared, g)
    if bill.shard_rows:
        report.shard_budget = st.shard_budget()
    sloops = plan_kwargs.get("sloops")
    report.sloops_asked = int(sloops) if isinstance(sloops, int | float) else 0
    # Reported rather than refused: planning ahead of the research is legitimate.
    if report.sloops_asked:
        report.sloop_gate = st.research_gate("production_boost")
    if bill.sloop_used_rows:
        report.sloop_budget = st.sloop_budget()

    # A power-blind objective pushes clocks up and hides the cost (docs/planning.md §8.2i).
    report.overclocked = _overclocked(sol)
    report.needed_buildings = {
        building_id
        for p in sol.processes
        if (building_id := p["building_id"]) and st.built(building_id) == 0
    }
    report.zero_exports = _zero_exports(g, prepared, sol)
    report.flows = [e for e in sol.logistics if e["rate"] > 0]
    report.logistics_item_ids, report.logistics_item_errors = _logistics_item_ids(
        g, report.flows, logistics_items
    )
    return report
