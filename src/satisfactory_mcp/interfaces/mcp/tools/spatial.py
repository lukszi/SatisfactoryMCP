"""Map queries: regions, coordinates, resource nodes, build sites, map links."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from ....domain.planning import journal, pins
from ....domain.spatial import caves, finder, geo, heightfield, place
from ....domain.spatial import nodes as nodes_mod
from ....domain.spatial import ranking as ranking_mod
from ....domain.spatial import regions as regions_mod
from ....domain.spatial.origin import (
    NODE_PREFIX,
    PLAN_PREFIX,
    PLAYER_WORDS,
    RUN_PREFIXES,
    resolve_origin,
)
from ....domain.spatial.select import SELECTOR_HELP
from ....domain.world import conduits as conduits_mod
from ....presenters.text import primitives as render
from ..app import (
    AsOf,
    Limit,
    _item_id,
    _state,
    actor,
    follow,
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

    rows = [
        (
            r["name"],
            r["direction"],
            r["grid"],
            f"{int(r['anchor'][0] / 100)},{int(r['anchor'][1] / 100)}",
            render.num(r["area_km2"]),
            r["nodes"],
        )
        for r in regions_mod.region_rows(table, rid)
    ]
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

    field = heightfield.load_field()
    player = st.player_position() if st and at.strip().casefold() in PLAYER_WORDS else None
    found = place.describe(
        st, game(), x, y, radius_m, terrain_field=field, hint_z_cm=player[2] if player else None
    )
    label = found.label
    near = found.probe

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
    if reading is not None and not reading.height_known:
        fields.append(("terrain_m", "unknown"))
        fields.append(("cave", reading.cave_note))
    elif reading is not None:
        accuracy = "" if reading.accuracy_m is None else f", +-{reading.accuracy_m:g}m"
        fields.append(("terrain_m", f"{reading.z_m:.1f} ({reading.source}{accuracy})"))
        if reading.cave != caves.NONE:
            fields.append(("cave", reading.cave_note))
        if reading.ambiguous:
            bare = reading.terrain_z_m
            fields.append(("terrain_bare_m", "unknown" if bare is None else f"{bare:.1f}"))
            notes.append(
                "terrain_m here may be a rock top or roof rather than the floor beneath it"
                + ("" if bare is None else "; terrain_bare_m is the sculpted ground under it")
            )
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
            mid = near.middle(values)
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
    counted = found.conduits
    if counted is not None:
        fields.append(
            (
                "conduits",
                (
                    f"{counted['belt']} belt run(s), {counted['pipe']} pipe run(s) "
                    f"within {found.conduit_radius_m:g}m"
                ),
            )
        )
        if counted["belt"] or counted["pipe"]:
            notes.append("search_conduits lists those runs with endpoints, lengths and elevation")
    else:
        notes.append("no save read: belts and pipes here are unknown, not absent")
    fields += _surroundings(found)
    notes += found.notes
    return render.envelope(render.kv(fields), "", notes)


def _surroundings(found) -> list[tuple[str, str]]:
    g = game()
    out = []
    if found.nearest:
        n = found.nearest[0]
        out.append(
            (
                "nearest_node",
                (
                    f"{g.item_name(n['resource'])} {n['purity']} {n['distance_m']:.0f}m "
                    f"(node:{n['instance'].rsplit('.', 1)[-1]})"
                ),
            )
        )
    if found.fields:
        f = found.fields[0]
        names = ", ".join(g.item_name(r) for r in f.resources)
        out.append(
            (
                "fields",
                (
                    f"{found.fields_total} within {place.FIELD_REACH_M:g}m; nearest {names}, "
                    f"{f.size} node(s) {f.distance_m:.0f}m ({f.selector})"
                ),
            )
        )
    if found.pickups_total is not None:
        out.append(("pickups", f"{found.pickups_total} remaining within {place.PICKUP_REACH_M:g}m"))
    return out


def _networks_view(g, st, origin: tuple[float, float], where: str, limit, offset: int) -> str:
    """One row per fluid network in the world: what it carries, and what it ends on.

    The view that makes a run list navigable. A run is one placed pipe and there are 503
    of them; a NETWORK is the connected plumbing system they belong to, which is the unit
    a player thinks in and the reason "is there a pipe from here to there" has an answer
    at all.
    """
    order = conduits_mod.networks(st, origin)

    rows = []
    start = max(0, offset)
    for view in order[start : start + render.clamp(limit, default=12)]:
        extra = len(view.touches) - 4
        rows.append(
            (
                view.network if view.network is not None else "-",
                g.item_name(view.fluid) if view.fluid else "?",
                view.pieces,
                f"{view.length_m:.0f}m",
                f"{view.centre[0] / 100:.0f},{view.centre[1] / 100:.0f}",
                f"{view.z_min_m:.0f}..{view.z_max_m:.0f}",
                f"{view.distance_m:.0f}m",
                ", ".join(view.touches[:4]) + (f" +{extra} more" if extra > 0 else "") or "?",
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
    loose = next((v for v in order if v.network is None), None)
    if loose is not None:
        notes.append(
            f"{loose.pieces} pipe piece(s) belong to no network at all -- placed, "
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
    radius_m: float = conduits_mod.NEAR_RADIUS_M,
    to: Annotated[
        str | None,
        Field(description="second area: list only runs passing near BOTH, same forms as near"),
    ] = None,
    to_radius_m: Annotated[
        float | None, Field(description="radius around `to`, defaults to radius_m")
    ] = None,
    conduit_kind: Annotated[str | None, Field(description="belt | pipe | all")] = None,
    show: Annotated[str, Field(description="runs | networks")] = "runs",
    network: Annotated[
        int | None, Field(description="list every pipe of this fluid network (show=networks ids)")
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 12,
    offset: int = 0,
    kind: Annotated[str | None, Field(description="retired -- write conduit_kind= instead")] = None,
    ctx: Context | None = None,
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
    follow(
        st,
        ctx,
        "search_conduits",
        "conduits",
        {
            "near": near,
            "radius_m": None if radius_m == conduits_mod.NEAR_RADIUS_M else f"{radius_m:g}",
            "to": to,
            "to_radius_m": None if to_radius_m is None else f"{to_radius_m:g}",
            "conduit_kind": want,
            "view": "networks" if view == "networks" else None,
            "network": network,
        },
        f"searched belts and pipes near {near}",
    )

    if view == "networks":
        try:
            origin, where = resolve_origin(st, near)
        except ValueError as exc:
            return f"! {exc}"
        if want == "belt":
            return "! show='networks' lists fluid networks; a belt chain belongs to none"
        return _networks_view(g, st, origin, where, limit, offset)

    found = conduits_mod.search(
        st, near, radius_m, to=to, to_radius_m=to_radius_m, kind=want, network=network
    )
    if found.error:
        return found.error
    hits, where, belts, pipes = found.hits, found.where, found.belts, found.pipes
    bridge_notes = found.bridged

    label = f"{want} run(s)" if want else "conduit run(s)"
    scope = f"within {radius_m:g}m of {where}"
    if found.second is not None:
        scope += f" AND {found.to_radius_m:g}m of {found.where_to}"
    if network is not None:
        label = "pipe run(s)"
        scope = f"on fluid network {network}, distance from {where}"

    fluids = sorted({g.item_name(r.fluid) for r in pipes if r.fluid})
    summary = (
        f"# {st.age_note}\n"
        f"# {len(hits)} {label} {scope}: "
        f"{len(belts)} belt ({sum(r.length_m for r in belts):.0f}m drawn), "
        f"{len(pipes)} pipe ({sum(r.length_m for r in pipes):.0f}m"
        + (f"; {', '.join(fluids)}" if fluids else "")
        + ")"
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


def _follow_nodes(st, ctx, view, resource, purity, kind, status, near, where) -> None:
    def given(value: str | None) -> str | None:
        return None if not value or value.strip().casefold() == "all" else value

    rid = _item_id(resource) if given(resource) else None
    name = game().item_name(rid) if rid else "every"
    shown = "fields" if view == "fields" else "nodes"
    follow(
        st,
        ctx,
        "search_resource_nodes",
        shown,
        {
            "resource": rid,
            "purity": given(purity),
            "kind": given(kind),
            "status": given(status),
            "near": near,
        },
        f"searched {name} {shown}" + (f" near {where}" if where else ""),
    )


def _slider(radius: str) -> bool:
    try:
        return 500.0 <= float(radius) <= 5000.0
    except ValueError:
        return False


def _rank_pane(sources: list[str] | None) -> dict:
    """The rank pane's settings, when ``sources`` says no more than it can."""
    out: dict = {}
    for term in sources or []:
        head, _, body = term.partition(":")
        head = head.strip().casefold()
        place, at, radius = body.rpartition("@")
        if head == "near" and at and "at" not in out and _slider(radius):
            out["at"], out["within_m"] = place.strip(), radius.strip()
        elif head == "purity" and body.strip().casefold() == "pure":
            out["pure"] = 1
        else:
            return {}
    return out


@mcp.tool(structured_output=False)
def search_resource_nodes(
    sources: list[str] | None = None,
    resource: str | None = None,
    purity: Annotated[str | None, Field(description="pure | normal | impure | all")] = None,
    kind: Annotated[str | None, Field(description="node | well_sat | geyser | all")] = None,
    only_free: bool = False,
    status: Annotated[
        str | None, Field(description="free | tapped | all; only_free=true means free")
    ] = None,
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
    ctx: Context | None = None,
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

    view = (show or "fields").strip().casefold()
    view = {"field": "fields", "node": "nodes"}.get(view, view)
    if view not in finder.VIEWS:
        return f"! unknown show {show!r}. Choose from: fields, nodes, nearest"
    wanted = (status or ("free" if only_free else "all")).strip().casefold()
    if wanted not in finder.STATUSES:
        return f"! unknown status {status!r}. Choose from: free, tapped, all"

    st = None
    try:
        st = _state(save, world, as_of)
    except Exception:
        pass

    if view == "nearest" and not near:
        return "! show='nearest' needs near=<x,y | me | factory name> to measure from"
    found = finder.find_nodes(
        st,
        g,
        sources=sources,
        resource=resource,
        purity=purity,
        kind=kind,
        status=wanted,
        view=view,
        near=near,
        resolve_resource=_item_id,
    )
    if found.error:
        return found.error
    if found.unselected:
        return render.envelope("# no nodes selected", "", [*found.errors, SELECTOR_HELP])
    if st is not None:
        _follow_nodes(st, ctx, view, resource, purity, kind, wanted, near, found.where)
    rows_all = found.rows
    if not rows_all:
        return render.envelope(
            f"# no nodes in {found.description}",
            "",
            found.errors or ["try widening the selector"],
        )

    rm = regions_mod.load_regions()
    mixed = found.mixed
    notes = list(found.notes)
    where = found.where
    start = max(0, offset)
    n = render.clamp(limit, default=25)
    if view in ("nodes", "nearest"):
        show_distance = found.origin is not None
        rows = [
            (
                r["instance"].rsplit(".", 1)[-1],
                g.item_name(r["resource"]) if mixed else r["purity"],
                *(
                    (f"{r['distance_m']:.0f}m",)
                    if show_distance
                    else (r["purity"] if mixed else r["kind"],)
                ),
                r["grid"],
                f"{int(r['x'] / 100)},{int(r['y'] / 100)}",
                f"{r['z'] / 100:.0f}",
                render.num(r["rate"]),
                {"locked": "LOCKED"}.get(finder.status_of(r), finder.status_of(r)),
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
        clusters = found.fields
        crows = [
            (
                c.region or regions_mod.OFF_MAP,
                c.grid,
                c.direction,
                f"{int(c.centroid[0] / 100)},{int(c.centroid[1] / 100)}",
                c.size,
                ",".join(f"{n}{k[0]}" for k, n in sorted(c.purities.items())),
                render.num(c.total),
                render.num(c.free),
                f"{c.diameter_m:.0f}m",
                "LOCKED" if c.locked else "",
            )
            for c in clusters[start : start + n]
        ]
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

    head = ""
    if found.elevation is not None:
        low, high = found.elevation
        head = (
            f"\n# elevation {low:.0f}..{high:.0f}m (span {high - low:.0f}m); fluid, so "
            "uphill runs need pumps and downhill runs do not"
        )

    water = found.water
    if water is not None:
        level = water["sea_level_m"]
        per_pump = water["per_pump_m3_min"]
        body = (
            "## open water\n"
            + render.kv(
                [
                    ("bodies drawn from", len(water["bodies"])),
                    ("pumps built", water["pumps"]),
                    ("per pump at 100%", f"{per_pump:.0f} m3/min" if per_pump is not None else ""),
                    (
                        "sea level",
                        f"{level:.1f}m (pumps span {water['sea_level_span_m']:.2f}m)"
                        if level is not None
                        else "",
                    ),
                ]
            )
            + "\n"
            + render.table(
                ("body", "pumps"),
                sorted(water["bodies"].items(), key=lambda kv: -kv[1]),
                total=len(water["bodies"]),
            )
            + "\n\n"
            + body
        )

    tapped = "tapped " if wanted == "tapped" else ""
    return render.envelope(
        f"# {found.description}: {len(rows_all)} {tapped}node(s), "
        f"{render.num(found.total)} {found.unit} total, "
        f"{render.num(found.free)} free and reachable\n"
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
            "'node:<id>', 'machine:<id>', 'slab:<n>', 'chain:<n>'/'pipe:<n>', 'plan:<name>' -- or "
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
    pin: Annotated[bool, Field(description="also pin it on the page (pin:N)")] = False,
    mode: Annotated[
        str | None,
        Field(description="base map type for the local link, an id settings() lists, or plain"),
    ] = None,
    ctx: Context | None = None,
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

    ``pin=True`` also pins the place for the page (a node, factory, sited plan or point;
    pinning it twice returns the pin it already has), and the page shows it at once.
    ``mode`` opens the local link on that base map instead of the shared default.
    """
    from ....domain.maps import registry as maps
    from ....domain.spatial import maplink

    if mode:
        mode = "map" if mode.strip() == "artwork" else mode.strip()
        entry, _where = maps.lookup(mode)
        if mode != maps.PLAIN and (entry is None or entry.get("status") != "ready"):
            known = ", ".join([*maps.known_ids(), maps.PLAIN])
            return f"! no base map {mode!r}; known: {known}"

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
        show=maplink.show_ref(
            node=node["instance"] if node is not None else None,
            run=text if kind.casefold() in RUN_PREFIXES else None,
            label=label.name if label is not None and label.name == where else None,
        ),
        mode=mode or "",
    )
    body = f"local map: {local}\npublic map: {maplink.map_url(*origin, tokens, zoom=zoom)}"
    if tokens:
        body += "\n# layers: " + ", ".join(tokens)
    notes.append(
        "the local map is this project's own web map and draws YOUR save, with the server "
        "running; the public one is satisfactory-calculator.com and knows the vanilla "
        "world only -- nothing you built is on it"
    )
    if pin:
        body += "\n" + _pin_place(st, text, node, label, origin, resources, ctx)
    return render.envelope(
        f"# {where} at {round(origin[0] / 100, 1):g},{round(origin[1] / 100, 1):g} (metres)",
        body,
        notes,
    )


def _pin_place(st, text: str, node, label, origin, resources, ctx) -> str:
    """Pin what ``show_on_map`` showed; the ``pin:`` line, or why nothing was pinned."""
    if st is None:
        return "! not pinned: the save could not be read"
    head = text.partition(":")[0].casefold()
    if resources and node is None:
        return "! not pinned: a whole resource is no place; pin one node:<id> or x,y instead"
    if head == "pin":
        n = pins.parse(text)
        return f"pin: already pin:{n}" if n is not None else "! not pinned"
    if node is not None:
        kind, ref = "node", {"node": node["instance"]}
    elif head == PLAN_PREFIX:
        stored = st.plans.find(text.partition(":")[2].strip())
        kind, ref = "plan", {"plan": stored.key if stored is not None else ""}
    elif label is not None:
        kind, ref = "factory", {"factory": label.name}
    else:
        kind = "point"
        ref = {"x_m": round(origin[0] / 100.0, 1), "y_m": round(origin[1] / 100.0, 1)}
    try:
        row, existing = pins.create(st, kind, ref)
    except pins.PinError as exc:
        return f"! not pinned: {exc}"
    except Exception as exc:
        return f"! not pinned: the pins are busy ({type(exc).__name__})"
    if existing:
        return f"pin: already {row['id']} {row['text']}"
    plan = ref.get("plan") if kind == "plan" else None
    journal.append(
        st.world_id,
        "pin.add",
        actor=actor(ctx),
        plan=plan,
        args={"n": row["n"], "kind": kind},
        text=f"pinned {row['id']} {row['text']}",
    )
    return f"pin: pinned as {row['id']} {row['text']}"


@mcp.tool(structured_output=False)
def rank_build_sites(
    resource: str,
    sources: list[str] | None = None,
    limit: Limit = 5,
    top: Annotated[int | None, Field(description="deprecated alias for limit")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: Context | None = None,
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
    n = render.clamp(top if top is not None else limit, default=5)

    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return (
            f"could not read save: {exc} (site ranking needs a save to know what is already built)"
        )

    ranked = finder.rank(st, g, rid, sources, resolve_resource=_item_id)
    sel = ranked.selection
    if ranked.unselected:
        return render.envelope("# no candidates", "", [*sel.errors, SELECTOR_HELP])
    follow(
        st,
        ctx,
        "rank_build_sites",
        "rank",
        {"resource": rid, **_rank_pane(sources)},
        f"ranked build sites for {g.item_name(rid)}",
    )
    scored = ranked.scored
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
        v = finder.site_view(sc, rm)
        alt = v["alt_m"]
        out_rows.append(
            (
                render.num(v["score"]),
                v["region"] or regions_mod.OFF_MAP,
                v["grid"],
                f"{int(v['x'] / 100)},{int(v['y'] / 100)}",
                v["nodes"],
                render.num(v["untapped"]),
                f"{render.num(v['spread_m'])}m",
                "-" if v["to_infra_m"] is None else f"{render.num(v['to_infra_m'])}m",
                render.num(v["purity"]),
                "-" if alt is None else f"{alt:+.0f}m",
                "-" if v["rough_m"] is None else f"{v['rough_m']:.1f}m",
                "-" if v["slope_deg"] is None else f"{v['slope_deg']:.0f}deg",
                "-" if v["wet_pct"] is None else f"{v['wet_pct']:.0f}%",
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
    if not ranked.terrain:
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
    if ranked.consumer_z is None:
        notes.append("no refineries found, so altitude is not shown")
    notes += ranked.notes

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
    ctx: Context | None = None,
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

    found = place.here(st, g, radius_m)
    follow(st, ctx, "whereami", "", {}, "looked where the player is")
    if found.player is None:
        return "no player pawn in this save, so there is no position to report"
    x, y, z = found.player
    label = found.label
    near = found.nodes
    rows = [
        (
            g.item_name(n["resource"]),
            n["purity"],
            render.num(n["rate"]),
            f"{n['distance_m']:.0f}m",
            n["direction"],
            {"locked": "LOCKED"}.get(finder.status_of(n), finder.status_of(n)),
        )
        for n in near[: render.clamp(limit, default=8)]
    ]

    notes = [f"use near:me@{radius_m:g} as a source selector to plan around here"]
    notes += found.notes
    field = heightfield.load_field()
    reading = field.z(x, y, hint_z_cm=z) if field is not None else None
    cave_line = reading.cave_note if reading is not None else None
    if found.nearest_building is not None:
        name, d = found.nearest_building
        notes.append(f"nearest building: {name} at {d:.0f}m")
    if found.pawns > 1:
        notes.append(f"{found.pawns} pawns in this save; showing the one holding a build gun")

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
                        *([("cave", cave_line)] if cave_line else []),
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
