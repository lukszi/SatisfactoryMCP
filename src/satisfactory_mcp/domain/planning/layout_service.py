"""Everything ``plan_layout`` has to WORK OUT before a schematic can be written down.

``build_layout`` turns a solution into blocks, buses and floors. What sat around it in
the tool was a second job: solving the plan in the first place, finding the best pump
this save can place so a riser count is against a real tier, asking which fluids the
floor order makes climb, and then -- per ``show`` -- one more domain question each.
Those questions are genuinely different (a site partition, a construction bill, a trunk
plan, a fit against an existing platform) but they are all lookups, not sentences, so
they answer here and the presenter decides which of them is worth a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as replace_solution

from ...core.gamedata.model import GameData
from ..factories.resolve import resolve_factory
from ..world.state import WorldState
from .carrier import TierChoice
from .layout import Layout, build_layout, fluid_head
from .materials import build_materials
from .optimize import Solution
from .prepare import PreparedPlan, prepare
from .sites import claim_processes, partition
from .trunks import plan_trunks

__all__ = ["LayoutReport", "build_layout_report"]


@dataclass
class LayoutReport:
    """A solved plan, its schematic, and whatever ``show`` asked on top."""

    #: ``None`` only when the carrier tiers did not resolve, which is answered before
    #: anything is solved.
    prepared: PreparedPlan | None
    tiers: TierChoice
    lay: Layout | None = None
    #: One stack per declared site, in spec order, with anything unclaimed last. Empty
    #: when no partition was given, in which case ``lay`` is the single stack. When set,
    #: ``lay`` is those same stacks concatenated (stages kept disjoint per site), so
    #: every whole-plan total still reads off one object.
    site_layouts: list[tuple[str, Layout]] = field(default_factory=list)
    #: The best pump this save can PLACE, which is not the best pump that exists.
    pump_cls: str = ""
    pump_name: str = "pump"
    pump_head_m: float = 0.0
    #: ``fluid_head`` rows the floor order makes climb; the rest fall and cost nothing.
    climbing: list[dict] = field(default_factory=list)
    #: The one ``show``-specific answer: a SitePlan, a MaterialsBill or a TrunkPlan.
    #: ``None`` for the show modes the layout already answers by itself -- and for
    #: show='sites' with no sites, which is a question that cannot be asked.
    show_payload: object | None = None
    fit: object | None = None
    #: The factory the fit was assessed against -- named, or recalled from the plan --
    #: under the canonical name the selector resolved it to.
    scope_name: str | None = None


def build_layout_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
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
    # No supply probe: a layout that cannot be solved is answered by plan_factory, and
    # the extra solve buys nothing here beyond the pointer the presenter already gives.
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = LayoutReport(prepared=prepared, tiers=tiers)
    if prepared.failure:
        return report
    sol = prepared.solution

    if sites:
        # A declared partition means SEPARATE BUILDINGS, so each site gets its own
        # stack and its own floor ordering. One merged stack was measured getting this
        # badly wrong: order_floors_by="head" over a three-site plan fused rig, hall
        # and resin plant into one 80 m tower and priced 46 pumps of fluid lift where
        # the per-site stacks need 6.
        lay, report.site_layouts = _layout_by_site(
            g,
            sol,
            sites,
            belt_ipm=tiers.belt_ipm,
            pipe_m3min=tiers.pipe_m3min,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
        )
        report.lay = lay
    else:
        report.lay = lay = build_layout(
            g,
            sol,
            belt_ipm=tiers.belt_ipm,
            pipe_m3min=tiers.pipe_m3min,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
        )

    # Floors follow CHAIN DEPTH, which keeps the schematic in build order but says
    # nothing about head. Chain depth tends to make every fluid climb; the model has no
    # terrain and no view of where crude arrives, so the cost is named, not optimised.
    # The best pump the player can build, so a riser count is against a real tier.
    #
    # The best pump the player can actually build, not the best that exists: a Mk2
    # lifts 50 m against a Mk1's 20, so quoting Mk2 to someone who has not unlocked it
    # understates the build by more than half.
    pump = max(
        (b for c, b in g.buildings.items() if b.head_lift_m and c in st.unlocked_building_ids),
        key=lambda b: b.head_lift_m,
        default=None,
    )
    if pump is not None:
        report.pump_cls, report.pump_name = pump.cls, pump.name
        report.pump_head_m = pump.head_lift_m
    report.climbing = [d for d in fluid_head(lay, report.pump_head_m) if d["direction"] == "climbs"]

    if show == "sites":
        if not sites:
            # Nothing further is worth computing: without a partition there is no
            # question to answer, and the caller has to be told how to ask it.
            return report
        report.show_payload = partition(prepared, g, sites)
    elif show == "materials":
        # Foundations live here and nowhere else -- they are not machines, so no build
        # table counts them, and at 5 Concrete each a big deck outweighs most of the
        # machine bill. This is why the construction bill hangs off plan_layout rather
        # than plan_factory: only the layout knows how many tiles the plan stands on.
        #
        # TOTAL, not `lay.foundations`. That property is the PEAK floor, which is what
        # sizes the site -- floors stack, so the ground you need is the biggest one. But
        # you pour concrete for every floor, so charging the peak would understate the
        # deck by however many storeys the stack has.
        # Risers are part of the build and were missing entirely, so a fluid-heavy plan's
        # bill understated itself. Counted from the floors the fluid actually crosses,
        # priced at the best pump this save can place.
        riser_pumps = sum(row["pumps"] for row in report.climbing)
        extra = (
            [{"building_id": report.pump_cls, "machines": riser_pumps}]
            if pump is not None and riser_pumps
            else []
        )
        report.show_payload = build_materials(
            g, [*sol.processes, *extra], st.stock(), lay.total_foundations
        )
    elif show == "trunks":
        # The destination decides which end of each chain is "far", so it decides the
        # sign of every lift. A named factory is the honest answer when there is one;
        # otherwise the field's own centroid, said out loud rather than assumed.
        target, target_label = None, "the node field's centroid"
        if factory:
            resolved_name, machines = resolve_factory(st, factory)
            pts = [m["pos"] for m in machines if m.get("pos")]
            if pts:
                target = (
                    sum(p[0] for p in pts) / len(pts),
                    sum(p[1] for p in pts) / len(pts),
                )
                target_label = resolved_name
        report.show_payload = plan_trunks(prepared, g, target, target_label)

    report.scope_name = factory
    if report.scope_name is None and plan:
        stored = st.plans.find(plan)
        report.scope_name = (stored.factory or None) if stored else None
    if report.scope_name and report.scope_name.startswith("/"):
        report.scope_name = None
    if report.scope_name:
        from .fit import assess_fit

        resolved_name, machines = resolve_factory(st, report.scope_name)
        report.scope_name = resolved_name
        report.fit = assess_fit(resolved_name, machines, lay, st.structures, st.projection)
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

    The unit of assignment is the same as ``partition``'s -- ``claim_processes``, so the
    floors and the interface table can never disagree about where a machine stands.
    Anything unclaimed or contested lands in a trailing ``(unassigned)`` stack rather
    than vanishing: a block dropped here would silently shrink the materials bill.

    The merge is a relabelling, not a re-solve: every site's stages, floor indexes and
    buses are shifted by a per-site offset so they stay disjoint and contiguous in the
    combined object. That keeps ``fluid_head`` exact on the concatenation -- a stage
    maps to one floor, and the floors between two same-site stages are same-site -- so
    the whole-plan riser count is the SUM of the per-site counts, never a lift invented
    between buildings that share no pipe.
    """
    claims = claim_processes(sol.processes, spec)
    groups: list[tuple[str, list[dict]]] = []
    for name in spec:
        procs = [p for p in sol.processes if claims.get(p["pid"]) == [name]]
        if procs:
            groups.append((name, procs))
    leftover = [p for p in sol.processes if len(claims.get(p["pid"], [])) != 1]
    if leftover:
        groups.append(("(unassigned)", leftover))

    site_layouts: list[tuple[str, Layout]] = []
    blocks, buses, floors, warnings = [], [], [], []
    stage_base = 0
    index_base = 0
    for name, procs in groups:
        sub = build_layout(
            g,
            replace_solution(sol, processes=procs),
            belt_ipm=belt_ipm,
            pipe_m3min=pipe_m3min,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
        )
        # Shift IN PLACE, uniformly, so the sub-layout stays self-consistent and the
        # merged view shares its objects rather than describing different ones.
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
