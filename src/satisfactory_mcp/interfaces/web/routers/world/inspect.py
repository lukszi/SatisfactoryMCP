"""The point inspector: what is at a coordinate, and how well each part of it is known.

What each answer means and why it declines where it does: docs/web-wire.md "Inspect". The
module shadows the stdlib's name only inside this package. Handler names are operation_ids.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.gamedata.model import GameData
from .....domain.collectibles.views import Placement
from .....domain.spatial import caves, geo, heightfield, surroundings
from .....domain.spatial import elevation as spatial_elevation
from .....domain.spatial import nodes as spatial_nodes
from .....domain.spatial import regions as spatial_regions
from .....domain.world.state import WorldState
from ... import terrain
from ...serial import (
    CollectibleRow,
    FoundField,
    Region,
    TableAge,
    cm_to_m,
    collectible_json,
    error_response,
    found_field_json,
    game_data,
    node_identity,
    region_json,
    stale_tables,
    world_state,
)

__all__ = ["INSPECT_NEAREST", "INSPECT_RADIUS_M", "router"]

router = APIRouter(prefix="/api")

#: How far a click looks for known elevations, metres: ``describe_location``'s default.
INSPECT_RADIUS_M = 200.0

#: How many nodes a click reports: enough to see what a site is next to.
INSPECT_NEAREST = 5


class InspectAt(TypedDict):
    """The coordinate that was asked about, rounded to the decimetre it was answered at."""

    x_m: float
    y_m: float


CaveWord = Literal["none", "below", "inside"]


class Elevation(TypedDict):
    """One probe: four labelled answers, and the reason for every number it declines to give.

    ``radius_m``, ``ground_count`` and ``built_count`` are never null; every other figure is
    null where nothing measured it, which is never the same as 0.
    """

    radius_m: float
    terrain_m: float | None
    terrain_source: str | None
    terrain_accuracy_m: float | None
    terrain_bare_m: float | None
    terrain_ambiguous: bool
    terrain_cave: CaveWord
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

    The coordinates are the static node table's and never null. ``occupant_cls`` is null
    where the occupancy join found nothing, and for all five when the save could not be read.
    ``resource_name`` is the same word ``/api/nodes`` uses.
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


class _Pickup(Placement):
    """A row of ``surroundings.pickups_near``: a placement, labelled and judged a spoiler."""

    label: str
    spoiler: bool


class InspectResponse(TypedDict):
    """What ``/api/inspect`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``region`` is null for ocean and off-map. ``save_error`` is set exactly when the save
    would not load, and the rest is still a real answer from the static tables.
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


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _fill_note(near: spatial_elevation.Elevation) -> str | None:
    """Which of its two causes left ``fill_m`` null, or ``None`` when it has a value."""
    if near.fill_m is not None:
        return None
    if len(near.ground_m) < spatial_elevation.MIN_GROUND_SAMPLES:
        return (
            f"not enough ground samples ({len(near.ground_m)} of "
            f"{spatial_elevation.MIN_GROUND_SAMPLES} within {near.radius_m:g} m)"
        )
    if not near.built_m:
        return f"nothing built within {near.radius_m:g} m"
    return None


def _terrain_notes(probe: heightfield.Reading | None) -> tuple[str | None, str | None]:
    """``(terrain_note, cave_note)``: why the field gave no height, and the cave line."""
    if probe is None:
        no_data = (
            "the field has no data at this point -- open ocean, or a cave mouth"
            if terrain.field() is not None
            else "no terrain field on this machine (run tools/gen_world_heightmap.py)"
        )
        return no_data, None
    if not probe.height_known:
        return probe.cave_note, None
    return None, probe.cave_note


def _water_note(probe: heightfield.Reading | None) -> str | None:
    """Why a submerged point has no water depth: its ground is too coarse to subtract from."""
    if probe is None or not probe.submerged or probe.water_depth_m is not None:
        return None
    return (
        f"the ground under this water is the {probe.source} layer, which is too "
        "coarse to subtract a surface from, so the depth here is not known"
    )


def _elevation_json(near: spatial_elevation.Elevation) -> Elevation:
    """A probe as JSON, each source labelled and kept apart from the others."""
    probe = near.terrain
    unknown = probe is not None and not probe.height_known
    terrain_note, cave_note = _terrain_notes(probe)
    # Read off the samples present, so a new non-ground source arrives without an edit here.
    built_sources = tuple(s for s in near.counts if s not in spatial_elevation.GROUND_SOURCES)
    return {
        "radius_m": near.radius_m,
        "terrain_m": None if unknown else _rounded(near.terrain_m),
        "terrain_source": probe.source if probe else None,
        "terrain_accuracy_m": probe.accuracy_m if probe else None,
        "terrain_bare_m": _rounded(probe.terrain_z_m) if probe else None,
        "terrain_ambiguous": bool(probe and probe.ambiguous),
        # ``caves`` answers in its three words.
        "terrain_cave": cast(CaveWord, probe.cave if probe else caves.NONE),
        "terrain_cave_note": cave_note,
        "terrain_water_m": _rounded(probe.water_m) if probe and probe.submerged else None,
        "terrain_water_depth_m": _rounded(probe.water_depth_m) if probe else None,
        "terrain_water_note": _water_note(probe),
        "terrain_note": terrain_note,
        "ground_m": _rounded(near.median(*spatial_elevation.GROUND_SOURCES)),
        "ground_spread_m": _rounded(near.spread(*spatial_elevation.GROUND_SOURCES)),
        "ground_count": len(near.ground_m),
        "built_m": _rounded(near.median(*built_sources)) if built_sources else None,
        "built_count": len(near.built_m),
        "fill_m": _rounded(near.fill_m),
        "fill_note": _fill_note(near),
        "counts": dict(near.counts),
    }


def _nearest_json(node: spatial_nodes.MeasuredNode, game: GameData) -> NearestNode:
    return {
        **node_identity(node, game),
        "kind": node["kind"],
        "purity": node["purity"],
        "x_m": cm_to_m(node["x"]),
        "y_m": cm_to_m(node["y"]),
        "z_m": cm_to_m(node["z"]),
        "occupied": node["tapped"],
        "occupant_cls": node["tapped_by"],
        "distance_m": node["distance_m"],
        "spoiler": not node["tapped"] and not node["reachable"],
    }


@router.get("/inspect", response_model=InspectResponse)
def inspect(
    request: Request,
    x_m: float,
    y_m: float,
    radius_m: Annotated[float, Query(ge=1, le=2000)] = INSPECT_RADIUS_M,
    save: str | None = None,
    world: str | None = None,
) -> InspectResponse | JSONResponse:
    """What is at a coordinate: region, measured ground, nodes, fields, conduits, pickups.

    ``radius_m`` is the elevation reach; a save that will not load still gets an answer.
    """
    try:
        table = spatial_nodes.load_nodes()
        spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return error_response(str(exc), 404)

    st: WorldState | None = None
    save_error: str | None = None
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    game = game_data(request)
    x, y = x_m * 100.0, y_m * 100.0
    found = surroundings.describe_point(st, game, x, y, radius_m, terrain_field=terrain.field())
    nearest = found.nearest
    stale: list[TableAge] = []
    if st is not None:
        stale = stale_tables(st, table, [node["instance"] for node in nearest])
    counted = found.conduits
    # The rows ``pickups_near`` labels and judges, which its signature leaves untyped.
    pickups = cast("list[_Pickup]", found.pickups)
    return {
        "at": {"x_m": round(x_m, 1), "y_m": round(y_m, 1)},
        "region": region_json(found.label),
        "elevation": _elevation_json(found.probe),
        "nearest": [_nearest_json(node, game) for node in nearest],
        "grid": geo.grid_cell(x, y),
        "direction": geo.direction_of(x, y),
        "conduits": (
            None
            if counted is None
            else {
                "belt": counted["belt"],
                "pipe": counted["pipe"],
                "radius_m": found.conduit_radius_m,
            }
        ),
        "fields": [found_field_json(f, game) for f in found.fields],
        "pickups": [{**collectible_json(p, p["spoiler"]), "label": p["label"]} for p in pickups],
        "pickups_within": found.pickups_total,
        "pickups_within_spoilers": found.pickups_spoilers,
        "stale": stale,
        "save_error": save_error,
    }
