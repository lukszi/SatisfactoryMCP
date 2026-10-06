"""Resource nodes: searching them, and ranking fields for a new extraction site."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from .....domain.spatial import ranking as ranking_mod
from .....domain.spatial import regions as regions_mod
from .....domain.spatial.nodes import search as node_search
from .....domain.spatial.nodes.selectors import SELECTOR_HELP
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit

#: The page's rank pane slides its radius between these; outside them it cannot follow chat.
PAGE_RADIUS_MIN_M = 500.0
PAGE_RADIUS_MAX_M = 5000.0


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


def _status_cell(row: dict) -> str:
    status = node_search.status_of(row)
    return {"locked": "LOCKED"}.get(status, status)


def _follow_nodes(st, ctx, view, resource, purity, kind, status, near, where) -> None:
    """Journal the search, so a page following chat opens the same nodes view."""

    def given(value: str | None) -> str | None:
        return None if not value or value.strip().casefold() == "all" else value

    resource_id = app.resolve_item_id(resource) if given(resource) else None
    name = app.game().item_name(resource_id) if resource_id else "every"
    shown = "fields" if view == "fields" else "nodes"
    app.journal_world_find(
        st,
        ctx,
        "search_resource_nodes",
        shown,
        {
            "resource": resource_id,
            "purity": given(purity),
            "kind": given(kind),
            "status": given(status),
            "near": near,
        },
        f"searched {name} {shown}" + (f" near {where}" if where else ""),
    )


def _fits_page_radius_slider(radius: str) -> bool:
    try:
        return PAGE_RADIUS_MIN_M <= float(radius) <= PAGE_RADIUS_MAX_M
    except ValueError:
        return False


def _rank_pane(sources: list[str] | None) -> dict:
    """The rank pane's settings, when ``sources`` says no more than it can."""
    out: dict = {}
    for term in sources or []:
        head, _, body = term.partition(":")
        head = head.strip().casefold()
        origin_text, at, radius = body.rpartition("@")
        if head == "near" and at and "at" not in out and _fits_page_radius_slider(radius):
            out["at"], out["within_m"] = origin_text.strip(), radius.strip()
        elif head == "purity" and body.strip().casefold() == "pure":
            out["pure"] = 1
        else:
            return {}
    return out


def _node_table(found, g, all_rows, regions, window: render.Page) -> tuple[str, list[str]]:
    """One row per node, by yield or by distance, and what its columns mean."""
    mixed = found.mixed
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
            _status_cell(r),
            _occupant(r, g),
            regions.label_for_node(r).name or "-",
        )
        for r in window.of(all_rows)
    ]
    headers = (
        "node_id",
        "resource" if mixed else "purity",
        f"dist to {found.where}" if show_distance else ("purity" if mixed else "kind"),
        "grid",
        "x,y(m)",
        "z(m)",
        "rate",
        "status",
        "occupant",
        "region",
    )
    body = render.table(headers, rows, total=len(all_rows), offset=window.start, limit=window.size)
    notes = [
        "node_id doubles as a source selector: node:<id>",
        (
            "occupant is the extractor standing on the node at its saved clock; OFF means "
            "it is switched off, so that node's rate is not being produced"
        ),
    ]
    return body, notes


def _field_table(found, window: render.Page) -> str:
    """One row per field: nodes clustered within 200 m, ranked by yield."""
    clusters = found.fields
    field_rows = [
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
        for c in window.of(clusters)
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
    return render.table(
        headers, field_rows, total=len(clusters), offset=window.start, limit=window.size
    )


def _open_water_block(water: dict) -> str:
    """The water no node carries: bodies drawn from, pumps on each, the sea level."""
    level = water["sea_level_m"]
    per_pump = water["per_pump_m3_min"]
    return (
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
    )


@app.tool()
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
    if gone := app.retired(("mode", mode, "show"), ("group", group, "show")):
        return gone
    g = app.game()

    view = (show or "fields").strip().casefold()
    view = {"field": "fields", "node": "nodes"}.get(view, view)
    if view not in node_search.VIEWS:
        return f"! unknown show {show!r}. Choose from: fields, nodes, nearest"
    wanted = (status or ("free" if only_free else "all")).strip().casefold()
    if wanted not in node_search.STATUSES:
        return f"! unknown status {status!r}. Choose from: free, tapped, all"

    st, _reason = app.load_world_or_none(save, world, as_of)

    if view == "nearest" and not near:
        return "! show='nearest' needs near=<x,y | me | factory name> to measure from"
    found = node_search.find_nodes(
        st,
        g,
        sources=sources,
        resource=resource,
        purity=purity,
        kind=kind,
        status=wanted,
        view=view,
        near=near,
        resolve_resource=app.resolve_item_id,
    )
    if found.error:
        return found.error
    if found.unselected:
        return render.envelope("# no nodes selected", "", [*found.errors, SELECTOR_HELP])
    if st is not None:
        _follow_nodes(st, ctx, view, resource, purity, kind, wanted, near, found.where)
    all_rows = found.rows
    if not all_rows:
        return render.envelope(
            f"# no nodes in {found.description}",
            "",
            found.errors or ["try widening the selector"],
        )

    regions = regions_mod.load_regions()
    notes = list(found.notes)
    window = render.page(limit, offset, default=25)
    if view in ("nodes", "nearest"):
        body, table_notes = _node_table(found, g, all_rows, regions, window)
        notes += table_notes
    else:
        body = _field_table(found, window)
        notes.append('show="nodes" lists individual nodes; show="nearest" ranks by distance')

    head = ""
    if found.elevation is not None:
        low, high = found.elevation
        head = (
            f"\n# elevation {low:.0f}..{high:.0f}m (span {high - low:.0f}m); fluid, so "
            "uphill runs need pumps and downhill runs do not"
        )
    if found.water is not None:
        body = _open_water_block(found.water) + "\n\n" + body

    tapped = "tapped " if wanted == "tapped" else ""
    return render.envelope(
        f"# {found.description}: {len(all_rows)} {tapped}node(s), "
        f"{render.num(found.total)} {found.unit} total, "
        f"{render.num(found.free)} free and reachable\n"
        f"# rates at 100% clock; coords in metres{head}",
        body,
        notes,
    )


def _site_notes(ranked, selection) -> list[str]:
    """How the score is weighted, and what the terrain columns can and cannot claim."""
    notes = [*selection.errors]
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
    return notes


@app.tool()
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
    g = app.game()
    resource_id = app.resolve_item_id(resource)
    if resource_id is None:
        return f"no resource matching {resource!r}"
    shown = render.clamp(top if top is not None else limit, default=5)

    st = app.load_world(
        save, world, as_of, purpose=" (site ranking needs a save to know what is already built)"
    )

    ranked = node_search.rank(st, g, resource_id, sources, resolve_resource=app.resolve_item_id)
    selection = ranked.selection
    if ranked.unselected:
        return render.envelope("# no candidates", "", [*selection.errors, SELECTOR_HELP])
    app.journal_world_find(
        st,
        ctx,
        "rank_build_sites",
        "rank",
        {"resource": resource_id, **_rank_pane(sources)},
        f"ranked build sites for {g.item_name(resource_id)}",
    )
    scored = ranked.scored
    if not scored:
        return render.envelope(
            f"# no untapped {g.item_name(resource_id)} in {selection.description}",
            "",
            [
                "every reachable node here already has an extractor",
                *selection.errors,
            ],
        )

    regions = regions_mod.load_regions()
    unit = "m3/min" if g.items[resource_id].is_fluid else "/min"
    rows = []
    for scored_site in scored[:shown]:
        site = node_search.site_view(scored_site, regions)
        alt = site["alt_m"]
        rows.append(
            (
                render.num(site["score"]),
                site["region"] or regions_mod.OFF_MAP,
                site["grid"],
                f"{int(site['x'] / 100)},{int(site['y'] / 100)}",
                site["nodes"],
                render.num(site["untapped"]),
                f"{render.num(site['spread_m'])}m",
                "-" if site["to_infra_m"] is None else f"{render.num(site['to_infra_m'])}m",
                render.num(site["purity"]),
                "-" if alt is None else f"{alt:+.0f}m",
                "-" if site["rough_m"] is None else f"{site['rough_m']:.1f}m",
                "-" if site["slope_deg"] is None else f"{site['slope_deg']:.0f}deg",
                "-" if site["wet_pct"] is None else f"{site['wet_pct']:.0f}%",
            )
        )

    return render.envelope(
        f"# {len(scored)} candidate {g.item_name(resource_id)} field(s) in "
        f"{selection.description}, untapped and reachable only\n"
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
            rows,
            total=len(scored),
            limit=shown,
            hint="raise limit, or narrow with sources= -- a ranking has no offset",
        ),
        _site_notes(ranked, selection),
    )
