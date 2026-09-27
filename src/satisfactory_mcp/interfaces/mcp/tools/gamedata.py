"""Game-data lookups: items, recipes, alternates, buildings.

Read-only over the normalized dump. Nothing here touches a save."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ....core.gamedata import search
from ....core.gamedata.unlocks import granted_by_label
from ....presenters.text import primitives as render
from ....presenters.text.search import render_search
from ..app import AsOf, Limit, _item_id, _state, game, mcp, retired


def _no_save_note(reason: str | None) -> str:
    """Why the HAVE/LOCKED column is blank, in the one wording all three tools use.

    Three tools here mark rows against the save and all three blank the column when it
    will not read. Two of them used to blank it in SILENCE, which produces a confident
    table of dashes that reads as "nothing is unlocked" -- the single most misleading
    answer this surface can give a player deciding what to build next, because it is
    indistinguishable from a correct answer about a fresh world.

    The reason is carried through rather than summarised: "could not read save" is the
    same sentence for a save mid-write, a save directory that is not there and a game
    patch this parser has not caught up with, and only the first is worth retrying.
    """
    detail = f" ({reason})" if reason else ""
    return f"no save could be read{detail}, so HAVE/LOCKED is blank -- game data only"


@mcp.tool(structured_output=False)
def search_items(query: str, limit: Limit = 10, offset: int = 0) -> str:
    """Find items by name. Returns form, energy and sink points."""
    hits = search.find_items(game(), query)
    page = hits[offset : offset + render.clamp(limit)]
    rows = [
        (i.name, "fluid" if i.is_fluid else "solid", render.num(i.energy_mj), i.sink_points)
        for i in page
    ]
    body = render.table(
        ("item", "form", "MJ", "sink_pts"), rows, total=len(hits), offset=offset, limit=limit
    )
    footer = render.ids_footer((i.name, i.cls) for i in page)
    return render.envelope(f"# {len(hits)} item(s) matching {query!r}", body + "\n" + footer)


@mcp.tool(structured_output=False)
def recipe_detail(recipe_id: str) -> str:
    """Exact numbers for one recipe: rates, machine, power, unlock source.

    Takes a class id OR a display name. Refusing the name cost a caller two round trips
    to fetch an id this function could resolve itself, which is a poor trade for strictness
    that buys nothing -- `match_recipes` already does exactly this resolution for
    `exclude_recipes`.
    """
    from ....domain.planning.scenario import find_recipe

    g = game()
    r, hits = find_recipe(g, recipe_id)
    if r is None and hits:
        # Ambiguous is not the same as unknown, and listing the candidates is the
        # answer rather than an invitation to search again.
        shown = ", ".join(g.recipes[h].name for h in hits[:8])
        return f"{recipe_id!r} matches {len(hits)} recipes: {shown}"
    if r is None:
        return f"unknown recipe {recipe_id!r} -- use search_recipes to find the id"
    b = g.machine(r)
    ing = render.flows((g.item_name(f.item), f.per_min, False) for f in r.ingredients)
    out = render.flows((g.item_name(f.item), f.per_min, False) for f in r.products)
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
        f"in/min : {ing}",
        f"out/min: {out}",
        f"unlock : {', '.join(unlocks) or '-'}",
    ]
    if r.is_variable_power:
        lines.append(
            f"variable power: {render.num(r.power_min_mw)}-{render.num(r.power_max_mw)} MW"
        )
    return "\n".join(lines)


@mcp.tool(structured_output=False)
def alternates_for_item(
    item: str,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    include_locked: bool = True,
) -> str:
    """Every automatable recipe that makes an item, alternates first.

    When a save is readable, each row is marked HAVE or LOCKED, and a LOCKED one says
    which schematic would grant it -- a hard drive and a milestone are different work.
    """
    g = game()
    iid = _item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    producers = search.makers_of(g, iid)
    # ``None``, not an empty set. The status column blanks for both "no save" and "a save
    # whose recipe list is empty", and only one of those is a fact about the world -- so
    # the reason is carried rather than collapsed, and said out loud in the notes below.
    have: set[str] | None = None
    save_error: str | None = None
    try:
        have = _state(save, world, as_of).available_recipe_ids
    except Exception as exc:
        save_error = str(exc)
    shown = [r for r in producers if include_locked or have is None or r.cls in have]
    # Only when something is locked: on a page where everything is HAVE the column would
    # be a row of blanks.
    granted = have is not None and any(r.cls not in have for r in shown)
    rows = []
    for r in shown:
        status = "-" if have is None else ("HAVE" if r.cls in have else "LOCKED")
        b = g.machine(r)
        row = [
            r.name,
            f"{b.name} {render.num(g.recipe_power_mw(r))}MW" if b else "-",
            render.flows((g.item_name(f.item), f.per_min, False) for f in r.ingredients),
            render.flows((g.item_name(f.item), f.per_min, False) for f in r.products),
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
    # The blank status column is the WHOLE point of this tool for a player deciding what to
    # build, and a blank that means "could not read your save" reads exactly like a blank
    # that means "nothing is unlocked". Same note ``list_buildings`` already carries.
    notes = [_no_save_note(save_error)] if have is None else []
    return render.envelope(
        f"# {len(rows)} automatable recipe(s) make {g.item_name(iid)} "
        f"({n_alt} alternate). rates=/min at 100% clock, one machine.",
        body + "\n" + footer,
        notes,
    )


@mcp.tool(structured_output=False)
def search_recipes(
    query: str = "",
    consumes: str | None = None,
    produces: str | None = None,
    recipe_kind: Annotated[
        str, Field(description="part | building | manual | all")
    ] = "part",
    only_alternates: bool = False,
    include_events: bool = False,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
    offset: int = 0,
    kind: Annotated[
        str | None, Field(description="retired -- write recipe_kind= instead")
    ] = None,
) -> str:
    """Search recipes by name, or by what they consume/produce. Marks HAVE/LOCKED.

    ``consumes="Rubber"`` is the reverse lookup: every recipe that eats an item.
    ``recipe_kind`` is "part" (default), "building" (build-gun costs), "manual" or
    "all" -- and the header counts EVERY kind over the whole recipe table whatever it
    is set to, so a part-only view still says how many buildings eat the item.
    """
    if gone := retired(("kind", kind, "recipe_kind")):
        return gone
    g = game()
    notes: list[str] = []
    consumes_id = produces_id = None
    if consumes:
        consumes_id = _item_id(consumes)
        if consumes_id is None:
            return f"no item matching {consumes!r}"
    if produces:
        produces_id = _item_id(produces)
        if produces_id is None:
            return f"no item matching {produces!r}"
    if consumes_id and produces_id:
        notes.append("consumes and produces are ANDed: this is the loop test, not a union")

    have: set[str] | None = None
    try:
        have = _state(save, world, as_of).available_recipe_ids
    except Exception as exc:
        notes.append(_no_save_note(str(exc)))

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


@mcp.tool(structured_output=False)
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

    Rows are marked HAVE or LOCKED against the save when one can be read. That matters
    most for ``logistics``: a planner assuming a belt or pipe tier it has not unlocked
    gets every line count wrong by a factor and nothing says so, which is the worst
    failure mode a planner has.

    Paged: ``all`` is 539 buildings and unpaged it ran to ~60k characters, which is not
    an answer, it is a context eviction. The envelope says how many more there are and
    which offset fetches them.
    """
    if gone := retired(("kind", kind, "building_kind")):
        return gone
    g = game()
    try:
        st = _state(save, world, as_of)
        unlocked, built = st.unlocked_building_ids, st.built_counts
        save_error = None
    except Exception as exc:
        # Game data alone is still a useful answer; the columns just go blank -- and the
        # note at the bottom says which save could not be read and why.
        st, unlocked, built = None, None, {}
        save_error = str(exc)
    # Every kind reachable, and nothing unreachable. "all" used to match nothing at all,
    # and the AWESOME Sink and both Pipeline Pumps fell through every branch -- so a
    # caller could not check sink draw or pump head from the data and fell back on
    # general knowledge, which is precisely the failure the rest of this surface works to
    # prevent. Pumps are logistics: they move fluid, and their head lift is the number
    # a fluid plan needs.
    kinds = {
        "production": lambda b: b.is_manufacturer,
        "extractor": lambda b: b.is_extractor,
        "generator": lambda b: b.is_generator,
        "logistics": lambda b: bool(b.items_per_min or b.flow_m3_min or b.head_lift_m),
        # The build-piece families, grouped by native class. "foundation" was refused
        # while "all" was accepted, which made the only route to a foundation's size a
        # 60k-character page-through.
        "foundation": lambda b: "Foundation" in b.native,
        "ramp": lambda b: "Ramp" in b.native,
        "wall": lambda b: "Wall" in b.native or "Door" in b.native,
        "pillar": lambda b: "Pillar" in b.native,
        "beam": lambda b: "Beam" in b.native,
        "architecture": lambda b: any(t in b.native for t in _ARCH_TOKENS),
        "all": lambda b: True,
    }
    want = kinds.get((building_kind or "").strip().casefold())
    if want is None:
        return (
            f"! unknown building_kind {building_kind!r}. "
            f"Choose from: {', '.join(sorted(kinds))}"
        )
    picks = [b for b in g.buildings.values() if want(b)]
    picks.sort(key=lambda b: b.name)
    offset = max(0, offset)
    page = picks[offset : offset + render.clamp(limit, default=25)]
    rows = []
    for b in page:
        detail = ""
        if b.is_extractor and b.base_extract_rate:
            detail = f"{render.num(b.extract_rate('normal'))}/min @normal"
        elif b.is_generator:
            detail = f"{render.num(b.power_production_mw)}MW out"
            if b.requires_supplemental:
                detail += f", {render.num(b.supplemental_m3_min())} m3/min water"
        elif b.items_per_min:
            detail = f"{render.num(b.items_per_min)} items/min"
        elif b.flow_m3_min:
            detail = f"{render.num(b.flow_m3_min)} m3/min"
        elif b.head_lift_m:
            detail = (
                f"lifts {render.num(b.head_lift_m)}m head (max {render.num(b.max_head_lift_m)})"
            )
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
                detail,
            )
        )

    notes = [
        (
            "size is the axis-aligned clearance box (WxDxH); 'found' is the 8m "
            "foundations one machine covers, ignoring edges shared with a neighbour, "
            "so a row of N machines needs somewhat fewer than N x found"
        )
    ]
    if unlocked is not None and building_kind == "logistics" and st is not None:
        belt, pipe = st.best_belt(), st.best_pipe()
        chosen = (
            ", ".join(f"{g.buildings[c].name} ({v:g})" for c, v in (belt, pipe) if c)
            or "none unlocked"
        )
        notes.append(
            f"planning defaults to the fastest UNLOCKED tier: {chosen}. A tier assumed "
            "rather than checked changes every belt and pipe count in a plan"
        )
    elif unlocked is None:
        notes.append(_no_save_note(save_error))
    # Page-scoped, like the footer: naming buildings the caller cannot see in this
    # page's rows would read as rows gone missing.
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
            offset=offset,
            limit=limit,
        )
        + "\n"
        + render.ids_footer((b.name, b.cls) for b in page),
        notes,
    )
