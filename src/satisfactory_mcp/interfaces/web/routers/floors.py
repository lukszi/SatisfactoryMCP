"""``/api/floors``: the floor decomposition of a world, one storey at a time.

**Nullability is not decoration.** ``_m``, ``_xyz`` and ``_yaw`` all return ``float |
None``, so every field they produce is declared that way even where the reference world has
never produced a null: a response_model is a validator as well as a schema, and a field
declared ``float`` that arrives null is a 500 rather than a null.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
``floors_view``, not ``floors``, for exactly that reason.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import floors as ffloors
from ....domain.factories import select as fselect
from ....domain.world.state import WorldState
from .. import terrain
from ..serial import cm_to_m, error_response, world_state, xyz_m

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- floors


class FloorDeck(TypedDict):
    """One band, identified. What a run's ``ends`` are made of."""

    platform: int
    ordinal: int
    top_m: float | None


class FloorBand(TypedDict):
    """One floor of one platform. See ``_band_json`` for what each field means."""

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

    ``ends`` is always two entries, head then tail, either of which may be ``null`` where
    that end is over no deck. A pair rather than a list is what it means, and JSON has no
    pair -- so the length is a promise the prose makes and the schema cannot.
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
    #: Keyed by ``ffloors.GROUPS`` and ``ffloors.MEMBERSHIPS``. Left as open maps rather
    #: than spelled out as four fields each: the two vocabularies are the domain's, they
    #: are exported from there, and restating them here would be a second place to update.
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
    #: Keyed by ``ffloors.MEMBERSHIPS``, and ``placements`` by ``ffloors.GROUPS`` less
    #: ``band`` -- what landed on a floor is listed inside its own band, by id.
    runs: dict[str, list[FloorRun]]
    placements: dict[str, list[FloorPlacement]]
    violations: list[FloorRun]
    rules: FloorRules


def _band_json(band: ffloors.Band) -> FloorBand:
    """One floor: where its deck is, how big it is, and what stands on it -- by id.

    ``machines`` and ``attachments`` are instance ids. ``deck_rows`` is the same idea for
    the concrete, by the only name a lightweight buildable has: the subsystem stores no
    instance ids at all, so a deck is listed by its pieces' POSITIONS in ``/api/structures``,
    which both sides derive from one ``saveio.rows`` walk in one order. Without it a client
    can only re-derive a deck from heights, and this world's 1 m and 2 m half-steps are
    exactly where that goes wrong.

    ``deck_rows`` is the pieces at the band's own LEVEL and ``pieces`` is the size of the
    cluster it was found in. Both are reported rather than reconciled, and a client that
    draws a deck wants ``deck_rows``.

    ``span_m`` is how much the band's own level is spread, and it is not the storey height:
    the distance to the floor above is the next band's ``top_m``.
    """
    return {
        "ordinal": band.ordinal,
        "top_m": cm_to_m(band.top_cm),
        "low_m": cm_to_m(band.low_cm),
        "high_m": cm_to_m(band.high_cm),
        "span_m": cm_to_m(band.span_cm),
        "pieces": band.pieces,
        "cells": band.cells,
        "area_m2": round(band.area_m2, 1),
        # Against the platform's own largest band, so it is a statement about this platform
        # rather than about the world.
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
        "centre_m": [cm_to_m(platform.centre_cm[0]), cm_to_m(platform.centre_cm[1])],
        "extent_m": [cm_to_m(platform.extent_cm[0]), cm_to_m(platform.extent_cm[1])],
        # The premise, per platform rather than averaged: the share of this platform's
        # foundation pieces that landed within epsilon of one of its own bands.
        "clean": round(platform.clean, 4),
        # Naming only. Neither took any part in deciding where the floors are.
        "label": platform.label,
        "slab": platform.slab,
        "bands": [_band_json(b) for b in platform.bands],
    }


def _deck_json(deck: ffloors.Deck | None) -> FloorDeck | None:
    if deck is None:
        return None
    return {"platform": deck.platform, "ordinal": deck.ordinal, "top_m": cm_to_m(deck.top_cm)}


def _run_json(run: ffloors.Run) -> FloorRun:
    """One belt chain or one pipe, keyed by the join a client already has.

    For a belt that is ``chain``, the field ``/api/belts`` puts on every piece. For a pipe
    it is the row's position in ``/api/pipes``, which is the same positional key
    ``domain.world.flow`` uses to attach a direction. Neither carries the polyline again.
    """
    return {
        "kind": run.kind,
        "key": run.key,
        "pieces": run.pieces,
        "lift": run.lift,
        "rise_m": cm_to_m(run.rise_cm),
        # Tall enough that it can only be a floor connector, which is a different claim
        # from being a lift: a quarter of lift chains are belt-height jogs on one deck.
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
    """What is built, one storey at a time: the floor decomposition of a world.

    Nothing in the save says "floor". ``domain.factories.floors`` recovers them from the
    geometry -- 4-connected platforms of 8 m foundation cells, then a per-platform cluster
    of deck heights -- and this endpoint parses the query, calls it once, and rounds.

    **It ships ids, not geometry.** A client already has every machine, splitter, belt and
    pipe from ``/api/machines``, ``/api/structures``, ``/api/belts`` and ``/api/pipes``; the
    one thing it cannot derive is which floor each of them is on. So a band lists
    ``machines`` and ``attachments`` as instance leaves, and a run is keyed by its belt
    ``chain`` or its pipe row position -- the joins those payloads already carry.

    **The runs are grouped by what they do to a floor**, not listed flat:

    * ``same-deck`` -- both ends over one band, and the set a floor filter draws.
    * ``connector`` -- the ends are on two different bands. This is how you leave a floor,
      and it is where the lifts and risers are.
    * ``terrain`` -- neither end is over a deck.
    * ``mixed`` -- one end on a deck, one on the ground.

    **``placements`` is only what did NOT land on a floor**, since what did is listed by id
    inside its own band. The three ways of not being on one: ``exempt`` (a miner stands on a
    resource node and a water extractor on water -- by native class, not by a substring),
    ``terrain`` (measured against the heightfield) and ``off-deck``.

    **``terrain_measured`` says whether the ground was consulted at all.** The 1 m
    heightfield is derived from the reader's own game install and most machines have none,
    in which case nothing can be in the ``terrain`` group and an empty one would otherwise
    read as "nothing is on the ground here".

    ``?factory=`` takes a label the player gave a factory, or any selector the MCP tools
    take; ``?platform=`` takes the index this endpoint hands out, which is stable across
    calls. Either narrows placements and runs to that footprint, including the ones
    underneath it, since "what is under this deck" is part of the question.

    A save too old to carry ``FGLightweightBuildableSubsystem`` is a **200 with a
    ``note``**, not an error and not an empty list: the world has floors, this file cannot
    show them, and those are different sentences.
    """
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)

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
        # A riser that lands both ends on one band cannot happen, so one here is a symptom
        # of the decomposition drifting and is reported rather than swallowed.
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
