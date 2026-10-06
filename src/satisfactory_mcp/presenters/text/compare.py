"""The route comparison as compact TSV."""

from __future__ import annotations

from ...domain.planning.analysis.recipe_routes import (
    PROBE_RATE,
    Route,
    RouteComparison,
    short_recipe_name,
)
from . import primitives as render

__all__ = ["render_comparison"]


def render_comparison(cmp: RouteComparison, limit: int = 10) -> str:
    """The routes as compact TSV, best first."""
    unit = cmp.primary_unit
    short_primary = cmp.primary_name.split()[-1].lower()
    head = (
        f"# {len(cmp.routes)} route(s) make {cmp.item_name} at {render.num(cmp.rate)}/min. "
        "one producer pinned per route, LP picks the rest; "
        "mach=at best raw/fewest at any raw, MW=the chain's own draw."
    )
    if not cmp.routes:
        return render.envelope(head, "", cmp.notes)

    gap = _gap_line(cmp, short_primary, unit)
    headers = [
        "route",
        f"{render.num(PROBE_RATE)}{short_primary}->",
        f"{short_primary}/unit",
        "mach",
        "MW",
    ]
    if cmp.generator_mw_per_unit:
        headers.append(f"MW/{short_primary}")
    headers += ["byproduct", "with"]
    n_cols = len(headers)

    rows = []
    for r in cmp.routes[: render.clamp(limit)]:
        if not r.ok:
            rows.append(
                [short_recipe_name(r.name), r.status.upper(), *["-"] * (n_cols - 3), r.note]
            )
            continue
        row = [
            short_recipe_name(r.name),
            render.num(r.yield_per_probe),
            render.num(r.per_unit, 4),
            _machines(r),
            # round() first: render.num strips trailing zeros, so formatting -190.1
            # to zero places yields the string "-19".
            render.num(round(r.mw)),
        ]
        if cmp.generator_mw_per_unit:
            row.append(render.num(r.power_yield))
        row.append(
            ", ".join(f"{b.name} {render.num(b.rate)} {b.outlet.upper()}" for b in r.byproducts[:2])
            or "-"
        )
        row.append(_with(r.upstream))
        rows.append(row)

    body = render.table(
        headers,
        rows,
        total=len(cmp.routes),
        hint="raise limit -- the routes are ranked, so there is no offset",
    )
    footer = render.ids_footer(
        (short_recipe_name(r.name), r.recipe) for r in cmp.routes[: render.clamp(limit)]
    )
    return render.envelope(head + ("\n" + gap if gap else ""), body + "\n" + footer, cmp.notes)


def _machines(r: Route) -> str:
    """``9/6``: buildings at the cheapest raw draw, and the fewest at any draw.

    Two numbers because they answer two different decisions -- Recycled Plastic is 9
    buildings when crude is what you are short of and 6 when buildings are.
    """
    if r.machines_floor and r.machines_floor < r.machines:
        return f"{r.machines}/{r.machines_floor}"
    return str(r.machines)


def _with(upstream: list[str], keep: int = 2) -> str:
    if not upstream:
        return "-"
    if len(upstream) <= keep:
        return " + ".join(upstream)
    return " + ".join(upstream[:keep]) + f" +{len(upstream) - keep}"


def _gap_line(cmp: RouteComparison, short_primary: str, unit: str) -> str:
    """The best-to-worst spread as a line of its own, so the multiple is stated rather than
    left to be divided out of two table cells."""
    ok = cmp.feasible
    if len(ok) < 2:
        return ""
    best, worst = ok[0], ok[-1]
    parts = [
        (
            f"# best vs worst: {short_recipe_name(best.name)} needs {render.num(best.per_unit, 4)} vs "
            f"{render.num(worst.per_unit, 4)} {unit} {cmp.primary_name} per {cmp.item_name} "
            f"({render.num(worst.per_unit / best.per_unit)}x)"
        )
    ]
    if best.power_yield and worst.power_yield:
        parts.append(
            f"burnt in the {cmp.generator}: {render.num(best.power_yield)} vs "
            f"{render.num(worst.power_yield)} net MW per {unit} {short_primary} "
            f"({render.num(best.power_yield / worst.power_yield)}x)"
        )
    return "; ".join(parts)
