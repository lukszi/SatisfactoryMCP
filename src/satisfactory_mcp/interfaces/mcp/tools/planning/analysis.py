"""Questions about one item or plan: byproducts, routes, bills of materials, unlocks."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .....core.gamedata.unlocks import granted_by_label
from .....domain.planning.analysis import bom as bom_mod
from .....domain.planning.analysis import recipe_routes
from .....domain.planning.analysis.byproducts import analyse
from .....domain.planning.analysis.sensitivity import sweep_unlocks
from .....domain.planning.solver.prepare import prepare
from .....presenters.text import primitives as render
from .....presenters.text.bom import render_bom
from .....presenters.text.byproducts import render_byproducts
from .....presenters.text.compare import render_comparison
from ... import app
from ...params import AsOf, Limit, PlanName
from ._requests import _recall_request, _solve_args


@app.tool()
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
    g = app.game()
    st = app.load_world(save, world, as_of)
    report = analyse(
        g,
        st,
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        allow_sinks=allow_sinks,
        item=app.resolve_item_id(item) if item else None,
        exclude_recipes=exclude_recipes,
    )
    return render_byproducts(g, report, limit=render.clamp(limit, default=12))


@app.tool()
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
    g = app.game()
    st = app.load_world(save, world, as_of)
    iid = app.resolve_item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    result = recipe_routes.compare_routes(
        g,
        st,
        iid,
        rate=rate,
        allow_sinks=allow_sinks,
        outlets=outlets,
        per_resource=app.resolve_item_id(per_resource) if per_resource else None,
    )
    return render_comparison(result, limit=render.clamp(limit, default=10))


@app.tool()
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
    g = app.game()
    st = app.load_world(save, world, as_of)
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


def _drives_offering(st) -> dict[str, int]:
    """Recipe class id -> the pending hard drive offering it: claimable today."""
    on_offer: dict[str, int] = {}
    for offer in st.hard_drive_offers:
        for option in offer.options:
            for recipe in option["recipes"]:
                on_offer[recipe.cls] = offer.hard_drive_id
    return on_offer


def _unlock_notes(st, sweep, movers, on_offer: dict[str, int], plan: str | None) -> list[str]:
    """What the ranking can and cannot claim, and which gains are claimable now."""
    notes = [
        (
            f"{sweep.tried} locked alternate(s) tested, {len(movers)} changed this plan. "
            "The rest are worth nothing HERE -- which is a result, not a gap: it is the "
            "answer you would otherwise get by walking the tree by hand"
        ),
        (
            "deltas are an UPPER bound: a candidate is solved as if any machine it needs "
            "already existed, and that machine is named in 'needs'"
        ),
    ]
    if sweep.unsolved:
        # Adding a recipe only ever widens the LP, so an unsolved counterfactual is a
        # solver failure and never a verdict on the recipe.
        notes.append(
            f"INFEASIBLE: {len(sweep.unsolved)} candidate(s) did not solve with the recipe "
            "added, so their worth is UNKNOWN rather than zero -- "
            + render.capped([r.name for r in sweep.unsolved], 4)
        )
    notes.append(
        "'activates' is what the gain DEPENDS on -- processes the counterfactual switches "
        "on that this plan does not currently use. A headline number that turns on "
        "reintroducing a chain you deleted is a decision, not a free win"
    )
    # Ad-hoc arguments measure a different plant from the saved one, and the answers differ.
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
    return notes


@app.tool()
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
    plan: PlanName = None,
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
    if gone := app.retired(("search", search, "query")):
        return gone
    g = app.game()
    st = app.load_world(save, world, as_of)

    supplied = _solve_args(
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
    request = _recall_request(st, plan, supplied)

    prepared = prepare(g, st, request.kwargs, objective_label=request.objective, diagnose=False)
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
    # The granted-by cell reads the very recipe the sweep measured, not a second lookup.
    swept = {r.cls: r for r in pool}
    on_offer = _drives_offering(st)
    movers = sweep.movers
    rows = [
        (
            render.num(r.gain),
            f"{r.gain / sweep.baseline:+.1%}" if sweep.baseline else "",
            r.name[:34],
            # Never truncated: a cut-off schematic name is a name the reader cannot look up.
            granted_by_label(st.game, swept[r.recipe], width=40),
            render.num(r.machines_delta),
            f"drive {on_offer[r.recipe]}" if r.recipe in on_offer else "",
            ", ".join(r.needs)[:18],
            ", ".join(r.activates)[:40],
        )
        for r in movers[: render.clamp(limit, default=15)]
    ]
    notes = [*request.notes, *_unlock_notes(st, sweep, movers, on_offer, request.plan)]
    return render.envelope(
        "\n".join(
            [
                f"# unlock value for {request.objective}"
                + (f" ({request.name})" if request.name else ""),
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
