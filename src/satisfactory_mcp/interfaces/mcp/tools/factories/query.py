"""``factory_query``: one question about one factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from pydantic import Field

from .....domain.factories.query import ASPECTS as QUERY_ASPECTS
from .....domain.factories.query import build_view
from .....domain.factories.select import resolve_factory
from .....domain.spatial import nodes as nodes_mod
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit

#: factory_query aspects that print an items/min rate, and therefore owe the reader the
#: sentence saying which window "measured" was measured over.
FLOW_ASPECTS = frozenset({"summary", "balance", "outputs", "inputs", "internal"})

FLOW_COLUMNS = ("item", "per min", "per min (measured)", "no monitor")


def _measured_cells(view, item: str, side: str) -> tuple[str, str]:
    """The measured rate for one item, and the nameplate rate no monitor can see.

    "?" rather than 0 when nothing readable touches the item that way: unknown and
    stopped are different claims, and printing zero makes the second.
    """
    flow = view.flows[item]
    blind = flow[f"unmonitored_{side}"]
    return (
        render.num(flow[f"measured_{side}"]) if view.measurable(item, side) else "?",
        render.num(blind) if blind > 1e-9 else "",
    )


def _brief(view, pairs, side: str) -> str:
    """``Iron Plate 30/min (measured 12), ...`` for the summary's makes/needs/keeps lines."""
    out = []
    for item, rate in pairs:
        flow = view.flows[item]
        seen = (
            f"measured {flow[f'measured_{side}']:.0f}"
            if view.measurable(item, side)
            else "no monitor"
        )
        out.append(f"{item} {rate:.0f}/min ({seen})")
    return ", ".join(out) or "-"


def _summary_section(view, g, window: render.Page) -> str:
    head = render.kv(
        [
            ("machines", view.size),
            ("at", f"{int(view.centroid[0] / 100)},{int(view.centroid[1] / 100)}"),
            ("spread", f"{view.spread_m:.0f}m"),
            ("draw", f"{view.draw_mw:.0f} MW"),
            ("generation", f"{view.generation_mw:.0f} MW" if view.generation_mw else ""),
            ("recipes", len(view.recipes)),
            ("issues", len(view.issues) or ""),
        ]
    )
    makes = _brief(view, view.outputs()[:5], "produced")
    needs = _brief(view, view.inputs()[:5], "consumed")
    keeps = _brief(view, view.internal()[:5], "produced")
    return f"## summary\n{head}\nmakes: {makes}\nneeds: {needs}\nkeeps: {keeps}"


def _balance_section(view, g, window: render.Page) -> str:
    rows = [
        (
            r["item"],
            render.num(r["made"]),
            render.num(r["used"]),
            f"{r['net']:+.1f}",
            "?" if r["measured_net"] is None else f"{r['measured_net']:+.1f}",
            r["verdict"],
        )
        for r in view.balance()
    ]
    return "## balance (items/min at saved clocks)\n" + render.paged_table(
        ("item", "made", "used", "net", "net (measured)", ""), rows, window
    )


def _flow_section(aspect: str) -> Callable:
    """The ``outputs`` or ``inputs`` section: net surplus, or net deficit."""
    side = "produced" if aspect == "outputs" else "consumed"

    def section(view, g, window: render.Page) -> str:
        data = view.outputs() if aspect == "outputs" else view.inputs()
        rows = [(k, render.num(v), *_measured_cells(view, k, side)) for k, v in window.of(data)]
        return f"## {aspect}\n" + render.table(
            FLOW_COLUMNS, rows, total=len(data), offset=window.start, limit=window.size
        )

    return section


def _internal_section(view, g, window: render.Page) -> str:
    data = view.internal()
    body = (
        render.table(
            FLOW_COLUMNS,
            [(k, render.num(v), *_measured_cells(view, k, "produced")) for k, v in window.of(data)],
            total=len(data),
            offset=window.start,
            limit=window.size,
        )
        if data
        else "none: every item this factory touches crosses its boundary"
    )
    return (
        "## internal (made and consumed inside this factory, at saved clocks)\n"
        "# nothing on this list crosses the boundary: it neither needs feeding\n"
        f"# nor leaves, which is what a finished line looks like\n{body}"
    )


def _machines_section(view, g, window: render.Page) -> str:
    rows = [
        (
            m.instance,
            g.building_name(m.building) or m.building,
            m.recipe or "-",
            f"{m.clock:.0%}",
            f"{m.pos[0] / 100:.0f},{m.pos[1] / 100:.0f},{m.pos[2] / 100:.0f}",
            "paused" if m.paused else "",
        )
        for m in sorted(view.machines, key=lambda x: (x.building, x.recipe))
    ]
    return "## machines\n" + render.paged_table(
        ("instance", "building", "recipe", "clock", "x,y,z(m)", ""), rows, window
    )


def _recipes_section(view, g, window: render.Page) -> str:
    return "## recipes\n" + render.paged_table(
        ("recipe", "machines"), view.recipes.most_common(), window
    )


def _buildings_section(view, g, window: render.Page) -> str:
    rows = [(g.building_name(cls) or cls, count) for cls, count in view.buildings.most_common()]
    return "## buildings\n" + render.paged_table(("building", "count"), rows, window)


def _power_section(view, g, window: render.Page) -> str:
    return "## power\n" + render.kv(
        [
            ("draw (nameplate)", f"{view.draw_mw:.1f} MW"),
            ("draw (measured)", f"{view.measured_draw_mw:.1f} MW"),
            ("generation", f"{view.generation_mw:.1f} MW"),
            ("net (nameplate)", f"{view.generation_mw - view.draw_mw:+.1f} MW"),
            ("net (measured)", f"{view.generation_mw - view.measured_draw_mw:+.1f} MW"),
        ]
    )


def _nodes_section(view, g, window: render.Page) -> str:
    rows = [
        (
            node,
            resource,
            purity,
            g.building_name(extractor) or extractor,
            f"{clock:.0%}",
            left if left is not None else "-",
        )
        for node, resource, purity, extractor, clock, left in window.of(view.nodes)
    ]
    return "## resource nodes\n" + render.table(
        ("node", "resource", "purity", "extractor", "clock", "left"),
        rows,
        total=len(view.nodes),
        offset=window.start,
        limit=window.size,
    )


def _links_section(view, g, window: render.Page) -> str:
    return (
        "## material links across the boundary\n"
        "# machines reached on the far side, not an edge count -- asymmetric by\n"
        "# nature, since the first machine of a small set blocks the rest\n"
        + render.paged_table(
            ("other side", "machines reached"),
            view.links.most_common(),
            window,
            total=len(view.links),
        )
    )


def _issues_section(view, g, window: render.Page) -> str:
    body = render.bullets(window.of(view.issues)) if view.issues else "none"
    return f"## issues ({len(view.issues)})\n{body}"


#: One section renderer per aspect, in the vocabulary ``show`` takes.
_ASPECT_SECTIONS: dict[str, Callable] = {
    "summary": _summary_section,
    "balance": _balance_section,
    "outputs": _flow_section("outputs"),
    "inputs": _flow_section("inputs"),
    "internal": _internal_section,
    "machines": _machines_section,
    "recipes": _recipes_section,
    "buildings": _buildings_section,
    "power": _power_section,
    "nodes": _nodes_section,
    "links": _links_section,
    "issues": _issues_section,
}


def _query_notes(st, view, asked: list[str]) -> list[str]:
    """What the answer's numbers mean: node identity, the measured window, unlabelled links."""
    # Identity only: this tool quotes no coordinate, so a renamed node is its one exposure.
    notes = list(
        nodes_mod.identity_notes(nodes_mod.skew_for_save(st.header), [row[0] for row in view.nodes])
    )
    if "power" in asked:
        notes.append(
            "measured weights each machine's draw by its own 300s productivity window. "
            f"{view.unmonitored} machine(s) here keep no monitor and are charged in FULL, "
            "since unknown utilisation must not read as idle"
        )
    if FLOW_ASPECTS.intersection(asked):
        notes.append(
            "measured is each machine's rate scaled by the share of its own ~300s "
            "productivity window it spent producing -- the window that ENDED when this save "
            "was written, not a live reading. A line idle at that moment measures 0 and is "
            "not broken"
        )
        notes.append(
            f"{view.producing_now} of {view.producers} machine(s) here were mid-production "
            "at the instant the save was written, which is the sharper check on a 0"
        )
        if view.unmonitored_producers:
            notes.append(
                f"{view.unmonitored_producers} machine(s) here keep no monitor. Their rate "
                "stands in 'no monitor' and is NOT in measured, so measured is a FLOOR; '?' "
                "marks a row where every machine is one of them and measured is unknown "
                "rather than zero"
            )
    loose = view.links.get("(unlabelled)")
    if loose:
        notes.append(
            f"{loose} connection(s) cross into machines no label covers -- "
            "run propose_factories to see what they are"
        )
    return notes


@app.tool()
def factory_query(
    factory: Annotated[str, Field(description="a label name, or any selector e.g. 'proposal:3'")],
    show: Annotated[
        str, Field(description="comma-separated: " + ", ".join(QUERY_ASPECTS))
    ] = "summary",
    of: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    limit: Limit = 15,
    offset: int = 0,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Ask one thing about one factory: what it makes, needs, draws, or touches.

    `show` accepts several at once, e.g. "balance,power,links"; `offset` pages every table
    in the answer at once.

    - **summary** size, position, top recipes, net power
    - **balance** per-item produced vs consumed vs net
    - **outputs** net surplus: it leaves the factory, or it backs up
    - **inputs** net deficit: it has to be fed in from outside
    - **internal** made and eaten inside the set
    - **machines** every machine with its building, recipe and clock
    - **recipes** / **buildings** counts
    - **power** draw vs generation, nameplate AND measured
    - **nodes** resource nodes its extractors sit on
    - **links** which other factories it exchanges material with
    - **issues** paused, recipe-less, or unresolved machines

    Every rate is printed twice: NAMEPLATE at the saved clock, and MEASURED, scaled by the
    share of each machine's last productivity window spent producing -- so a line idle when
    the save was written measures 0 without being broken. A machine with no monitor is left
    out of measured and shown apart.
    """
    if gone := app.retired(("of", of, "show")):
        return gone
    st = app.load_world(save, world, as_of)
    name, machines = resolve_factory(st, factory)
    if not machines:
        return f"! {factory!r} resolved to no machines that still exist in this save"

    view = build_view(name, machines, st.graph, st.game, st.projection, st.labels)
    asked = [a.strip().casefold() for a in show.split(",") if a.strip()]
    unknown = [a for a in asked if a not in QUERY_ASPECTS]
    if unknown:
        return f"! unknown aspect(s) {unknown}. Choose from: {', '.join(QUERY_ASPECTS)}"

    window = render.page(limit, offset)
    chunks = [_ASPECT_SECTIONS[aspect](view, st.game, window) for aspect in asked]
    return render.envelope(
        f"# {st.age_note}\n# {name}: {view.size} machines",
        "\n\n".join(chunks),
        _query_notes(st, view, asked),
    )
