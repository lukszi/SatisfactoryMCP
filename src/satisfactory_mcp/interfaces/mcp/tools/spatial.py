"""Map queries: regions, coordinates, resource nodes, build sites, map links."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ....domain.spatial import elevation, geo, heightfield
from ....domain.spatial import nodes as nodes_mod
from ....domain.spatial import ranking as ranking_mod
from ....domain.spatial import regions as regions_mod
from ....domain.spatial.origin import NODE_PREFIX, resolve_origin
from ....domain.spatial.select import SELECTOR_HELP, select_nodes
from ....presenters.text import primitives as render
from ..app import (
    AsOf,
    Limit,
    _item_id,
    _state,
    game,
    mcp,
    retired,
)


@mcp.tool(structured_output=False)
def list_regions(
    resource: str | None = None,
    with_resource: Annotated[
        str | None, Field(description="retired -- write resource= instead")
    ] = None,
) -> str:
    """Named map regions, optionally only those containing a given resource.

    Region names are ADVISORY: the boundaries are the game's own map areas, downsampled
    to a 256 m grid to publish and a 64 m one to look up in, so a name near a boundary can
    be one cell out. Use them to talk about places, not to compute with -- every node row
    also carries an exact grid cell.

    `anchor` is a coordinate that provably lies in the region, which a centroid does not:
    a concave region's mean lands on its neighbour's ground, and the map has drawn its
    names at the anchor all along.
    """
    if gone := retired(("with_resource", with_resource, "resource")):
        return gone
    g = game()
    rm = regions_mod.load_regions()
    table = nodes_mod.load_nodes()
    rid = _item_id(resource) if resource else None
    if resource and rid is None:
        return f"no resource matching {resource!r}"

    pool = table.by_resource(rid) if rid else table.nodes
    rows = []
    for name in rm.names():
        info = rm.summary(name)
        hits = rm.filter_nodes(pool, name)
        if rid and not hits:
            continue
        # Every column is read off the anchor, so the row cannot name a direction and a
        # grid cell belonging to a point it does not print.
        ax, ay = info["anchor"] or info["centroid"]
        rows.append(
            (
                name,
                geo.direction_of(ax, ay),
                geo.grid_cell(ax, ay),
                f"{int(ax / 100)},{int(ay / 100)}",
                render.num(info["area_km2"]),
                len(hits),
            )
        )
    rows.sort(key=lambda r: -r[5])
    scope = f" containing {g.item_name(rid)}" if rid else ""
    return render.envelope(
        f"# {len(rows)} region(s){scope}; anchor in metres",
        render.table(("region", "dir", "grid", "anchor(m)", "km2", "nodes"), rows, total=len(rows)),
        [
            f"names are advisory, ~{rm.meta.get('accuracy_m', 256)}m boundary accuracy",
            (
                "the anchor is a cell that provably belongs to the region, not its "
                "centroid: a concave region's mean lands in its neighbour"
            ),
            "any region name works as a source selector for plan_factory",
        ],
    )


@mcp.tool(structured_output=False)
def describe_location(
    at: Annotated[
        str,
        Field(
            description="the place: 'x,y' in metres, 'me', a named factory, "
            "'slab:<n>', or a run id like 'chain:7'"
        ),
    ] = "",
    radius_m: Annotated[
        float, Field(description="how far to look for known elevations, metres")
    ] = 200.0,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Name the region at a place, sample its elevation, and count what runs through.

    `at=` takes `'x,y'` in metres, or anything else this project prints an id for: `me`,
    a named factory, `slab:<n>` from `factory_map show=slabs` -- including the bare
    platforms nothing else would take -- or a `chain:`/`pipe:` run from `search_conduits`.

    Returns 'off-map or ocean' rather than guessing the nearest land region.

    Elevation is answered two ways and the two are never averaged. Where this machine
    carries the extracted 1 m terrain field, `terrain_m` is one texel read at exactly
    this coordinate, with the layer that answered, that layer's measured accuracy, and
    the water surface and depth where water stands. Everything else is a SAMPLE
    population reported with its count and spread: resource nodes rest on terrain and
    are quoted as ground, foundations and buildings are quoted separately as built
    elevation because a platform is wherever the player put it, and the gap between the
    two is the fill already stacked there.

    Belts and pipes are counted too, measured against the runs' drawn lines rather than
    their corner points, so a conduit crossing mid-span is seen. With a readable save,
    a zero here means nothing runs through -- absence in this output is absence in the
    world. `search_conduits` lists the runs themselves.
    """
    # The node table alone covers the whole map and needs no save, so an unexplored
    # coordinate still gets an answer. A readable save adds the dense sources -- and is
    # what every `at=` form but a bare coordinate is resolved against.
    st = None
    try:
        st = _state(save, world, as_of)
    except Exception:
        pass

    if not at.strip():
        return (
            "! describe_location needs at=<place>: 'x,y' in metres, 'me', a named "
            "factory, 'slab:<n>', or a run id like 'chain:7'"
        )
    try:
        (x, y), where = resolve_origin(st, at)
    except ValueError as exc:
        return f"! {exc}"

    rm = regions_mod.load_regions()
    label = rm.label_for(x, y)
    table = nodes_mod.load_nodes()
    field = heightfield.load_field()
    near = elevation.probe(x, y, elevation.sample_points(table, st), radius_m, terrain_field=field)

    # Echoed because `at=` can resolve to somewhere the caller never typed, and every
    # number below is about THAT point. A bare coordinate resolves to itself, so naming it
    # twice would read as two facts.
    here = f"{x / 100:.0f},{y / 100:.0f}"
    fields = [
        ("at", here + (f" ({where})" if where and where != here else "")),
        ("region", label.describe()),
        ("confidence", label.confidence),
        ("grid", geo.grid_cell(x, y)),
        ("direction_from_centre", geo.direction_of(x, y)),
        ("bearing_deg", render.num(geo.bearing_deg(x, y))),
    ]
    notes: list[str] = []
    reading = near.terrain
    if reading is not None:
        accuracy = "" if reading.accuracy_m is None else f", +-{reading.accuracy_m:g}m"
        fields.append(("terrain_m", f"{reading.z_m:.1f} ({reading.source}{accuracy})"))
        if reading.submerged:
            depth = reading.water_depth_m
            fields.append(("water_surface_m", f"{reading.water_m:.1f}"))
            fields.append(("water_depth_m", "unknown" if depth is None else f"{depth:.1f}"))
            if depth is None:
                notes.append(
                    f"the ground under this water is the {reading.source} layer, too coarse "
                    "to subtract a surface from, so the depth here is not known"
                )
    elif field is None:
        notes.append(
            "no terrain field on this machine, so the heights below are things standing "
            "nearby rather than the ground -- run tools/gen_world_heightmap.py to measure it"
        )
    else:
        notes.append("the terrain field has no data at this point -- open ocean, or a cave mouth")
    if near.samples:
        for what, values in (("ground", near.ground), ("built", near.built)):
            if not values:
                continue
            mid = values[len(values) // 2]
            fields.append(
                (
                    f"{what}_elevation_m",
                    f"{mid:.0f} (median of {len(values)}, {min(values):.0f}..{max(values):.0f})",
                )
            )
        fields.append(
            (
                "samples",
                ", ".join(f"{n} {src}" for src, n in sorted(near.counts.items()))
                + f" within {radius_m:g}m",
            )
        )
        fill = near.fill_m
        if fill is not None and abs(fill) >= 1.0:
            notes.append(
                f"built surface sits {fill:+.0f}m relative to the nearest ground samples "
                "-- that gap is foundation already stacked here, not terrain"
            )
        notes.append(
            "these elevations are SAMPLED from things standing nearby, never interpolated: "
            "resource nodes rest on the ground, foundations and buildings are wherever "
            "they were placed"
        )
    else:
        notes.append(
            f"no known elevation within {radius_m:g}m: nothing is built here and no "
            "resource node is near -- "
            + (
                "the terrain reading above is the whole answer here"
                if reading is not None
                else "widen radius_m or accept that this is unsurveyed ground"
            )
        )
    # Conduits are counted against their drawn lines, not their corner points, so a belt
    # crossing mid-span is seen. Reported even at zero: with a readable save, absence in
    # this answer finally means absence in the world.
    if st is not None:
        from ....domain.world import conduits as conduits_mod

        counted = conduits_mod.near_counts(st.conduit_runs, x, y, radius_m)
        fields.append(
            (
                "conduits",
                (
                    f"{counted['belt']} belt run(s), {counted['pipe']} pipe run(s) "
                    f"within {radius_m:g}m"
                ),
            )
        )
        if counted["belt"] or counted["pipe"]:
            notes.append("search_conduits lists those runs with endpoints, lengths and elevation")
    else:
        notes.append("no save read: belts and pipes here are unknown, not absent")
    # Ground elevation here IS node z, so a stale node row is a stale ground level -- and
    # it can flip the 1 m threshold the fill note above is quoted at. Only the nodes inside
    # the probe radius are in scope, so an untouched location stays silent.
    notes += nodes_mod.position_notes(
        nodes_mod.skew_for_save(st.header if st else None, table),
        [n["instance"] for n in table.filter(center=(x, y), radius_m=radius_m)],
    )
    return render.envelope(render.kv(fields), "", notes)


def _networks_view(g, st, origin: tuple[float, float], where: str, limit, offset: int) -> str:
    """One row per fluid network in the world: what it carries, and what it ends on.

    The view that makes a run list navigable. A run is one placed pipe and there are 503
    of them; a NETWORK is the connected plumbing system they belong to, which is the unit
    a player thinks in and the reason "is there a pipe from here to there" has an answer
    at all.
    """
    grouped: dict[object, list] = {}
    for run in st.conduit_runs:
        if run.kind == "pipe":
            grouped.setdefault(run.network, []).append(run)
    order = sorted(grouped.items(), key=lambda kv: -sum(r.length_m for r in kv[1]))

    rows = []
    start = max(0, offset)
    for net, runs in order[start : start + render.clamp(limit, default=12)]:
        fluid = next((r.fluid for r in runs if r.fluid), None)
        ends = [e for r in runs for e in (r.a, r.b)]
        # What the system TOUCHES: a plug naming another run is plumbing continuing, not
        # something the network delivers to.
        touches: list[str] = []
        for run in runs:
            for name in (run.a.plugs, run.b.plugs, *run.via):
                if name and not name.startswith(("pipe:", "chain:")) and name not in touches:
                    touches.append(name)
        extra = len(touches) - 4
        centre = (
            f"{sum(e.x for e in ends) / len(ends) / 100:.0f},"
            f"{sum(e.y for e in ends) / len(ends) / 100:.0f}"
        )
        rows.append(
            (
                net if net is not None else "-",
                g.item_name(fluid) if fluid else "?",
                len(runs),
                f"{sum(r.length_m for r in runs):.0f}m",
                centre,
                f"{min(r.z_min_m for r in runs):.0f}..{max(r.z_max_m for r in runs):.0f}",
                f"{min(r.dist_m(*origin) for r in runs):.0f}m",
                ", ".join(touches[:4]) + (f" +{extra} more" if extra > 0 else "") or "?",
            )
        )

    named = [r for r in (st.projection.get("pipe_networks") or ()) if isinstance(r, dict)]
    notes = [
        (
            "a network is ONE connected plumbing system: everything on it shares a fluid "
            "and a pressure, so two places on the same network are joined even where no "
            "single pipe spans them"
        ),
        (
            f"the save names a fluid for {len(named)} of its networks; a '?' here is one "
            "it does not -- drained, or plumbed and never run"
        ),
        "search_conduits near=<x,y> lists the individual runs on any of these",
    ]
    if None in grouped:
        notes.append(
            f"{len(grouped[None])} pipe piece(s) belong to no network at all -- placed, "
            "but joined to nothing that holds fluid"
        )
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(order)} fluid network(s), most pipe first; centre in metres, "
        f"distance measured from {where}",
        render.table(
            ("network", "carries", "pieces", "pipe", "centre(m)", "z(m)", "dist", "touches"),
            rows,
            total=len(order),
            offset=start,
            limit=limit,
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def search_conduits(
    near: Annotated[
        str,
        Field(
            description="centre: 'x,y' in metres, 'me', a named factory, or a run id "
            "from this tool ('chain:7', 'pipe:333')"
        ),
    ],
    radius_m: float = 250.0,
    to: Annotated[
        str | None,
        Field(description="second area: list only runs passing near BOTH, same forms as near"),
    ] = None,
    to_radius_m: Annotated[
        float | None, Field(description="radius around `to`, defaults to radius_m")
    ] = None,
    conduit_kind: Annotated[str | None, Field(description="belt | pipe | all")] = None,
    show: Annotated[str, Field(description="runs | networks")] = "runs",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 12,
    offset: int = 0,
    kind: Annotated[
        str | None, Field(description="retired -- write conduit_kind= instead")
    ] = None,
) -> str:
    """Belt and pipe runs near a point or between two areas: ends, length, elevation.

    The web map has drawn these all along; this is the text answer to "is there a pipe
    between those extractors and that platform, where does it run, how long is it". A
    run is one belt CHAIN (consecutive conveyor pieces, split at splitters, mergers and
    machines) or one placed pipeline piece. Longest first; each row carries both ends
    with what stands there where known, the drawn length, and the elevation span.

    `show="networks"` answers the other size of question: one row per FLUID NETWORK in
    the whole world, what each carries, how much pipe it is, where its middle is and
    what it ends on. A network is one connected plumbing system, so that is the view
    that tells you which system a run belongs to; `radius_m` and `to` do not narrow it,
    and the distance column places each network relative to `near`.

    `near` and `to` accept a coordinate in metres, `me`, a named factory, or one of this
    tool's own run ids -- `chain:7`, `pipe:333` -- which centres on that run's midpoint,
    so the ids in the `connects` column can be followed one call at a time. With `to`
    set, only runs passing within both radii are listed. Proximity is measured against
    the runs' drawn lines, not their corner points, so a run crossing mid-span counts.

    Long lists page with `offset=`, and the truncation line names the next offset --
    a busy junction can carry hundreds of chains and the tail of that list is as real
    as its head.
    """
    if gone := retired(("kind", kind, "conduit_kind")):
        return gone
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc} (conduits are read from the save)"

    want = (conduit_kind or "").strip().casefold() or None
    if want == "all":
        want = None
    if want not in (None, "belt", "pipe"):
        return f"! unknown conduit_kind {conduit_kind!r}. Choose from: belt, pipe, all"
    view = (show or "runs").strip().casefold()
    if view not in ("runs", "networks"):
        return f"! unknown show {show!r}. Choose from: runs, networks"

    try:
        origin, where = resolve_origin(st, near)
    except ValueError as exc:
        return f"! {exc}"
    if view == "networks":
        if want == "belt":
            return "! show='networks' lists fluid networks; a belt chain belongs to none"
        return _networks_view(g, st, origin, where, limit, offset)
    second, where2 = None, ""
    if to is not None:
        try:
            second, where2 = resolve_origin(st, to)
        except ValueError as exc:
            return f"! {exc}"
    r2 = to_radius_m if to_radius_m is not None else radius_m

    # In between-mode a run must pass near BOTH areas -- but a pipe ROUTE is usually
    # several pieces, so the game's own network id is consulted too: one network is one
    # connected plumbing system, and a network touching both areas joins them even when
    # no single piece spans the distance. Belts have no such id (a route through a
    # splitter is several chains), so a note owns that gap rather than a guess.
    hits = []
    bridged: dict[int, dict] = {}
    direct_nets: set[int] = set()
    for run in st.conduit_runs:
        if want is not None and (run.kind == "pipe") != (want == "pipe"):
            continue
        near_a = run.dist_m(*origin) <= radius_m
        if second is None:
            if near_a:
                hits.append(run)
            continue
        near_b = run.dist_m(*second) <= r2
        if near_a and near_b:
            hits.append(run)
            if run.network is not None:
                direct_nets.add(run.network)
        elif run.network is not None and (near_a or near_b):
            entry = bridged.setdefault(run.network, {"fluid": run.fluid, "a": 0, "b": 0})
            entry["a"] += near_a
            entry["b"] += near_b
    hits.sort(key=lambda r: -r.length_m)

    label = f"{want} run(s)" if want else "conduit run(s)"
    scope = f"within {radius_m:g}m of {where}"
    if second is not None:
        scope += f" AND {r2:g}m of {where2}"

    belts = [r for r in hits if r.kind != "pipe"]
    pipes = [r for r in hits if r.kind == "pipe"]
    fluids = sorted({g.item_name(r.fluid) for r in pipes if r.fluid})
    summary = (
        f"# {st.age_note}\n"
        f"# {len(hits)} {label} {scope}: "
        f"{len(belts)} belt ({sum(r.length_m for r in belts):.0f}m drawn), "
        f"{len(pipes)} pipe ({sum(r.length_m for r in pipes):.0f}m"
        + (f"; {', '.join(fluids)}" if fluids else "")
        + ")"
    )

    bridge_notes = [
        f"pipe network {net} ({g.item_name(entry['fluid']) if entry['fluid'] else '?'}) "
        f"touches BOTH areas -- one connected plumbing system, {entry['a']} piece(s) near "
        f"{where} and {entry['b']} near {where2}, though no single piece spans both"
        for net, entry in sorted(bridged.items())
        if entry["a"] and entry["b"] and net not in direct_nets
    ]
    if second is not None and want != "pipe" and (belts or hits or bridge_notes):
        bridge_notes.append(
            "a belt route through a splitter is several chains, so a chain near only one "
            "end may still continue to the other -- follow its connects column, or "
            "trace_upstream from the machine it feeds"
        )

    if not hits:
        return render.envelope(
            summary,
            "",
            [
                *bridge_notes,
                (
                    "this reads the save's own belt and pipe geometry, so nothing listed "
                    "means nothing runs there -- widen radius_m to check further out"
                ),
            ],
        )

    def _end(e) -> str:
        return f"{e.x / 100:.0f},{e.y / 100:.0f},{e.z / 100:.0f}"

    rows = []
    start = max(0, offset)
    for run in hits[start : start + render.clamp(limit, default=12)]:
        joiner = "->" if run.directed else "--"
        connects = f"{(run.a.plugs or '?')[:22]} {joiner} {(run.b.plugs or '?')[:22]}"
        if run.via:
            connects += " via " + ", ".join(run.via)[:24]
        rows.append(
            (
                run.ident,
                run.label,
                f"{run.length_m:.0f}m",
                _end(run.a),
                _end(run.b),
                f"{run.z_min_m:.0f}..{run.z_max_m:.0f}",
                # A pipe says WHAT it carries (the network's own answer, ? where no
                # network claims it); a belt has no such fact, so it quotes capacity.
                (g.item_name(run.fluid) if run.fluid else "?")
                if run.kind == "pipe"
                else render.rate(run.rate, "/min"),
                run.basis or "-",
                connects,
            )
        )
    notes = [
        *bridge_notes,
        (
            "a/b are the run's ends in metres; -> is travel/flow direction, -- means the "
            "direction is not established. 'connects' is the nearest placed thing whose "
            "footprint covers the end -- a geometric read, ? where nothing known stands "
            "there, and a chain:/pipe: entry is the run it continues into, which this "
            "tool takes straight back as near= to walk the route"
        ),
        (
            "nothing in the save records which way a pipe flows, so 'basis' is the "
            "evidence the arrow was INFERRED from: a typed machine port, a pump or valve, "
            "or propagated from the rest of the network. '-' is a belt, whose order is "
            "the pieces' own and is not inferred"
        ),
        (
            "length is the drawn line: a bend whose tangents the save records is "
            "integrated along its spline, so this is the number the map measures too"
        ),
    ]
    if any("-mk" in r.label for r in hits):
        notes.append("a mixed-tier chain shows its tier span and quotes the slowest cap")
    return render.envelope(
        summary,
        render.table(
            ("id", "kind", "len", "a(m)", "b(m)", "z(m)", "carries", "basis", "connects"),
            rows,
            total=len(hits),
            offset=start,
            limit=limit,
        ),
        notes,
    )


def _occupant(row: dict, g) -> str:
    """The extractor standing on a node, at its clock. ``-`` where none does.

    A node whose miner is switched off still reads ``tapped``, and the difference between
    a tapped node and one being MINED is the whole of "is this worth reclaiming".
    """
    cls = row.get("tapped_by")
    if not cls:
        return "-"
    parts = [g.building_name(cls) or cls]
    clock = row.get("tapped_clock")
    if clock is not None:
        parts.append(f"@{clock:.0%}")
    if row.get("tapped_paused"):
        parts.append("OFF")
    return " ".join(parts)


@mcp.tool(structured_output=False)
def search_resource_nodes(
    sources: list[str] | None = None,
    resource: str | None = None,
    purity: Annotated[str | None, Field(description="pure | normal | impure | all")] = None,
    kind: Annotated[str | None, Field(description="node | well_sat | geyser | all")] = None,
    only_free: bool = False,
    show: Annotated[str, Field(description="fields | nodes | nearest")] = "fields",
    near: Annotated[
        str | None,
        Field(description="origin for show=nearest: 'x,y' in metres, 'me', or a factory name"),
    ] = None,
    mode: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    group: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """Resource nodes, in one of three views.

    - **fields** (default) clusters nodes within 200 m and ranks by yield -- "where is
      there a lot of iron".
    - **nodes** lists one row per node, ranked by yield, with ids reusable as selectors.
    - **nearest** lists one row per node ranked by DISTANCE from `near`, with the
      distance shown -- "what is closest". Requires `near`.

    `sources` is a list of selectors; locations union, filters intersect::

        ["north"]                          northern half of the map
        ["region:Northern Forest"]         one named region
        ["near:0,-2000@800"]               within 800 m of (0, -2000) metres
        ["grid:X3Y4"]                      one 1.024 km grid cell
        ["node:BP_ResourceNode26_99"]      one specific node
        ["north", "resource:Crude Oil"]    crude oil in the north
        ["bbox:-500,-2500,600,-1800"]      a rectangle, metres

    `near` accepts a coordinate in metres, `me` for the player, or the name of a
    labelled factory -- "the nearest free coal to the coal powerplant" needs no
    coordinates. Giving `near` in any view adds a distance column.

    All three views page with `offset=`; the ranking is stable, so the tail of 127 iron
    nodes is reachable 25 at a time.

    **Water is the exception to everything above.** Open water carries no node, so asking
    for it returns only the fracking satellites; the bodies already being pumped, the pumps
    on each and the measured sea level are printed beside them instead.
    """
    if gone := retired(("mode", mode, "show"), ("group", group, "show")):
        return gone
    g = game()
    table = nodes_mod.load_nodes()

    view = (show or "fields").strip().casefold()
    view = {"field": "fields", "node": "nodes"}.get(view, view)
    if view not in ("fields", "nodes", "nearest"):
        return f"! unknown show {show!r}. Choose from: fields, nodes, nearest"

    spec = list(sources or [])
    for extra, value in (("resource", resource), ("purity", purity), ("kind", kind)):
        if value:
            spec.append(f"{extra}:{value}")

    st = None
    try:
        st = _state(save, world, as_of)
    except Exception:
        pass

    origin = None
    where = ""
    if near:
        try:
            origin, where = resolve_origin(st, near)
        except ValueError as exc:
            return f"! {exc}"
    if view == "nearest" and origin is None:
        return "! show='nearest' needs near=<x,y | me | factory name> to measure from"

    sel = select_nodes(spec or None, table.nodes, resolve_resource=_item_id, st=st)
    if sel.errors and not sel.nodes:
        return render.envelope("# no nodes selected", "", [*sel.errors, SELECTOR_HELP])

    rows_all = nodes_mod.annotate(
        sel.nodes,
        g,
        st.projection if st else None,
        st.unlocked_building_ids if st else None,
    )
    if only_free:
        rows_all = [r for r in rows_all if not r["tapped"]]
    if origin is not None:
        for r in rows_all:
            r["_d"] = geo.distance_m((r["x"], r["y"]), origin)
    if not rows_all:
        return render.envelope(
            f"# no nodes in {sel.description}",
            "",
            sel.errors or ["try widening the selector"],
        )

    rm = regions_mod.load_regions()
    resources = sorted({r["resource"] for r in rows_all})
    mixed = len(resources) > 1
    unit = "mixed" if mixed else ("m3/min" if g.items[resources[0]].is_fluid else "/min")
    total = sum(r["rate"] for r in rows_all)
    free = nodes_mod.capacity(rows_all, only_free=True)
    locked_rate = sum(r["rate"] for r in rows_all if not r["reachable"])

    notes = list(sel.errors)
    if st is None:
        notes.append("no save read: tapped/free unknown, everything shown as free")
    else:
        if locked_rate:
            notes.append(
                f"{render.num(locked_rate)} excluded from free: needs an extractor this "
                "world has not unlocked (marked LOCKED)"
            )
        unres = nodes_mod.unresolved_extractors(st.projection)
        if unres:
            notes.append(
                f"{len(unres)} extractor(s) unmatched to a node (mostly water pumps), "
                "so free may be overstated"
            )
        # This tool quotes z per node AND joins the save by instance name, so both halves
        # of a stale table bite here. Scoped to the rows in THIS answer: a query that
        # returns none of the drifted rows says nothing at all.
        notes += nodes_mod.skew_notes(
            nodes_mod.skew_for_save(st.header, table),
            [r["instance"] for r in rows_all],
        )

    start = max(0, offset)
    n = render.clamp(limit, default=25)
    if view in ("nodes", "nearest"):
        if view == "nearest":
            rows_all.sort(key=lambda r: r["_d"])
        else:
            rows_all.sort(key=lambda r: (-r["rate"], r["instance"]))
        show_distance = origin is not None
        rows = [
            (
                r["instance"].rsplit(".", 1)[-1],
                g.item_name(r["resource"]) if mixed else r["purity"],
                *(
                    (f"{r['_d']:.0f}m",)
                    if show_distance
                    else (r["purity"] if mixed else r["kind"],)
                ),
                r["grid"],
                f"{int(r['x'] / 100)},{int(r['y'] / 100)}",
                f"{r['z'] / 100:.0f}",
                render.num(r["rate"]),
                "tapped" if r["tapped"] else ("LOCKED" if not r["reachable"] else "free"),
                _occupant(r, g),
                rm.label_for_node(r).name or "-",
            )
            for r in rows_all[start : start + n]
        ]
        headers = (
            "node_id",
            "resource" if mixed else "purity",
            f"dist to {where}" if show_distance else ("purity" if mixed else "kind"),
            "grid",
            "x,y(m)",
            "z(m)",
            "rate",
            "status",
            "occupant",
            "region",
        )
        body = render.table(headers, rows, total=len(rows_all), offset=start, limit=n)
        notes.append("node_id doubles as a source selector: node:<id>")
        notes.append(
            "occupant is the extractor standing on the node at its saved clock; OFF means "
            "it is switched off, so that node's rate is not being produced"
        )
    else:
        clusters = geo.cluster(rows_all, link_m=200.0)
        crows = []
        for c in clusters[start : start + n]:
            cx, cy, _cz = c.centroid
            label = rm.label_for(cx, cy)
            c_free = sum(m["rate"] for m in c.members if not m["tapped"] and m["reachable"])
            crows.append(
                (
                    label.name or regions_mod.OFF_MAP,
                    geo.grid_cell(cx, cy),
                    geo.direction_of(cx, cy),
                    f"{int(cx / 100)},{int(cy / 100)}",
                    c.size,
                    ",".join(f"{n}{k[0]}" for k, n in sorted(c.purities().items())),
                    render.num(sum(m["rate"] for m in c.members)),
                    render.num(c_free),
                    f"{c.diameter_m:.0f}m",
                    "" if all(m["reachable"] for m in c.members) else "LOCKED",
                )
            )
        headers = (
            "region",
            "grid",
            "dir",
            "centre(m)",
            "n",
            "purity",
            "total",
            "free",
            "spread",
            "note",
        )
        body = render.table(headers, crows, total=len(clusters), offset=start, limit=n)
        notes.append('show="nodes" lists individual nodes; show="nearest" ranks by distance')

    # Elevation matters for fluids and nothing else: a pipe running downhill is free and
    # one running uphill needs head. The SPAN is reported, never a pump count -- head per
    # pump is a game rule this project has no data for, and guessing it would be the kind
    # of invented number the rest of this file exists to avoid.
    zs = [r["z"] / 100.0 for r in rows_all if "z" in r]
    head = ""
    if zs and any(g.items[r].is_fluid for r in resources if r in g.items):
        low, high = min(zs), max(zs)
        head = (
            f"\n# elevation {low:.0f}..{high:.0f}m (span {high - low:.0f}m); fluid, so "
            "uphill runs need pumps and downhill runs do not"
        )

    # Water is the one resource whose supply is not in the node table at all: every row this
    # tool can return for it is a fracking satellite, so "0 free and reachable" is a fact
    # about the satellites and says nothing about the lakes.
    if "Desc_Water_C" in resources:
        notes.insert(
            0,
            "open water carries NO NODE: a Water Extractor is placed on a shoreline, has no "
            "purity and draws a flat rate, so there is no node cap for it to be free "
            "against. Every row above is a fracking satellite, which does sit on a node",
        )
        if st is not None:
            wv = st.water_volumes()
            pump = g.buildings.get("Build_WaterPump_C")
            level = wv["sea_level_m"]
            body = (
                "## open water\n"
                + render.kv(
                    [
                        ("bodies drawn from", len(wv["volumes"])),
                        ("pumps built", wv["pumps"]),
                        (
                            "per pump at 100%",
                            f"{pump.extract_rate('normal', 1.0):.0f} m3/min" if pump else "",
                        ),
                        (
                            "sea level",
                            f"{level:.1f}m (pumps span {wv['sea_level_span_m']:.2f}m)"
                            if level is not None
                            else "",
                        ),
                    ]
                )
                + "\n"
                + render.table(
                    ("body", "pumps"),
                    sorted(wv["volumes"].items(), key=lambda kv: -kv[1]),
                    total=len(wv["volumes"]),
                )
                + "\n\n"
                + body
            )
            notes.insert(
                1,
                "a body is the FGWaterVolume each pump's mExtractableResource names. Its "
                "SHAPE is level geometry and is not in the save, so this says how many "
                "separate shorelines are already worked, not how much is left in them -- "
                "and sea level is measured off those pumps, not assumed",
            )

    return render.envelope(
        f"# {sel.description}: {len(rows_all)} node(s), "
        f"{render.num(total)} {unit} total, {render.num(free)} free and reachable\n"
        f"# rates at 100% clock; coords in metres{head}",
        body,
        notes,
    )


@mcp.tool(structured_output=False)
def show_on_map(
    at: Annotated[
        str,
        Field(
            description="any place -- 'x,y' in metres, 'me', a factory label, "
            "'node:<id>', 'slab:<n>', 'chain:<n>'/'pipe:<n>', 'plan:<name>' -- or "
            "'resource:Crude Oil' for every node of one resource"
        ),
    ],
    layers: Annotated[
        list[str] | None,
        Field(description="explicit sublayer tokens, overriding the guess"),
    ] = None,
    zoom: float = 4.75,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Map links centred on something: this project's own map, and the public one.

    Two links for every place. The LOCAL one opens this project's web map, which draws
    the reader's own save -- their machines, their belts, their siting. The
    satisfactory-calculator.com one opens a third-party map of the vanilla world, which
    knows the terrain and the nodes and nothing the player built.

    `at` is the same place vocabulary every other tool takes (see docs/selectors.md),
    plus one kind of its own: `resource:<name>` centres on the centroid of EVERY node of
    that resource and switches its overlays on, which is a viewport rather than a place
    and is why no other tool accepts it.

    Only the Crude Oil layer tokens are confirmed; the rest follow the same pattern and
    are flagged. A wrong token still opens the map in the right place, just without that
    overlay.
    """
    from ....domain.spatial import maplink

    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception:
        st = None

    table = nodes_mod.load_nodes()
    notes: list[str] = []
    resources: list[str] = []
    text = at.strip()
    kind, _sep, value = text.partition(":")

    node = None
    if kind.casefold() == "resource":
        item = _item_id(value.strip())
        if not item or item not in maplink.LAYERS:
            return f"! no map layer for resource {value.strip()!r}"
        rows = table.by_resource(item)
        if not rows:
            return f"! no {g.item_name(item)} nodes on the map"
        origin = (
            sum(r["x"] for r in rows) / len(rows),
            sum(r["y"] for r in rows) / len(rows),
        )
        where = f"all {len(rows)} {g.item_name(item)} node(s)"
        resources = [item]
        notes.append(
            "centred on the centroid of every node of that resource, which may be open "
            "water if they are spread across the map -- pass node:<id> or x,y to pin it"
        )
    else:
        try:
            origin, where = resolve_origin(st, text)
        except ValueError as exc:
            return f"! {exc}"
        # The resolver answers with a point; the overlay wants the node's own resource,
        # so a node place is looked up again HERE rather than given a second grammar.
        if kind.casefold() == NODE_PREFIX:
            short = {k.rsplit(".", 1)[-1]: v for k, v in table.by_instance().items()}
            node = short.get(value.strip())
        if node is not None:
            resources = [node["resource"]]
            where = f"{where} -- {g.item_name(node['resource'])}"

    # Which variants a resource actually HAS, read from the node table rather than
    # assumed: Coal is node-only, so emitting coalWellPure would be a token invented for
    # something that does not exist. Only oil, nitrogen and water have wells.
    kinds = sorted(
        {
            "well" if r["kind"].startswith("well") else "node"
            for res in resources
            for r in table.by_resource(res)
        }
    )
    tokens = layers or maplink.layers_for(resources, kinds or None)

    # Identity only. A metre of drift is far below one pixel of a map link at any zoom
    # this emits, so the position note would be noise -- but "your save does not call it
    # that" is something the reader will hit again the next time they paste the id.
    if node is not None:
        notes += nodes_mod.identity_notes(
            nodes_mod.skew_for_save(st.header if st else None, table), [node["instance"]]
        )

    # The local map goes FIRST and for every target, not only for a sited plan: it is the
    # only one of the two that can draw this world, and a link to a map that cannot see
    # the player's factory is not the answer to "show me my factory".
    label = st.labels.find(text) if st and node is None and not resources else None
    local = maplink.local_map_url(
        origin[0] / 100.0,
        origin[1] / 100.0,
        world=st.world_id if st else "",
        show=f"label:{label.name}" if label is not None and label.name == where else "",
    )
    body = f"local map: {local}\npublic map: {maplink.map_url(*origin, tokens, zoom=zoom)}"
    if tokens:
        body += "\n# layers: " + ", ".join(tokens)
    notes.append(
        "the local map is this project's own web map and draws YOUR save, with the server "
        "running; the public one is satisfactory-calculator.com and knows the vanilla "
        "world only -- nothing you built is on it"
    )
    return render.envelope(
        f"# {where} at {int(origin[0] / 100)},{int(origin[1] / 100)} (metres)", body, notes
    )


@mcp.tool(structured_output=False)
def rank_build_sites(
    resource: str,
    sources: list[str] | None = None,
    limit: Limit = 5,
    top: Annotated[int | None, Field(description="deprecated alias for limit")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Rank candidate fields for a new extraction site, best first.

    Scores untapped REACHABLE capacity against spread, distance to your existing
    buildings, and purity mix. Every raw component is shown so you can re-weight:
    the single score is a starting point, not a verdict.

    ``sources`` narrows the search area using the same selectors as
    search_resource_nodes; omit it to search the whole map.

    A ranking does not page: the rows below the cut score worse by construction, so raise
    `limit` or narrow `sources` rather than looking for an offset.
    """
    g = game()
    rid = _item_id(resource)
    if rid is None:
        return f"no resource matching {resource!r}"
    table = nodes_mod.load_nodes()
    n = render.clamp(top if top is not None else limit, default=5)

    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return (
            f"could not read save: {exc} (site ranking needs a save to know what is already built)"
        )

    spec = [*(sources or []), f"resource:{rid}"]
    sel = select_nodes(spec, table.nodes, resolve_resource=_item_id, st=st)
    if sel.errors and not sel.nodes:
        return render.envelope("# no candidates", "", [*sel.errors, SELECTOR_HELP])

    rows = nodes_mod.annotate(sel.nodes, g, st.projection, st.unlocked_building_ids)
    clusters = geo.cluster(rows, link_m=200.0)
    terrain = heightfield.load_field()
    scored = ranking_mod.rank_sites(
        clusters,
        infra=st.infra_points(),
        consumer_z=st.consumer_z(),
        terrain=terrain,
    )
    if not scored:
        return render.envelope(
            f"# no untapped {g.item_name(rid)} in {sel.description}",
            "",
            [
                "every reachable node here already has an extractor",
                *sel.errors,
            ],
        )

    rm = regions_mod.load_regions()
    unit = "m3/min" if g.items[rid].is_fluid else "/min"
    out_rows = []
    for sc in scored[:n]:
        cx, cy, _cz = sc.centroid
        raw = sc.raw
        alt = raw["altitude_vs_consumer_m"]
        out_rows.append(
            (
                render.num(sc.score),
                rm.label_for(cx, cy).name or regions_mod.OFF_MAP,
                geo.grid_cell(cx, cy),
                f"{int(cx / 100)},{int(cy / 100)}",
                raw["nodes"],
                render.num(raw["untapped_rate"]),
                f"{render.num(raw['spread_m'])}m",
                "-"
                if raw["distance_to_infra_m"] is None
                else f"{render.num(raw['distance_to_infra_m'])}m",
                render.num(raw["purity_quality"]),
                "-" if alt is None else f"{alt:+.0f}m",
                "-" if raw["pad_roughness_m"] is None else f"{raw['pad_roughness_m']:.1f}m",
                "-" if raw["pad_slope_deg"] is None else f"{raw['pad_slope_deg']:.0f}deg",
                "-" if raw["pad_submerged_pct"] is None else f"{raw['pad_submerged_pct']:.0f}%",
            )
        )

    notes = [*sel.errors]
    notes.append(
        "weights: throughput 1.00, spread -0.35, distance -0.25, purity +0.20, "
        "roughness -0.10 (min-max normalised across these candidates only)"
    )
    notes.append(
        "alt is the field's height above your refineries: POSITIVE means fluid flows "
        "downhill to them and needs no pipeline pumps"
    )
    if terrain is None:
        notes.append(
            "no terrain field on this machine, so rough/slope/wet are blank -- run "
            "tools/gen_world_heightmap.py against your game install to fill them"
        )
    else:
        notes.append(
            f"rough/slope/wet describe a {ranking_mod.SITE_PAD_M:.0f} m square at the "
            f"field's centre and are DESCRIPTIONS, not a verdict -- steep and wet sites are "
            f"built on foundations every day, which is why roughness carries the smallest "
            f"weight here. rough is bump height off a best-fit plane, so a clean ramp reads "
            f"near zero however steep it is"
        )
    if st.consumer_z() is None:
        notes.append("no refineries found, so altitude is not shown")
    # `alt` is a node z minus a refinery z, and it decides whether a fluid run needs pumps.
    # Scoped to the candidate nodes, not the whole table.
    notes += nodes_mod.skew_notes(
        nodes_mod.skew_for_save(st.header, table), [r["instance"] for r in rows]
    )

    return render.envelope(
        f"# {len(scored)} candidate {g.item_name(rid)} field(s) in {sel.description}, "
        f"untapped and reachable only\n"
        f"# {st.age_note}\n# rates {unit} at 100% clock; coords in metres",
        render.table(
            (
                "score",
                "region",
                "grid",
                "centre(m)",
                "n",
                "untapped",
                "spread",
                "to_infra",
                "purity",
                "alt",
                "rough",
                "slope",
                "wet",
            ),
            out_rows,
            total=len(scored),
            limit=n,
            hint="raise limit, or narrow with sources= -- a ranking has no offset",
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def whereami(
    radius_m: float = 500.0,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 8,
) -> str:
    """Where the player is standing, and what is around them.

    Position comes from the Char_Player_C pawn in the save, so it is wherever you
    were when it was written -- an autosave can be several minutes stale. Use
    ``near:me@<radius>`` as a source selector in the planning tools to scope work to
    here.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    here = st.player_position()
    if here is None:
        return "no player pawn in this save, so there is no position to report"
    x, y, z = here
    rm = regions_mod.load_regions()
    label = rm.label_for(x, y)

    table = nodes_mod.load_nodes()
    near = nodes_mod.annotate(
        table.filter(center=(x, y), radius_m=radius_m),
        g,
        st.projection,
        st.unlocked_building_ids,
    )
    near.sort(key=lambda n: geo.distance_m((n["x"], n["y"]), (x, y)))
    rows = [
        (
            g.item_name(n["resource"]),
            n["purity"],
            render.num(n["rate"]),
            f"{geo.distance_m((n['x'], n['y']), (x, y)):.0f}m",
            geo.direction_of(n["x"], n["y"], x, y),
            "tapped" if n["tapped"] else ("LOCKED" if not n["reachable"] else "free"),
        )
        for n in near[: render.clamp(limit, default=8)]
    ]

    builds = [r for r in st._all_records() if r.get("pos")]
    closest = min(
        builds,
        key=lambda r: geo.distance_m((r["pos"][0], r["pos"][1]), (x, y)),
        default=None,
    )
    notes = [f"use near:me@{radius_m:g} as a source selector to plan around here"]
    # Distances here are measured FROM the table's coordinates, so a stale row makes
    # "nearest node" quietly wrong. Scoped to what is actually within radius_m.
    notes += nodes_mod.skew_notes(
        nodes_mod.skew_for_save(st.header, table), [n["instance"] for n in near]
    )
    if closest is not None:
        d = geo.distance_m((closest["pos"][0], closest["pos"][1]), (x, y))
        name = g.buildings[closest["cls"]].name if closest["cls"] in g.buildings else closest["cls"]
        notes.append(f"nearest building: {name} at {d:.0f}m")
    if len(st.players) > 1:
        notes.append(f"{len(st.players)} pawns in this save; showing the one holding a build gun")

    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("x,y,z(m)", f"{x / 100:.0f},{y / 100:.0f},{z / 100:.0f}"),
                        ("region", label.describe()),
                        ("grid", geo.grid_cell(x, y)),
                        ("from_map_centre", geo.direction_of(x, y)),
                    ]
                ),
                f"# {len(near)} node(s) within {radius_m:g}m",
            ]
        ),
        render.table(
            ("resource", "purity", "rate", "dist", "dir", "status"),
            rows,
            total=len(near),
            limit=render.clamp(limit, default=8),
            hint=(
                "raise limit or shrink radius_m; for the whole tail use "
                "search_resource_nodes(show='nearest', near='me'), which pages"
            ),
        ),
        notes,
    )
