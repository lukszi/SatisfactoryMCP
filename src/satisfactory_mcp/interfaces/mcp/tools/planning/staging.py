"""A plan against the save: what to change, and the order to switch it on in."""

from __future__ import annotations

from typing import Annotated, NamedTuple

from mcp.server.fastmcp import Context
from pydantic import Field

from .....domain.planning.progress.commission_service import build_commission_report
from .....domain.planning.progress.diff_service import build_diff_report
from .....domain.planning.progress.stages import partition_id
from .....domain.planning.stored.plan_args import PlanLogError
from .....presenters.text.commission import render_commission
from .....presenters.text.diff import render_diff
from ... import app
from ...params import AsOf, Biomass, Limit, PlanName
from ._plan_log import journal_view, world_plan_log
from ._requests import factory_value, recall_request, solve_args


class StagePosition(NamedTuple):
    """Where a plan's startup stages stood when this process last read them."""

    partition: str
    current: int
    count: int
    rev: int


_stages_seen: dict[tuple[str, str], StagePosition] = {}


def _stored_plan_state(st, plan: str | None):
    """The stored version ``plan`` names, as the plan log holds it, or None."""
    if not plan:
        return None
    found = st.plans.find(plan)
    if found is None:
        return None
    try:
        return world_plan_log(st).state(found.key)
    except PlanLogError:
        return None


def _stage_position_text(current: int, count: int) -> str:
    if not count:
        return "no startup order fits the headroom"
    if not current:
        return f"every stage of {count} built"
    return f"stage {current} of {count}"


def _renumbered(st, stored, tracking) -> str:
    """The note that the stages moved since this process last read this plan, or ''."""
    if stored is None or tracking is None:
        return ""
    count = len(tracking.stages) if tracking.ok else 0
    now = StagePosition(partition_id(tracking), tracking.current if count else 0, count, stored.rev)
    seen = _stages_seen.get((st.world_id, stored.key))
    _stages_seen[(st.world_id, stored.key)] = now
    if seen is None or seen.partition == now.partition:
        return ""
    was = _stage_position_text(seen.current, seen.count)
    was = f"you were in {was}" if seen.count and seen.current else f"before, {was}"
    return (
        f"the stages changed since you last read this plan (v{seen.rev} -> v{now.rev}): "
        f"{was}, now {_stage_position_text(now.current, now.count)}"
    )


def _shared_power(biomass: bool | None) -> tuple[bool, str, list[str]]:
    """``biomass`` or the shared setting, the shared stage headroom, and any unread note."""
    head, unread = app.shared_setting("stage_headroom")
    notes = [unread] if unread else []
    biomass, _unread = app.biomass_setting(biomass)
    return biomass, head, notes


@app.tool()
def diff_vs_save(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    show_cost: bool = True,
    plan: PlanName = None,
    stage: Annotated[
        int | None,
        Field(description="one startup stage's delta; 0 for the stage overview"),
    ] = None,
    factory: Annotated[
        str | None,
        Field(description="count this factory as built; also 'auto', 'world' or 'none'"),
    ] = None,
    biomass: Biomass = None,
    ctx: Context | None = None,
) -> str:
    """What to change to get from the factory you have to the one plan_factory plans.

    Takes plan_factory's arguments and re-solves; both tools print a plan id hashed over the
    arguments and the save-derived inputs, so the same id is provably the same plan.

    Machines are matched by IDENTITY, never by position: a manufacturer on (building,
    recipe), a generator on its building, an extractor on its node; a machine running
    another recipe is busy, not spare. Actions are ordered free-first -- UNPAUSE, SETRECIPE,
    then BUILD -- and the power arithmetic charges only the machines still to place. Where a
    machine cannot be identified (Water Extractors) the answer is a RANGE.

    Recall a stored plan with ``plan=`` and the diff is also grouped by STARTUP STAGE, the
    partition commission_plan emits; ``stage=<n>`` narrows to one stage's delta and
    ``stage=0`` asks for the overview without a stored plan, numbered from the arguments.

    Built and energised differ, and the save proves one direction only: a machine that
    produced in its last 300 s window had power; one that did not may be unpowered, starved,
    blocked or idle. A stage is never called unpowered, only built with nothing proven
    running.

    Saves are read-only: this never proposes writing one, and there is no dismantle action.
    Machines standing among the plan but not in it are listed for you to judge.
    """
    g = app.game()
    st = app.load_world(save, world, as_of)

    supplied = solve_args(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
    )
    request = recall_request(st, plan, supplied)
    plan_notes = list(request.notes)
    stored = _stored_plan_state(st, request.plan)
    biomass, default, unread = _shared_power(biomass)
    plan_notes += unread

    report = build_diff_report(
        g,
        st,
        request.kwargs,
        objective=request.objective,
        plan=request.plan,
        plan_name=request.name,
        stage=stage,
        factory=factory_value(factory),
        biomass=biomass,
        stored=stored,
        default=default,
    )
    view = {"view": "track", "stage": stage if stage and stage >= 1 else None, "section": "stages"}
    journal_view(st, request.plan, "diff_vs_save", ctx, view)
    moved = _renumbered(st, stored, report.tracking)
    if moved:
        plan_notes = [moved, *plan_notes]

    return render_diff(
        st,
        report,
        objective=request.objective,
        limit=limit,
        show_cost=show_cost,
        stage=stage,
        plan_name=request.name,
        plan_notes=plan_notes,
    )


@app.tool()
def commission_plan(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
    sloops: int = 0,
    headroom_mw: Annotated[
        float | None,
        Field(description="grid power free for startup; default reads it from the save"),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
    plan: PlanName = None,
    biomass: Biomass = None,
    ctx: Context | None = None,
) -> str:
    """In what order to switch a built plant on, without blowing the fuse.

    A STARTUP order, not a build order: a machine draws only when it runs, so the whole
    plant is built first, drawing nothing, and then energised block by block. Nothing here
    says what to build first.

    At every step, energised consumer draw must stay under the headroom plus generation from
    generators already burning fuel; exceeding it blows the fuse and stops the whole grid,
    the feeding plant included, until it is reset by hand.
    Generators energise for free, so a wave costs its consumers and its generators' output
    pays for the next wave.

    Takes plan_factory's arguments, or recall a saved plan with ``plan=``.
    """
    g = app.game()
    st = app.load_world(save, world, as_of)

    supplied = solve_args(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
    )
    request = recall_request(st, plan, supplied)
    plan_notes = list(request.notes)

    stored = _stored_plan_state(st, request.plan)
    biomass, default, unread = _shared_power(biomass)
    plan_notes += unread
    report = build_commission_report(
        g,
        st,
        request.kwargs,
        headroom_mw,
        objective=request.objective,
        biomass=biomass,
        stored=stored,
        default=default,
    )
    view = {"view": "track", "stage": None, "section": "startup"}
    journal_view(st, request.plan, "commission_plan", ctx, view)
    moved = _renumbered(st, stored, report.tracking) if headroom_mw is None else ""
    if moved:
        plan_notes = [moved, *plan_notes]

    return render_commission(
        st,
        report,
        objective=request.objective,
        limit=limit,
        offset=offset,
        plan_name=request.name,
        plan_notes=plan_notes,
    )
