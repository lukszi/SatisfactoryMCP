"""Game-data lookups: items, recipes, alternates, buildings.

Read-only over the normalized dump; ``alternates_for_item(plan=)`` also re-solves a stored
plan and journals the look (docs/planner-p3_contract.md §6.1)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from pydantic import Field

from ....core.gamedata import search
from ....core.gamedata.model import Building, GameData
from ....core.gamedata.unlocks import granted_by_label
from ....domain.planning.analysis import swaps
from ....domain.planning.analysis.views import SwapOption
from ....domain.planning.stored.planlog import PlanLog
from ....domain.planning.stored.recall import expand_plan_pin
from ....domain.session import journal
from ....domain.world.state import WorldState
from ....presenters.text import primitives as render
from ....presenters.text.search import item_flows, render_search
from .. import app
from ..params import AsOf, Limit


def _no_save_note(reason: str | None) -> str:
    """Why the HAVE/LOCKED column is blank, in the one wording all three tools use.

    A blank column read without this note says "nothing is unlocked". The reason is carried
    through because "could not read save" covers a save mid-write, a missing directory and a
    game patch, and only the first is worth retrying.
    """
    detail = f" ({reason})" if reason else ""
    return f"no save could be read{detail}, so HAVE/LOCKED is blank -- game data only"


@app.tool()
def search_items(query: str, limit: Limit = 10, offset: int = 0) -> str:
    """Find items by name. Returns form, energy and sink points."""
    hits = search.find_items(app.game(), query)
    window = render.page(limit, offset)
    page = window.of(hits)
    rows = [
        (i.name, "fluid" if i.is_fluid else "solid", render.num(i.energy_mj), i.sink_points)
        for i in page
    ]
    body = render.table(
        ("item", "form", "MJ", "sink_pts"),
        rows,
        total=len(hits),
        offset=window.start,
        limit=window.size,
    )
    footer = render.ids_footer((i.name, i.cls) for i in page)
    return render.envelope(f"# {len(hits)} item(s) matching {query!r}", body + "\n" + footer)


@app.tool()
def recipe_detail(recipe_id: str) -> str:
    """Exact numbers for one recipe: rates, machine, power, unlock source.

    Takes a class id OR a display name, resolved the way `exclude_recipes` resolves one.
    """
    g = app.game()
    r, hits = search.find_recipe(g, recipe_id)
    if r is None and hits:
        # Ambiguous is not unknown: listing the candidates is the answer.
        shown = ", ".join(g.recipes[h].name for h in hits[:8])
        return f"{recipe_id!r} matches {len(hits)} recipes: {shown}"
    if r is None:
        return f"unknown recipe {recipe_id!r} -- use search_recipes to find the id"
    b = g.machine(r)
    unlocks = [g.schematics[s].name for s in r.unlocked_by if s in g.schematics]
    lines = [
        f"{r.name}  ({'ALTERNATE' if r.is_alternate else r.kind})",
        render.kv(
            [
                ("machine", b.name if b else "-"),
                ("cycle", f"{render.num(r.duration_s)}s"),
                ("power", f"{render.num(g.recipe_power_mw(r))}MW"),
            ]
        ),
        f"in/min : {item_flows(g, r.ingredients)}",
        f"out/min: {item_flows(g, r.products)}",
        f"unlock : {', '.join(unlocks) or '-'}",
    ]
    if r.is_variable_power:
        lines.append(
            f"variable power: {render.num(r.power_min_mw)}-{render.num(r.power_max_mw)} MW"
        )
    return "\n".join(lines)


def _delta_cells(option: SwapOption) -> list[str]:
    delta = option["delta"]
    if delta is None:
        return ["-", "-", "-"]
    if not delta["comparable"]:
        return [delta["text"], "-", "-"]
    raw = ", ".join(f"{i['name']} {i['delta']:+g}" for i in delta["inputs"][:2])
    return [f"{delta['machines']:+d}", f"{delta['mw_draw']:+g}", raw or "0"]


def _alternates_in_plan(
    g: GameData,
    st: WorldState,
    iid: str,
    plan: str,
    include_locked: bool,
    ctx: app.ToolContext | None,
) -> str:
    """The alternates table against a stored plan: status and deltas per recipe."""
    try:
        key, echo = expand_plan_pin(st, plan)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    stored = st.plans.find(key)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none)"
        return f"! no saved plan named {plan!r}. Saved: {known}"
    state = PlanLog(st.world_id).state(stored.key)
    result = swaps.swap_deltas(g, st, state, iid, spoilers=include_locked)
    rows: list[list[str]] = []
    for option in result["options"]:
        r = g.recipes[option["recipe_id"]]
        status = option["status"]
        if option["banned_by"] and status == "banned":
            status += f" by {option['banned_by']!r}"
        rows.append(
            [
                option["name"],
                option["machine"] or "-",
                item_flows(g, r.ingredients),
                item_flows(g, r.products),
                "HAVE" if option["unlocked"] else "LOCKED",
                status,
                *_delta_cells(option),
            ]
        )
    headers = ["recipe", "building", "in/min", "out/min", "status", "in plan"]
    body = render.table([*headers, "Δmach", "ΔMW draw", "Δraw"], rows)
    footer = render.ids_footer((o["name"], o["recipe_id"]) for o in result["options"])
    notes = [echo] if echo else []
    if result["hidden"]:
        notes.append(f"{result['hidden']} locked recipe(s) hidden (include_locked=false)")
    notes.append(
        "deltas compare the plan with each recipe required against the plan as it is; "
        "facts, not a ranking"
    )
    notes.append(
        f'plan "{state.name}" v{state.rev}; require one with plan_factory(plan="{state.name}", '
        f'required=[...], base_rev={state.rev}, save_as="{state.name}")'
    )
    journal.append(
        st.world_id,
        "plan.view",
        actor=app.actor(ctx),
        sav=app.save_token(st),
        tool="alternates_for_item",
        plan=state.key,
        rev=state.rev,
        args={"view": "alternates", "item": iid},
        text=f"looked at recipes for {g.item_name(iid)}",
    )
    return render.envelope(f"# {result['text']}", body + "\n" + footer, notes)


@app.tool()
def alternates_for_item(
    item: str,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    include_locked: bool = True,
    plan: Annotated[
        str | None,
        Field(description="a stored plan: add what requiring each recipe would change in it"),
    ] = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """Every automatable recipe that makes an item, alternates first.

    When a save is readable, each row is marked HAVE or LOCKED, and a LOCKED one says
    which schematic would grant it -- a hard drive and a milestone are different work.
    With ``plan`` each recipe also carries its status in that plan and what requiring it
    would change there: machines, MW draw and the first raw inputs.
    """
    g = app.game()
    iid = app.resolve_item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    if plan:
        st = app.load_world(save, world, as_of)
        return _alternates_in_plan(g, st, iid, plan, include_locked, ctx)
    producers = search.makers_of(g, iid)
    # None, not an empty set: "no save" and "a save with no recipes" are different facts.
    st, save_error = app.load_world_or_none(save, world, as_of)
    have: set[str] | None = st.available_recipe_ids if st is not None else None
    shown = [r for r in producers if include_locked or have is None or r.cls in have]
    # Only when something is locked; otherwise the column is a row of blanks.
    granted = have is not None and any(r.cls not in have for r in shown)
    rows: list[list[str]] = []
    for r in shown:
        status = "-" if have is None else ("HAVE" if r.cls in have else "LOCKED")
        b = g.machine(r)
        row = [
            r.name,
            f"{b.name} {render.num(g.recipe_power_mw(r))}MW" if b else "-",
            item_flows(g, r.ingredients),
            item_flows(g, r.products),
            status,
        ]
        if granted:
            row.append("" if status == "HAVE" else granted_by_label(g, r, width=60))
        rows.append(row)
    n_alt = sum(1 for r in producers if r.is_alternate)
    headers = ["recipe", "building", "in/min", "out/min", "status"]
    if granted:
        headers.append("granted by")
    body = render.table(headers, rows)
    footer = render.ids_footer((r.name, r.cls) for r in producers)
    notes = [_no_save_note(save_error)] if have is None else []
    return render.envelope(
        f"# {len(rows)} automatable recipe(s) make {g.item_name(iid)} "
        f"({n_alt} alternate). rates=/min at 100% clock, one machine.",
        body + "\n" + footer,
        notes,
    )


@app.tool()
def search_recipes(
    query: str = "",
    consumes: str | None = None,
    produces: str | None = None,
    recipe_kind: Annotated[str, Field(description="part | building | manual | all")] = "part",
    only_alternates: bool = False,
    include_events: bool = False,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
    offset: int = 0,
    kind: Annotated[str | None, Field(description="retired -- write recipe_kind= instead")] = None,
) -> str:
    """Search recipes by name, or by what they consume/produce. Marks HAVE/LOCKED.

    ``consumes="Rubber"`` is the reverse lookup: every recipe that eats an item.
    ``recipe_kind`` is "part" (default), "building" (build-gun costs), "manual" or
    "all" -- and the header counts EVERY kind over the whole recipe table whatever it
    is set to, so a part-only view still says how many buildings eat the item.
    """
    if gone := app.retired(("kind", kind, "recipe_kind")):
        return gone
    g = app.game()
    notes: list[str] = []
    consumes_id = produces_id = None
    if consumes:
        consumes_id = app.resolve_item_id(consumes)
        if consumes_id is None:
            return f"no item matching {consumes!r}"
    if produces:
        produces_id = app.resolve_item_id(produces)
        if produces_id is None:
            return f"no item matching {produces!r}"
    if consumes_id and produces_id:
        notes.append("consumes and produces are ANDed: this is the loop test, not a union")

    st, save_error = app.load_world_or_none(save, world, as_of)
    have: set[str] | None = None
    if st is None:
        notes.append(_no_save_note(save_error))
    else:
        have = st.available_recipe_ids

    try:
        hits, census = search.search(
            g,
            query=query,
            consumes=consumes_id,
            produces=produces_id,
            recipe_kind=recipe_kind,
            only_alternates=only_alternates,
            include_events=include_events,
            unlocked=have,
        )
    except ValueError as exc:
        return f"! {exc}"
    subject = "matching " + repr(query) if query else "in the game"
    column = ""
    if consumes_id:
        subject = f"consume {g.item_name(consumes_id)}"
        column = f"uses {g.item_name(consumes_id)}"
    elif produces_id:
        subject = f"produce {g.item_name(produces_id)}"
        column = f"makes {g.item_name(produces_id)}"
    if query and (consumes_id or produces_id):
        subject += f" and match {query!r}"
    if only_alternates:
        subject += " (alternates only)"
    return render_search(
        g,
        hits,
        census,
        subject,
        column,
        limit=limit,
        offset=offset,
        recipe_kind=recipe_kind,
        notes=notes,
    )


#: The build-piece families, named by the native class each lives under. "Wall" also
#: catches the corner-wall natives, which is the grouping the game itself uses; doors
#: are walls you can walk through and are listed with them.
_ARCH_TOKENS = ("Foundation", "Ramp", "Wall", "Pillar", "Beam", "Stair", "Walkway", "Door")

#: Which buildings each ``building_kind`` lists. Pumps are logistics: they move fluid, and
#: their head lift is the number a fluid plan needs.
_BUILDING_KINDS: dict[str, Callable[[Building], bool]] = {
    "production": lambda b: b.is_manufacturer,
    "extractor": lambda b: b.is_extractor,
    "generator": lambda b: b.is_generator,
    "logistics": lambda b: bool(b.items_per_min or b.flow_m3_min or b.head_lift_m),
    "foundation": lambda b: "Foundation" in b.native,
    "ramp": lambda b: "Ramp" in b.native,
    "wall": lambda b: "Wall" in b.native or "Door" in b.native,
    "pillar": lambda b: "Pillar" in b.native,
    "beam": lambda b: "Beam" in b.native,
    "architecture": lambda b: any(t in b.native for t in _ARCH_TOKENS),
    "all": lambda b: True,
}


def _building_detail(b: Building) -> str:
    """The one figure that says what a building does: its rate, its output or its lift."""
    if b.is_extractor and b.base_extract_rate:
        return f"{render.num(b.extract_rate('normal'))}/min @normal"
    if b.is_generator:
        detail = f"{render.num(b.power_production_mw)}MW out"
        if b.requires_supplemental:
            detail += f", {render.num(b.supplemental_m3_min())} m3/min water"
        return detail
    if b.items_per_min:
        return f"{render.num(b.items_per_min)} items/min"
    if b.flow_m3_min:
        return f"{render.num(b.flow_m3_min)} m3/min"
    if b.head_lift_m:
        return f"lifts {render.num(b.head_lift_m)}m head (max {render.num(b.max_head_lift_m)})"
    return ""


@app.tool()
def list_buildings(
    building_kind: Annotated[
        str,
        Field(
            description="production | extractor | generator | logistics | foundation | "
            "ramp | wall | pillar | beam | architecture | all"
        ),
    ] = "production",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
    kind: Annotated[
        str | None, Field(description="retired -- write building_kind= instead")
    ] = None,
) -> str:
    """Buildings by kind: production, extractor, generator, logistics, foundation,
    ramp, wall, pillar, beam, architecture (all five families together), or all.

    Rows are marked HAVE or LOCKED against the save when one can be read -- it matters most
    for ``logistics``, where a planner assuming a tier it has not unlocked gets every line
    count wrong. Paged with ``offset=``: ``all`` is hundreds of buildings.
    """
    if gone := app.retired(("kind", kind, "building_kind")):
        return gone
    g = app.game()
    # Game data alone is still a useful answer: without a save the columns go blank and a
    # note says which save could not be read and why.
    st, save_error = app.load_world_or_none(save, world, as_of)
    unlocked, built = (st.unlocked_building_ids, st.built_counts) if st is not None else (None, {})
    chosen_kind = (building_kind or "").strip().casefold()
    want = _BUILDING_KINDS.get(chosen_kind)
    if want is None:
        return (
            f"! unknown building_kind {building_kind!r}. "
            f"Choose from: {', '.join(sorted(_BUILDING_KINDS))}"
        )
    picks = sorted((b for b in g.buildings.values() if want(b)), key=lambda b: b.name)
    window = render.page(limit, offset, default=25)
    page = window.of(picks)
    rows: list[tuple[object, ...]] = []
    for b in page:
        fp = b.footprint
        have = "" if unlocked is None else ("HAVE" if b.cls in unlocked else "LOCKED")
        rows.append(
            (
                have,
                b.name,
                built.get(b.cls, "") or "",
                f"{render.num(b.power_mw)}MW",
                render.num(b.max_clock),
                b.sloop_slots,
                str(fp) if fp else "-",
                fp.foundations if fp else "-",
                _building_detail(b),
            )
        )

    notes = [
        (
            "size is the axis-aligned clearance box (WxDxH); 'found' is the 8m "
            "foundations one machine covers, ignoring edges shared with a neighbour, "
            "so a row of N machines needs somewhat fewer than N x found"
        )
    ]
    if unlocked is not None and chosen_kind == "logistics" and st is not None:
        best = [carrier for carrier in (st.best_belt(), st.best_pipe()) if carrier is not None]
        chosen = (
            ", ".join(f"{g.buildings[c].name} ({v:g})" for c, v in best if c) or "none unlocked"
        )
        notes.append(
            f"planning defaults to the fastest UNLOCKED tier: {chosen}. A tier assumed "
            "rather than checked changes every belt and pipe count in a plan"
        )
    elif unlocked is None:
        notes.append(_no_save_note(save_error))
    # Page-scoped, like the footer: naming buildings this page does not show would read as
    # rows gone missing.
    unknown = [b.name for b in page if not b.footprint]
    if unknown:
        notes.append(
            f"no clearance data, so no size: {', '.join(sorted(unknown))}. "
            "plan_layout leaves these out of its space budget rather than guessing"
        )

    return render.envelope(
        f"# {len(picks)} {building_kind} building(s)",
        render.table(
            (
                "have",
                "building",
                "built",
                "power",
                "max_clock",
                "sloops",
                "size",
                "found",
                "detail",
            ),
            rows,
            total=len(picks),
            offset=window.start,
            limit=limit,
        )
        + "\n"
        + render.ids_footer((b.name, b.cls) for b in page),
        notes,
    )
