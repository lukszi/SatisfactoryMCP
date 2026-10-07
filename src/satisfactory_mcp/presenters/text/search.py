"""Recipe search results as compact TSV.

The units are not comparable and must not be rendered as if they were: only part
recipes run in a machine, so only they have a per-minute rate. Building and manual
rows carry the per-craft amount and are suffixed ``/build`` and ``/craft`` so no row
can be misread as a throughput. See ``core.gamedata.search`` for the census rules.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...core.gamedata.model import Flow, GameData, Recipe
from ...core.gamedata.search import KINDS, Census, Hit
from ...core.gamedata.unlocks import granted_by_label
from . import primitives as render

__all__ = ["item_flows", "render_search"]

#: How one unit of each kind is measured. A part recipe is a throughput; the other
#: two are one-off costs, and their duration field is a constant 1.0 s.
_UNIT = {"part": "/min", "building": "/build", "manual": "/craft"}

#: What performs each kind. Doubles as the "machine" column, so no separate kind
#: column is needed.
_PERFORMED_BY = {"building": "build gun", "manual": "by hand"}


def _machine(game: GameData, r: Recipe) -> str:
    if r.kind != "part":
        return _PERFORMED_BY.get(r.kind, "-")
    b = game.machine(r)
    return b.name if b else "-"


def item_flows(game: GameData, parts: Iterable[Flow], *, per_craft: bool = False) -> str:
    """A recipe's ingredients or products as ``30 Crude Oil + 20 Water``, per minute by
    default, or per craft for the recipes that do not run in a machine."""
    return render.flows(
        (game.item_name(p.item), p.amount if per_craft else p.per_min) for p in parts
    )


def _census_header(census: Census, subject: str) -> str:
    """The completeness claim, and the evidence for it, in one line."""
    if not census.total:
        return f"# no recipe {subject} (searched all {census.scanned} recipes)"
    parts: list[str] = []
    for kind in KINDS:
        n = census.by_kind.get(kind, 0)
        if not n:
            continue
        gate = ""
        have, locked = census.have.get(kind, 0), census.locked.get(kind, 0)
        if have or locked:
            gate = f" [{have} HAVE, {locked} LOCKED]"
        parts.append(f"{n} {kind}{gate}")
    return (
        f"# {census.total} recipe(s) {subject}: "
        + ", ".join(parts)
        + f". Counted over all {census.scanned} recipes;"
        + " recipe_kind/limit change the rows, never these totals."
    )


def _search_notes(
    census: Census, subject: str, recipe_kind: str, mixed: bool, notes: list[str] | None
) -> list[str]:
    """What the page leaves out by kind and by event, and what its units mean."""
    all_notes = list(notes or [])
    # A building that eats the item is invisible to a part-only view, and silence there
    # reads as "nothing else".
    hidden = [
        f"{census.by_kind[k]} {k}"
        for k in KINDS
        if k != recipe_kind and census.by_kind.get(k) and recipe_kind not in ("all", "", None)
    ]
    if hidden:
        all_notes.append(
            f"recipe_kind={recipe_kind!r} hides "
            + " and ".join(hidden)
            + f" recipe(s) that also {subject}"
            " -- pass recipe_kind='building', 'manual' or 'all' to see them"
        )
    if census.events and not any("FICSMAS" in n for n in all_notes):
        all_notes.append(f"{census.events} FICSMAS event recipe(s) counted but not shown")
    if mixed:
        all_notes.append(
            "/min is throughput for one machine at 100% clock; /build and /craft are "
            "one-off costs and are NOT rates"
        )
    return all_notes


def render_search(
    game: GameData,
    hits: list[Hit],
    census: Census,
    subject: str,
    item_column: str = "",
    limit: int = 10,
    offset: int = 0,
    recipe_kind: str = "part",
    notes: list[str] | None = None,
) -> str:
    window = render.page(limit, offset)
    page = window.of(hits)
    show_qty = bool(item_column)
    show_status = any(h.unlocked is not None for h in hits)
    # Only part recipes have a rate at all, so the /min suffix goes in the header
    # when every row is one, and onto each cell when the page mixes kinds.
    mixed = any(h.recipe.kind != "part" for h in page)
    # A LOCKED row without this is a dead end, and on a page with nothing locked the
    # column is a column of blanks -- so it appears exactly where it answers something.
    show_granted = any(h.unlocked is False for h in page)

    headers = ["recipe", "machine"]
    if show_qty:
        headers.append(item_column)
    headers += ["in", "out"] if mixed else ["in/min", "out/min"]
    if show_status:
        headers.append("status")
    if show_granted:
        headers.append("granted by")

    rows: list[list[str]] = []
    for h in page:
        r = h.recipe
        row = [r.name, _machine(game, r)]
        if show_qty:
            row.append(f"{render.num(h.qty)}{_UNIT.get(r.kind, '')}")
        per_craft = r.kind != "part"
        row += [
            item_flows(game, r.ingredients, per_craft=per_craft),
            item_flows(game, r.products, per_craft=per_craft),
        ]
        if show_status:
            row.append("HAVE" if h.unlocked else ("LOCKED" if h.unlocked is False else "-"))
        if show_granted:
            row.append(granted_by_label(game, r, width=60) if h.unlocked is False else "")
        rows.append(row)

    body = render.table(
        headers,
        rows,
        total=len(hits),
        offset=window.start,
        limit=window.size,
        hint="or narrow the query.",
    )
    return render.envelope(
        _census_header(census, subject),
        body + "\n" + render.ids_footer((h.recipe.name, h.recipe.cls) for h in page),
        _search_notes(census, subject, recipe_kind, mixed, notes),
    )
