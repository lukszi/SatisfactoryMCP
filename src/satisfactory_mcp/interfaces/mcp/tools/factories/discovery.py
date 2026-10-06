"""Finding factories: the candidate signals, the coherence proposals, a selector preview."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .....domain.factories import candidates, cohere, naming
from .....domain.factories import select as machine_select
from .....domain.factories.select import INDEX_WARNING as GRAPH_INDEX_WARNING
from .....domain.factories.select import SELECTOR_HELP as GRAPH_SELECTOR_HELP
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit

#: Bare slabs below this many tiles are helper pads (a tile under a pole, a jump-pad
#: landing) and are summarised in one line that quotes the threshold; 12 is a 3x4 pour.
BARE_TILE_FLOOR = 12

#: The candidate tables' columns, for power islands and belt components alike.
CANDIDATE_COLUMNS = ("src", "n", "x,y(m)", "spread", "named", "labels", "makes")

#: What ``factory_map(show=)`` can draw.
FACTORY_MAP_VIEWS = ("candidates", "named", "slabs", "unlabelled", "all")


def _z_range(slab) -> str:
    """A slab's elevation in metres -- both ends of it where they differ.

    A platform poured over three storeys stands at both heights, and a plan that reads only
    the bottom one puts a machine under the floor.
    """
    lo, hi = slab.z_span[0] / 100, slab.z_span[1] / 100
    return f"{lo:.0f}" if round(lo) == round(hi) else f"{lo:.0f}..{hi:.0f}"


def _slab_shape(slab) -> tuple:
    """Bounding box, elevation and storeys -- what a build plan needs past the centre."""
    return (
        (
            f"{int(slab.bbox[0] / 100)},{int(slab.bbox[1] / 100)}"
            f"..{int(slab.bbox[2] / 100)},{int(slab.bbox[3] / 100)}"
        ),
        _z_range(slab),
        slab.storeys,
    )


def _empty_platform(select: list[str], structures) -> str:
    """The platform a lone ``slab:`` term names, when nothing stands on it yet.

    An empty string when the selector is anything else, so the caller's own "matched no
    machines" still speaks for every other way of picking nothing.
    """
    if len(select) != 1 or not select[0].strip().casefold().startswith("slab:"):
        return ""
    try:
        slab = structures.slabs[int(select[0].strip().split(":", 1)[1])]
    except (ValueError, IndexError):
        return ""
    box, z, floors = _slab_shape(slab)
    return (
        render.kv(
            [
                ("slab", slab.index),
                ("machines", 0),
                ("tiles", slab.tiles),
                ("at", f"{int(slab.centre[0] / 100)},{int(slab.centre[1] / 100)}"),
                ("extent", f"{int(slab.extent[0] / 100)}x{int(slab.extent[1] / 100)}m"),
                ("bbox(m)", box),
                ("z(m)", z),
                ("floors", floors),
            ]
        )
        + "\nnothing stands on this platform yet -- it is poured ground, not a factory"
    )


def _candidate_row(st, candidate, store, labelled: set[str]) -> tuple:
    named = {store.label_for(m).name for m in candidate.machines if store.label_for(m)}
    covered = sum(1 for m in candidate.machines if m in labelled)
    return (
        candidate.source,
        candidate.size,
        f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)}",
        f"{candidate.spread_m:.0f}m",
        f"{covered}/{candidate.size}" if covered else "-",
        ", ".join(sorted(named))[:40] or "-",
        naming.lead_of(st, candidate.machines, candidate)[0][:44],
    )


def _named_section(st, machines: set[str]) -> tuple[list[str], list[str]]:
    """The labelled factories with how much of each still stands, and their review notes."""
    store = st.labels
    rows = []
    for label in sorted(store.labels, key=lambda x: -len(x.anchors)):
        alive = set(label.anchors) & machines
        candidate = candidates.describe(sorted(alive), st.graph, st.game, st.projection, "label")
        rows.append(
            (
                label.name,
                len(label.anchors),
                f"{len(alive)}/{len(label.anchors)}",
                f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)}",
                f"{candidate.spread_m:.0f}m",
                naming.lead_of(st, sorted(alive), candidate)[0][:44],
            )
        )
    chunks = [
        "## named\n"
        + render.table(("name", "machines", "alive", "x,y(m)", "spread", "makes"), rows)
    ]
    notes = [
        f"{issue['name']}: {issue['missing']} anchor machine(s) gone "
        f"(recall {issue['recall']}) -- {issue['status']}"
        for issue in store.review(machines)
    ]
    return chunks, notes


def _candidate_sections(st, islands, belts, labelled: set[str], window: render.Page) -> list[str]:
    """Power islands, then the belt components no label covers yet."""
    store = st.labels
    fresh = [c for c in belts if not store.covers(c.machines)]
    out = []
    for title, found in (
        ("## power islands (bases)", islands),
        ("## belt components (lines), unnamed first", fresh),
    ):
        rows = [_candidate_row(st, c, store, labelled) for c in window.of(found)]
        out.append(
            f"{title}\n"
            + render.table(
                CANDIDATE_COLUMNS, rows, total=len(found), offset=window.start, limit=window.size
            )
        )
    return out


def _bare_platforms_section(structures, window: render.Page) -> tuple[str, list]:
    """The platforms no machine stands on, and the listed ones shown on this page."""
    occupied = set(structures.slab_of.values())
    bare = [s for s in structures.slabs if s.index not in occupied]
    listed = [s for s in bare if s.tiles >= BARE_TILE_FLOOR]
    pads = [s for s in bare if s.tiles < BARE_TILE_FLOOR]
    if not bare:
        return "## bare platforms (no machines): none", window.of(listed)
    rows = [
        (
            slab.index,
            slab.tiles,
            f"{int(slab.centre[0] / 100)},{int(slab.centre[1] / 100)}",
            f"{int(slab.extent[0] / 100)}x{int(slab.extent[1] / 100)}m",
            *_slab_shape(slab),
        )
        for slab in window.of(listed)
    ]
    header = f"## bare platforms (no machines): {len(bare)}, {sum(s.tiles for s in bare)} tiles"
    body = render.table(
        ("slab", "tiles", "x,y(m)", "extent", "bbox(m)", "z(m)", "floors"),
        rows,
        total=len(listed),
        offset=window.start,
        limit=window.size,
    )
    if pads:
        body += (
            f"\n# plus {len(pads)} pad(s) under {BARE_TILE_FLOOR} tiles "
            f"({sum(s.tiles for s in pads)} tiles total), summarised here "
            "by that threshold"
        )
    return f"{header}\n{body}", window.of(listed)


def _slab_sections(st, machines: set[str], window: render.Page) -> tuple[list[str], list[str]]:
    """Foundation slabs carrying machines, the bare platforms beside them, and their notes."""
    structures, store = st.structures, st.labels
    groups = structures.groups()
    rows = []
    for group in window.of(groups):
        index = structures.slab_of[group[0]]
        slab = structures.slabs[index]
        candidate = candidates.describe(group, st.graph, st.game, st.projection, "structure")
        names = sorted({lbl.name for m in group if (lbl := store.label_for(m))})
        rows.append(
            (
                index,
                len(group),
                slab.tiles,
                f"{int(slab.centre[0] / 100)},{int(slab.centre[1] / 100)}",
                f"{int(slab.extent[0] / 100)}x{int(slab.extent[1] / 100)}m",
                *_slab_shape(slab),
                ", ".join(names)[:34] or "-",
                naming.lead_of(st, group, candidate)[0][:38],
            )
        )
    census = structures.summary()
    slabs_table = (
        f"## foundation slabs ({census['slabs']} platforms, {census['tiles']} tiles, "
        f"{census['machines_on_slabs']} machines on one)\n"
        + render.table(
            (
                "slab",
                "machines",
                "tiles",
                "x,y(m)",
                "extent",
                "bbox(m)",
                "z(m)",
                "floors",
                "labels",
                "makes",
            ),
            rows,
            total=len(groups),
            offset=window.start,
            limit=window.size,
        )
    )
    bare_table, bare_shown = _bare_platforms_section(structures, window)

    notes = []
    shown = [structures.slabs[structures.slab_of[g[0]]] for g in window.of(groups)] + bare_shown
    if shown:
        notes.append(
            "extent and bbox span tile CENTRES, so a platform's poured edge reaches "
            "about half a tile past the box quoted"
        )
    if any(slab.storeys > 1 for slab in shown):
        notes.append(
            "floors is the z span counted in 4 m storeys, so a slab poured UP A "
            "HILLSIDE counts its climb as decks -- read it beside z(m) rather "
            "than as a tower"
        )
    ground = len(machines) - len(structures.slab_of)
    if ground:
        notes.append(
            f"{ground} machine(s) stand on no foundation at all -- slabs cannot see "
            "them, so this signal is a candidate and never the arbiter."
        )
    return [slabs_table, bare_table], notes


def _unlabelled_section(st, labelled: set[str]) -> list[str]:
    """What no label covers, by what it makes."""
    loose = candidates.unassigned(st.graph, labelled)
    if not loose:
        return []
    grouped = candidates.describe(loose, st.graph, st.game, st.projection, "unlabelled")
    top = ", ".join(f"{name} {count}" for name, count in grouped.products.most_common(12))
    return [f"## unlabelled: {len(loose)} machine(s)\n{top or '(no recipes set)'}"]


@app.tool()
def factory_map(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 12,
    offset: int = 0,
    show: Annotated[str, Field(description=" | ".join(FACTORY_MAP_VIEWS))] = "all",
) -> str:
    """Proposed factories, from power islands and belt topology, plus what is named.

    Three independent signals, because none is right alone: power islands separate outposts
    but merge a grown-together base into one blob; belt components shatter that blob into
    fragments; foundation slabs are the sharpest but miss ground-built parts. Where they
    disagree, carve the difference with `name_factory` and a `product:`, `near:` or `slab:`
    selector.

    show=slabs also lists BARE platforms -- poured foundations carrying no machine yet --
    with tile count, extent, bounding box and elevation; pads under a stated tile threshold
    are summarised in one line.
    """
    want = show.strip().casefold()
    if want not in FACTORY_MAP_VIEWS:
        return f"! unknown show {show!r}. Choose from: {', '.join(FACTORY_MAP_VIEWS)}"
    st = app.load_world(save, world, as_of)
    graph, store = st.graph, st.labels
    island_candidates, belt_candidates = candidates.bases_and_lines(graph, st.game, st.projection)
    labelled = store.assigned()
    machines = set(graph.machines())
    window = render.page(limit, offset)

    chunks: list[str] = []
    notes: list[str] = []
    if want in ("all", "named") and store.labels:
        named, named_notes = _named_section(st, machines)
        chunks += named
        notes += named_notes
    if want in ("all", "candidates"):
        chunks += _candidate_sections(st, island_candidates, belt_candidates, labelled, window)
    if want in ("all", "slabs"):
        slabs, slab_notes = _slab_sections(st, machines, window)
        chunks += slabs
        notes += slab_notes
    if want in ("all", "unlabelled"):
        chunks += _unlabelled_section(st, labelled)
    if want in ("all", "candidates", "slabs"):
        notes.append(GRAPH_INDEX_WARNING)
    if island_candidates and island_candidates[0].size > 100:
        notes.append(
            f"the largest power island holds {island_candidates[0].size} machines across "
            f"{island_candidates[0].spread_m:.0f}m -- that is a grown-together base, not one "
            "factory. Carve it with name_factory(select=['product:Steel Ingot','near:x,y@150'])."
        )

    return render.envelope(
        f"# {st.age_note}\n# {len(machines)} machines; {len(island_candidates)} power island(s), "
        f"{len(belt_candidates)} belt component(s); {len(store.labels)} named, "
        f"{len(labelled & machines)} machine(s) covered",
        "\n\n".join(chunks),
        notes,
    )


@app.tool()
def propose_factories(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 15,
    offset: int = 0,
    max_span_m: Annotated[float, Field(description="cap on a proposal's diameter, metres")] = 250.0,
    unnamed_only: bool = False,
) -> str:
    """One coherence score over every signal, agglomerated into proposed factories.

    Combines foundation slabs, proximity, belt connectivity, shared products and supply
    links. It errs toward splitting: a proposal never merges two factories, but one factory
    may come back in pieces.

    Use `name_factory` on what it proposes. `unnamed_only=True` answers "what have I built
    and not named". The `#` column is the `proposal:<n>` selector every other tool takes,
    counted over ALL proposals, so it does not shift when you page.
    """
    st = app.load_world(save, world, as_of)
    store = st.labels
    proposals = (
        st.proposals
        if max_span_m == cohere.MAX_SPAN_M
        else cohere.propose(st.graph, st.game, st.projection, st.structures, max_span_m=max_span_m)
    )
    window = render.page(limit, offset)
    rows = []
    shown = 0
    suggested = naming.proposal_names(st, proposals)
    for k, proposal in enumerate(proposals):
        names = sorted({lbl.name for m in proposal.machines if (lbl := store.label_for(m))})
        if unnamed_only and store.covers(proposal.machines):
            continue
        shown += 1
        if not window.start < shown <= window.end:
            continue
        candidate = candidates.describe(
            proposal.machines, st.graph, st.game, st.projection, "proposal"
        )
        rows.append(
            (
                k,
                proposal.size,
                f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)}",
                f"{candidate.spread_m:.0f}m",
                "+".join(str(x) for x in proposal.parts)
                if len(proposal.parts) > 1
                else proposal.size,
                "+".join(signal for signal, _ in proposal.evidence.most_common(3)),
                ", ".join(names)[:26] or "-",
                (suggested.get(k) or naming.lead_of(st, proposal.machines, candidate)[0])[:34],
            )
        )
    covered = sum(1 for pr in proposals for m in pr.machines if store.label_for(m))
    return render.envelope(
        f"# {st.age_note}\n# {len(proposals)} proposal(s) over "
        f"{len(st.graph.machines())} machines; {covered} already named",
        render.table(
            ("#", "machines", "x,y(m)", "spread", "parts", "evidence", "labels", "name or makes"),
            rows,
            total=shown,
            offset=window.start,
            limit=window.size,
        ),
        [
            (
                f"clusters only LINK within {max_span_m:.0f}m, so a sprawling factory is "
                "offered in pieces -- raise max_span_m if yours is bigger"
            ),
            (
                "a 'parts' column with more than one number means dependents were "
                "absorbed: a cluster whose belts and pipes lead almost only into one "
                "other factory joins it, however far away it sits"
            ),
            GRAPH_INDEX_WARNING,
        ],
    )


@app.tool()
def select_machines(
    select: Annotated[
        list[str], Field(description=f"selector terms, ANDed. {GRAPH_SELECTOR_HELP}")
    ],
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    split: Annotated[bool, Field(description="keep only the largest spatial cluster")] = False,
    expand: Annotated[bool, Field(description="pull in everything belted to the result")] = False,
) -> str:
    """Preview which machines a selector picks, before naming them.

    Worth running first on anything product-based: a product selector also picks machines
    feeding that product inside other sites. `slab:<n>` answers "what stands on this
    platform", and describes an empty platform rather than refusing it.
    """
    st = app.load_world(save, world, as_of)
    picked = machine_select.select_machines(select, st, split=split, expand=expand)
    if not picked:
        empty = _empty_platform(select, st.structures)
        if empty:
            return render.envelope(f"# {st.age_note}", empty)
        return "! that selector matched no machines"

    candidate = candidates.describe(picked, st.graph, st.game, st.projection, "selector")
    groups = candidates.cluster_machines(picked, st.projection)
    parts = [
        render.kv(
            [
                ("machines", candidate.size),
                ("at", f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)}"),
                ("spread", f"{candidate.spread_m:.0f}m"),
                ("clusters", len(groups)),
            ]
        ),
        "products: "
        + (", ".join(f"{k} {v}" for k, v in candidate.products.most_common(10)) or "-"),
        "buildings: "
        + ", ".join(
            f"{v}x {st.game.building_name(k) or k}" for k, v in candidate.buildings.most_common(8)
        ),
    ]
    if len(groups) > 1:
        largest = candidates.describe(groups[0], st.graph, st.game, st.projection, "selector")
        parts.append(
            f"! {len(groups)} separate sites {[len(g) for g in groups]}; the largest is "
            f"{largest.size} at {int(largest.centroid[0] / 100)},"
            f"{int(largest.centroid[1] / 100)}. Pass split=true to keep only that one."
        )
    clashes = {lbl.name for m in picked if (lbl := st.labels.label_for(m))}
    if clashes:
        parts.append("already named: " + ", ".join(sorted(clashes)))
    return render.envelope(
        f"# {st.age_note}", "\n".join(parts), machine_select.pin_notes(select, st)
    )
