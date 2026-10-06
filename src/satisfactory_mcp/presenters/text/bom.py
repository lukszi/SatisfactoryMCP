"""The bill of materials as compact TSV."""

from __future__ import annotations

from ...domain.planning.analysis.bom import BOM
from . import primitives as render

__all__ = ["render_bom"]


def render_bom(bom: BOM, limit: int = 20, offset: int = 0) -> str:
    if bom.status == "raw":
        return render.envelope(f"# BOM {render.num(bom.qty)} {bom.item_name}/min", "", bom.notes)
    if not bom.ok:
        return render.envelope(
            f"# BOM {render.num(bom.qty)} {bom.item_name}/min: INFEASIBLE", "", bom.notes
        )

    raw = ", ".join(
        f"{render.num(v)} {_short_item(bom, k)}"
        for k, v in sorted(bom.raw.items(), key=lambda kv: -kv[1])
    )
    intermediates = sum(1 for r in bom.rows if not r.is_raw and not r.is_target)
    summary = (
        f"# BOM {render.num(bom.qty)} {bom.item_name}/min: raw {raw or 'none'}"
        f" -- {bom.machines} machines, {render.num(abs(bom.mw))} MW,"
        f" {intermediates} intermediate(s). All figures /min."
    )

    notes = list(bom.notes)
    for members in bom.loops:
        notes.append(
            f"production loop: {' <-> '.join(members)}. Their 'made' figures exceed what "
            "leaves the plant because the loop recirculates -- this is why a bill cannot "
            "be expanded recursively"
        )
    if bom.byproducts:
        notes.append(
            "byproducts needing an outlet: "
            + ", ".join(f"{render.num(v)} {_short_item(bom, k)}" for k, v in bom.byproducts.items())
        )
    if bom.sunk:
        notes.append(
            "sunk (needs a belt to an AWESOME Sink or the line stalls): "
            + ", ".join(f"{render.num(v)} {_short_item(bom, k)}" for k, v in bom.sunk.items())
        )
    if bom.alternates:
        notes.append(f"{len(bom.alternates)} alternate(s) chosen: " + ", ".join(bom.alternates))
    notes.append(
        "min_raw is degenerate over this recipe set: one optimal raw vector of several. "
        "Water is priced last by a lexicographic tie-break; pin the chain with "
        "only_recipes/exclude_recipes for an arithmetic answer"
    )

    window = render.page(limit, offset, default=20)
    page = window.of(bom.rows)
    rows = [
        (
            r.name,
            render.num(r.made),
            render.num(r.used),
            ", ".join(r.recipes) or "-",
            r.machines or "-",
            r.building or "-",
        )
        for r in page
    ]
    body = render.table(
        ("item", "made", "used", "recipe", "machines", "building"),
        rows,
        total=len(bom.rows),
        offset=window.start,
        limit=window.size,
    )
    ids = render.ids_footer(
        (r.name, r.recipe_ids[0]) for r in page if r.recipe_ids and len(r.recipe_ids) == 1
    )
    return render.envelope(summary, body + ("\n" + ids if ids else ""), notes)


def _short_item(bom: BOM, item: str) -> str:
    for row in bom.rows:
        if row.item == item:
            return row.name
    return item
