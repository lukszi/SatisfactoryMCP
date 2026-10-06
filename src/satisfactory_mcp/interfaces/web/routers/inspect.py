"""The point inspector: what is at a coordinate, and how well each part of it is known.

Named for its route rather than for the stdlib module it shadows: the shadow is only a name
in this package, since every ``import inspect`` in Python 3 is absolute and still finds the
standard library.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from fastapi import APIRouter, Query, Request

from ....domain.collectibles import service as collectibles_service
from ....domain.spatial import caves, geo, surroundings
from ....domain.spatial import elevation as spatial_elevation
from ....domain.spatial import nodes as spatial_nodes
from ....domain.spatial import regions as spatial_regions
from ....domain.world.state import WorldState
from .. import terrain
from ..serial import (
    CollectibleRow,
    FoundField,
    Region,
    TableAge,
    _fail,
    _field_json,
    _label_json,
    _pickup_json,
    _resource_name,
    _state,
    _xyz,
)

__all__ = ["INSPECT_NEAREST", "INSPECT_RADIUS_M", "router"]

router = APIRouter(prefix="/api")


# ----------------------------------------------------------- point inspector


#: How far a click looks for known elevations, metres. The same default
#: ``describe_location`` uses, so the map and the MCP tool answer one question one way.
INSPECT_RADIUS_M = 200.0

#: How many nodes a click reports. Enough to see what a site is next to; more would make
#: the popup a second copy of the node table.
INSPECT_NEAREST = 5


class InspectAt(TypedDict):
    """The coordinate that was asked about, rounded to the decimetre it was answered at.

    Neither field is nullable: both are required query parameters, so a request that carries
    no coordinate is a 422 before the handler runs and never reaches this shape.
    """

    x_m: float
    y_m: float


class Elevation(TypedDict):
    """One probe as JSON. The nullables here are the point of the endpoint, not slack in it.

    Named for the payload rather than for ``spatial_elevation.Elevation``, the domain object
    this is built FROM: that one holds populations, this one holds the four labelled answers
    plus the reason for every number it declines to give -- see ``_elevation_json``.

    ``radius_m``, ``ground_count`` and ``built_count`` are the three that cannot be null;
    everything else goes through ``_round``, which is ``None`` in, ``None`` out.
    """

    radius_m: float
    terrain_m: float | None
    terrain_source: str | None
    terrain_accuracy_m: float | None
    terrain_bare_m: float | None
    terrain_ambiguous: bool
    terrain_cave: Literal["none", "below", "inside"]
    terrain_cave_note: str | None
    terrain_water_m: float | None
    terrain_water_depth_m: float | None
    terrain_water_note: str | None
    terrain_note: str | None
    ground_m: float | None
    ground_spread_m: float | None
    ground_count: int
    built_m: float | None
    built_count: int
    fill_m: float | None
    fill_note: str | None
    counts: dict[str, int]


class NearestNode(TypedDict):
    """One of the five nodes nearest a right-clicked point.

    The coordinates are the static node table's own three floats and are not nullable.
    ``occupant_cls`` is null wherever the occupancy join found nothing, and null for ALL
    five whenever the save could not be read, which ``save_error`` says out loud.

    ``resource`` is the class id and ``resource_name`` the word a reader reads, from the same
    helper ``/api/nodes`` uses -- the inspector and the node dot must not name one fact two
    ways.
    """

    id: str
    name: str
    resource: str
    resource_name: str
    kind: str
    purity: str
    x_m: float
    y_m: float
    z_m: float
    occupied: bool
    occupant_cls: str | None
    distance_m: float
    spoiler: bool


class ConduitCount(TypedDict):
    """Runs passing within ``radius_m`` of the point, lifts counted as belts."""

    belt: int
    pipe: int
    radius_m: float


class NearPickup(CollectibleRow):
    """A remaining placement within 500 m, with the category's one word."""

    label: str


class InspectResponse(TypedDict):
    """What ``/api/inspect`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``region`` is ``null`` for ocean and off-map -- ``_label_json``'s refusal, which this
    layer must not undo. ``save_error`` is non-null exactly when the save would not load,
    and the answer is still a real answer: the node table is static and needs no ``.sav``.
    """

    at: InspectAt
    region: Region | None
    elevation: Elevation
    nearest: list[NearestNode]
    grid: str
    direction: str
    conduits: ConduitCount | None
    fields: list[FoundField]
    pickups: list[NearPickup]
    pickups_within: int | None
    pickups_within_spoilers: int
    stale: list[TableAge]
    save_error: str | None


def _elevation_json(near: spatial_elevation.Elevation) -> Elevation:
    """A probe as JSON, with the reason for every number it declines to give.

    Four sources, each labelled as what it is. ``terrain_m`` is the extracted heightfield
    read bilinearly at exactly the coordinate asked about; ``terrain_ambiguous`` says it may
    be a rock top or roof, with ``terrain_bare_m`` the landscape under it. ``terrain_cave``
    is ``below`` where a cave lies under the point, with ``terrain_cave_note`` the line to
    show; under ``inside`` with no cave floor found ``terrain_m`` is null and the note is
    ``terrain_note``. Ground and built
    are populations of things standing nearby. They stay apart out to the page: a node rests on
    terrain, a foundation is wherever the player put it, and one median over the three would
    be a number describing none of them. ``terrain_source`` says which layer of the field
    answered and ``terrain_accuracy_m`` what the generator measured for that layer, because
    a 0.2 m landscape texel and a 3.9 m fill texel are not the same claim.

    ``fill_m`` is ``null`` more often than not, so ``fill_note`` names which of its two
    causes applied -- too few nearby nodes, or nothing built nearby. It is never rendered as
    0: zero fill is a real and different measurement. ``terrain_note`` does the same job for
    the field, whose two causes are no field on this machine and a coordinate the field has
    no data for.

    ``terrain_water_m`` is the water surface's own height, which the channel takes from a
    cooked water volume's bounding box and knows to centimetres wherever there is water at
    all. ``terrain_water_depth_m`` is that minus the ground, and only exists where the ground
    under the water was itself measured at 1 m -- over the fill layer, which is most of the
    ocean, subtracting a 3.9 m-quantised raster from a sea surface produces a number nobody
    measured, so it is ``null`` with ``terrain_water_note`` saying why, and never 0.0.
    """
    ground, built = near.ground_m, near.built_m
    # Derived from the samples actually present rather than from a hardcoded list, so a
    # new non-ground source in the domain module arrives here without an edit.
    built_sources = tuple(s for s in near.counts if s not in spatial_elevation.GROUND_SOURCES)

    fill = near.fill_m
    note = None
    if fill is None:
        if len(ground) < spatial_elevation.MIN_GROUND_SAMPLES:
            note = (
                f"not enough ground samples ({len(ground)} of "
                f"{spatial_elevation.MIN_GROUND_SAMPLES} within {near.radius_m:g} m)"
            )
        elif not built:
            note = f"nothing built within {near.radius_m:g} m"

    def _round(value: float | None) -> float | None:
        return None if value is None else round(value, 1)

    terrain_probe = near.terrain
    terrain_note = None
    cave = terrain_probe.cave if terrain_probe else caves.NONE
    cave_note = terrain_probe.cave_note if terrain_probe else None
    unknown = terrain_probe is not None and not terrain_probe.height_known
    if unknown:
        terrain_note, cave_note = cave_note, None
    elif terrain_probe is None:
        terrain_note = (
            "the field has no data at this point -- open ocean, or a cave mouth"
            if terrain.field() is not None
            else "no terrain field on this machine (run tools/gen_world_heightmap.py)"
        )
    water_note = None
    if (
        terrain_probe is not None
        and terrain_probe.submerged
        and terrain_probe.water_depth_m is None
    ):
        water_note = (
            f"the ground under this water is the {terrain_probe.source} layer, which is too "
            "coarse to subtract a surface from, so the depth here is not known"
        )

    return {
        "radius_m": near.radius_m,
        "terrain_m": None if unknown else _round(near.terrain_m),
        "terrain_source": terrain_probe.source if terrain_probe else None,
        "terrain_accuracy_m": terrain_probe.accuracy_m if terrain_probe else None,
        "terrain_bare_m": _round(terrain_probe.terrain_z_m) if terrain_probe else None,
        "terrain_ambiguous": bool(terrain_probe and terrain_probe.ambiguous),
        "terrain_cave": cave,
        "terrain_cave_note": cave_note,
        "terrain_water_m": (
            _round(terrain_probe.water_m) if terrain_probe and terrain_probe.submerged else None
        ),
        "terrain_water_depth_m": _round(terrain_probe.water_depth_m) if terrain_probe else None,
        "terrain_water_note": water_note,
        "terrain_note": terrain_note,
        "ground_m": _round(near.median(*spatial_elevation.GROUND_SOURCES)),
        "ground_spread_m": _round(near.spread(*spatial_elevation.GROUND_SOURCES)),
        "ground_count": len(ground),
        "built_m": _round(near.median(*built_sources)) if built_sources else None,
        "built_count": len(built),
        "fill_m": _round(fill),
        "fill_note": note,
        "counts": dict(near.counts),
    }


def _nearest_json(n: dict, game) -> NearestNode:
    return {
        "id": n["instance"],
        "name": str(n["instance"]).rsplit(".", 1)[-1],
        "resource": n["resource"],
        "resource_name": _resource_name(game, n["resource"]),
        "kind": n["kind"],
        "purity": n["purity"],
        **_xyz((n["x"], n["y"], n["z"])),
        "occupied": n["tapped"],
        "occupant_cls": n["tapped_by"],
        "distance_m": n["distance_m"],
        "spoiler": not n["tapped"] and not n["reachable"],
    }


@router.get("/inspect", response_model=InspectResponse)
def inspect(
    request: Request,
    x_m: float,
    y_m: float,
    radius_m: Annotated[float, Query(ge=1, le=2000)] = INSPECT_RADIUS_M,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What is at a coordinate: region, measured ground, nodes, fields, conduits, pickups.

    Every answer comes out of ``surroundings.describe_point``, the function
    ``describe_location`` calls; this endpoint converts metres to the save's centimetres and
    rounds. ``radius_m`` is the elevation reach; conduits count within 250 m, fields and
    pickups look 500 m out.

    **A failed save is not a failed answer.** The node table is static, covers the whole map
    and needs no ``.sav`` at all, so a world whose save will not load still gets its region,
    its ground elevation and its nearest nodes; what it loses is the built population and
    the occupancy join, and ``save_error`` says so rather than letting "no extractor here"
    quietly mean "no save here".

    **It prefers the extracted terrain where there is any.** On a machine that has run
    ``tools/gen_world_heightmap.py``, the 1 m field answers "how high is it here" with one
    number at the coordinate asked about instead of a population of things standing near it.
    Where there is no field, or the field has no data there, the population answers.

    Not cached: the probe is a few milliseconds over the whole reference world, so a
    per-(world, save) cache would buy an invalidation bug. The heightfield itself is cached
    by its loader, keyed on its own sidecar's mtime, so this endpoint stays a caller.
    """
    try:
        table = spatial_nodes.load_nodes()
        spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    st: WorldState | None = None
    save_error: str | None = None
    try:
        st = _state(request, save, world)
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    game = request.app.state.game()
    x, y = x_m * 100.0, y_m * 100.0
    found = surroundings.describe_point(st, game, x, y, radius_m, terrain_field=terrain.field())
    nearest = found.nearest
    stale = []
    if st is not None:
        nodes_age = spatial_nodes.table_age(st.header, table, [n["instance"] for n in nearest])
        pickups_age = collectibles_service.table_age(st)
        stale = [
            age
            for age in (nodes_age, pickups_age)
            if age is not None and (age["behind"] or age["observed_matches"] is False)
        ]
    counted = found.conduits
    return {
        "at": {"x_m": round(x_m, 1), "y_m": round(y_m, 1)},
        "region": _label_json(found.label),
        "elevation": _elevation_json(found.probe),
        "nearest": [_nearest_json(n, game) for n in nearest],
        "grid": geo.grid_cell(x, y),
        "direction": geo.direction_of(x, y),
        "conduits": None if counted is None else {**counted, "radius_m": found.conduit_radius_m},
        "fields": [_field_json(f, game) for f in found.fields],
        "pickups": [{**_pickup_json(p, p["spoiler"]), "label": p["label"]} for p in found.pickups],
        "pickups_within": found.pickups_total,
        "pickups_within_spoilers": found.pickups_spoilers,
        "stale": stale,
        "save_error": save_error,
    }
