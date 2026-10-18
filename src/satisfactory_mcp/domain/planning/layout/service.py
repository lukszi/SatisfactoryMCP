"""Everything ``plan_layout`` has to WORK OUT before a schematic can be written down.

It solves the plan, stacks it (one stack per declared site), finds the best pump this save
can place and the fluids the floor order makes climb, then answers the one ``show`` question
asked. All lookups, never sentences: the presenter decides what is worth a table.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from typing_extensions import TypedDict

from ....core.gamedata.model import Building, GameData
from ...factories import candidates
from ...factories.select import resolve_factory
from ...spatial import geo
from ...world.state import WorldState
from ..solver.carrier import TierChoice
from ..solver.model import ProcessRow, Solution
from ..solver.prepare import PreparedPlan, prepare
from .fit import FitReport, assess_fit
from .head import HeadRow, fluid_head
from .materials import MachineCount, MaterialsBill, build_materials
from .model import Block, Bus, Floor, Layout
from .schematic import build_layout
from .site_partition import SitePlan, claim_processes, partition
from .trunks import TrunkPlan, plan_trunks

__all__ = ["LayoutReport", "build_layout_report"]


@dataclass
class LayoutReport:
    """A solved plan, its schematic, and whatever ``show`` asked on top."""

    #: ``None`` only when the carrier tiers did not resolve, answered before any solve.
    prepared: PreparedPlan | None
    tiers: TierChoice
    layout: Layout | None = None
    #: One stack per declared site, in spec order, anything unclaimed last; empty without a
    #: partition. ``layout`` is then those stacks concatenated, so whole-plan totals read
    #: off one object.
    site_layouts: list[tuple[str, Layout]] = field(default_factory=list[tuple[str, Layout]])
    #: The best pump this save can PLACE, which is not the best pump that exists.
    pump_cls: str = ""
    pump_name: str = "pump"
    pump_head_m: float = 0.0
    #: ``fluid_head`` rows the floor order makes climb; the rest fall and cost nothing.
    climbing: list[HeadRow] = field(default_factory=list[HeadRow])
    #: The ``show``-specific answer; ``None`` where the layout answers by itself, and for
    #: show='sites' without sites, a question that cannot be asked.
    show_payload: SitePlan | MaterialsBill | TrunkPlan | None = None
    fit: FitReport | None = None
    #: The factory the fit was assessed against, under the name the selector resolved.
    scope_name: str | None = None


class _Stacking(TypedDict):
    """How every stack of one report is built."""

    belt_ipm: float
    pipe_m3min: float
    max_floor_foundations: int
    order_floors_by: str


def _best_placeable_pump(g: GameData, st: WorldState) -> Building | None:
    """The pipeline pump with the most head this save has unlocked (docs/planning.md §8.5c)."""
    return max(
        (b for c, b in g.buildings.items() if b.head_lift_m and c in st.unlocked_building_ids),
        key=lambda b: b.head_lift_m,
        default=None,
    )


def _materials(
    g: GameData, st: WorldState, report: LayoutReport, sol: Solution, layout: Layout
) -> MaterialsBill:
    """Every storey's foundations, and the risers, at the best placeable pump (§8.5f, §8.5m)."""
    riser_pumps = sum(row["pumps"] for row in report.climbing)
    extra: list[MachineCount] = (
        [{"building_id": report.pump_cls, "machines": riser_pumps}]
        if report.pump_cls and riser_pumps
        else []
    )
    rows: list[MachineCount] = [*sol.processes, *extra]
    return build_materials(g, rows, st.stock(), layout.total_foundations)


def _trunks(g: GameData, st: WorldState, prepared: PreparedPlan, factory: str | None) -> TrunkPlan:
    """The trunk lines toward a named factory, or toward the node field's own centroid."""
    # The destination decides which end of each chain is "far", so it decides the sign of
    # every lift; the centroid is said out loud rather than assumed.
    target, target_label = None, "the node field's centroid"
    if factory:
        resolved_name, machines = resolve_factory(st, factory)
        placed = candidates.positions(st.projection)
        found = geo.centroid([placed[m][:2] for m in machines if m in placed])
        if found:
            target, target_label = found, resolved_name
    return plan_trunks(prepared, g, target, target_label)


def _fit_scope(
    report: LayoutReport, layout: Layout, st: WorldState, factory: str | None, plan: str | None
) -> None:
    """Assess the layout against the factory named, or the one the stored plan names."""
    report.scope_name = factory
    if report.scope_name is None and plan:
        stored = st.plans.find(plan)
        report.scope_name = (stored.factory or None) if stored else None
    if report.scope_name and report.scope_name.startswith("/"):
        report.scope_name = None
    if report.scope_name:
        resolved_name, machines = resolve_factory(st, report.scope_name)
        report.scope_name = resolved_name
        report.fit = assess_fit(resolved_name, machines, layout, st.structures, st.projection)


def build_layout_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: Mapping[str, object],
    tiers: TierChoice,
    *,
    objective: str = "",
    show: str = "floors",
    sites: dict[str, list[str]] | None = None,
    max_floor_foundations: int = 0,
    order_floors_by: str = "chain",
    factory: str | None = None,
    plan: str | None = None,
) -> LayoutReport:
    """Solve ``plan_kwargs``, schematise it, and answer whatever ``show`` needs.

    A ``SelectorError`` from a named factory propagates: an unresolvable selector is the
    caller's mistake, not a fact about the layout.
    """
    # No supply probe: plan_factory answers an unsolvable plan.
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = LayoutReport(prepared=prepared, tiers=tiers)
    sol = prepared.solution
    if sol is None:
        return report

    stacking = _Stacking(
        belt_ipm=tiers.belt_ipm,
        pipe_m3min=tiers.pipe_m3min,
        max_floor_foundations=max_floor_foundations,
        order_floors_by=order_floors_by,
    )
    if sites:
        # Separate buildings, so each site gets its own stack and floor order (§8.5m).
        layout, report.site_layouts = _layout_by_site(g, sol, sites, **stacking)
    else:
        layout = build_layout(g, sol, **stacking)
    report.layout = layout

    # Best pump this save can place, so riser counts use a real tier.
    pump = _best_placeable_pump(g, st)
    if pump is not None:
        report.pump_cls, report.pump_name = pump.cls, pump.name
        report.pump_head_m = pump.head_lift_m
    report.climbing = [
        d for d in fluid_head(layout, report.pump_head_m) if d["direction"] == "climbs"
    ]

    if show == "sites":
        if not sites:
            # Without a partition there is no question to answer; the presenter says how.
            return report
        report.show_payload = partition(prepared, g, sites)
    elif show == "materials":
        report.show_payload = _materials(g, st, report, sol, layout)
    elif show == "trunks":
        report.show_payload = _trunks(g, st, prepared, factory)
    _fit_scope(report, layout, st, factory, plan)
    return report


def _layout_by_site(
    g: GameData,
    sol: Solution,
    spec: dict[str, list[str]],
    *,
    belt_ipm: float,
    pipe_m3min: float,
    max_floor_foundations: int,
    order_floors_by: str,
) -> tuple[Layout, list[tuple[str, Layout]]]:
    """One stack per declared site, plus the concatenation the report totals read from.

    Sites are claimed by ``claim_processes``, as ``partition`` claims them, and anything
    unclaimed lands in a trailing ``(unassigned)`` stack. The merge shifts each site's
    stages, floors and buses by an offset, so riser counts sum per site (§8.5m).
    """
    claims = claim_processes(sol.processes, spec)
    groups: list[tuple[str, list[ProcessRow]]] = []
    for name in spec:
        procs = [p for p in sol.processes if claims.get(p["pid"]) == [name]]
        if procs:
            groups.append((name, procs))
    leftover = [p for p in sol.processes if len(claims.get(p["pid"], [])) != 1]
    if leftover:
        groups.append(("(unassigned)", leftover))

    site_layouts: list[tuple[str, Layout]] = []
    blocks: list[Block] = []
    buses: list[Bus] = []
    floors: list[Floor] = []
    warnings: list[str] = []
    stage_base = 0
    index_base = 0
    for name, procs in groups:
        sub = build_layout(
            g,
            replace(sol, processes=procs),
            belt_ipm=belt_ipm,
            pipe_m3min=pipe_m3min,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
        )
        # Shifted in place, so the merged view shares the sub-layout's objects.
        for b in sub.blocks:
            b.stage += stage_base
        for bus in sub.buses:
            bus.from_stage += stage_base
            bus.to_stage += stage_base
        for f in sub.floors:
            if f.stage is not None:
                f.stage += stage_base
            f.index += index_base
            f.site = name
        site_layouts.append((name, sub))
        blocks += sub.blocks
        buses += sub.buses
        floors += sub.floors
        for w in sub.warnings:
            tagged = f"{name}: {w}"
            if tagged not in warnings:
                warnings.append(tagged)
        stage_base = max((b.stage for b in sub.blocks), default=stage_base) + 1
        index_base = floors[-1].index + 1 if floors else 0

    merged = Layout(blocks=blocks, buses=buses, floors=floors, warnings=warnings)
    return merged, site_layouts
