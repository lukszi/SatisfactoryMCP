"""``/api/machines`` and ``/api/structures``: everything the player physically placed.

Actor records against interned rows, the line every placement layer's nullability follows:
docs/web-wire.md "Placements". Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.gamedata.footprint import FOUNDATION_M
from .....core.gamedata.model import pretty_class
from .....core.saveio import rows as saverows
from .....core.saveio.records import instance_leaf
from .....domain.factories import health
from .....domain.world.state import WorldState
from ...serial import (
    building_footprint,
    cm_to_m,
    placement_fields,
    require_world,
    yaw_deg,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")

#: The three actor lists ``/api/machines`` sends, in wire order.
MACHINE_KINDS = ("machines", "extractors", "generators")


# --------------------------------------------------------------------- helpers


class PlacementRow(TypedDict):
    """A machine, an extractor or a generator: one row shape, three layers.

    ``cls`` and ``name`` are never null: these are actor records. The coordinates are null
    where the transform did not decode, and ``yaw`` where the projection predates schema 12,
    which is not a facing of zero. ``clock`` is null with no overclock property (250% is
    ``2.5``) and ``recipe_name`` with no recipe. ``w_m``/``l_m``/``h_m`` go null together
    for a building with no clearance box (belts, pipes, rails, poles) or no dump entry.

    ``state`` is one of ``health.STATES``; ``paused`` is the save's own field beside it.
    ``uptime`` is the share of the machine's ~300 s window it spent producing, null with no
    monitor at all, which is not zero. ``actionable`` is ``state in health.ACTIONABLE``, so
    the map keeps no list of its own; ``factory`` is the label anchoring the machine, if any.
    """

    instance_leaf: str
    cls: str
    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    recipe: str | None
    recipe_name: str | None
    clock: float | None
    paused: bool
    state: str
    actionable: bool
    uptime: float | None
    yaw: float | None
    w_m: float | None
    l_m: float | None
    h_m: float | None
    factory: str | None


class MachinesResponse(TypedDict):
    """What ``/api/machines`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    machines: list[PlacementRow]
    extractors: list[PlacementRow]
    generators: list[PlacementRow]


class StructureRow(TypedDict):
    """One lightweight buildable: a foundation, a ramp, a wall, a catwalk.

    ``cls`` is null for a row whose class index is past the legend's end. The coordinates
    never are: a row that will not read as three numbers is dropped. ``yaw`` is null for a
    schema-11 row, which has no fifth column, and for a rotation that will not decode.
    """

    cls: str | None
    x_m: float
    y_m: float
    z_m: float
    yaw: float | None


class StructuresResponse(TypedDict):
    """What ``/api/structures`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    structures: list[StructureRow]
    count: int
    tile_m: float


def _record_row(
    st: WorldState,
    row: dict,
    verdicts: dict[str, health.MachineHealth],
    owners: dict[str, str],
) -> PlacementRow:
    """One machine/extractor/generator, flattened for the map; extents per docs/web-wire.md."""
    leaf = instance_leaf(row.get("instance", ""))
    verdict = verdicts[leaf]
    footprint = building_footprint(st.game, row.get("cls") or "")
    recipe_id = row.get("recipe")
    recipe = st.game.recipes.get(recipe_id) if recipe_id else None
    return {
        **placement_fields(st.game, row),
        "recipe": recipe_id,
        "recipe_name": recipe.name if recipe else pretty_class(recipe_id),
        "clock": row.get("clock"),
        "paused": bool(row.get("paused", False)),
        "state": verdict.state,
        "actionable": verdict.state in health.ACTIONABLE,
        "uptime": None if verdict.uptime is None else round(verdict.uptime, 3),
        "h_m": round(footprint.height_m, 1) if footprint else None,
        "factory": owners.get(leaf),
    }


# ------------------------------------------------------------------- machines


@router.get("/machines", response_model=MachinesResponse)
def machines(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every placed machine, extractor and generator, with the reason it is or is not
    running."""
    st = require_world(request, save, world)
    projection = st.projection
    leaves = [
        instance_leaf(row.get("instance", ""))
        for kind in MACHINE_KINDS
        for row in projection.get(kind, ())
    ]
    # Total by construction: assess walks MACHINE_KINDS too, so the lookup cannot miss.
    assessed = health.assess("map", leaves, st.game, projection, st.graph)
    verdicts = {verdict.instance: verdict for verdict in assessed.machines}
    owners = {leaf: label.name for label in st.labels.labels for leaf in label.anchors}
    return {
        kind: [_record_row(st, row, verdicts, owners) for row in projection.get(kind, ())]
        for kind in MACHINE_KINDS
    }


# ----------------------------------------------------------------- structures


@router.get("/structures", response_model=StructuresResponse)
def structures(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every lightweight buildable the player placed: foundations, ramps, walls, catwalks,
    one row per piece at its centre, with the grid edge they are built on."""
    st = require_world(request, save, world)

    out = [
        {
            "cls": piece.cls,
            "x_m": cm_to_m(piece.x),
            "y_m": cm_to_m(piece.y),
            "z_m": cm_to_m(piece.z),
            "yaw": yaw_deg(piece.yaw),
        }
        for piece in saverows.iter_structures(st.projection)
    ]
    return {"structures": out, "count": len(out), "tile_m": FOUNDATION_M}
