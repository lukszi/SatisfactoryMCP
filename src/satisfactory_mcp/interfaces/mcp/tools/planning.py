"""The optimiser surface: plans, layouts, diffs, bills of materials.

Also plan persistence, since a stored plan is a stored planning REQUEST."""

from __future__ import annotations

import os
from typing import Annotated

from pydantic import Field

from ....core.gamedata.unlocks import granted_by_label
from ....domain.factories.select import SelectorError
from ....domain.planning import bom as bom_mod
from ....domain.planning import compare
from ....domain.planning import provenance as prov
from ....domain.planning import siting as siting_mod
from ....domain.planning.carrier import resolve_tiers
from ....domain.planning.commission_service import build_commission_report
from ....domain.planning.diff_service import build_diff_report
from ....domain.planning.layout_service import LayoutReport, build_layout_report
from ....domain.planning.planlog import Actor, PlanLog, PlanLogError
from ....domain.planning.prepare import prepare
from ....domain.planning.recall import PLAN_DEFAULTS
from ....domain.planning.recall import recall_plan as _plan_kwargs
from ....domain.planning.report import build_plan_report
from ....domain.planning.scenario import build_scenario
from ....domain.planning.sensitivity import sweep_unlocks
from ....domain.planning.store import PLAN_ARGS
from ....presenters.text import byproducts as byproducts_text
from ....presenters.text import primitives as render
from ....presenters.text.bom import render_bom
from ....presenters.text.commission import render_commission
from ....presenters.text.compare import render_comparison
from ....presenters.text.diff import ENERGISED_CAVEAT, RANGE_CAVEAT, render_diff
from ....presenters.text.layout import render_layout
from ....presenters.text.plan_factory import render_plan_factory
from ..app import AsOf, Limit, _item_id, _state, game, mcp, retired

#: The stored-argument defaults, re-exported under their old home for ``server``. The
#: two stage caveats keep their old home too: they were read from here before they had
#: a presenter to live in.
_ = (PLAN_DEFAULTS, ENERGISED_CAVEAT, RANGE_CAVEAT)


def _arg_text(value) -> str:
    """One stored argument as the reader typed it, never as the solver resolved it."""
    if isinstance(value, dict):
        return ", ".join(
            f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in value.items()
        )
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def _cut(text: str, width: int) -> str:
    """Truncate visibly: a silently clipped source list reads as the whole list."""
    return text if len(text) <= width else text[: width - 1] + "~"


def _plan_detail(st, stored) -> str:
    """One stored plan in full, without solving it.

    ``plan_factory plan=<name>`` answers a different question at LP cost: it prints what
    the request RESOLVES to today. This prints the request.
    """
    sit = siting_mod.parse(stored)
    head = [
        f"# plan {stored.name!r}",
        f"# {st.age_note}",
        render.kv(
            [
                ("plan_id", stored.plan_id or "(none)"),
                ("saved_against", stored.created),
                ("for_factory", stored.factory),
            ]
        ),
    ]
    if stored.notes:
        head.append(f"notes: {stored.notes}")
    if sit is not None:
        head.append(f"sited: {sit.describe()}")

    stored_args = [(k, stored.args[k]) for k in PLAN_ARGS if k in stored.args]
    parts = [
        "# the stored REQUEST -- every argument not listed is at its default\n"
        + render.table(("argument", "value"), [(k, _arg_text(v)) for k, v in stored_args])
    ]
    field = (stored.provenance or {}).get("selectors") or ()
    if field:
        parts.append(
            "# what each source selector resolved to WHEN SAVED\n"
            + render.table(
                ("selector", "nodes", "box(m)"),
                [
                    (
                        e.get("selector", ""),
                        e.get("count", 0),
                        ",".join(f"{v:g}" for v in e["bbox"]) if e.get("bbox") else "-",
                    )
                    for e in field
                ],
            )
        )

    notes = ["nothing was solved here: pass plan=<name> to plan_factory for today's answer"]
    try:
        if build_scenario(st.game, st, **stored.kwargs()).plan_id != stored.plan_id:
            notes.append(
                "the WORLD has moved since this was saved (an unlock, a freed node, a new "
                "building), so re-solving it will not reproduce the plan_id above"
            )
        notes.extend(prov.notes(st.game, st, stored))
    except Exception as exc:  # a stored plan can outlive the thing it referenced
        notes.append(f"the staleness checks could not run: {type(exc).__name__}: {exc}")
    return render.envelope("\n".join(head), "\n".join(parts), notes)


@mcp.tool(structured_output=False)
def list_plans(
    name: Annotated[
        str | None,
        Field(description="one plan's full stored request, siting and field, unsolved"),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Plans saved for this world, and whether the world has moved under them.

    ``name`` prints one plan in full instead: the arguments as they were stored, what its
    source selectors resolved to when saved, and its whole siting. Nothing is solved, so
    this answers "what did I ask for" -- ``plan_factory plan=<name>`` answers the other
    question, what those arguments resolve to today, and pays an LP solve for it.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    if name:
        stored = st.plans.find(name)
        if stored is None:
            known = ", ".join(x.name for x in st.plans.plans) or "(none)"
            return f"! no saved plan named {name!r}. Saved: {known}"
        return _plan_detail(st, stored)
    if not st.plans.plans:
        return render.envelope(
            f"# no plans saved for world {st.plans.world_id!r}",
            "Pass save_as=<name> to plan_factory to store one.",
        )
    rows = []
    unrecorded = []
    drifted = False
    for stored in st.plans.plans:
        status = []
        try:
            req = build_scenario(st.game, st, **stored.kwargs())
            if req.plan_id != stored.plan_id:
                status.append("world moved")
            # Eager, not lazy, because it was measured rather than guessed: re-resolving
            # one selector over the 608-row node table is 0.7 ms, against the full
            # build_scenario this loop already pays per plan. A "check it yourself" marker
            # would have cost the reader a round trip to save nothing.
            for drift in prov.compare(st.game, st, stored):
                status.append(f"field {drift.then}->{drift.now}")
                drifted = True
            if not prov.recorded(stored):
                unrecorded.append(stored.name)
        except Exception as exc:  # a stored plan can outlive the thing it referenced
            status.append(f"broken: {type(exc).__name__}")
        args = stored.args
        sit = siting_mod.parse(stored)
        sited = "-"
        if sit is not None:
            # Origin, orientation and box in one cell: yaw and footprint used to need a
            # second call, and a plan's site is not a point.
            sited = f"{sit.x_m:.0f},{sit.y_m:.0f}"
            sited += f" y{sit.yaw_deg:g}" if sit.yaw_deg else ""
            sited += f" {sit.width_m:g}x{sit.depth_m:g}" if sit.has_footprint else ""
        rows.append(
            (
                stored.name,
                args.get("objective", "max_mw"),
                args.get("target_item") or "-",
                _cut(",".join(args.get("sources") or []), 36) or "whole map",
                stored.factory or "-",
                sited,
                "; ".join(status),
                _cut(stored.notes, 36),
            )
        )
    notes = [
        (
            "'world moved' means the plan is unchanged but the solve inputs are not "
            "-- an unlock, a freed node or a new building. Re-run it to see how"
        ),
        (
            "pass name=<plan> for one plan's stored arguments, its recorded field and its "
            "full siting, without solving anything"
        ),
    ]
    if any("~" in str(cell) for row in rows for cell in row):
        notes.append("a '~' ends a cell this table had to cut; name=<plan> prints it whole")
    if drifted:
        notes.append(
            "'field N->M' means the plan's own selectors no longer resolve to the nodes it "
            "was saved against -- the map layer under a name was re-cut, so the plan now "
            "plans over a different part of the world. Recall it for which nodes moved"
        )
    if unrecorded:
        # Named rather than counted: "2 plans" is a statistic, and the reader needs to
        # know WHICH ones are unchecked before trusting a blank status column.
        notes.append(
            f"no recorded field, so the check above cannot run: {', '.join(unrecorded)} "
            "-- these predate it. A blank status is 'not checked', not 'unchanged'; "
            "re-run one with save_as=<its own name> to record what its selectors mean"
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(rows)} saved plan(s)",
        render.table(
            ("name", "objective", "target", "sources", "factory", "sited(m)", "status", "notes"),
            rows,
        ),
        notes,
    )


def _log(st) -> PlanLog:
    return PlanLog(st.world_id, st.header.get("session_name") or "")


def _actor() -> Actor:
    return Actor("chat", "", os.getpid())


def _at_head(st, key: str, ops: list[dict]):
    """Until the tools take ``base_rev``: write at the head, as the old store did."""
    log = _log(st)
    return log.push(key, log.head_rev(key), ops, actor=_actor())


@mcp.tool(structured_output=False)
def forget_plan(
    name: str, save: str | None = None, world: str | None = None, as_of: AsOf = None
) -> str:
    """Delete a saved plan. Nothing in the world is touched."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    stored = st.plans.find(name)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none)"
        return f"! no saved plan named {name!r}. Saved: {known}"
    try:
        _at_head(st, stored.key, [{"op": "forget"}])
    except PlanLogError as exc:
        return f"! {exc}; nothing forgotten"
    return f"forgot plan {stored.name!r}"


@mcp.tool(structured_output=False)
def rename_plan(
    name: str,
    to: Annotated[str, Field(description="the new name")],
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Rename a saved plan. Nothing is re-solved and nothing else about it changes.

    The plan keeps its id, its recorded field, its siting and its notes -- a name is the
    only thing here a player picked, and it was the only thing they could not correct
    without saving the plan again under a second name and forgetting the first.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    stored = st.plans.find(name)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none)"
        return f"! no saved plan named {name!r}. Saved: {known}"
    wanted = to.strip()
    if not wanted:
        return "! a plan name cannot be blank"
    # Case-insensitively, because `find` matches that way: two plans differing only in
    # case would make every later recall ambiguous.
    taken = wanted.casefold()
    if any(p is not stored and p.name.casefold() == taken for p in st.plans.plans):
        return f"! this world already has a plan named {wanted!r}"
    was = stored.name
    if was == wanted:
        return f"plan {was!r} already has that name"
    try:
        _at_head(st, stored.key, [{"op": "rename", "name": wanted}])
    except PlanLogError as exc:
        return f"! {exc}; nothing renamed"
    path = PlanLog.dir_for(st.world_id)
    return render.envelope(
        f"# renamed plan {was!r} to {wanted!r}\nstored in {path}",
        "",
        [
            (
                f"recall it as plan={wanted!r}; the plan id, field record, siting and "
                "notes are untouched"
            )
        ],
    )


@mcp.tool(structured_output=False)
def site_plan(
    plan: str,
    at: Annotated[
        str,
        Field(
            description="site origin (the footprint's CENTRE): 'x,y[,z]' in metres, "
            "'me', a factory name, 'slab:<n>' or a run id. Blank keeps the stored origin"
        ),
    ] = "",
    yaw_deg: Annotated[
        float | None,
        Field(description="degrees about world Z, positive +X towards +Y; omit to keep"),
    ] = None,
    footprint: Annotated[
        str,
        Field(
            description="'WxD' in metres ('96' = square). Blank keeps the stored one, "
            "or derives the layout's own square if none is stored"
        ),
    ] = "",
    clear: bool = False,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Record, update or clear WHERE a stored plan stands. Nothing is re-solved.

    A plan stores what to build; this stores where -- origin (x, y and optionally z, in
    metres, at the footprint's centre), orientation (yaw about world Z, the same
    convention the save stores machine facing with), and footprint (width x depth,
    metres). The footprint defaults to the square plan_layout budgets for the plan's
    largest floor, and the record keeps track of whether it was measured or derived.

    Once sited: plan recalls print the siting; ``diff_vs_save plan=<name>`` adds an
    approximate what-stands-on-the-pad census; ``show_on_map at='plan:<name>'``
    centres a map link on the origin.

    The siting is a RECORD of your decision, not a constraint on the solve -- re-running
    the plan neither reads nor moves it, and ``save_as`` over the same name keeps it.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    stored = st.plans.find(plan)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none)"
        return f"! no saved plan named {plan!r}. Saved: {known}"

    if clear:
        if not stored.siting:
            return f"plan {stored.name!r} carries no siting; nothing to clear"
        try:
            _at_head(st, stored.key, [{"op": "site", "value": None}])
        except PlanLogError as exc:
            return f"! {exc}; nothing cleared"
        return f"cleared the siting of plan {stored.name!r}. The plan itself is untouched"

    existing = siting_mod.parse(stored)
    if not at and existing is None:
        return (
            f"! plan {stored.name!r} has no siting yet, so there is no origin to keep -- "
            "pass at='x,y[,z]' in metres, 'me', a factory name, 'slab:<n>' or a run id"
        )

    when = str(st.header.get("save_datetime") or st.header.get("filename") or "")
    try:
        if at:
            sit = siting_mod.build_siting(
                g,
                st,
                at=at,
                yaw_deg=yaw_deg if yaw_deg is not None else (existing.yaw_deg if existing else 0.0),
                footprint=footprint
                or (
                    f"{existing.width_m:g}x{existing.depth_m:g}"
                    if existing and existing.has_footprint
                    else ""
                ),
                plan_kwargs=stored.kwargs(),
                when=when,
            )
        else:
            # Origin kept, yaw and/or footprint updated in place.
            width, depth, source = existing.width_m, existing.depth_m, existing.source
            if footprint:
                width, depth = siting_mod.parse_footprint(footprint)
                source = "given"
            sit = siting_mod.Siting(
                x_m=existing.x_m,
                y_m=existing.y_m,
                z_m=existing.z_m,
                yaw_deg=yaw_deg if yaw_deg is not None else existing.yaw_deg,
                width_m=width,
                depth_m=depth,
                source=source,
                origin_label=existing.origin_label,
                when=when,
            )
    except ValueError as exc:
        return f"! {exc}"

    try:
        _at_head(st, stored.key, [{"op": "site", "value": sit.to_dict()}])
    except PlanLogError as exc:
        return f"! {exc}; not sited"
    path = PlanLog.dir_for(st.world_id)

    from ....domain.spatial import maplink, regions

    label = regions.load_regions().label_for(sit.x_m * 100, sit.y_m * 100)
    verb = "re-sited" if existing else "sited"
    return render.envelope(
        f"# {verb} plan {stored.name!r}: {sit.describe()}\n"
        f"# region: {label.describe()}\n"
        f"stored in {path}",
        "map: "
        + maplink.local_map_url(sit.x_m, sit.y_m, world=st.plans.world_id)
        + "\n"
        + maplink.map_url(sit.x_m * 100, sit.y_m * 100),
        [
            (
                f"diff_vs_save plan={stored.name!r} now reports what stands inside this "
                f"footprint; show_on_map at='plan:{stored.name}' centres on it"
            ),
            (
                "the siting is a record, not a constraint: re-solving the plan neither "
                "reads nor moves it"
            ),
        ],
    )


def _save_request(st, name, kwargs, plan_id, notes, factory, when, field, sit) -> str:
    """Create ``name``, or save over the live plan of that exact name at its head."""
    log = _log(st)
    wanted = name.strip().casefold()
    existing = next((s for s in log.heads() if s.name.casefold() == wanted), None)
    siting = sit.to_dict() if sit is not None else None
    if existing is None:
        return log.create(
            name,
            kwargs,
            actor=_actor(),
            notes=notes,
            factory=factory,
            siting=siting,
            plan_id=plan_id,
            provenance=field,
            created=when,
        ).state.name
    extra = [{"op": "set", "field": "notes", "value": notes}] if notes else []
    extra += [{"op": "set", "field": "factory", "value": factory}] if factory else []
    extra += [{"op": "site", "value": siting}] if siting else []
    log.push_args(
        existing.key,
        log.head_rev(existing.key),
        kwargs,
        actor=_actor(),
        extra=extra,
        stamp=lambda _state: {"plan_id": plan_id, "provenance": field},
    )
    return existing.name


@mcp.tool(structured_output=False)
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
    logistics_items: Annotated[
        list[str] | None,
        Field(description="items whose belt/pipe rows to pin, whatever their volume"),
    ] = None,
    water_extractors: Annotated[
        int | None,
        Field(description="how many Water Extractors your site can actually hold"),
    ] = None,
    sloops: Annotated[
        int,
        Field(description="Somersloops the plan may spend; 0 spends none"),
    ] = 0,
    recycle_once: Annotated[
        list[str] | None,
        Field(description="recipes that may run but must not feed each other, e.g. ['Recycled']"),
    ] = None,
    supplied: Annotated[
        dict[str, float] | None,
        Field(description="items another plan hands this one, {item: per-minute}"),
    ] = None,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    save_as: Annotated[str | None, Field(description="store this request under a name")] = None,
    plan_notes_text: Annotated[str, Field(description="note stored with save_as")] = "",
    for_factory: Annotated[str, Field(description="factory label this plan is for")] = "",
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
) -> str:
    """Optimise a factory with an LP over this world's unlocked recipes.

    ``sources`` says which resource nodes may feed the plan, as a list of selectors --
    named regions, radii, grid cells, compass directions, or specific node ids::

        ["north"]                        everything in the northern half
        ["region:Northern Forest"]       one named region
        ["near:0,-2000@900"]             within 900 m of (0, -2000) metres
        ["node:BP_ResourceNode30_103"]   one exact node (repeatable)
        ["grid:X3Y4", "grid:X3Y5"]       specific grid cells
        ["north", "resource:Crude Oil"]  narrow a location to one resource

    Omit it and the whole map is in scope. Use search_resource_nodes to discover ids.

    Machine counts are whole buildings at a derived clock: a 52.8 machine-equivalent
    result is reported as 53 machines at 99.6%. That is exact, always a clean ratio,
    and provably the power-optimal way to run that throughput, so ordinary ratio
    underclocking is automatic and needs no parameter.

    ``extractor_clocks`` overclocks the SOURCE NODES only, e.g. [1.0, 1.5, 2.0, 2.5].
    That is the usual play: a node set is fixed, so speed is the only way to get more
    out of it, whereas overclocking production machines mostly burns power. Each
    machine above 100% needs Power Shards, which nothing here counts.

    ``clocks`` is only for asking a different question: passing [0.5, 1.0] lets the
    solver SPREAD throughput over more machines to save power, which is real but not
    free, so each machine is priced at ``machine_cost_mw`` (default 5 MW, just above
    the 2.58 MW/machine that trade was measured to be worth). Overclock modes are not
    offered by default because they consume Power Shards, which nothing here counts.

    objective: max_mw | max_item | min_raw | min_machines | min_power.
    Every item is balanced as an EQUALITY, so a byproduct with no consumer makes the
    plan infeasible rather than silently vanishing.

    ``exports`` is the whitelist of what may leave, and the single most load-bearing
    argument here; default is power only, which is often infeasible for crude oil::

        exports=["MW"]                        power out, plant must be self-powered
        exports=["Plastic", "Rubber"]         items out, NO power export
        exports=["MW", "Plastic", "Rubber"]   both -- MW must be listed explicitly

    Two things worth reading twice. The power token is **MW** (``mw``, ``power`` and
    ``Power`` all work too), not the item name of anything. And ``exports``
    **replaces** the default rather than extending it: naming an item drops MW, which
    is deliberate, because exporting MW also forbids drawing from the existing grid.
    A token matching no item is refused by name rather than solved around.

    ``sloops`` is a BUDGET, not a switch: it is how many Somersloops you will actually
    commit, and the solver spends up to that many wherever they buy the most. Default 0
    spends none, because only a fixed number exist on the whole map and a plan that
    quietly assumed them would be unbuildable. Each one costs 4x power for 2x output on
    its machine, so they are placed one at a time across many machines rather than
    filling one -- output is linear in sloops and power is quadratic, so spreading wins.

    ``logistics_items`` pins named items into the belt/pipe table however small their
    flow, as rows ADDED to the ``limit`` biggest by volume. Without it, a two-item
    question can fall off the bottom of a big plan's flow table.

    ``site_at`` says where the plan will STAND. On its own it makes the plan's water
    assumption MEASURED rather than assumed: the terrain at that pad is read and the note
    quotes how much of it is under water, at what level, and how far below the dry ground.
    It never changes a number the LP produced -- how many extractors a body of water holds
    is placement geometry no data here carries. With ``save_as`` it is also recorded, with
    yaw and footprint, so later calls can answer "does what stands there match it"
    (diff_vs_save) and "show me" (show_on_map at='plan:<name>'); a recalled plan that
    was sited is measured at its own site without being told again. Use site_plan to set or
    move the siting of an already-stored plan.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
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
        recycle_once=recycle_once,
        supplied=supplied,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    # Its own pair, never written back over the arguments: a recalled plan's site is
    # measured here, and re-saving that plan must not turn its stored yaw and z into the
    # defaults this call happens to carry.
    measure_at, measure_pad = siting_mod.plan_site_args(st, plan, site_at or "", site_footprint)
    report = build_plan_report(
        g,
        st,
        plan_kwargs,
        logistics_items,
        objective=objective,
        site_at=measure_at,
        site_footprint=measure_pad,
    )

    # Persistence is an interface side effect, not part of the answer: the plan is stored
    # here and the resulting sentence handed to the presenter like any other note.
    save_as_note = ""
    if save_as and report.prepared.failure is None:
        plan_id = report.prepared.request.plan_id
        # What the selectors resolved to, stored WITH the request. plan_id hashes the
        # extractor census, so it moves when the world does -- it cannot move when a
        # selector starts meaning a different part of the map, which is a different
        # staleness and the one that re-planned this world's reference plan in silence.
        field = prov.record(g, st, plan_kwargs.get("sources"))
        when = str(st.header.get("save_datetime") or st.header.get("filename") or "")
        sit = None
        if site_at:
            # Resolved BEFORE the store is touched, so a bad coordinate refuses the whole
            # save rather than leaving a half-written plan behind. The footprint falls
            # back to the square the layout budgets, off the solution this call paid for.
            try:
                sit = siting_mod.build_siting(
                    g,
                    st,
                    at=site_at,
                    yaw_deg=site_yaw_deg,
                    footprint=site_footprint,
                    solution=report.prepared.solution,
                    plan_kwargs=plan_kwargs,
                    when=when,
                )
            except ValueError as exc:
                return f"! {exc} -- nothing saved"
        try:
            saved_name = _save_request(
                st, save_as, plan_kwargs, plan_id, plan_notes_text, for_factory, when, field, sit
            )
        except PlanLogError as exc:
            return f"! {exc} -- nothing saved"
        sited = f". Sited: {sit.describe()}" if sit is not None else ""
        path = PlanLog.dir_for(st.world_id)
        pinned = "; ".join(f"{e['selector']}={e['count']} node(s)" for e in field["selectors"])
        save_as_note = (
            f"saved as {saved_name!r} (plan_id {plan_id}) in {path}. "
            f"Recall with plan={saved_name!r} on plan_factory, plan_layout or diff_vs_save"
            + (f". Field recorded: {pinned}" if pinned else "")
            + sited
        )
    elif site_at:
        save_as_note = (
            "site_at was measured but not RECORDED: a siting lives on a STORED plan. Pass "
            "save_as=<name> here, or site an existing plan with site_plan"
        )

    return render_plan_factory(
        g,
        st,
        report,
        objective=objective,
        only_free_nodes=only_free_nodes,
        limit=limit,
        plan_name=plan_name,
        plan_notes=plan_notes,
        save_as_note=save_as_note,
    )


@mcp.tool(structured_output=False)
def plan_layout(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    show: Annotated[
        str, Field(description="floors | blocks | buses | trunks | materials | sites")
    ] = "floors",
    detail: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    # Without these plan_layout re-solves at defaults and schematises a DIFFERENT plan
    # than the one being laid out -- measured at 15,043 MW against the 83,737 MW plan it
    # was asked to draw, because base extraction is a sixth of overclocked.
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    water_extractors: Annotated[
        int | None,
        Field(description="how many Water Extractors your site can actually hold"),
    ] = None,
    sloops: Annotated[
        int,
        Field(description="Somersloops the plan may spend; 0 spends none"),
    ] = 0,
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
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    factory: Annotated[
        str | None,
        Field(description="fit the layout against this factory's existing platform"),
    ] = None,
) -> str:
    """Turn a plan into a buildable schematic: blocks, buses and floors.

    Same arguments as plan_factory, plus ``show``: "floors" (default, the stack),
    "blocks" (every module with its size and rates), "buses" (item flows),
    "trunks" (which resource nodes share each pipe or belt run into the site),
    "materials" (what the whole thing costs to build, machines plus deck), or
    "sites" (cut the plan into named modules and report what crosses between them).

    This is a SCHEMATIC, not a blueprint. It gives modules, connections, floor
    assignment and a space budget. It deliberately does NOT give world coordinates or
    belt routing -- there is no terrain data here, so those would be invented.

    Blocks are split by throughput: 46 Refineries needing 1380 m3/min of crude cannot
    share one manifold when a Mk2 pipe carries 600, so that is 3 blocks. Floors follow
    chain depth, with a logistics deck between each pair of production floors.
    """
    g = game()
    if gone := retired(("detail", detail, "show")):
        return gone
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

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

    # Same solve-shaping arguments as plan_factory, so a layout can be asked for
    # directly rather than only via a saved plan.
    supplied = dict(
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
        # Into the SCENARIO, not just into build_layout. Passing a tier to the schematic
        # while the solve kept the default is the same drift 8.5a documents: the trunk
        # view reads sc.pipe_m3min, so pipe_tier="Mk1" changed the block split and left
        # the trunk count untouched, describing two different plants in one response.
        #
        # Only when the caller ASKED for a tier, though. Passing the resolved default
        # through made every recalled plan report "overridden this call: belt_ipm,
        # pipe_m3min" -- an override the user never made, which is exactly the kind of
        # noise that trains a reader to skip the override line that does matter.
        belt_ipm=tiers.belt_ipm if tiers.asked_belt else None,
        pipe_m3min=tiers.pipe_m3min if tiers.asked_pipe else None,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    try:
        report = build_layout_report(
            g,
            st,
            plan_kwargs,
            tiers,
            objective=objective,
            show=show,
            sites=sites,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
            factory=factory,
            plan=plan,
        )
    except SelectorError as exc:
        return f"! {exc}"

    return render_layout(
        g,
        st,
        report,
        objective=objective,
        show=show,
        limit=limit,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


@mcp.tool(structured_output=False)
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
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    stage: Annotated[
        int | None,
        Field(description="one startup stage's delta; 0 for the stage overview"),
    ] = None,
    factory: Annotated[
        str | None,
        Field(description="only count this factory's machines as already built"),
    ] = None,
) -> str:
    """What to change to get from the factory you have to the one plan_factory plans.

    Takes exactly plan_factory's arguments and re-solves, because the server keeps no
    state. Both tools print a plan id hashed over the arguments AND the save-derived
    solve inputs, so two responses carrying the same id are provably the same plan.

    Machines are matched by IDENTITY, never by position: a manufacturer on (building,
    recipe), a generator on its building alone since its fuel is piped in rather than
    set on the machine, an extractor on the node it occupies. A Refinery running some
    other recipe is busy, not spare, so it never counts toward the plan.

    Actions are ordered free-first -- UNPAUSE, then SETRECIPE on machines that produce
    nothing today, then BUILD. Stages follow the plan's own chain depth and the power
    arithmetic is INCREMENTAL, charging only the machines you have yet to place. Where
    a machine cannot be identified at all (Water Extractors have no recipe and no
    resolvable node) the answer is a RANGE, never a number.

    Recall a stored plan with ``plan=`` and the diff is also grouped by STARTUP STAGE --
    the same partition commission_plan emits -- so it answers "which stage am I in".
    ``stage=<n>`` narrows to one stage's delta; ``stage=0`` asks for the overview
    without a stored plan, at the cost that the numbering moves when the arguments do.

    Built and energised are DIFFERENT states and the save separates them in one
    direction only: a machine that produced in the last 300s window certainly had
    power, while one that did not may be unpowered, starved, blocked or idle. Grid
    membership is not persisted at all, so a stage is never reported as "unpowered" --
    only as built with nothing proven running, which is exactly what a finished but
    not-yet-energised block looks like.

    Saves are read-only: this never proposes writing one, and there is no dismantle
    action. Machines standing among the plan but not in it are listed for you to judge.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
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
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    try:
        report = build_diff_report(
            g,
            st,
            plan_kwargs,
            objective=objective,
            plan=plan,
            plan_name=plan_name,
            stage=stage,
            factory=factory,
        )
    except SelectorError as exc:
        return f"! {exc}"

    return render_diff(
        g,
        st,
        report,
        objective=objective,
        limit=limit,
        show_cost=show_cost,
        stage=stage,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


@mcp.tool(structured_output=False)
def explain_byproducts(
    objective: str = "max_mw",
    target_item: str | None = None,
    item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 12,
) -> str:
    """Explain which byproducts stall a plan, and what can legally consume them.

    Every item balance is an equality, so a byproduct with no consumer makes a plan
    INFEASIBLE rather than silently vanishing. This says WHICH item is stuck, whether
    it can be sunk (solids only -- a fluid must be consumed exactly or packaged
    first), and which recipes would absorb it, split into ones this world has
    unlocked and ones it does not.

    Pass ``item`` to focus on one byproduct instead of the whole plan.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    return byproducts_text.explain(
        g,
        st,
        objective=objective,
        target_item=target_item,
        item=_item_id(item) if item else None,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        limit=render.clamp(limit, default=12),
    )


@mcp.tool(structured_output=False)
def compare_recipe_options(
    item: str,
    rate: float = 100.0,
    per_resource: str | None = None,
    outlets: list[str] | None = None,
    allow_sinks: bool = True,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
) -> str:
    """Rank whole ROUTES to make an item by what each actually costs.

    Not a recipe list -- alternates_for_item already does that. Each route is solved
    end to end with the LP, so the comparison is Crude -> Alt HOR -> Diluted Fuel
    against Crude -> Fuel, priced in raw resource per unit, whole buildings, net
    power, and byproducts needing an outlet.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    iid = _item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    result = compare.compare_routes(
        g,
        st,
        iid,
        rate=rate,
        allow_sinks=allow_sinks,
        outlets=outlets,
        per_resource=_item_id(per_resource) if per_resource else None,
    )
    return render_comparison(result, limit=render.clamp(limit, default=10))


@mcp.tool(structured_output=False)
def bom(
    item: str,
    qty: float = 60.0,
    allow_sinks: bool = True,
    outlets: list[str] | None = None,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    offset: int = 0,
) -> str:
    """Flattened bill of materials: total raw and intermediate rates for qty/min of an item.

    ``qty`` is a RATE, per minute. Solved by the LP, never by expanding the recipe
    tree: Recycled Plastic and Recycled Rubber form a real 2-cycle, so an expansion
    has no correct depth limit. Every row names the recipe chosen for that item,
    because alternates change the totals materially.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        result = bom_mod.build_bom(
            g,
            st,
            item,
            qty=qty,
            allow_sinks=allow_sinks,
            outlets=outlets,
            exclude_recipes=exclude_recipes,
            only_recipes=only_recipes,
        )
    except ValueError as exc:
        return str(exc)
    return render_bom(result, limit=render.clamp(limit, default=20), offset=max(0, offset))


@mcp.tool(structured_output=False)
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
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
) -> str:
    """In what order to switch a built plant on, without blowing the fuse.

    This is a STARTUP order, not a build order, and the difference removes most of the
    problem. Building costs materials, not power -- a machine draws only when it runs --
    so the whole plant can be constructed at leisure, drawing nothing, and then energised
    block by block. Nothing here tells you what to build first.

    The constraint is one line, and it is hard: at every step, energised consumer draw
    must stay under the headroom plus generation from generators already burning fuel.
    Exceeding it in Satisfactory does not degrade gracefully -- the fuse blows and the
    whole grid stops until it is reset by hand, including the plant that was feeding it.

    Generators are free to energise (0 MW draw, read from the dump), so a wave costs its
    consumers and refunds its generators, and that refund pays for the next wave.

    Takes plan_factory's arguments, or recall a saved plan with ``plan=``.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
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
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    report = build_commission_report(g, st, plan_kwargs, headroom_mw, objective=objective)

    return render_commission(
        g,
        st,
        report,
        objective=objective,
        limit=limit,
        offset=offset,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


@mcp.tool(structured_output=False)
def rank_unlocks(
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
    query: Annotated[
        str | None, Field(description="only test alternates whose name matches")
    ] = None,
    search: Annotated[str | None, Field(description="retired -- write query= instead")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 15,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
) -> str:
    """What every locked alternate recipe would be worth to THIS plan.

    One counterfactual per candidate: solve the plan, solve it again with the recipe
    added, report the difference. It answers "which unlock should I chase" with a number
    in the plan's own units instead of a tier list, because a recipe's worth depends
    entirely on what you already have.

    A zero is an answer. Most candidates change nothing, and "you are not missing anything
    here" is a decision -- it is otherwise reached by walking the recipe tree by hand.

    Deltas are an UPPER bound: a candidate needing a machine you have not built is judged
    as if you had it, and the machine is named. Alternates currently offered by a pending
    hard drive are flagged, which is the difference between "worth having" and "claimable
    now".
    """
    if gone := retired(("search", search, "query")):
        return gone
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
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
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    if prepared.failure:
        return render.envelope(
            f"# {prepared.failure.headline} -- nothing to rank against",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )

    pool = st.locked_alternates
    if query:
        needle = query.strip().casefold()
        pool = [r for r in pool if needle in r.name.casefold()]
        if not pool:
            return f"! no LOCKED alternate matches {query!r}"

    sweep = sweep_unlocks(prepared.request, st, pool)
    # The candidates by id, so the granted-by cell is answered off the same recipe the
    # sweep measured rather than a second lookup that could miss.
    swept = {r.cls: r for r in pool}
    # Which of these you could claim today. A recipe worth 14,540 MW that is sitting in a
    # pending drive is a different instruction from one that needs a drive you have not
    # found yet.
    on_offer: dict[str, int] = {}
    for offer in st.hard_drive_offers:
        for option in offer.options:
            for recipe in option["recipes"]:
                on_offer[recipe.cls] = offer.hard_drive_id

    movers = sweep.movers
    rows = [
        (
            render.num(r.gain),
            f"{r.gain / sweep.baseline:+.1%}" if sweep.baseline else "",
            r.name[:34],
            # The work you actually do, in the same words search_recipes and recipe_detail
            # use: a hard drive and a milestone are different evenings. Never truncated --
            # a cut-off schematic name is a name the reader cannot look up.
            granted_by_label(st.game, swept[r.recipe], width=40),
            render.num(r.machines),
            f"drive {on_offer[r.recipe]}" if r.recipe in on_offer else "",
            ", ".join(r.needs)[:18],
            ", ".join(r.activates)[:40],
        )
        for r in movers[: render.clamp(limit, default=15)]
    ]
    # A ranking, so no offset: what falls off the bottom is what changed this plan least.
    notes = [*plan_notes]
    if plan_name:
        notes.insert(0, f"recalled saved plan {plan_name!r}")
    notes.append(
        f"{sweep.tried} locked alternate(s) tested, {len(movers)} changed this plan. "
        "The rest are worth nothing HERE -- which is a result, not a gap: it is the "
        "answer you would otherwise get by walking the tree by hand"
    )
    notes.append(
        "deltas are an UPPER bound: a candidate is solved as if any machine it needs "
        "already existed, and that machine is named in 'needs'"
    )
    if sweep.unsolved:
        # Adding a recipe only ever widens the LP, so an unsolved counterfactual is a
        # solver failure and never a verdict on the recipe.
        shown = sweep.unsolved[:4]
        notes.append(
            f"INFEASIBLE: {len(sweep.unsolved)} candidate(s) did not solve with the recipe "
            "added, so their worth is UNKNOWN rather than zero -- "
            + ", ".join(r.name for r in shown)
            + (f" (+{len(sweep.unsolved) - len(shown)} more)" if len(sweep.unsolved) > 4 else "")
        )
    notes.append(
        "'activates' is what the gain DEPENDS on -- processes the counterfactual switches "
        "on that this plan does not currently use. A headline number that turns on "
        "reintroducing a chain you deleted is a decision, not a free win"
    )
    # The trap this tool set for its own author. Run with ad-hoc arguments it measures a
    # DIFFERENT plant from the one saved, and the answers genuinely differ: Turbo Blend
    # Fuel is worth +13.6% against unconstrained Spire Coast and exactly nothing against
    # the saved plan, which bans Turbofuel and coal generators.
    if not plan and st.plans.plans:
        notes.append(
            "measured against the ARGUMENTS GIVEN, not against a saved plan. This world "
            f"has {len(st.plans.plans)} saved plan(s) ("
            + ", ".join(x.name for x in st.plans.plans[:3])
            + ") whose exclusions may forbid these gains -- pass plan=<name> to rank "
            "against the architecture you actually chose"
        )
    claimable = [r for r in movers if r.recipe in on_offer]
    if claimable:
        notes.append(
            "claimable NOW from a pending hard drive: "
            + ", ".join(f"{r.name} (drive {on_offer[r.recipe]})" for r in claimable[:4])
            + " -- use advise_hard_drive_pick for that drive's full comparison"
        )
    return render.envelope(
        "\n".join(
            [
                f"# unlock value for {objective}" + (f" ({plan_name})" if plan_name else ""),
                f"# {st.age_note}",
                (
                    f"baseline={render.num(sweep.baseline)}  candidates={sweep.tried}  "
                    f"movers={len(movers)}"
                ),
            ]
        ),
        render.table(
            (
                "gain",
                "vs base",
                "alternate",
                "granted by",
                "machines",
                "on offer",
                "needs",
                "activates",
            ),
            rows,
            total=len(movers),
            limit=render.clamp(limit, default=15),
            hint="raise limit, or narrow with query= -- a ranking has no offset",
        ),
        notes,
    )
