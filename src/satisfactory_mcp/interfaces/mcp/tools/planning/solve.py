"""``plan_factory``: solve a plan, and store the request when asked to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, NamedTuple

from mcp.server.fastmcp import Context
from pydantic import Field

from .....core.filelock import LockTimeout
from .....domain.planning import siting as siting_mod
from .....domain.planning.readout.report import PlanFactoryReport, build_plan_report
from .....domain.planning.stored import provenance as prov
from .....domain.planning.stored.planlog import InvalidOp, PlanArgs, PlanLog, PlanLogError, Pushed
from .....domain.planning.stored.recall import UNSAVED_OVERRIDE, overrides_of, with_overrides
from .....domain.session import journal, pins
from .....presenters.text.plan_factory import render_plan_factory
from ... import app
from ...params import (
    AsOf,
    Limit,
    LogisticsItems,
    OverclockLast,
    PaybackHours,
    PlanName,
    PowerPrice,
    RecycleOnce,
    Required,
    RowOverclock,
    Sloops,
    Supplied,
    WaterExtractors,
)
from ._plan_log import BUSY, _head_stamper, _needs_base, _world_plan_log, _write
from ._requests import (
    RecalledRequest,
    _factory_value,
    _plan_pin,
    _power_refusal,
    _recall_request,
    _refusal,
    _resolve_required,
    _resolve_row_overclock,
    _solve_args,
)


@dataclass(frozen=True)
class PlanMeta:
    """What a stored plan carries beside its arguments; ``factory`` None leaves it alone."""

    notes: str
    factory: str | None
    created: str = ""


@dataclass(frozen=True)
class SaveRequest:
    """What ``save_as`` asks this call to store, and the stored plan it writes over."""

    name: str
    base_rev: int | None
    existing: object | None
    meta: PlanMeta
    logistics_items: list[str] | None
    site_at: str | None
    site_yaw_deg: float
    site_footprint: str


class PinnedArgs(NamedTuple):
    """The arguments that may name pins, with each pin swapped for what it stands for."""

    plan: str | None
    sources: list[str] | None
    required: list[str] | None
    exclude_recipes: list[str] | None
    notes: list[str]


def _plan_by_live_name(st, name: str):
    wanted = name.strip().casefold()
    return next((p for p in st.plans.plans if p.name.casefold() == wanted), None)


def _canonical_pins(st, plan, sources, required, exclude_recipes) -> PinnedArgs:
    """Every pin in the pinnable arguments resolved, with its echo; else a ``Refusal``."""
    try:
        plan, notes = _plan_pin(st, plan)
        sources, said = pins.expand(st, "sources", sources) if sources else (sources, [])
        notes += said
        required, said = pins.expand(st, "required", required) if required else (required, [])
        notes += said
        if exclude_recipes:
            exclude_recipes, said = pins.expand(st, "exclude_recipes", exclude_recipes)
            notes += said
    except (KeyError, pins.PinError) as exc:
        raise app.Refusal(f"! {_refusal(exc)}; nothing solved") from None
    return PinnedArgs(plan, sources, required, exclude_recipes, notes)


def _journal_args(plan_kwargs: dict, logistics_items: list[str] | None) -> dict | None:
    """The solve's arguments as the journal stores them: only those off their default."""
    raw = {k: v for k, v in plan_kwargs.items() if v is not None}
    if logistics_items:
        raw["logistics_items"] = list(logistics_items)
    try:
        args = PlanArgs.from_dict(raw)
    except InvalidOp:
        return None
    blank = PlanArgs().to_dict()
    return {k: v for k, v in args.to_dict().items() if v != blank[k]}


def _solve_text(plan_kwargs: dict, feasible: bool, recalled) -> str:
    """One line for the journal: what was solved for, and whether it solved."""
    rates = plan_kwargs.get("export_minimums") or {}
    if rates:
        what = ", ".join(f"{k} {v:g}/min" for k, v in rates.items())
    else:
        what = plan_kwargs.get("target_item") or ", ".join(plan_kwargs.get("exports") or ["MW"])
    objective = plan_kwargs.get("objective") or "max_mw"
    on = f' plan "{recalled.name}" v{recalled.rev}:' if recalled is not None else ""
    return f"{'solved' if feasible else 'infeasible:'}{on} {what} ({objective})"


def _journal_solve(st, ctx, request: RecalledRequest, logistics_items, feasible: bool) -> None:
    """Journal a solve nothing was saved from, so a page following chat sees it."""
    stored = st.plans.find(request.plan) if request.plan else None
    kept = logistics_items
    if kept is None and stored is not None:
        kept = list(_world_plan_log(st).state(stored.key, stored.rev).args.logistics_items)
    journal.append(
        st.world_id,
        "plan.solve",
        actor=app.actor(ctx),
        sav=app.save_token(st),
        tool="plan_factory",
        plan=stored.key if stored is not None else None,
        rev=stored.rev if stored is not None else None,
        args=_journal_args(request.kwargs, kept),
        text=_solve_text(request.kwargs, feasible, stored),
    )


def _save_new(st, name, plan_kwargs, logistics, meta: PlanMeta, field, plan_id, sit, ctx) -> Pushed:
    args = dict(plan_kwargs)
    if logistics:
        args["logistics_items"] = list(logistics)
    return _world_plan_log(st).create(
        name,
        args,
        actor=app.actor(ctx),
        sav=app.save_token(st),
        notes=meta.notes,
        factory=meta.factory or "",
        siting=sit.to_dict() if sit is not None else None,
        plan_id=plan_id,
        provenance=field,
        created=meta.created,
    )


def _save_target(st, save_as: str, base_rev):
    """The stored plan ``save_as`` writes over, or None for a new one; or a refusal.

    A live name wins. Failing that, with ``base_rev``, the plan that carried that name at
    ``base_rev`` (renamed since) or whose key it is, so a rename merges instead of forking.
    """
    live = _plan_by_live_name(st, save_as)
    if live is not None or base_rev is None:
        return live, ""
    log = _world_plan_log(st)
    wanted = save_as.strip().casefold()
    hits = []
    for state in log.heads(include_forgotten=True):
        if state.key == wanted and not state.forgotten:
            return state, ""
        if not isinstance(base_rev, int) or not 1 <= base_rev <= state.rev:
            continue
        try:
            then = log.state(state.key, base_rev)
        except PlanLogError:
            continue
        if not then.forgotten and then.name.casefold() == wanted:
            hits.append(state)
    if len(hits) > 1:
        keys = ", ".join(f'"{s.name}" (key {s.key})' for s in hits)
        return None, (
            f'! no plan is called "{save_as}" now, and {len(hits)} plans were at '
            f"v{base_rev}: {keys}. Pass save_as=<key>; nothing saved"
        )
    return (hits[0] if hits else None), ""


def _save_over(
    st, existing, base_rev, plan_kwargs, logistics, meta: PlanMeta, sit, ctx, overrides=None
):
    """Write the request over ``existing`` at ``base_rev``; ``overrides`` merges a recall."""
    log = _world_plan_log(st)

    def push() -> Pushed:
        base = log.state(existing.key, base_rev)
        if overrides is None:
            args = dict(plan_kwargs)
        else:
            args = with_overrides(base.kwargs(), overrides)
        args["logistics_items"] = (
            list(logistics) if logistics is not None else list(base.args.logistics_items)
        )
        extra = [{"op": "set", "field": "notes", "value": meta.notes}] if meta.notes else []
        extra += (
            [{"op": "set", "field": "factory", "value": meta.factory}]
            if meta.factory is not None
            else []
        )
        extra += [{"op": "site", "value": sit.to_dict()}] if sit is not None else []
        return log.push_args(
            existing.key,
            base_rev,
            args,
            actor=app.actor(ctx),
            sav=app.save_token(st),
            extra=extra,
            stamp=_head_stamper(st),
        )

    return _write(existing.name, "nothing saved", push)


def _store_request(
    st,
    save: SaveRequest,
    report: PlanFactoryReport,
    request: RecalledRequest,
    supplied: dict,
    ctx,
) -> tuple[str, str]:
    """Store the solved request under ``save.name``: the sentence saying so, and any tail.

    A site is resolved before the store is touched, so a bad coordinate refuses the whole
    save rather than leaving a half-written plan.
    """
    g = app.game()
    plan_id = report.prepared.request.plan_id
    # What the selectors resolved to, stored WITH the request: plan_id moves when the world
    # does, never when a selector starts meaning a different part of the map.
    field = prov.record(g, st, request.kwargs.get("sources"))
    sit = None
    if save.site_at:
        try:
            sit = siting_mod.build_siting(
                g,
                st,
                at=save.site_at,
                yaw_deg=save.site_yaw_deg,
                footprint=save.site_footprint,
                solution=report.prepared.solution,
                plan_kwargs=request.kwargs,
                when=save.meta.created,
            )
        except ValueError as exc:
            raise app.Refusal(f"! {exc} -- nothing saved") from None
    path = PlanLog.dir_for(st.world_id)
    pinned = "; ".join(f"{e['selector']}={e['count']} node(s)" for e in field["selectors"])
    existing = save.existing
    recall = (
        f"Recall with plan={(existing.name if existing else save.name.strip())!r} on plan_factory, plan_layout or diff_vs_save"
        + (f". Field recorded: {pinned}" if pinned else "")
        + (f". Sited: {sit.describe()}" if sit is not None else "")
    )
    if existing is None:
        try:
            made = _save_new(
                st,
                save.name,
                request.kwargs,
                save.logistics_items,
                save.meta,
                field,
                plan_id,
                sit,
                ctx,
            )
        except LockTimeout:
            raise app.Refusal(BUSY) from None
        except PlanLogError as exc:
            raise app.Refusal(f"! {exc} -- nothing saved") from None
        note = (
            f'saved as "{made.state.name}" v1 (key {made.key}, plan_id {plan_id}) in '
            f"{path}. {recall}"
        )
        if save.base_rev is not None:
            note += f". base_rev={save.base_rev} was ignored: this is a new plan"
        return note, ""
    recalled = st.plans.find(request.plan) if request.plan else None
    same = recalled is not None and recalled.key == existing.key
    pushed, tail = _save_over(
        st,
        existing,
        save.base_rev,
        request.kwargs,
        save.logistics_items,
        save.meta,
        sit,
        ctx,
        overrides_of(supplied) if same else None,
    )
    note = f'saved over "{pushed.state.name}" in {path}. {recall}' if pushed is not None else ""
    return note, tail


def _mark_override_notes(notes: list[str], save_as: str | None, save_note: str) -> list[str]:
    """The recall's "not saved" override notes, reworded once this call saved or tried to."""
    if save_note.startswith("saved"):
        return [n.replace(UNSAVED_OVERRIDE, "(saved by this call)") for n in notes]
    if save_as:
        return [n.replace(UNSAVED_OVERRIDE, "(not saved: see below)") for n in notes]
    return notes


@app.tool()
def plan_factory(
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
    limit: Limit = 15,
    logistics_items: LogisticsItems = None,
    water_extractors: WaterExtractors = None,
    sloops: Sloops = 0,
    recycle_once: RecycleOnce = None,
    supplied: Supplied = None,
    required: Required = None,
    payback_hours: PaybackHours = None,
    overclock_last: OverclockLast = None,
    power_price: PowerPrice = None,
    row_overclock: RowOverclock = None,
    plan: PlanName = None,
    save_as: Annotated[str | None, Field(description="store this request under a name")] = None,
    base_rev: Annotated[
        int | None,
        Field(description="the plan version you read; needed to save over an existing plan"),
    ] = None,
    plan_notes_text: Annotated[str, Field(description="note stored with save_as")] = "",
    for_factory: Annotated[
        str,
        Field(description="factory this plan is built at; also 'auto', 'whole world', 'none'"),
    ] = "",
    site_at: Annotated[
        str | None,
        Field(
            description="with save_as: record where this plan will STAND -- 'x,y[,z]' in "
            "metres, 'me', a factory name, 'slab:<n>' or a run id (the footprint's centre)"
        ),
    ] = None,
    site_yaw_deg: Annotated[
        float,
        Field(description="site orientation: degrees about world Z, positive +X towards +Y"),
    ] = 0.0,
    site_footprint: Annotated[
        str,
        Field(description="site footprint 'WxD' in metres; blank = the layout's own square"),
    ] = "",
    ctx: Context | None = None,
) -> str:
    """Optimise a factory with an LP over this world's unlocked recipes.

    ``sources`` lists the resource-node selectors that may feed the plan -- named regions,
    radii, grid cells, compass directions, or node ids::

        ["north"]                        everything in the northern half
        ["region:Northern Forest"]       one named region
        ["near:0,-2000@900"]             within 900 m of (0, -2000) metres
        ["node:BP_ResourceNode30_103"]   one exact node (repeatable)
        ["grid:X3Y4", "grid:X3Y5"]       specific grid cells
        ["north", "resource:Crude Oil"]  narrow a location to one resource

    Omit it and the whole map is in scope; search_resource_nodes lists node ids.

    Machine counts are whole buildings at a derived clock (52.8 machine-equivalents is 53
    machines at 99.6%), the power-optimal way to run that throughput.

    ``extractor_clocks`` overclocks the SOURCE NODES only, e.g. [1.0, 1.5, 2.0, 2.5].
    ``clocks`` lets the solver spread throughput over more, slower machines to save power,
    each machine priced at ``machine_cost_mw``. Neither counts the Power Shards an
    overclock needs.

    objective: max_mw | max_item | min_raw | min_machines | min_power. Every item balances
    as an EQUALITY, so a byproduct with no consumer makes the plan infeasible rather than
    silently vanishing.

    ``exports`` is the whitelist of what may leave; the default is power only::

        exports=["MW"]                        power out, plant must be self-powered
        exports=["Plastic", "Rubber"]         items out, NO power export
        exports=["MW", "Plastic", "Rubber"]   both -- MW must be listed explicitly

    The power token is **MW** (``mw``, ``power`` and ``Power`` work too). ``exports``
    **replaces** the default rather than extending it, and exporting MW forbids drawing from
    the grid. A token matching no item is refused by name.

    ``sloops`` is a BUDGET of Somersloops to commit, spent where they buy the most; 0 spends
    none. Each costs 4x power for 2x output on its machine, so they spread across machines.

    ``payback_hours`` spreads a row over more, slower machines while the power saved repays
    their build points within that many hours of play, at ``power_price`` points per MWh
    (the save's grid mix unless given); 0 is the plain build. ``overclock_last`` builds a
    row one machine short with the last one overclocked, weighed against the shards in hand
    plus those craftable from slugs; ``row_overclock`` overrides it per row. From 5 h,
    max_mw and min_power price each machine at its build points instead of
    ``machine_cost_mw``. Both are stored with the plan and follow the shared settings until
    set; "default" puts a recalled plan back on them. Extractors, generators and somersloop
    rows never move.

    ``required`` names recipes (exact name or class id) that must make their item; every
    other recipe for that item is excluded. A locked or banned one is refused by name.

    ``logistics_items`` pins items into the belt/pipe table, ADDED to the ``limit`` biggest.

    ``save_as`` stores the request. Over an existing plan it needs ``base_rev``, the version
    you read (list_plans name=): edits to other settings merge, and the same setting changed
    by someone else is refused as outdated.

    ``site_at`` says where the plan will STAND: the terrain at that pad is read and the
    water note says how much is under water, at what level and how far below dry ground,
    never changing an LP number. With ``save_as`` it is recorded with yaw and footprint,
    for diff_vs_save's on-site census and show_on_map at='plan:<name>'; a recalled sited
    plan is measured at its own site. site_plan moves a stored plan's siting.
    """
    g = app.game()
    st = app.load_world(save, world, as_of)
    pinned = _canonical_pins(st, plan, sources, required, exclude_recipes)
    plan = pinned.plan

    required_ids, refused = _resolve_required(pinned.required)
    if refused:
        return refused
    row_overclock, refused = _resolve_row_overclock(row_overclock)
    if refused:
        return refused

    existing, refusal = _save_target(st, save_as, base_rev) if save_as else (None, "")
    if refusal:
        return refusal
    if existing is not None and base_rev is None:
        return _needs_base(existing.name, existing.rev, "nothing saved")
    if (
        plan
        and existing is not None
        and plan.strip().casefold() == save_as.strip().casefold()
        and st.plans.find(existing.key) is not None
    ):
        plan = existing.key

    supplied = _solve_args(
        objective=objective,
        target_item=target_item,
        sources=pinned.sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=pinned.exclude_recipes,
        required=required_ids,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
        recycle_once=recycle_once,
        supplied=supplied,
        payback_hours=payback_hours,
        overclock_last=overclock_last,
        power_price=power_price,
        row_overclock=row_overclock,
    )
    if refused := _power_refusal(supplied):
        return refused
    request = _recall_request(st, plan, supplied)

    # Its own pair, never written back over the arguments: a recalled plan's site is
    # measured here, and re-saving that plan must not turn its stored yaw and z into the
    # defaults this call happens to carry.
    measure_at, measure_pad = siting_mod.plan_site_args(
        st, request.plan, site_at or "", site_footprint
    )
    report = build_plan_report(
        g,
        st,
        request.kwargs,
        logistics_items,
        objective=request.objective,
        site_at=measure_at,
        site_footprint=measure_pad,
    )

    save_note, tail = "", ""
    if save_as and report.prepared.failure is None:
        when = str(st.header.get("save_datetime") or st.header.get("filename") or "")
        save_request = SaveRequest(
            name=save_as,
            base_rev=base_rev,
            existing=existing,
            meta=PlanMeta(plan_notes_text, _factory_value(for_factory), when),
            logistics_items=logistics_items,
            site_at=site_at,
            site_yaw_deg=site_yaw_deg,
            site_footprint=site_footprint,
        )
        save_note, tail = _store_request(st, save_request, report, request, supplied, ctx)
    elif site_at:
        save_note = (
            "site_at was measured but not RECORDED: a siting lives on a STORED plan. Pass "
            "save_as=<name> here, or site an existing plan with site_plan"
        )
    if not save_as:
        _journal_solve(st, ctx, request, logistics_items, report.prepared.failure is None)

    out = render_plan_factory(
        g,
        st,
        report,
        objective=request.objective,
        only_free_nodes=only_free_nodes,
        limit=limit,
        plan_name=request.name,
        plan_notes=_mark_override_notes([*pinned.notes, *request.notes], save_as, save_note),
        save_as_note=save_note,
    )
    return f"{out}\n{tail}" if tail else out
