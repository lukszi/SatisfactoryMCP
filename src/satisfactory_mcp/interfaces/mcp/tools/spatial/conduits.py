"""``search_conduits``: belt and pipe runs, and the fluid networks they belong to."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from .....domain.spatial.places import resolve_place
from .....domain.world import conduit_search
from .....domain.world import conduits as conduits_mod
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit

#: What each conduit row's columns mean; said on every page of runs.
RUN_NOTES = (
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
)


def _networks_view(g, st, origin: tuple[float, float], where: str, limit, offset: int) -> str:
    """One row per fluid network in the world: what it carries, and what it ends on.

    A run is one placed pipe; a NETWORK is the connected plumbing system it belongs to,
    the unit a player thinks in.
    """
    order = conduit_search.networks(st, origin)
    window = render.page(limit, offset, default=12)
    rows = [
        (
            view.network if view.network is not None else "-",
            g.item_name(view.fluid) if view.fluid else "?",
            view.pieces,
            f"{view.length_m:.0f}m",
            f"{view.centre[0] / 100:.0f},{view.centre[1] / 100:.0f}",
            f"{view.z_min_m:.0f}..{view.z_max_m:.0f}",
            f"{view.distance_m:.0f}m",
            render.capped(view.touches, 4, more=" +{n} more") or "?",
        )
        for view in window.of(order)
    ]

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
            offset=window.start,
            limit=limit,
        ),
        notes,
    )


def _end_xyz(end) -> str:
    return f"{end.x / 100:.0f},{end.y / 100:.0f},{end.z / 100:.0f}"


def _run_row(g, run) -> tuple:
    joiner = "->" if run.directed else "--"
    connects = f"{(run.a.plugs or '?')[:22]} {joiner} {(run.b.plugs or '?')[:22]}"
    if run.via:
        connects += " via " + ", ".join(run.via)[:24]
    return (
        run.ident,
        run.label,
        f"{run.length_m:.0f}m",
        _end_xyz(run.a),
        _end_xyz(run.b),
        f"{run.z_min_m:.0f}..{run.z_max_m:.0f}",
        # A pipe says WHAT it carries (? where no network claims it); a belt has no such
        # fact, so it quotes capacity.
        (g.item_name(run.fluid) if run.fluid else "?")
        if run.kind == "pipe"
        else render.rate(run.rate, "/min"),
        run.basis or "-",
        connects,
    )


def _runs_view(g, st, found, want, network, radius_m: float, limit, offset: int) -> str:
    """The runs a search found, longest first, with both ends and what stands at each."""
    hits, belts, pipes = found.hits, found.belts, found.pipes
    label = f"{want} run(s)" if want else "conduit run(s)"
    scope = f"within {radius_m:g}m of {found.where}"
    if found.second is not None:
        scope += f" AND {found.to_radius_m:g}m of {found.where_to}"
    if network is not None:
        label = "pipe run(s)"
        scope = f"on fluid network {network}, distance from {found.where}"

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
                *found.bridged,
                (
                    "this reads the save's own belt and pipe geometry, so nothing listed "
                    "means nothing runs there -- widen radius_m to check further out"
                ),
            ],
        )

    window = render.page(limit, offset, default=12)
    rows = [_run_row(g, run) for run in window.of(hits)]
    notes = [*found.bridged, *RUN_NOTES]
    if any("-mk" in r.label for r in hits):
        notes.append("a mixed-tier chain shows its tier span and quotes the slowest cap")
    return render.envelope(
        summary,
        render.table(
            ("id", "kind", "len", "a(m)", "b(m)", "z(m)", "carries", "basis", "connects"),
            rows,
            total=len(hits),
            offset=window.start,
            limit=limit,
        ),
        notes,
    )


@app.tool()
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

    A run is one belt CHAIN (consecutive conveyor pieces, split at splitters, mergers and
    machines) or one placed pipeline piece. Longest first; each row carries both ends with
    what stands there, the drawn length and the elevation span.

    `show="networks"` lists one row per FLUID NETWORK in the world instead: what it carries,
    how much pipe, where its middle is and what it ends on. `radius_m` and `to` do not
    narrow it; the distance column is measured from `near`.

    `near` and `to` take a coordinate in metres, `me`, a named factory, or one of this
    tool's run ids (`chain:7`, `pipe:333`), which centres on that run's midpoint so the
    `connects` column can be followed. With `to`, only runs passing within both radii are
    listed; proximity is measured against the drawn lines, so a run crossing mid-span
    counts. Long lists page with `offset=`.
    """
    if gone := app.retired(("kind", kind, "conduit_kind")):
        return gone
    g = app.game()
    st = app.load_world(save, world, as_of, purpose=" (conduits are read from the save)")

    want = (conduit_kind or "").strip().casefold() or None
    if want == "all":
        want = None
    if want not in (None, "belt", "pipe"):
        return f"! unknown conduit_kind {conduit_kind!r}. Choose from: belt, pipe, all"
    view = (show or "runs").strip().casefold()
    if view not in ("runs", "networks"):
        return f"! unknown show {show!r}. Choose from: runs, networks"
    app.journal_world_find(
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
            origin, where = resolve_place(st, near)
        except ValueError as exc:
            return f"! {exc}"
        if want == "belt":
            return "! show='networks' lists fluid networks; a belt chain belongs to none"
        return _networks_view(g, st, origin, where, limit, offset)

    found = conduit_search.search(
        st, near, radius_m, to=to, to_radius_m=to_radius_m, kind=want, network=network
    )
    if found.error:
        return found.error
    return _runs_view(g, st, found, want, network, radius_m, limit, offset)
