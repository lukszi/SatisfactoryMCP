"""``plan_layout``: a plan as a buildable schematic."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .....core.gamedata.model import GameData
from .....domain.planning.layout.service import LayoutReport, build_layout_report
from .....domain.planning.progress.diff import solution_of
from .....domain.planning.readout import payback, summary
from .....domain.planning.solver.carrier import resolve_tiers
from .....domain.planning.solver.prepare import PreparedPlan
from .....domain.world.state import WorldState
from .....presenters.text.layout import LAYOUT_VIEWS, render_layout
from ... import app
from ...params import (
    AsOf,
    Limit,
    OverclockLast,
    PaybackHours,
    PlanName,
    PowerPrice,
    RowOverclock,
    Sloops,
    WaterExtractors,
)
from ._plan_log import journal_view
from ._requests import power_refusal, recall_request, resolve_row_overclock, solve_args


def _payback_notes(g: GameData, st: WorldState, prepared: PreparedPlan) -> list[str]:
    """What the payback horizon would trade on this solve, as notes."""
    sol = solution_of(prepared)
    draw = sum(-p["mw"] for p in sol.processes if p["mw"] < 0)
    view = summary.power_view(g, st, prepared.request, sol, round(sol.machines_total), draw)
    return payback.trade_text(view)


@app.tool()
def plan_layout(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    show: Annotated[str, Field(description=" | ".join(LAYOUT_VIEWS))] = "floors",
    detail: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    # plan_factory's solve arguments, so the layout draws the plan that was asked for.
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    water_extractors: WaterExtractors = None,
    sloops: Sloops = 0,
    payback_hours: PaybackHours = None,
    overclock_last: OverclockLast = None,
    power_price: PowerPrice = None,
    row_overclock: RowOverclock = None,
    sites: Annotated[
        dict[str, list[str]] | None,
        Field(
            description=(
                'show="sites": {"rig": ["Heavy Oil Residue", ...], "hall": ["MW"]} '
                "-- MW/power claims every generator"
            )
        ),
    ] = None,
    max_floor_foundations: Annotated[
        int,
        Field(description="cap a deck at this many 8m foundations; 0 = one stage per deck"),
    ] = 0,
    order_floors_by: Annotated[
        str, Field(description='"chain" (build order) or "head" (minimise fluid lift)')
    ] = "chain",
    belt_tier: Annotated[
        str, Field(description="belt tier name; blank = the fastest you have unlocked")
    ] = "",
    pipe_tier: Annotated[
        str, Field(description="pipe tier name; blank = the fastest you have unlocked")
    ] = "",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    plan: PlanName = None,
    factory: Annotated[
        str | None,
        Field(description="fit the layout against this factory's existing platform"),
    ] = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """Turn a plan into a buildable schematic: blocks, buses and floors.

    The plan_factory arguments it declares (a saved plan, ``plan=``, carries the rest), plus
    ``show``: "floors" (default, the stack), "blocks" (every module, its size and rates),
    "buses" (item flows), "trunks" (which nodes share each pipe or belt run in), "materials"
    (build cost, machines plus deck), or "sites" (the plan cut into named modules, and what
    crosses between them).

    A SCHEMATIC, not a blueprint: modules, connections, floor assignment and a space budget,
    but no world coordinates or belt routing -- there is no terrain data to place them on.
    Blocks are split by carrier throughput, and floors follow chain depth with a logistics
    deck between production floors.
    """
    g = app.game()
    if gone := app.retired(("detail", detail, "show")):
        return gone
    wanted = show.strip().casefold()
    if wanted not in LAYOUT_VIEWS:
        return f"! unknown show {show!r}. Choose from: {', '.join(LAYOUT_VIEWS)}"
    show = wanted
    st = app.load_world(save, world, as_of)

    row_choices, refused = resolve_row_overclock(row_overclock)
    if refused:
        return refused
    tiers = resolve_tiers(g, st, belt_tier, pipe_tier)
    if tiers.errors:
        return render_layout(
            g,
            st,
            LayoutReport(prepared=None, tiers=tiers),
            objective=objective,
            show=show,
            limit=limit,
        )

    supplied = solve_args(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        water_extractors=water_extractors,
        sloops=sloops,
        # A tier reaches the scenario so the solve and the schematic agree, and only when
        # asked for: a resolved default would read as an override on every recall.
        belt_ipm=tiers.belt_ipm if tiers.asked_belt else None,
        pipe_m3min=tiers.pipe_m3min if tiers.asked_pipe else None,
        payback_hours=payback_hours,
        overclock_last=overclock_last,
        power_price=power_price,
        row_overclock=row_choices,
    )
    if refused := power_refusal(supplied):
        return refused
    request = recall_request(st, plan, supplied)
    plan_notes = list(request.notes)

    report = build_layout_report(
        g,
        st,
        request.kwargs,
        tiers,
        objective=request.objective,
        show=show,
        sites=sites,
        max_floor_foundations=max_floor_foundations,
        order_floors_by=order_floors_by,
        factory=factory,
        plan=request.plan,
    )
    journal_view(st, request.plan, "plan_layout", ctx)
    if report.prepared is not None and report.prepared.ok:
        plan_notes += _payback_notes(g, st, report.prepared)

    return render_layout(
        g,
        st,
        report,
        objective=request.objective,
        show=show,
        limit=limit,
        plan_name=request.name,
        plan_notes=plan_notes,
    )
