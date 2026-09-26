"""Factory identity: proposing, naming, querying and health.

The tools over ``graph/`` -- candidates from several signals, persisted
labels, and the two read-outs built on a named machine set."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from pydantic import Field

from ....core.saveio import ports
from ....domain.factories.query import ASPECTS as QUERY_ASPECTS
from ....domain.factories.resolve import resolve_factory
from ....domain.factories.select import INDEX_WARNING as GRAPH_INDEX_WARNING
from ....domain.factories.select import SELECTOR_HELP as GRAPH_SELECTOR_HELP
from ....domain.factories.select import SelectorError
from ....domain.factories.trace import power_at_risk, trace
from ....domain.spatial import nodes as nodes_mod
from ....presenters.text import primitives as render
from ..app import AsOf, Limit, _state, game, mcp, retired

#: Bare (machine-less) slabs at or above this many tiles are listed individually by
#: factory_map; smaller ones are one summary line. 12 tiles is a 3x4 pour of 8 m
#: foundations -- below that a bare slab is a helper pad (a tile under a power pole, a
#: jump-pad landing), and the reference save has dozens of those against a handful of
#: real platforms. The response quotes this number so a summarised pad is a known
#: omission rather than a blind spot.
BARE_TILE_FLOOR = 12

#: factory_query aspects that print an items/min rate, and therefore owe the reader the
#: sentence saying which window "measured" was measured over.
FLOW_ASPECTS = frozenset({"summary", "balance", "outputs", "inputs", "internal"})

#: How many unpowerable machines factory_health names before it counts the rest, per note.
#: Named at all because the instance name is what show_on_map and factory_query take back,
#: so a list of eight is a list of eight things the reader can go and look at; a hundred of
#: them is one unwired BLOCK, and its name is not in this note.
UNWIRED_NAMED = 8


def _named(instances: list[str]) -> str:
    """The first ``UNWIRED_NAMED`` instance names, and a count of the rest."""
    rest = len(instances) - UNWIRED_NAMED
    return ", ".join(instances[:UNWIRED_NAMED]) + (f", and {rest} more" if rest > 0 else "")


def _z_range(slab) -> str:
    """A slab's elevation in metres -- both ends of it where they differ.

    Not just the deck it starts on: a platform poured over three storeys stands at
    BOTH heights, and a build plan that reads only the bottom one puts a machine
    under the floor. One number where the whole pour is flat, which is most of them.
    """
    lo, hi = slab.z_span[0] / 100, slab.z_span[1] / 100
    return f"{lo:.0f}" if round(lo) == round(hi) else f"{lo:.0f}..{hi:.0f}"


def _slab_shape(slab) -> tuple:
    """Bounding box, elevation and storeys -- what a build plan needs past the centre.

    The same three columns for a slab carrying machines as for a bare one. They were only
    ever printed for bare platforms, so the ones you could actually build against were the
    ones that reported a footprint.
    """
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


def _pick(st, terms: list[str], split: bool = False, expand: bool = False) -> list[str]:
    """Resolve selector terms against one world state. Raises ``SelectorError``."""
    from ....domain.factories import select as gsel

    return gsel.select_machines(terms, st, split=split, expand=expand)


def _overlaps(store, machines, name: str) -> list[str]:
    """Which OTHER labels already hold the machines about to be named here."""
    stolen: dict[str, int] = {}
    for machine in machines:
        other = store.label_for(machine)
        if other and other.name.casefold() != name.strip().casefold():
            stolen[other.name] = stolen.get(other.name, 0) + 1
    return [f"overlaps {other!r} on {n} machine(s)" for other, n in sorted(stolen.items())]


def _cand_row(c, store, labelled: set[str]) -> tuple:
    named = {store.label_for(m).name for m in c.machines if store.label_for(m)}
    covered = sum(1 for m in c.machines if m in labelled)
    return (
        c.source,
        c.size,
        f"{int(c.centroid[0] / 100)},{int(c.centroid[1] / 100)}",
        f"{c.spread_m:.0f}m",
        f"{covered}/{c.size}" if covered else "-",
        ", ".join(sorted(named))[:40] or "-",
        c.name_hint()[:44],
    )


#: Run ids named in a trace's route note. The reference world's deepest walk crosses 166
#: runs, so this is a sample to follow and the counts beside it are the whole answer.
VIA_NAMED = 6


def _via(crossed: list) -> str:
    """The route a trace took, as runs rather than as the hundreds of nodes they contract.

    Named in the order the walk met them, so the sample is the near end of the chain rather
    than six consecutive pipes of whichever network sorts first. Belt runs are named beside
    the pipes since schema 20 gave a belt segment its actor index.
    """
    if not crossed:
        return ""
    belts = sum(1 for link in crossed if link.medium == ports.CONVEYOR)
    idents = [link.ident for link in crossed if link.ident]
    counted = " and ".join(
        f"{n} {word} run(s)" for n, word in ((belts, "belt"), (len(crossed) - belts, "pipe")) if n
    )
    out = f"the route ran through {counted}, {sum(link.pieces for link in crossed)} pieces in all"
    if idents:
        rest = len(idents) - VIA_NAMED
        out += (
            "; it crossed "
            + ", ".join(idents[:VIA_NAMED])
            + (f" and {rest} more" if rest > 0 else "")
            + ", which search_conduits and show_on_map both take"
        )
    return out


def _feed_row(machine, feed) -> tuple:
    """One starved input and the run that should be bringing it.

    The not-fed verdicts read differently on purpose: nothing arriving is a finding and so is
    a network no source reaches, while a run whose far end the save joins to no actor is not.
    """
    from ....domain.factories.health import JOINED, NOTHING, OPEN, UNFED

    carrier = "conveyor" if feed.medium == ports.CONVEYOR else "pipe"
    if feed.verdict == NOTHING:
        arrives, far = f"NO {carrier} arrives", ""
    else:
        arrives = f"{feed.run or carrier} x{feed.pieces}"
        far = "far end joined to nothing" if feed.verdict == OPEN else f"{feed.far_name} {feed.far}"
        if feed.verdict == JOINED:
            far += " (which way is unresolved)"
        if feed.verdict == UNFED:
            far += " -- and NO source anywhere on this network"
        if feed.makes:
            far += " -- MAKES it"
    return (machine.instance, feed.item, arrives, far, feed.far_state)


@mcp.tool(structured_output=False)
def factory_map(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 12,
    offset: int = 0,
    show: Annotated[
        str, Field(description="candidates | named | slabs | unlabelled | all")
    ] = "all",
) -> str:
    """Proposed factories, from power islands and belt topology, plus what is named.

    Three independent signals are reported rather than one answer, because none is
    right alone: power islands separate outposts but leave a grown-together base as one
    476-machine blob; belt components shatter that blob into fragments; foundation slabs
    are the sharpest of the three but say nothing about the ground-built parts of a
    factory. Where they disagree, carve the difference with `name_factory` and a
    `product:`, `near:` or `slab:` selector.

    show=slabs also lists BARE platforms -- poured foundations carrying no machine yet
    -- with tile count, extent, bounding box and elevation, because a freshly built
    platform is a real place a build plan refers to. Pads under a stated tile threshold
    are summarised in one line.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories import identity

    gr = st.graph
    store = st.labels
    base_c, line_c = identity.candidates(gr, st.game, st.projection)
    labelled = store.assigned()
    machines = set(gr.machines())
    n = render.clamp(limit)
    start = max(0, offset)
    end = start + n

    want = show.casefold()
    chunks: list[str] = []
    notes: list[str] = []

    if want in ("all", "named") and store.labels:
        rows = []
        for label in sorted(store.labels, key=lambda x: -len(x.anchors)):
            alive = set(label.anchors) & machines
            cand = identity.describe(sorted(alive), gr, st.game, st.projection, "label")
            rows.append(
                (
                    label.name,
                    len(label.anchors),
                    f"{len(alive)}/{len(label.anchors)}",
                    f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}",
                    f"{cand.spread_m:.0f}m",
                    cand.name_hint()[:44],
                )
            )
        chunks.append(
            "## named\n"
            + render.table(("name", "machines", "alive", "x,y(m)", "spread", "makes"), rows)
        )
        for issue in store.review(machines):
            notes.append(
                f"{issue['name']}: {issue['missing']} anchor machine(s) gone "
                f"(recall {issue['recall']}) -- {issue['status']}"
            )

    if want in ("all", "candidates"):
        rows = [_cand_row(c, store, labelled) for c in base_c[start:end]]
        chunks.append(
            "## power islands (bases)\n"
            + render.table(
                ("src", "n", "x,y(m)", "spread", "named", "labels", "makes"),
                rows,
                total=len(base_c),
                offset=start,
                limit=n,
            )
        )
        fresh = [c for c in line_c if not store.covers(c.machines)]
        rows = [_cand_row(c, store, labelled) for c in fresh[start:end]]
        chunks.append(
            "## belt components (lines), unnamed first\n"
            + render.table(
                ("src", "n", "x,y(m)", "spread", "named", "labels", "makes"),
                rows,
                total=len(fresh),
                offset=start,
                limit=n,
            )
        )

    if want in ("all", "slabs"):
        sx = st.structures
        rows = []
        for group in sx.groups()[start:end]:
            index = sx.slab_of[group[0]]
            slab = sx.slabs[index]
            cand = identity.describe(group, gr, st.game, st.projection, "structure")
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
                    cand.name_hint()[:38],
                )
            )
        census = sx.summary()
        chunks.append(
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
                total=len(sx.groups()),
                offset=start,
                limit=n,
            )
        )

        # Bare platforms too. show=slabs used to list only slabs CARRYING machines, so a
        # freshly poured 1,901-foundation platform -- the most important object in that
        # build -- was invisible, and its extent got reconstructed from nine
        # describe_location probes by hand. The threshold below keeps helper pads (a
        # tile under a pole, a jump-pad) from drowning the platforms worth naming, and
        # the output states it so a summarised pad is a known omission, not a blind one.
        occupied = set(sx.slab_of.values())
        bare = [s for s in sx.slabs if s.index not in occupied]
        listed = [s for s in bare if s.tiles >= BARE_TILE_FLOOR]
        pads = [s for s in bare if s.tiles < BARE_TILE_FLOOR]
        if bare:
            brows = [
                (
                    slab.index,
                    slab.tiles,
                    f"{int(slab.centre[0] / 100)},{int(slab.centre[1] / 100)}",
                    f"{int(slab.extent[0] / 100)}x{int(slab.extent[1] / 100)}m",
                    *_slab_shape(slab),
                )
                for slab in listed[start:end]
            ]
            header = (
                f"## bare platforms (no machines): {len(bare)}, {sum(s.tiles for s in bare)} tiles"
            )
            body = render.table(
                ("slab", "tiles", "x,y(m)", "extent", "bbox(m)", "z(m)", "floors"),
                brows,
                total=len(listed),
                offset=start,
                limit=n,
            )
            if pads:
                body += (
                    f"\n# plus {len(pads)} pad(s) under {BARE_TILE_FLOOR} tiles "
                    f"({sum(s.tiles for s in pads)} tiles total), summarised here "
                    "by that threshold"
                )
            chunks.append(f"{header}\n{body}")
        else:
            chunks.append("## bare platforms (no machines): none")

        shown = [sx.slabs[sx.slab_of[g[0]]] for g in sx.groups()[start:end]] + listed[start:end]
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

        ground = len(machines) - len(sx.slab_of)
        if ground:
            notes.append(
                f"{ground} machine(s) stand on no foundation at all -- slabs cannot see "
                "them, so this signal is a candidate and never the arbiter."
            )

    if want in ("all", "unlabelled"):
        loose = identity.unassigned(gr, labelled)
        if loose:
            grouped = identity.describe(loose, gr, st.game, st.projection, "unlabelled")
            top = ", ".join(f"{name} {count}" for name, count in grouped.products.most_common(12))
            chunks.append(f"## unlabelled: {len(loose)} machine(s)\n{top or '(no recipes set)'}")

    if want in ("all", "candidates", "slabs"):
        notes.append(GRAPH_INDEX_WARNING)

    if base_c and base_c[0].size > 100:
        notes.append(
            f"the largest power island holds {base_c[0].size} machines across "
            f"{base_c[0].spread_m:.0f}m -- that is a grown-together base, not one factory. "
            "Carve it with name_factory(select=['product:Steel Ingot','near:x,y@150'])."
        )

    return render.envelope(
        f"# {st.age_note}\n# {len(machines)} machines; {len(base_c)} power island(s), "
        f"{len(line_c)} belt component(s); {len(store.labels)} named, "
        f"{len(labelled & machines)} machine(s) covered",
        "\n\n".join(chunks),
        notes,
    )


@mcp.tool(structured_output=False)
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

    `show` accepts several at once, e.g. "balance,power,links". `offset` pages every
    table in the answer at once, so asking for one aspect at a time is what you want
    when a factory has more machines than fit.

    - **summary** size, position, top recipes, net power
    - **balance** per-item produced vs consumed vs net -- the sign is the point
    - **outputs** net surplus: it leaves the factory, or it backs up
    - **inputs** net deficit: it has to be fed in from outside
    - **internal** made and eaten inside the set -- the mark of a self-contained line
    - **machines** every machine with its building, recipe and clock
    - **recipes** / **buildings** counts
    - **power** draw vs generation, nameplate AND measured -- which factory is really
      burning the grid, rather than which could
    - **nodes** resource nodes its extractors sit on
    - **links** which other factories it exchanges material with
    - **issues** paused, recipe-less, or unresolved machines

    Every rate is printed twice. NAMEPLATE is the machine's recipe rate at its saved clock,
    which a starved factory still reports in full. MEASURED is that rate scaled by the share
    of its own productivity window each machine spent producing -- the window that ended
    when the save was written, so a line idle at that moment measures 0 and is not broken.
    A machine keeping no monitor is left out of measured entirely and shown separately,
    because counting it in full there would invent output.
    """
    from ....domain.factories.query import build_view

    if gone := retired(("of", of, "show")):
        return gone
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        name, machines = resolve_factory(st, factory)
    except SelectorError as exc:
        return f"! {exc}"
    if not machines:
        return f"! {factory!r} resolved to no machines that still exist in this save"

    view = build_view(name, machines, st.graph, st.game, st.projection, st.labels)
    asked = [a.strip().casefold() for a in show.split(",") if a.strip()]
    unknown = [a for a in asked if a not in QUERY_ASPECTS]
    if unknown:
        return f"! unknown aspect(s) {unknown}. Choose from: {', '.join(QUERY_ASPECTS)}"

    n = render.clamp(limit)
    start = max(0, offset)
    end = start + n
    chunks: list[str] = []
    g = st.game

    def bname(cls: str) -> str:
        # ``GameData.building_name``, not a local mangling: this tool and the map page
        # describe the same machines, and until this call they described them under
        # different names whenever the dump had no entry for the class.
        return g.building_name(cls) or cls

    def measured_cells(item: str, side: str) -> tuple[str, str]:
        """The measured rate for one item, and the nameplate rate no monitor can see.

        "?" rather than 0 when nothing readable touches the item that way: unknown and
        stopped are different claims, and printing zero makes the second.
        """
        f = view.flows[item]
        blind = f[f"unmonitored_{side}"]
        return (
            render.num(f[f"measured_{side}"]) if view.measurable(item, side) else "?",
            render.num(blind) if blind > 1e-9 else "",
        )

    def brief(pairs, side: str) -> str:
        out = []
        for k, v in pairs:
            f = view.flows[k]
            seen = (
                f"measured {f[f'measured_{side}']:.0f}"
                if view.measurable(k, side)
                else "no monitor"
            )
            out.append(f"{k} {v:.0f}/min ({seen})")
        return ", ".join(out) or "-"

    for aspect in asked:
        if aspect == "summary":
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
            makes = brief(view.outputs()[:5], "produced")
            needs = brief(view.inputs()[:5], "consumed")
            keeps = brief(view.internal()[:5], "produced")
            chunks.append(f"## summary\n{head}\nmakes: {makes}\nneeds: {needs}\nkeeps: {keeps}")
        elif aspect == "balance":
            rows = []
            for item in sorted(view.flows, key=lambda k: -abs(view.net(k))):
                f = view.flows[item]
                net = view.net(item)
                verdict = (
                    "surplus" if net > 1e-6 else "needs feeding" if net < -1e-6 else "internal"
                )
                readable = view.measurable(item, "produced") or view.measurable(item, "consumed")
                rows.append(
                    (
                        item,
                        render.num(f["produced"]),
                        render.num(f["consumed"]),
                        f"{net:+.1f}",
                        f"{view.measured_net(item):+.1f}" if readable else "?",
                        verdict,
                    )
                )
            chunks.append(
                "## balance (items/min at saved clocks)\n"
                + render.table(
                    ("item", "made", "used", "net", "net (measured)", ""),
                    rows[start:end],
                    total=len(rows),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect in ("outputs", "inputs"):
            data = view.outputs() if aspect == "outputs" else view.inputs()
            side = "produced" if aspect == "outputs" else "consumed"
            chunks.append(
                f"## {aspect}\n"
                + render.table(
                    ("item", "per min", "per min (measured)", "no monitor"),
                    [(k, render.num(v), *measured_cells(k, side)) for k, v in data[start:end]],
                    total=len(data),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect == "internal":
            data = view.internal()
            body = (
                render.table(
                    ("item", "per min", "per min (measured)", "no monitor"),
                    [
                        (k, render.num(v), *measured_cells(k, "produced"))
                        for k, v in data[start:end]
                    ],
                    total=len(data),
                    offset=start,
                    limit=n,
                )
                if data
                else "none: every item this factory touches crosses its boundary"
            )
            chunks.append(
                "## internal (made and consumed inside this factory, at saved clocks)\n"
                "# nothing on this list crosses the boundary: it neither needs feeding\n"
                f"# nor leaves, which is what a finished line looks like\n{body}"
            )
        elif aspect == "machines":
            rows = [
                (
                    m.instance,
                    bname(m.building),
                    m.recipe or "-",
                    f"{m.clock:.0%}",
                    f"{m.pos[0] / 100:.0f},{m.pos[1] / 100:.0f},{m.pos[2] / 100:.0f}",
                    "paused" if m.paused else "",
                )
                for m in sorted(view.machines, key=lambda x: (x.building, x.recipe))
            ]
            chunks.append(
                "## machines\n"
                + render.table(
                    ("instance", "building", "recipe", "clock", "x,y,z(m)", ""),
                    rows[start:end],
                    total=len(rows),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect == "recipes":
            chunks.append(
                "## recipes\n"
                + render.table(
                    ("recipe", "machines"),
                    view.recipes.most_common()[start:end],
                    total=len(view.recipes),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect == "buildings":
            chunks.append(
                "## buildings\n"
                + render.table(
                    ("building", "count"),
                    [(bname(c), v) for c, v in view.buildings.most_common()[start:end]],
                    total=len(view.buildings),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect == "power":
            chunks.append(
                "## power\n"
                + render.kv(
                    [
                        ("draw (nameplate)", f"{view.draw_mw:.1f} MW"),
                        ("draw (measured)", f"{view.measured_draw_mw:.1f} MW"),
                        ("generation", f"{view.generation_mw:.1f} MW"),
                        ("net (nameplate)", f"{view.generation_mw - view.draw_mw:+.1f} MW"),
                        (
                            "net (measured)",
                            f"{view.generation_mw - view.measured_draw_mw:+.1f} MW",
                        ),
                    ]
                )
            )
        elif aspect == "nodes":
            chunks.append(
                "## resource nodes\n"
                + render.table(
                    ("node", "resource", "purity", "extractor", "clock", "left"),
                    [
                        (a, b, c, bname(d), f"{e:.0%}", f if f is not None else "-")
                        for a, b, c, d, e, f in view.nodes[start:end]
                    ],
                    total=len(view.nodes),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect == "links":
            chunks.append(
                "## material links across the boundary\n"
                "# machines reached on the far side, not an edge count -- asymmetric by\n"
                "# nature, since the first machine of a small set blocks the rest\n"
                + render.table(
                    ("other side", "machines reached"),
                    view.links.most_common()[start:end],
                    total=len(view.links),
                    offset=start,
                    limit=n,
                )
            )
        elif aspect == "issues":
            body = render.bullets(view.issues[start:end]) if view.issues else "none"
            chunks.append(f"## issues ({len(view.issues)})\n{body}")

    notes = []
    # `left` in the nodes aspect is mResourcesLeft joined to the node table by instance
    # name, and resource/purity in that table come from the same join -- so a node a game
    # update renamed shows "?" for all three with no stated reason. Identity only: this
    # tool quotes no coordinate, so position drift is not its problem.
    notes += nodes_mod.identity_notes(
        nodes_mod.skew_for_save(st.header), [row[0] for row in view.nodes]
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
    return render.envelope(
        f"# {st.age_note}\n# {name}: {view.size} machines", "\n\n".join(chunks), notes
    )


@mcp.tool(structured_output=False)
def factory_health(
    factory: Annotated[
        str, Field(description="a label name, any selector, or 'all' for every named factory")
    ] = "all",
    limit: Limit = 15,
    offset: int = 0,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Measured uptime per machine, and WHY each stopped one is stopped.

    The only measured numbers in this MCP. Every manufacturing building keeps a fixed
    300-second productivity window; uptime is seconds-producing over that window.

    States, worst first: `paused`, `dead node` (extractor bound to no resource --
    a game update removed it), `no recipe`, `blocked` (output stack full),
    `starved` (input empty), `stalled` (has input, output has room, still not running),
    `intermittent`, `saturated`, `unmonitored`.

    A stalled machine no generator can reach over the wires says so in its `cause`, and
    every such machine is named in a note whatever state it is in -- separately for "no
    wire at all" and "wired to a circuit no generator stands on", which are different
    builds to finish. The save records no "has power" flag, so a machine a generator CAN
    reach is never called unpowered here whatever the grid is doing: the wire is the only
    electrical fact the file carries.

    For a STARVED machine it also says what physically feeds the input it lacks: the run
    that arrives, what stands at its far end and that feeder's own state, ONE hop back --
    `trace_upstream` walks the rest. "No conduit of that medium arrives" and "one arrives
    and the save joins its far end to nothing" are different rows and are never merged.

    A missing FLUID is diagnosed on the plumbing manual's own ladder -- (1) connection,
    (2) head lift, (3) flow rate -- and the `cause` names the FIRST rung that fires, so a
    line that cannot climb to the machine is never answered with its supply rates. Solids
    have no head-lift rung and read exactly as before.

    **Blocked counts as needing action**, alongside dead node, no recipe, starved and
    stalled: a full output box means nothing is taking what the machine makes. The sweep's
    `todo` column counts those five.

    The sweep over every factory also reports three plumbing faults that belong to no machine
    set: fluid buffers holding too little to output at their intake rate, pipeline pumps no
    wire reaches, and points where a line climbs above the head lift pushing it. All three
    are world-wide there, not scoped to a factory.

    `offset` pages every table in the answer at once, worst first throughout.
    """
    from ....domain.factories.health import (
        ACTIONABLE,
        FLOW_RATE,
        NOTHING,
        OPEN,
        RUNGS,
        STATES,
        UNFED,
        assess,
        summarise,
    )
    from ....domain.factories.select import SelectorError
    from ....domain.world.headlift import head_lift
    from ....domain.world.plumbing import dark_pumps, throttled_buffers

    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    alive = set(st.graph.machines())
    n = render.clamp(limit)
    start = max(0, offset)
    end = start + n

    if factory.strip().casefold() in ("all", "*"):
        if not st.labels.labels:
            return "! nothing named yet -- run propose_factories, then name_factory"
        from ....domain.factories.query import build_view

        rows, notes = [], []
        blocked_total = 0
        dark_total = 0
        for label in sorted(st.labels.labels, key=lambda x: -len(x.anchors)):
            standing = [m for m in label.anchors if m in alive]
            report = assess(label.name, standing, st.game, st.projection, st.graph)
            view = build_view(label.name, standing, st.graph, st.game, st.projection, st.labels)
            mean = report.mean_uptime
            actionable = sum(report.by_state[s] for s in ACTIONABLE)
            blocked_total += report.by_state["blocked"]
            dark_total += len(report.unwired) + len(report.no_generator)
            rows.append(
                (
                    label.name,
                    len(report.machines),
                    "-" if mean is None else f"{mean:.0%}",
                    f"{view.measured_draw_mw:.0f}",
                    report.by_state["blocked"] or "",
                    report.by_state["starved"] or "",
                    report.by_state["stalled"] or "",
                    report.by_state["no recipe"] or "",
                    report.by_state["dead node"] or "",
                    report.by_state["paused"] or "",
                    actionable or "",
                )
            )
        # Sort on the accumulated values, not on a column position: inserting a column
        # once silently reordered this table by the wrong field.
        rows.sort(key=lambda r: (-(r[-1] or 0), r[2]))
        notes.append(
            "measured MW is each machine's rated draw weighted by its own 300s productivity "
            "window -- what the factory is actually taking off the grid, not what it could"
        )
        if blocked_total:
            notes.append(
                f"{blocked_total} machine(s) are blocked -- their output stack is full, so "
                "nothing is taking what they make. They count in todo; factory_health on "
                "one factory names the items backing up"
            )
        if dark_total:
            # No column for it: this table is sorted on accumulated values because a
            # column once moved and took the sort order with it, and a count that is
            # zero on a finished factory does not earn eleven more cells.
            notes.append(
                f"{dark_total} machine(s) across these factories are on no circuit a "
                "generator stands on -- no wire at all, or a wire to a circuit with no "
                "source. factory_health on the one factory names them and says which"
            )
        chunks = [
            render.table(
                (
                    "factory",
                    "n",
                    "uptime",
                    "measured MW",
                    "blocked",
                    "starved",
                    "stalled",
                    "no recipe",
                    "dead node",
                    "paused",
                    "todo",
                ),
                rows[start:end],
                total=len(rows),
                offset=start,
                limit=n,
            )
        ]
        # Plumbing is world-wide here and nowhere else: a buffer and a pump belong to no
        # machine set, so the sweep is the only view either can honestly appear in.
        throttled = throttled_buffers(st.projection, st.game)
        if throttled:
            chunks.append(
                "## fluid buffers below the level they need\n"
                + render.table(
                    ("buffer", "fluid", "holding m3", "needs m3"),
                    [
                        (
                            b.instance,
                            st.game.item_name(b.fluid) if b.fluid else "-",
                            f"{b.stored_m3:.0f}",
                            f"{b.balance_m3:.0f}",
                        )
                        for b in throttled[start:end]
                    ],
                    total=len(throttled),
                    offset=start,
                    limit=n,
                )
            )
            notes.append(
                f"{len(throttled)} fluid buffer(s) world-wide are under the level they need: "
                "a buffer's head lift is the height of the fluid standing in it, so one "
                "holding less than 1.5 m of fluid outputs slower than it takes in, silently"
            )
        head = head_lift(st.projection, st.game, st.graph)
        if head.faults:
            chunks.append(
                "## fluid lines that climb higher than their supply can push\n"
                + render.table(
                    ("fluid", "crest m", "head m", "short by", "machines cut off", "state"),
                    [
                        (
                            st.game.item_name(c.fluid) if c.fluid else "-",
                            f"{c.crest_m:.1f}",
                            f"{c.head_m:.1f}",
                            f"{c.short_m:.1f}",
                            len(c.consumers),
                            "marginal" if c.marginal else "cut off",
                        )
                        for c in head.faults[start:end]
                    ],
                    total=len(head.faults),
                    offset=start,
                    limit=n,
                )
            )
            notes.append(
                f"{len(head.faults)} point(s) on the plumbing stand above the head lift "
                "behind them, so nothing past them is supplied -- a pump placed BEFORE the "
                "crest is the fix, and a second pump after it would add nothing"
            )
        if head.buffer_lines:
            chunks.append(
                "## lines running on a part-full buffer's own head\n"
                + render.table(
                    ("fluid", "rises to m", "buffer delivers at m", "over by", "machines past it"),
                    [
                        (
                            st.game.item_name(c.fluid) if c.fluid else "-",
                            f"{c.crest_m:.1f}",
                            f"{c.head_m:.1f}",
                            f"{c.short_m:.1f}",
                            len(c.consumers),
                        )
                        for c in head.buffer_lines[start:end]
                    ],
                    total=len(head.buffer_lines),
                    offset=start,
                    limit=n,
                )
            )
            notes.append(
                "a buffer passes incoming head on only when it is nearly full, so the line "
                "above one gets the buffer's own fluid level and no more. These are NOT "
                "called faults: the same shape runs at full uptime on this world, so the "
                "reading is 'this line has no margin above its buffer', not 'it is broken'"
            )
            if head.undecided_buffers:
                notes.append(
                    f"{head.undecided_buffers} buffer(s) sit in the band between the fill "
                    "measured not to pass head on and the one measured to, so a constant "
                    "settled them rather than a measurement"
                )
            if any(c.assumed for c in head.crests):
                notes.append(
                    "some of those rest on the 10 m of head lift a normal machine is assumed "
                    "to give, which is the plumbing manual's figure and is in no game data"
                )
        if head.unfed_ports:
            named = sorted(set(head.unfed_ports))
            rest = len(named) - UNWIRED_NAMED
            notes.append(
                f"{len(named)} machine(s) draw a fluid from a pipe network that reaches NO "
                "source at all -- rung (1) of the plumbing manual's order, a line to finish "
                "rather than a shortage, and not a head-lift fault: "
                + ", ".join(named[:UNWIRED_NAMED])
                + (f", and {rest} more" if rest > 0 else "")
            )
        dark, unseen = dark_pumps(st.projection, st.graph)
        if dark:
            rest = len(dark) - UNWIRED_NAMED
            notes.append(
                f"{len(dark)} pipeline pump(s) have no electrical connection: an unpowered "
                "pump still passes fluid but lifts nothing, so a line that climbs past it "
                "stops climbing and no machine on it looks broken -- "
                + ", ".join(dark[:UNWIRED_NAMED])
                + (f", and {rest} more" if rest > 0 else "")
            )
        if unseen:
            notes.append(
                f"{unseen} pipeline pump(s) are coupled to no pipe at all and were not "
                "checked for a wire"
            )
        return render.envelope(
            f"# {st.age_note}\n# uptime measured over a 300s window per machine",
            "\n\n".join(chunks),
            notes,
        )

    try:
        name, machines = resolve_factory(st, factory)
    except SelectorError as exc:
        return f"! {exc}"
    if not machines:
        return f"! {factory!r} resolved to no machines that still exist in this save"

    heads = head_lift(st.projection, st.game, st.graph)
    report = assess(name, machines, st.game, st.projection, st.graph, st.physical, heads)
    chunks = [summarise(report)]

    worst = report.worst(end)[start:]
    if worst:
        chunks.append(
            "## needs attention\n"
            + render.table(
                ("instance", "state", "uptime", "recipe", "cause"),
                [
                    (
                        m.instance,
                        m.state,
                        "-" if m.uptime is None else f"{m.uptime:.0%}",
                        m.recipe or st.game.building_name(m.building) or m.building,
                        ", ".join(m.cause),
                    )
                    for m in worst
                ],
                total=sum(1 for m in report.machines if m.needs_attention),
                offset=start,
                limit=n,
            )
        )
    if report.blocked_on:
        chunks.append(
            "## output backing up\n"
            + render.table(
                ("item", "machines blocked"),
                report.blocked_on.most_common()[start:end],
                total=len(report.blocked_on),
                offset=start,
                limit=n,
            )
        )
    if report.starved_of:
        chunks.append(
            "## inputs not arriving\n"
            + render.table(
                ("ingredient", "machines starved"),
                report.starved_of.most_common()[start:end],
                total=len(report.starved_of),
                offset=start,
                limit=n,
            )
        )
    supply = [(m, f) for m in report.machines for f in m.feeds]
    if supply:
        chunks.append(
            "## what feeds the missing input\n"
            + render.table(
                ("machine", "missing", "arrives by", "at the far end", "its state"),
                [_feed_row(m, f) for m, f in supply[start:end]],
                total=len(supply),
                offset=start,
                limit=n,
            )
        )
    # Only the crests that cut off a machine in THIS factory: a crest is a world-wide finding
    # and belongs to no machine set, but the ones behind these machines are why they are on
    # rung (2), and naming a crest is what says where the pump goes.
    standing = {m.instance for m in report.machines}
    crests = [c for c in heads.crests if standing.intersection(c.consumers)]
    if crests:
        chunks.append(
            "## where the fluid stops climbing\n"
            + render.table(
                ("fluid", "crest m", "head m", "short by", "machines here"),
                [
                    (
                        st.game.item_name(c.fluid) if c.fluid else "-",
                        f"{c.crest_m:.1f}",
                        f"{c.head_m:.1f}",
                        f"{c.short_m:.1f}",
                        len(standing.intersection(c.consumers)),
                    )
                    for c in crests[start:end]
                ],
                total=len(crests),
                offset=start,
                limit=n,
            )
        )

    notes = []
    if crests:
        notes.append(
            "a pump placed BEFORE the crest is the fix and a second one after it would add "
            "nothing -- head lift does not stack pump to pump, only with the height a pump "
            "already stands at"
        )
    if supply:
        notes.append(
            "the far end is ONE hop: trace_upstream walks the rest of the chain. A run "
            "carries whatever is put on it, so where several arrive the save does not say "
            "which was meant to bring the item -- only the one marked MAKES it provably could"
        )
        nothing = sum(1 for _m, f in supply if f.verdict == NOTHING)
        if nothing:
            notes.append(
                f"{nothing} of these inputs have NO conduit of that medium arriving at all -- "
                "the item cannot reach the machine, which is a build to finish, not a shortage"
            )
        sourceless = sum(1 for _m, f in supply if f.verdict == UNFED)
        if sourceless:
            notes.append(
                f"{sourceless} of these inputs arrive by a pipe from a real fitting whose "
                "network reaches NO source at all -- the conduit is fine and nothing "
                "anywhere puts that fluid into it, so the fix is a source rather than a "
                "pump or a reroute"
            )
        loose = sum(1 for _m, f in supply if f.verdict == OPEN)
        if loose:
            notes.append(
                f"{loose} run(s) do arrive and the save joins their far end to nothing, so "
                "the feeder is UNKNOWN there rather than absent -- a torn line or a build in "
                "progress. Not the same finding as the row above"
            )
        # Counted per (machine, ingredient), not per row: one input reached by three runs is
        # one diagnosis, and the rung is shared by all three.
        rungs = Counter(
            rung for _m, _item, rung in {(m.instance, f.item, f.rung) for m, f in supply if f.rung}
        )
        if rungs:
            reached = [
                f"{rungs[rung]} at ({i}) {rung}" for i, rung in enumerate(RUNGS, 1) if rungs[rung]
            ]
            notes.append(
                f"{sum(rungs.values())} missing fluid(s) walked the plumbing manual's order -- "
                "(1) connection, (2) head lift, (3) flow rate -- and each stops at the first "
                "rung that fires, which is what the bracket in its cause names: "
                + ", ".join(reached)
                + (
                    ". Reaching (3) means the head-lift model checked the climb and ruled its "
                    "own rung out"
                    if rungs[FLOW_RATE]
                    else ""
                )
            )
    if report.by_state["blocked"]:
        notes.append(
            f"{report.by_state['blocked']} blocked: output stack full, so its consumer "
            "is the bottleneck -- or nothing is drawing from it at all"
        )
    if report.by_state["dead node"]:
        notes.append(
            f"{report.by_state['dead node']} extractor(s) sit on NO resource node -- "
            "the node was removed, so they can never produce and must be rebuilt elsewhere"
        )
    if report.by_state["stalled"]:
        # This used to end "Check power before anything else", which was advice offered
        # because nothing here could check. The wires are in the save, so it is checked.
        dark = sum(1 for m in report.machines if m.state == "stalled" and m.cause)
        notes.append(
            f"{report.by_state['stalled']} stalled: has input, output has room, still not "
            + (
                f"producing, and {dark} of them no generator can reach over the wires -- "
                "their cause says whether that is no wire at all or a circuit with no source"
                if dark
                else "producing -- and every one of them is on a circuit some generator "
                "stands on, so power delivery, a switch or a monitor that has not caught up, "
                "not a connection left unbuilt"
            )
        )
    if report.unwired:
        notes.append(
            f"{len(report.unwired)} machine(s) have no electrical connection at all -- no wire "
            "reaches them, whatever else they are doing: " + _named(report.unwired)
        )
    if report.no_generator:
        notes.append(
            f"{len(report.no_generator)} machine(s) are wired to a circuit NO GENERATOR stands "
            "on, so the wire reaches them and no power does -- a different build to finish "
            "from the row above: " + _named(report.no_generator)
        )
    if report.by_state["unmonitored"]:
        notes.append(
            f"{report.by_state['unmonitored']} machine(s) keep no productivity monitor, "
            "so their uptime is unknown rather than zero"
        )
    assert STATES
    return render.envelope(f"# {st.age_note}\n# {name}", "\n\n".join(chunks), notes)


@mcp.tool(structured_output=False)
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

    Combines foundation slabs, proximity, belt connectivity, shared products and
    supply links. Validated leave-one-factory-out against the player's twelve
    hand-named factories: precision 1.000, recall 0.945, and precision was 1.000 on
    every fold -- it never merges two factories, it only ever splits one.

    Use `name_factory` on what it proposes. `unnamed_only=True` answers "what have I
    built and not named". The `#` column is the `proposal:<n>` selector every other tool
    takes, and it counts over ALL proposals -- so it does not shift when you page.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories import cohere, identity

    store = st.labels
    proposals = (
        st.proposals
        if max_span_m == cohere.MAX_SPAN_M
        else cohere.propose(st.graph, st.game, st.projection, st.structures, max_span_m=max_span_m)
    )
    n = render.clamp(limit)
    start = max(0, offset)
    rows = []
    shown = 0
    for k, pr in enumerate(proposals):
        names = sorted({lbl.name for m in pr.machines if (lbl := store.label_for(m))})
        if unnamed_only and store.covers(pr.machines):
            continue
        shown += 1
        if not start < shown <= start + n:
            continue
        cand = identity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        rows.append(
            (
                k,
                pr.size,
                f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}",
                f"{cand.spread_m:.0f}m",
                "+".join(str(x) for x in pr.parts) if len(pr.parts) > 1 else pr.size,
                "+".join(n for n, _ in pr.evidence.most_common(3)),
                ", ".join(names)[:26] or "-",
                cand.name_hint()[:34],
            )
        )
    total = shown
    covered = sum(1 for pr in proposals for m in pr.machines if store.label_for(m))
    return render.envelope(
        f"# {st.age_note}\n# {len(proposals)} proposal(s) over "
        f"{len(st.graph.machines())} machines; {covered} already named",
        render.table(
            ("#", "machines", "x,y(m)", "spread", "parts", "evidence", "labels", "makes"),
            rows,
            total=total,
            offset=start,
            limit=n,
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


@mcp.tool(structured_output=False)
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

    Worth running first on anything product-based: 17 machines make Concrete on the
    reference save, but 15 of them are a construction feed inside the steel site and
    only one is the player's "concrete setup".

    `slab:<n>` answers "what stands on this platform", and answers it for an empty one
    too: a poured platform with nothing on it yet is described rather than refused.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories import identity

    try:
        picked = _pick(st, select, split=split, expand=expand)
    except SelectorError as exc:
        return f"! {exc}"
    if not picked:
        empty = _empty_platform(select, st.structures)
        if empty:
            return render.envelope(f"# {st.age_note}", empty)
        return "! that selector matched no machines"

    cand = identity.describe(picked, st.graph, st.game, st.projection, "selector")
    groups = identity.cluster_machines(picked, st.projection)
    parts = [
        render.kv(
            [
                ("machines", cand.size),
                ("at", f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}"),
                ("spread", f"{cand.spread_m:.0f}m"),
                ("clusters", len(groups)),
            ]
        ),
        "products: " + (", ".join(f"{k} {v}" for k, v in cand.products.most_common(10)) or "-"),
        "buildings: "
        + ", ".join(
            f"{v}x {st.game.building_name(k) or k}" for k, v in cand.buildings.most_common(8)
        ),
    ]
    if len(groups) > 1:
        sub = identity.describe(groups[0], st.graph, st.game, st.projection, "selector")
        parts.append(
            f"! {len(groups)} separate sites {[len(g) for g in groups]}; the largest is "
            f"{sub.size} at {int(sub.centroid[0] / 100)},{int(sub.centroid[1] / 100)}. "
            "Pass split=true to keep only that one."
        )
    clashes = {lbl.name for m in picked if (lbl := st.labels.label_for(m))}
    if clashes:
        parts.append("already named: " + ", ".join(sorted(clashes)))
    return render.envelope(f"# {st.age_note}", "\n".join(parts))


@mcp.tool(structured_output=False)
def name_factory(
    name: str,
    select: Annotated[
        list[str], Field(description=f"selector terms, ANDed. {GRAPH_SELECTOR_HELP}")
    ],
    notes: str = "",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    split: Annotated[bool, Field(description="keep only the largest spatial cluster")] = False,
    expand: Annotated[bool, Field(description="pull in everything belted to the result")] = False,
    dry_run: bool = False,
) -> str:
    """Name a set of machines and persist it for this world.

    The label stores the machine instance ids, which are stable across saves, so it
    survives moving machines, adding to the factory, and autosave rotation. Calling
    this again with the same name RE-ANCHORS it to the current selection, dropping every
    machine the new selector misses -- `amend_factory` adds or drops a few without that,
    and `rename_factory` changes the name without touching the membership.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories import identity

    try:
        picked = _pick(st, select, split=split, expand=expand)
    except SelectorError as exc:
        return f"! {exc}"
    if not picked:
        return "! that selector matched no machines; nothing named"

    store = st.labels
    cand = identity.describe(picked, st.graph, st.game, st.projection, "label")
    existing = store.find(name)
    verb = "would name" if dry_run else ("re-anchored" if existing else "named")
    head = (
        f"{verb} {cand.size} machine(s) as {name!r} at "
        f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)} "
        f"(spread {cand.spread_m:.0f}m): {cand.name_hint()}"
    )
    warn = _overlaps(store, picked, name)
    if existing and not dry_run:
        kept = len(set(existing.anchors) & set(picked))
        warn.append(
            f"was {len(existing.anchors)} machine(s), {kept} kept, "
            f"{len(existing.anchors) - kept} dropped"
        )

    if dry_run:
        return render.envelope(f"# {head}", "", warn + ["dry run: nothing written"])

    when = st.header.get("save_datetime") or st.header.get("filename") or ""
    label = store.put(name, picked, notes=notes, when=str(when))
    label.centroid = cand.centroid
    label.signature = dict(cand.buildings)
    path = store.save()
    return render.envelope(f"# {head}", f"stored in {path}", warn)


def _repoint_plans(st, was: str, now: str) -> list[str]:
    """Point stored plans scoped to a factory at its new name.

    ``Plan.factory`` is resolved by name every time ``diff_vs_save`` or ``plan_layout``
    scopes itself to one, so a rename that left it behind would send both looking for a
    factory nothing answers to.
    """
    moved = []
    for plan in st.plans.plans:
        if plan.factory and plan.factory.casefold() == was.casefold():
            plan.factory = now
            moved.append(plan.name)
    if moved:
        st.plans.save()
    return moved


@mcp.tool(structured_output=False)
def rename_factory(
    name: str,
    to: Annotated[str, Field(description="the new name")],
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Rename a factory label. The machines it holds are not touched and nothing re-anchors.

    The label keeps its anchors, notes, centroid, signature and dates -- the name is the
    only thing here a player picked, and correcting one used to mean naming the whole
    selection again under a second name and forgetting the first.

    Stored plans scoped to this factory follow the new name. Renaming onto a name this
    world already uses is refused and says which label holds it.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories.labels import LabelError

    store = st.labels
    label = store.find(name)
    if label is None:
        known = ", ".join(x.name for x in store.labels) or "(none)"
        return f"! no label named {name!r}. Known: {known}"
    if label.name == to.strip():
        return f"factory {label.name!r} already has that name"
    try:
        was = store.rename(label, to)
    except LabelError as exc:
        return f"! {exc}"
    path = store.save()
    notes = [
        (
            f"recall it as factory={label.name!r}; its {len(label.anchors)} anchor "
            "machine(s), notes and dates are untouched"
        )
    ]
    if moved := _repoint_plans(st, was, label.name):
        notes.append(f"{len(moved)} stored plan(s) followed it: {', '.join(moved)}")
    return render.envelope(
        f"# renamed factory {was!r} to {label.name!r}\nstored in {path}", "", notes
    )


@mcp.tool(structured_output=False)
def amend_factory(
    name: str,
    add: Annotated[
        list[str] | None,
        Field(description=f"selector terms for what to ADD. {GRAPH_SELECTOR_HELP}"),
    ] = None,
    drop: Annotated[
        list[str] | None, Field(description="selector terms for what to DROP, same grammar")
    ] = None,
    prune_missing: Annotated[
        bool, Field(description="drop every anchor this save no longer has")
    ] = False,
    notes: str = "",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    dry_run: bool = False,
) -> str:
    """Add or drop individual machines on a label, without re-anchoring the rest of it.

    `name_factory` re-anchors a label to whatever its selector picks, so correcting one
    wrongly-included machine meant re-selecting the whole factory. This edits the
    membership: every anchor `add` and `drop` do not name is left exactly as it was,
    including ids this save no longer has.

    `add` runs first, then `drop`, then `prune_missing`, which clears the anchors
    `list_factories` reports gone. One machine is `machine:<instance>` on either side.
    Dropping the last machine is refused -- deleting a label is `forget_factory`.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories import identity
    from ....domain.factories.labels import LabelError

    store = st.labels
    label = store.find(name)
    if label is None:
        known = ", ".join(x.name for x in store.labels) or "(none)"
        return f"! no label named {name!r}. Known: {known}"
    if not (add or drop or prune_missing or notes):
        return "! nothing to amend: pass add=, drop=, prune_missing=true or notes="
    try:
        wanted = _pick(st, add) if add else []
        unwanted = _pick(st, drop) if drop else []
    except SelectorError as exc:
        return f"! {exc}"

    alive = set(st.graph.machines())
    before = list(label.anchors)
    fresh = sorted(set(wanted) - set(before))
    warn = _overlaps(store, fresh, label.name)
    # Before the attach, since ``covers`` reads every label including this one.
    if fresh and store.covers(fresh):
        warn.append(
            f"{len(fresh)} added machine(s) already have a name -- covers(), which the map "
            "and propose_factories read for that same question"
        )

    going = set(unwanted) | ({m for m in before if m not in alive} if prune_missing else set())
    try:
        added = store.attach(label, wanted)
        dropped = store.detach(label, going)
    except LabelError as exc:
        label.anchors = before
        return f"! {exc}"

    pruned = sum(1 for m in dropped if m not in alive)
    standing = sorted(set(label.anchors) & alive)
    cand = identity.describe(standing, st.graph, st.game, st.projection, "label")
    head = (
        f"{'would amend' if dry_run else 'amended'} {label.name!r}: "
        f"{len(before)} -> {len(label.anchors)} anchor(s), +{len(added)} -{len(dropped)}"
        + (f" ({pruned} of them already gone from this save)" if pruned else "")
    )
    if standing:
        head += (
            f"\n# {len(standing)} standing at "
            f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)} "
            f"(spread {cand.spread_m:.0f}m): {cand.name_hint()}"
        )
    if dry_run:
        label.anchors = before
        return render.envelope(f"# {head}", "", warn + ["dry run: nothing written"])
    if not added and not dropped and not notes:
        return render.envelope(f"# {head}", "", warn + ["nothing changed, so nothing written"])

    if standing:
        label.centroid = cand.centroid
        label.signature = dict(cand.buildings)
    if notes:
        label.notes = notes
    label.last_matched = str(st.header.get("save_datetime") or st.header.get("filename") or "")
    path = store.save()
    if left := len(set(label.anchors) - alive):
        warn.append(
            f"{left} anchor(s) still name machines this save does not have; "
            "prune_missing=true drops them, and nothing else does it on its own"
        )
    return render.envelope(f"# {head}", f"stored in {path}", warn)


@mcp.tool(structured_output=False)
def list_factories(save: str | None = None, world: str | None = None, as_of: AsOf = None) -> str:
    """Named factories for this world, with how much of each is still standing."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    from ....domain.factories import identity

    store = st.labels
    if not store.labels:
        return render.envelope(
            f"# {st.age_note}\n# no factories named yet for world {store.world_id!r}",
            "Run factory_map to see candidates, then name_factory to persist one.",
        )
    machines = set(st.graph.machines())
    rows = []
    for label in sorted(store.labels, key=lambda x: -len(x.anchors)):
        alive = sorted(set(label.anchors) & machines)
        cand = identity.describe(alive, st.graph, st.game, st.projection, "label")
        rows.append(
            (
                label.name,
                len(label.anchors),
                f"{len(alive)}/{len(label.anchors)}",
                f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}",
                f"{cand.spread_m:.0f}m",
                cand.name_hint()[:40],
                label.notes[:40],
            )
        )
    loose = len(identity.unassigned(st.graph, store.assigned()))
    from ....domain.factories.labels import LabelStore

    # Say where the file is. Labels are the one thing here a player authored by hand,
    # so another tool will want them, and reverse-engineering platformdirs to find them
    # is not a reasonable ask.
    return render.envelope(
        f"# {st.age_note}\n# {len(store.labels)} named, {loose} machine(s) unlabelled"
        f"\n# stored at {LabelStore.path_for(store.world_id)}"
        f"\n# also served as resource satisfactory://factories/labels",
        render.table(("name", "anchors", "alive", "x,y(m)", "spread", "makes", "notes"), rows),
        [f"{d['name']}: {d['status']} (recall {d['recall']})" for d in store.review(machines)],
    )


@mcp.tool(structured_output=False)
def forget_factory(
    name: str, save: str | None = None, world: str | None = None, as_of: AsOf = None
) -> str:
    """Delete a factory label. The machines themselves are untouched."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    store = st.labels
    label = store.find(name)
    if label is None:
        known = ", ".join(x.name for x in store.labels) or "(none)"
        return f"! no label named {name!r}. Known: {known}"
    store.remove(label.name)
    store.save()
    return f"forgot {label.name!r} ({len(label.anchors)} machine(s) released)"


@mcp.tool(structured_output=False)
def trace_upstream(
    seed: Annotated[
        str, Field(description="a machine instance, a factory label, or a building name")
    ],
    direction: Annotated[
        str, Field(description="up (what feeds it) | down (what it feeds)")
    ] = "up",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
) -> str:
    """What feeds a machine, or what it feeds -- walked on the save's own connections.

    `factory_query` answers this between LABELLED sets. This answers it for one machine or
    one building type, which is the question a cutover actually asks: thirteen Oil
    Extractors sit on the Spire nodes and twenty Fuel Generators are burning, and repiping
    the wrong extractor first drops several GW.

    Direction is READ, not guessed. Every material edge carries its connector role, and
    92.5% of the connectors landing on a machine name their direction outright; the rest
    are all on extractors or generators, whose own nature settles them. Where even that
    fails the edge is walked BOTH ways -- over-reporting a feeder is recoverable, missing
    one is not.

    Belts and pipes are walked THROUGH and left out of the table: a trace from the
    generators touches 331 nodes at depth 72, nearly all of it conveyor. What the route
    crossed is named instead in a note -- how many runs of each medium, and the ids
    `search_conduits` takes for the ones that have them.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    records = {r["instance"].rsplit(".", 1)[-1]: r for r in st._all_records()}
    seeds: list[str] = []
    what = seed.strip()
    if what in records:
        seeds = [what]
        subject = f"{records[what].get('cls', '?')} {what}"
    else:
        by_class = [
            inst
            for inst, rec in records.items()
            if rec.get("cls") == what
            or (
                rec.get("cls") in g.buildings
                and g.buildings[rec["cls"]].name.casefold() == what.casefold()
            )
        ]
        if by_class:
            seeds, subject = by_class, f"{len(by_class)}x {what}"
        else:
            try:
                name, machines = resolve_factory(st, what)
            except SelectorError as exc:
                return f"! {exc}"
            # resolve_factory hands back machine ids, already shortened. Indexing them as
            # records raised TypeError for every label and every selector.
            seeds = list(machines)
            subject = f"factory {name!r} ({len(seeds)} machines)"
    if not seeds:
        return f"! nothing matches {seed!r} -- give a machine instance, a building name, or a factory label"

    way = (direction or "up").strip().casefold()
    if way not in ("up", "down"):
        return f"! unknown direction {direction!r}. Choose from: up, down"
    result = trace(st, g, seeds, way)

    rows = []
    for name, group in sorted(result.by_class().items(), key=lambda kv: -len(kv[1])):
        hops = [r.hops for r in group]
        rows.append(
            (
                name[:28],
                group[0].kind,
                len(group),
                f"{min(hops)}..{max(hops)}" if min(hops) != max(hops) else str(min(hops)),
                ", ".join(r.instance for r in group[:3]),
            )
        )
    via = _via(result.crossed)
    notes = [
        (
            f"walked {result.visited} node(s) to depth {result.deepest}; the belt and pipe "
            "nodes are traversed and never listed one by one, because a path through them "
            "is unreadable -- "
            + (
                "the route note below names the RUNS they contract to instead"
                if via
                else "search_conduits lists the runs themselves, with endpoints and lengths"
            )
        ),
        (
            "direction comes from each edge's connector role, and from the machine's own "
            "nature where the role does not say. "
            + (
                f"{result.ambiguous} edge(s) have neither -- belt-to-belt and pipe-to-pipe "
                "segments, which are walked BOTH ways, so this list can over-report a "
                "feeder but never miss one"
                if result.ambiguous
                else "Every edge here states its direction"
            )
        ),
    ]
    if via:
        notes.append(via)
    if result.truncated:
        notes.append(
            "the walk stopped at its hop limit, so this is a FLOOR: machines further along "
            "the chain exist and are not listed"
        )
    mw, gens, running = power_at_risk(st, g, seeds)
    if gens:
        notes.append(
            f"downstream of this sits {gens} generator(s), {running} of them PROVEN running "
            f"in the last 300s window, worth {mw:,.0f} MW. Cutting this feed stops that "
            "power -- idle generators are not counted, since they are already not producing"
        )
    # An empty table is a real answer here and has to say which one it is. Seeding from a
    # whole label is the common way to get one: a self-contained factory owns its own chain,
    # so everything upstream of it is already inside the selection.
    if not rows:
        body = (
            "nothing outside this selection "
            + ("feeds it" if way == "up" else "is fed by it")
            + f" -- the walk crossed {result.visited} belt/pipe node(s) and reached no other "
            "machine. Narrow the seed (a product: or a single instance) to see the chain "
            "INSIDE it."
        )
    else:
        body = render.table(
            ("building", "kind", "count", "hops", "examples"),
            rows,
            total=len(rows),
            limit=render.clamp(limit, default=20),
            hint="raise limit -- one row per building class, biggest first, and no offset",
        )
    return render.envelope(
        f"# {st.age_note}\n# {'what feeds' if way == 'up' else 'what is fed by'} {subject}",
        body,
        notes,
    )
