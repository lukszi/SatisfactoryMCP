"""What you own and where it is: stock by item, containers, and the crates on the ground.

The text twins of ``/api/storage`` and ``/api/crates``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ....domain.spatial import regions as regions_mod
from ....domain.spatial.places import resolve_place
from ....domain.world.inventory import CRATE_KIND_TEXT, Holding
from ....presenters.text import primitives as render
from .. import app
from ..params import AsOf, Limit

#: How many item kinds a place lists before the rest become "+N more". Three names and a
#: count read as a box; twelve names read as a wall.
CONTENTS_KINDS = 3


def _place(regions, holding: Holding) -> tuple[str, str]:
    """A holding's region name and its coordinate in metres, both as printable cells."""
    if holding.pos is None:
        return "-", "-"
    x, y, _ = holding.pos
    return regions.label_for(x, y).name or regions_mod.OFF_MAP, f"{x / 100:.0f},{y / 100:.0f}"


def _contents(game, holding: Holding, kinds: int = CONTENTS_KINDS) -> str:
    held = [f"{render.num(n)} {game.item_name(i)}" for i, n in holding.items]
    return render.capped(held, kinds, more=", +{n} more") or "-"


def _fullness(holding: Holding) -> tuple[str, str]:
    """``fill`` as a percentage and the two numbers it came from, in this row's own unit."""
    fill = "-" if holding.fill is None else f"{holding.fill:.0%}"
    if holding.kind == "fluid":
        capacity = "?" if holding.capacity_m3 is None else f"{holding.capacity_m3:.0f}"
        return fill, f"{holding.total:.0f}/{capacity}m3"
    if holding.slots_used is None or not holding.slots:
        return fill, "-"
    return fill, f"{holding.slots_used}/{holding.slots}"


def _stock_totals(st, breakdown: dict, wanted, window: render.Page, limit, notes) -> str:
    """One row per item: spendable, and where the rest of it sits."""
    g = st.game
    rows = sorted(
        ((i, v) for i, v in breakdown.items() if wanted is None or i == wanted),
        key=lambda kv: (-kv[1]["spendable"], -kv[1]["machine"]),
    )
    table = render.table(
        ("item", "spendable", "carried", "storage", "depot", "buffers", "crates"),
        [
            (
                g.item_name(i),
                render.num(v["spendable"]),
                render.num(v["player"]) if v["player"] else "",
                render.num(v["storage"]) if v["storage"] else "",
                render.num(v["depot"]) if v["depot"] else "",
                render.num(v["machine"]) if v["machine"] else "",
                render.num(v["crate"]) if v["crate"] else "",
            )
            for i, v in window.of(rows)
        ],
        total=len(rows),
        offset=window.start,
        limit=limit,
    )
    notes.append("stock(item=..., where=True) says which containers hold one of them")
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(breakdown)} item kind(s) held, "
        f"{sum(1 for v in breakdown.values() if v['spendable'])} of them spendable",
        table,
        notes,
    )


def _stock_places(st, breakdown: dict, wanted, window: render.Page, limit, notes) -> str:
    """One row per container or crate holding the item, or holding anything at all."""
    g = st.game
    regions = regions_mod.load_regions()
    holdings = st.inventory.holdings(wanted)
    headers = ("amount" if wanted is not None else "holds", "place", "region", "x,y(m)", "source")
    rows = []
    for h in window.of(holdings):
        region, at = _place(regions, h)
        rows.append(
            (
                render.num(h.amount_of(wanted)) if wanted is not None else _contents(g, h, 2),
                g.building_name(h.cls) or h.cls,
                region,
                at,
                h.source if h.crate_kind is None else f"crate({h.crate_kind})",
            )
        )

    total = breakdown.get(wanted, {}) if wanted is not None else {}
    summary = f"# {st.age_note}\n# " + (
        render.kv(
            [
                ("item", g.item_name(wanted)),
                ("spendable", render.num(total.get("spendable", 0.0))),
                ("carried", render.num(total.get("player", 0.0))),
                ("depot", render.num(total.get("depot", 0.0))),
                ("in_buffers", render.num(total.get("machine", 0.0))),
            ]
        )
        if wanted is not None
        else f"{len(holdings)} place(s) hold something, biggest first"
    )
    notes.append(
        "carried and Depot stock stands nowhere on the map, so it is on the summary line "
        "rather than in a row"
    )
    notes.append("storage(item=...) says how full each of those containers is")
    return render.envelope(
        summary,
        render.table(headers, rows, total=len(holdings), offset=window.start, limit=limit),
        notes,
    )


@app.tool()
def stock(
    item: Annotated[
        str | None, Field(description="one item by name; omit for everything you own")
    ] = None,
    where: Annotated[
        bool, Field(description="list the places holding it instead of the per-item totals")
    ] = False,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """What you own, by item: spendable stock apart from what merely exists.

    Spendable is carried + storage + Dimensional Depot -- exactly the set every
    affordability check in this surface spends. Machine buffers and crate contents get
    their own columns and are never added in: buffer material is in transit, and a crate
    exists because something went wrong and deletes itself when emptied.

    ``where=True`` answers "and where is it": one row per container or crate holding the
    item, with the region it stands in and its coordinate. Carried and Depot stock has no
    place, so it is reported on the summary line instead. Fluids are in m3.
    """
    st = app.load_world(save, world, as_of)
    g = st.game

    wanted = None
    if item is not None:
        wanted = app.resolve_item_id(item)
        if wanted is None:
            return f"no item matches {item!r}"

    breakdown = st.inventory.breakdown()
    if wanted is not None and wanted not in breakdown:
        return (
            f"# {st.age_note}\n"
            f"# you hold no {g.item_name(wanted)} anywhere -- not carried, not in a "
            "container, not in the Depot, not in a machine buffer and not in a crate"
        )

    notes = [
        (
            "spendable = carried + storage + Dimensional Depot, the pool every affordability "
            "check spends. buffers is material inside machines and pipes and crates is what "
            "is lying on the ground; neither is spendable"
        ),
        "fluids are m3",
    ]
    window = render.page(limit, offset, default=25)
    if not where:
        return _stock_totals(st, breakdown, wanted, window, limit, notes)
    return _stock_places(st, breakdown, wanted, window, limit, notes)


@app.tool()
def storage(
    item: Annotated[
        str | None, Field(description="only containers holding this item, fullest first")
    ] = None,
    near: Annotated[
        str | None,
        Field(description="centre: 'x,y' in metres, 'me', or a named factory"),
    ] = None,
    radius_m: float = 500.0,
    container_kind: Annotated[str | None, Field(description="solid | fluid | all")] = None,
    empty: Annotated[bool, Field(description="include containers with nothing in them")] = False,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 15,
    offset: int = 0,
    kind: Annotated[
        str | None, Field(description="retired -- write container_kind= instead")
    ] = None,
) -> str:
    """Which container holds what, where it stands, and how full it is.

    Every storage container, Personal Storage Box, Depot uploader and fluid buffer the
    player built -- not splitters and mergers, whose one to three items in transit are not
    stock, and not machine buffers, which belong to their machine. Fullest first, or by
    how much of ``item`` they hold when one is named.

    ``fill`` is measured, not stated by the save: a container is its used slots over its
    slots, stacking each item at its own stack size, and a buffer is its m3 over what the
    class holds. It is ``-`` where either number is unknown.
    """
    if gone := app.retired(("kind", kind, "container_kind")):
        return gone
    st = app.load_world(save, world, as_of)
    g = st.game

    wanted = None
    if item is not None:
        wanted = app.resolve_item_id(item)
        if wanted is None:
            return f"no item matches {item!r}"
    want_kind = (container_kind or "").strip().casefold() or None
    if want_kind == "all":
        want_kind = None
    if want_kind not in (None, "solid", "fluid"):
        return f"! unknown container_kind {container_kind!r}. Choose from: solid, fluid, all"

    origin, at = None, ""
    if near is not None:
        try:
            origin, at = resolve_place(st, near)
        except ValueError as exc:
            return f"! {exc}"

    # The census counts every container and the filters decide only which are SHOWN.
    containers = [h for h in st.inventory.holdings() if h.source == "storage"]
    hits = [h for h in containers if want_kind is None or h.kind == want_kind]
    if wanted is not None:
        hits = sorted((h for h in hits if h.amount_of(wanted)), key=lambda h: -h.amount_of(wanted))
    if not empty:
        hits = [h for h in hits if h.total]
    if origin is not None:
        # ``resolve_place`` answers in centimetres, which is what the placements are in.
        reach = (radius_m * 100.0) ** 2
        hits = [
            h
            for h in hits
            if h.pos is not None
            and (h.pos[0] - origin[0]) ** 2 + (h.pos[1] - origin[1]) ** 2 <= reach
        ]

    solids = [h for h in containers if h.kind == "solid"]
    fluids = [h for h in containers if h.kind == "fluid"]
    scope = f" within {radius_m:g}m of {at}" if origin is not None else ""
    if wanted is not None:
        scope += f", holding {g.item_name(wanted)}"
    summary = (
        f"# {st.age_note}\n"
        f"# {len(containers)} container(s): {len(solids)} solid "
        f"({sum(1 for h in solids if h.total)} with something in), "
        f"{len(fluids)} fluid buffer(s); "
        f"{render.num(sum(h.total for h in solids))} item(s), "
        f"{render.num(sum(h.total for h in fluids))} m3\n"
        f"# {len(hits)} shown{scope}"
    )

    regions = regions_mod.load_regions()
    window = render.page(limit, offset, default=15)
    rows = []
    for h in window.of(hits):
        region, where = _place(regions, h)
        fill, used = _fullness(h)
        rows.append(
            (
                g.building_name(h.cls) or h.cls,
                region,
                where,
                fill,
                used,
                _contents(g, h),
            )
        )

    notes = [
        (
            "fill is used slots over slots for a container (each item at its own stack size) "
            "and m3 over class capacity for a buffer; '-' means one of the two is unknown"
        ),
        (
            "a fluid buffer names what the pipe network claims it holds, and '?' where no "
            "network claims it"
        ),
    ]
    if not empty:
        hidden = sum(1 for h in containers if not h.total)
        if hidden:
            notes.append(f"{hidden} empty container(s) hidden -- pass empty=True to see them")
    if not hits:
        notes.append(
            "no container matches -- this reads every container the save carries, so "
            "widen radius_m or drop a filter rather than reading it as an empty world"
        )
    notes.append("stock() is the same material summed per item rather than per box")
    return render.envelope(
        summary,
        render.table(
            ("container", "region", "x,y(m)", "fill", "used", "holds"),
            rows,
            total=len(hits),
            offset=window.start,
            limit=limit,
        ),
        notes,
    )


@app.tool()
def crates(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """The crates lying on the ground: what is in each one and where to walk to get it.

    A death crate is what you dropped when you died; a dismantle crate is the overflow from
    dismantling with a full inventory. Both are recoverable and NEITHER counts as spendable
    stock -- a crate deletes itself the moment it is emptied, so a plan that spent it would
    depend on somebody walking back there first.

    Whose crate it is the save does not say: the crate's only saved property is its type,
    so there is no owner, no timestamp and no cause to report.
    """
    st = app.load_world(save, world, as_of)
    g = st.game

    holdings = [h for h in st.inventory.holdings() if h.source == "crate"]
    regions = regions_mod.load_regions()
    window = render.page(limit, offset, default=25)
    rows = []
    for h in window.of(holdings):
        region, at = _place(regions, h)
        z = "-" if h.pos is None else f"{h.pos[2] / 100:.0f}"
        rows.append(
            (
                h.crate_kind,
                region,
                at,
                z,
                len(h.items),
                render.num(h.total),
                _contents(g, h, kinds=4),
            )
        )

    deaths = sum(1 for h in holdings if h.crate_kind == "death")
    notes = [
        (
            "crate contents are recoverable but never spendable: mam_research, plan_factory "
            "and every other cost check ignore them deliberately"
        ),
        *(
            f"kind={kind!r}: {gloss}"
            for kind, gloss in CRATE_KIND_TEXT.items()
            if any(h.crate_kind == kind for h in holdings)
        ),
    ]
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(holdings)} crate(s) on the ground, {deaths} from a death; "
        f"{render.num(sum(h.total for h in holdings))} item(s) in them",
        render.table(
            ("kind", "region", "x,y(m)", "z(m)", "kinds", "items", "contents"),
            rows,
            total=len(holdings),
            offset=window.start,
            limit=limit,
        ),
        notes,
    )
