"""Places: named regions, what is at a point, and where the player stands."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from .....domain.spatial import caves, geo, heightfield, surroundings
from .....domain.spatial import nodes as nodes_mod
from .....domain.spatial import regions as regions_mod
from .....domain.spatial.nodes import search as node_search
from .....domain.spatial.places import PLAYER_WORDS, resolve_place
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit


@app.tool()
def list_regions(
    resource: str | None = None,
    with_resource: Annotated[
        str | None, Field(description="retired -- write resource= instead")
    ] = None,
) -> str:
    """Named map regions, optionally only those containing a given resource.

    Region names are ADVISORY: the boundaries are the game's own map areas, downsampled to a
    256 m grid to publish and a 64 m one to look up in, so a name near a boundary can be one
    cell out. Use them to talk about places, not to compute with -- every node row also
    carries an exact grid cell.

    `anchor` is a coordinate that provably lies in the region, which a centroid does not: a
    concave region's mean lands on its neighbour's ground.
    """
    if gone := app.retired(("with_resource", with_resource, "resource")):
        return gone
    g = app.game()
    regions = regions_mod.load_regions()
    table = nodes_mod.load_nodes()
    resource_id = app.resolve_item_id(resource) if resource else None
    if resource and resource_id is None:
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
        for r in regions_mod.region_rows(table, resource_id)
    ]
    scope = f" containing {g.item_name(resource_id)}" if resource_id else ""
    return render.envelope(
        f"# {len(rows)} region(s){scope}; anchor in metres",
        render.table(("region", "dir", "grid", "anchor(m)", "km2", "nodes"), rows, total=len(rows)),
        [
            f"names are advisory, ~{regions.meta.get('accuracy_m', 256)}m boundary accuracy",
            (
                "the anchor is a cell that provably belongs to the region, not its "
                "centroid: a concave region's mean lands in its neighbour"
            ),
            "any region name works as a source selector for plan_factory",
        ],
    )


def _terrain_fields(reading, field) -> tuple[list[tuple[str, str]], list[str]]:
    """The terrain field's reading at the point, and what it cannot say there."""
    fields: list[tuple[str, str]] = []
    notes: list[str] = []
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
    return fields, notes


def _sample_fields(near, reading, radius_m: float) -> tuple[list[tuple[str, str]], list[str]]:
    """Elevations sampled from what stands nearby: ground and built apart, never averaged."""
    fields: list[tuple[str, str]] = []
    notes: list[str] = []
    if not near.samples:
        notes.append(
            f"no known elevation within {radius_m:g}m: nothing is built here and no "
            "resource node is near -- "
            + (
                "the terrain reading above is the whole answer here"
                if reading is not None
                else "widen radius_m or accept that this is unsurveyed ground"
            )
        )
        return fields, notes
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
    return fields, notes


def _conduit_fields(found) -> tuple[list[tuple[str, str]], list[str]]:
    """Belt and pipe runs through the point, counted against their drawn lines.

    Reported even at zero: with a readable save, absence here is absence in the world.
    """
    counted = found.conduits
    if counted is None:
        return [], ["no save read: belts and pipes here are unknown, not absent"]
    fields = [
        (
            "conduits",
            (
                f"{counted['belt']} belt run(s), {counted['pipe']} pipe run(s) "
                f"within {found.conduit_radius_m:g}m"
            ),
        )
    ]
    notes = []
    if counted["belt"] or counted["pipe"]:
        notes.append("search_conduits lists those runs with endpoints, lengths and elevation")
    return fields, notes


def _surroundings(found) -> list[tuple[str, str]]:
    g = app.game()
    out = []
    if found.nearest:
        nearest = found.nearest[0]
        out.append(
            (
                "nearest_node",
                (
                    f"{g.item_name(nearest['resource'])} {nearest['purity']} "
                    f"{nearest['distance_m']:.0f}m "
                    f"(node:{nearest['instance'].rsplit('.', 1)[-1]})"
                ),
            )
        )
    if found.fields:
        first = found.fields[0]
        names = ", ".join(g.item_name(r) for r in first.resources)
        out.append(
            (
                "fields",
                (
                    f"{found.fields_total} within {surroundings.FIELD_REACH_M:g}m; nearest {names}, "
                    f"{first.size} node(s) {first.distance_m:.0f}m ({first.selector})"
                ),
            )
        )
    if found.pickups_total is not None:
        out.append(
            ("pickups", f"{found.pickups_total} remaining within {surroundings.PICKUP_REACH_M:g}m")
        )
    return out


@app.tool()
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

    `at=` takes `'x,y'` in metres, `me`, a named factory, `slab:<n>` from `factory_map
    show=slabs` (bare platforms included), or a `chain:`/`pipe:` run from
    `search_conduits`. Off the map it answers 'off-map or ocean' rather than guessing the
    nearest land region.

    Elevation is answered two ways and never averaged. With the extracted terrain field,
    `terrain_m` is the texel at this coordinate, with its layer, that layer's accuracy, and
    the water surface and depth where water stands. The rest is a SAMPLE of what stands
    nearby, with count and spread: resource nodes are quoted as ground, foundations and
    buildings as built elevation, and the gap between them is fill already stacked there.

    Belts and pipes are counted against their drawn lines; with a readable save a zero means
    nothing runs through. `search_conduits` lists the runs.
    """
    # The node table alone covers the whole map, so an unexplored coordinate still gets an
    # answer; a readable save adds the dense sources and resolves every other `at=` form.
    st, _reason = app.load_world_or_none(save, world, as_of)

    if not at.strip():
        return (
            "! describe_location needs at=<place>: 'x,y' in metres, 'me', a named "
            "factory, 'slab:<n>', or a run id like 'chain:7'"
        )
    try:
        (x, y), where = resolve_place(st, at)
    except ValueError as exc:
        return f"! {exc}"

    field = heightfield.load_field()
    player = st.player_position() if st and at.strip().casefold() in PLAYER_WORDS else None
    found = surroundings.describe_point(
        st, app.game(), x, y, radius_m, terrain_field=field, hint_z_cm=player[2] if player else None
    )
    near = found.probe

    # Echoed because `at=` can resolve to somewhere the caller never typed; a bare
    # coordinate resolves to itself, so it is named once.
    here = f"{x / 100:.0f},{y / 100:.0f}"
    fields = [
        ("at", here + (f" ({where})" if where and where != here else "")),
        ("region", found.label.describe()),
        ("confidence", found.label.confidence),
        ("grid", geo.grid_cell(x, y)),
        ("direction_from_centre", geo.direction_of(x, y)),
        ("bearing_deg", render.num(geo.bearing_deg(x, y))),
    ]
    notes: list[str] = []
    for more_fields, more_notes in (
        _terrain_fields(near.terrain, field),
        _sample_fields(near, near.terrain, radius_m),
        _conduit_fields(found),
    ):
        fields += more_fields
        notes += more_notes
    fields += _surroundings(found)
    notes += found.notes
    return render.envelope(render.kv(fields), "", notes)


@app.tool()
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
    g = app.game()
    st = app.load_world(save, world, as_of)

    found = surroundings.player_surroundings(st, g, radius_m)
    app.journal_world_find(st, ctx, "whereami", "", {}, "looked where the player is")
    if found.player is None:
        return "no player pawn in this save, so there is no position to report"
    x, y, z = found.player
    nearby = found.nodes
    rows = [
        (
            g.item_name(node["resource"]),
            node["purity"],
            render.num(node["rate"]),
            f"{node['distance_m']:.0f}m",
            node["direction"],
            {"locked": "LOCKED"}.get(node_search.status_of(node), node_search.status_of(node)),
        )
        for node in nearby[: render.clamp(limit, default=8)]
    ]

    notes = [f"use near:me@{radius_m:g} as a source selector to plan around here"]
    notes += found.notes
    field = heightfield.load_field()
    reading = field.z(x, y, hint_z_cm=z) if field is not None else None
    cave_line = reading.cave_note if reading is not None else None
    if found.nearest_building is not None:
        name, distance = found.nearest_building
        notes.append(f"nearest building: {name} at {distance:.0f}m")
    if found.pawns > 1:
        notes.append(f"{found.pawns} pawns in this save; showing the one holding a build gun")

    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("x,y,z(m)", f"{x / 100:.0f},{y / 100:.0f},{z / 100:.0f}"),
                        ("region", found.label.describe()),
                        ("grid", geo.grid_cell(x, y)),
                        ("from_map_centre", geo.direction_of(x, y)),
                        *([("cave", cave_line)] if cave_line else []),
                    ]
                ),
                f"# {len(nearby)} node(s) within {radius_m:g}m",
            ]
        ),
        render.table(
            ("resource", "purity", "rate", "dist", "dir", "status"),
            rows,
            total=len(nearby),
            limit=render.clamp(limit, default=8),
            hint=(
                "raise limit or shrink radius_m; for the whole tail use "
                "search_resource_nodes(show='nearest', near='me'), which pages"
            ),
        ),
        notes,
    )
