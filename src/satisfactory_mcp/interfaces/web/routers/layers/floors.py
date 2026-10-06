"""``/api/floors``: the floor decomposition of a world, one storey at a time.

What the reply carries and why it ships ids rather than geometry: docs/web-wire.md
"Floors". Every metre field is ``float | None``, as ``cm_to_m`` is: a response_model that
met a null in a field declared ``float`` would fail the whole reply. The handler is
``floors_view``, its operation_id, so it stays that name.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from .....domain.factories import floors as ffloors
from .....domain.factories import select as fselect
from .....domain.world.state import WorldState
from ... import terrain
from ...serial import cm_to_m, error_response, point_m, require_world, xyz_m

__all__ = ["router"]

router = APIRouter(prefix="/api")


class FloorDeck(TypedDict):
    """One band, identified. What a run's ``ends`` are made of."""

    platform: int
    ordinal: int
    top_m: float | None


class FloorBand(TypedDict):
    """One floor of one platform; docs/web-wire.md "Floors" says what each field means."""

    ordinal: int
    top_m: float | None
    low_m: float | None
    high_m: float | None
    span_m: float | None
    pieces: int
    cells: int
    area_m2: float
    share: float
    minor: bool
    machines: list[str]
    attachments: list[str]
    deck_rows: list[int]
    machine_count: int
    attachment_count: int
    deck_row_count: int


class FloorPlatform(TypedDict):
    """One 4-connected run of foundation cells, and the bands its tops fall into."""

    index: int
    cells: int
    pieces: int
    area_m2: float
    centre_m: list[float | None]
    extent_m: list[float | None]
    clean: float
    label: str | None
    slab: int | None
    bands: list[FloorBand]


class FloorRun(TypedDict):
    """One belt chain or one pipe, keyed by the join the belt and pipe payloads carry.

    ``ends`` is always two entries, head then tail, either null where that end is over no
    deck.
    """

    kind: str
    key: int
    pieces: int
    lift: bool
    rise_m: float | None
    riser: bool
    ends: list[FloorDeck | None]


class FloorPlacement(TypedDict):
    """One thing that is NOT on a floor, and the reason it is not."""

    instance_leaf: str
    cls: str
    name: str
    kind: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    above_terrain_m: float | None


class FloorCounts(TypedDict):
    """The shape of the answer before the rows. Nested; see ``FloorReport.counts``."""

    platforms: int
    bands: int
    runs: int
    violations: int
    #: Keyed by ``ffloors.GROUPS`` and ``ffloors.MEMBERSHIPS``: open maps, so the domain's
    #: two vocabularies are not restated here.
    placements: dict[str, int]
    membership: dict[str, int]


class FloorRules(TypedDict):
    """The thresholds the answer was produced with, in the units the answer is in."""

    tile_m: float | None
    cluster_tol_m: float | None
    band_eps_m: float | None
    min_band_pieces: int
    belt_height_m: float | None
    riser_m: float | None
    terrain_tol_m: float
    minor_share: float


class FloorsResponse(TypedDict):
    """What ``/api/floors`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    note: str | None
    selection: str | None
    terrain_measured: bool
    counts: FloorCounts
    platforms: list[FloorPlatform]
    #: Keyed by ``ffloors.MEMBERSHIPS``; ``placements`` by ``ffloors.GROUPS`` less ``band``.
    runs: dict[str, list[FloorRun]]
    placements: dict[str, list[FloorPlacement]]
    violations: list[FloorRun]
    rules: FloorRules


def _band_json(band: ffloors.Band) -> FloorBand:
    """One floor: where its deck is, how big it is, and what stands on it, by id."""
    return {
        "ordinal": band.ordinal,
        "top_m": cm_to_m(band.top_cm),
        "low_m": cm_to_m(band.low_cm),
        "high_m": cm_to_m(band.high_cm),
        "span_m": cm_to_m(band.span_cm),
        "pieces": band.pieces,
        "cells": band.cells,
        "area_m2": round(band.area_m2, 1),
        "share": round(band.share, 3),
        "minor": band.minor,
        "machines": band.machines,
        "attachments": band.attachments,
        "deck_rows": band.rows,
        "machine_count": len(band.machines),
        "attachment_count": len(band.attachments),
        "deck_row_count": len(band.rows),
    }


def _platform_json(platform: ffloors.Platform) -> FloorPlatform:
    """One platform, and the provenance of the decomposition that produced it."""
    return {
        "index": platform.index,
        "cells": platform.cells,
        "pieces": platform.pieces,
        "area_m2": round(platform.area_m2, 1),
        "centre_m": point_m(platform.centre_cm),
        "extent_m": point_m(platform.extent_cm),
        "clean": round(platform.clean, 4),
        "label": platform.label,
        "slab": platform.slab,
        "bands": [_band_json(b) for b in platform.bands],
    }


def _deck_json(deck: ffloors.Deck | None) -> FloorDeck | None:
    if deck is None:
        return None
    return {"platform": deck.platform, "ordinal": deck.ordinal, "top_m": cm_to_m(deck.top_cm)}


def _run_json(run: ffloors.Run) -> FloorRun:
    """One belt chain or one pipe, keyed by the join a client already has."""
    return {
        "kind": run.kind,
        "key": run.key,
        "pieces": run.pieces,
        "lift": run.lift,
        "rise_m": cm_to_m(run.rise_cm),
        "riser": run.riser,
        "ends": [_deck_json(d) for d in run.ends],
    }


def _placement_json(st: WorldState, placement: ffloors.Placement) -> FloorPlacement:
    """One thing that is NOT on a floor, and the reason it is not."""
    return {
        "instance_leaf": placement.instance,
        "cls": placement.cls,
        "name": st.game.building_name(placement.cls),
        "kind": placement.kind,
        **xyz_m(placement.pos_cm),
        "above_terrain_m": (
            None if placement.above_terrain_m is None else round(placement.above_terrain_m, 1)
        ),
    }


@router.get("/floors", response_model=FloorsResponse)
def floors_view(
    request: Request,
    factory: str | None = None,
    platform: int | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What is built, one storey at a time: platforms, their floors, the runs between them
    and what stands on no floor, narrowed by ``?factory=`` or ``?platform=``."""
    st = require_world(request, save, world)

    try:
        report = ffloors.floor_decomposition(
            st, platform=platform, label=factory, terrain_field=terrain.field()
        )
    except fselect.SelectorError as exc:
        return error_response(str(exc))

    if report.note and (platform is not None or factory is not None) and not report.platforms:
        # A selection that matched nothing is a bad request; a save that cannot carry the
        # data at all is not, and falls through to the 200 with its note below.
        return error_response(report.note, 404)

    return {
        "note": report.note,
        "selection": report.selection,
        "terrain_measured": report.terrain_measured,
        "counts": report.counts(),
        "platforms": [_platform_json(p) for p in report.platforms],
        "runs": {
            membership: [_run_json(r) for r in report.runs_of(membership)]
            for membership in ffloors.MEMBERSHIPS
        },
        "placements": {
            group: [_placement_json(st, p) for p in report.group(group)]
            for group in ffloors.GROUPS
            if group != "band"
        },
        # A riser with both ends on one band cannot happen: one here is drift, so it is sent.
        "violations": [_run_json(r) for r in report.violations],
        "rules": {
            "tile_m": cm_to_m(ffloors.CELL_CM),
            "cluster_tol_m": cm_to_m(ffloors.CLUSTER_TOL_CM),
            "band_eps_m": cm_to_m(ffloors.BAND_EPS_CM),
            "min_band_pieces": ffloors.MIN_BAND_PIECES,
            "belt_height_m": cm_to_m(ffloors.BELT_HEIGHT_CM),
            "riser_m": cm_to_m(ffloors.RISER_CM),
            "terrain_tol_m": ffloors.TERRAIN_TOL_M,
            "minor_share": ffloors.MINOR_SHARE,
        },
    }
