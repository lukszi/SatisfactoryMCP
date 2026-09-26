"""``/api/machines`` and ``/api/structures``: everything the player physically placed.

Two endpoints and one row builder, because they answer one question in two resolutions. A
machine is an ACTOR -- it has an instance id, a recipe, a clock -- and a foundation is a
lightweight buildable with none of those, interned into a positional table because a record
per piece would be megabytes.

**THE LINE THIS FILE DRAWS, which its neighbours cite:** an ACTOR record always carries a
class, and an INTERNED table row may not. ``machines``/``extractors``/``generators`` are
actor records, written behind ``cls.startswith("Build_")``, so ``cls`` is a non-empty string
and ``building_name`` never falls through to ``None``. ``structures`` is the interned table,
where a class is an INDEX into a legend and the row whose index points past the end is a real
piece at a real place with no name.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata.footprint import FOUNDATION_M
from ....core.gamedata.model import pretty_class
from ....core.saveio import rows as saverows
from ....domain.factories import health
from ....domain.world.state import WorldState
from ..serial import _fail, _m, _state, _xyz, _yaw

__all__ = ["router"]

router = APIRouter(prefix="/api")

#: The three actor lists ``/api/machines`` sends, in wire order.
MACHINE_KINDS = ("machines", "extractors", "generators")


# --------------------------------------------------------------------- helpers


class PlacementRow(TypedDict):
    """A machine, an extractor or a generator: one row shape, three layers.

    ``cls`` and ``name`` are not nullable: these are actor records, on the line the module
    docstring draws.

    ``x_m``/``y_m``/``z_m`` are nullable because an actor whose transform did not decode has
    no ``pos``. ``yaw`` null means the projection predates schema 12 and the facing was never
    recorded, which is a different claim from a facing of zero. ``clock`` is null for a
    machine with no overclock property, and a float because 250% is ``2.5``.
    ``recipe_name`` is null wherever there is no recipe.

    ``w_m``/``l_m``/``h_m`` go null TOGETHER -- one clearance box, read whole or not at all
    -- for the buildings whose ``mClearanceData`` yields no box (belts, pipes, rails, poles)
    and for any class the docs dump does not carry.

    ``state`` is one of ``health.STATES`` and never null; ``paused`` is the save's own field
    beside it, where ``state`` is a reading of the buffers. ``uptime`` is the fraction of the
    machine's own ~300 s window it spent producing, null for a building carrying no monitor
    at all -- a different claim from zero. ``actionable`` is ``state in health.ACTIONABLE``,
    sent so the map cannot keep its own list.
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


class MachinesResponse(TypedDict):
    """What ``/api/machines`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    machines: list[PlacementRow]
    extractors: list[PlacementRow]
    generators: list[PlacementRow]


class StructureRow(TypedDict):
    """One lightweight buildable: a foundation, a ramp, a wall, a catwalk.

    ``cls`` is nullable here and not on ``PlacementRow``: this is the interned table, on the
    line the module docstring draws.

    The three coordinates are NOT nullable, which is ``iter_structures``' refusal rather than
    this layer's -- a row whose x, y or z will not read as a number is dropped there.

    ``yaw`` is the one that survives being unreadable: ``null`` for a schema-11 row with no
    fifth column at all, and for the schema-16 rotation that will not decode.
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


def _leaf(row: dict) -> str:
    return str(row.get("instance", "")).rsplit(".", 1)[-1]


def _record_row(st: WorldState, row: dict, verdict: health.MachineHealth) -> PlacementRow:
    """One machine/extractor/generator, flattened for the map.

    ``w_m``/``l_m`` are the X and Y extent of the union of the building's clearance boxes,
    which is what makes a Manufacturer draw bigger than a Constructor; ``h_m`` is the third
    side, and it is here because a floor view needs it -- a Refinery is 15 m tall on a 12 m
    storey, so it comes through the deck above and is in the way of anything built there.
    Null rather than guessed for a class the docs dump does not describe: the client picks
    the fallback, because a fallback drawn here would be indistinguishable from a measurement.

    ``yaw`` is what turns those extents from an axis-aligned box into the rectangle the
    player actually placed, so the two are read together or not at all.
    """
    cls = row.get("cls") or ""
    building = st.game.buildings.get(cls)
    footprint = getattr(building, "footprint", None) if building else None
    recipe_id = row.get("recipe")
    recipe = st.game.recipes.get(recipe_id) if recipe_id else None
    return {
        "instance_leaf": _leaf(row),
        "cls": row.get("cls"),
        # Readable words either way; the raw class stays in ``cls`` for anything that needs
        # the exact id, and the same holds for the recipe below.
        "name": st.game.building_name(cls),
        **_xyz(row.get("pos")),
        "recipe": recipe_id,
        "recipe_name": recipe.name if recipe else pretty_class(recipe_id),
        "clock": row.get("clock"),
        "paused": bool(row.get("paused", False)),
        "state": verdict.state,
        "actionable": verdict.state in health.ACTIONABLE,
        # Three decimals: at two, 0.9994 rounds onto 1.0 and health.SATURATED's line vanishes.
        "uptime": None if verdict.uptime is None else round(verdict.uptime, 3),
        "yaw": _yaw(row.get("yaw")),
        # Footprint is already metres; the projection's coordinates are not.
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
        "h_m": round(footprint.height_m, 1) if footprint else None,
    }


# ------------------------------------------------------------------- machines


@router.get("/machines", response_model=MachinesResponse)
def machines(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every placed actor, with the reason it is or is not running.

    ``health.assess`` is asked once for the whole world rather than per row. Over the
    reference projection's 570 actors: 1.3 ms to build these rows without it, 2.5 ms with.

    195 of those 570 are ``blocked``: a full output box, and ``actionable`` like the other
    states in ``health.ACTIONABLE``. How the map marks it: docs/save-projection.md §6.2d.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    p = st.projection
    leaves = [_leaf(row) for kind in MACHINE_KINDS for row in p.get(kind, ())]
    # Total by construction -- assess walks MACHINE_KINDS too -- and keyed on the leaf
    # /api/floors and the frontend's `_floor.id` already join on, so the lookup cannot miss.
    verdicts = {m.instance: m for m in health.assess("map", leaves, st.game, p, st.graph).machines}
    return {
        kind: [_record_row(st, row, verdicts[_leaf(row)]) for row in p.get(kind, ())]
        for kind in MACHINE_KINDS
    }


# ----------------------------------------------------------------- structures


@router.get("/structures", response_model=StructuresResponse)
def structures(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every lightweight buildable the player placed: foundations, ramps, walls, catwalks.

    These are the only record of what was physically BUILT -- they appear in no actor
    header, which is why the projection interns them separately as
    ``{"classes": [...], "instances": [[class_index, x, y, z, yaw], ...]}`` in centimetres.
    Decoded by ``core.saveio.rows``, which is where the guard lives for all ten readers
    of these interned tables: a malformed row costs one piece, not the endpoint.

    **Rotation is carried, as of schema 12**, and this docstring used to say the opposite
    -- the instance transform's quaternion was dropped at extraction and a client could
    only draw these axis-aligned, which is why an angled slab came out of the map as a
    staircase of squares. ``yaw`` is now the fifth column of a row and comes out as a
    ``yaw`` field: degrees about world Z, positive turning +X towards +Y. ``null`` for a
    projection cut before 12, which a client must keep drawing axis-aligned rather than
    reading as zero.

    One thing the projection still does not carry, and it is not invented here:

    * **Per-class size.** None of these classes has clearance data, so ``footprint`` is
      ``None`` for all eighteen of them. They are all built on the same grid instead,
      whose edge ``tile_m`` reports from ``FOUNDATION_M`` so the page does not hardcode 8.

    Positions are piece centres: on the reference save consecutive foundations of one
    slab sit exactly ``tile_m`` apart.

    A world with nothing built answers ``{"structures": [], "count": 0}`` -- an empty
    list is a real answer here, unlike a save that could not be read at all.

    Sent one row per piece, ungrouped. Measured on the reference world -- 8,347 pieces,
    708 KB (610 KB of it before the yaw column) -- which is the same order as
    ``/api/collectibles`` already ships (3,455 rows, 547 KB). Grouping into grid cells would halve a
    payload that is not the bottleneck and would cost the per-piece class the popup and
    the point inspector read.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    out = [
        {
            "cls": piece.cls,
            "x_m": _m(piece.x),
            "y_m": _m(piece.y),
            "z_m": _m(piece.z),
            # Optional on purpose: a row from a schema-11 projection is four columns long
            # and is still a real piece at a real place, it just has no facing. ``None``
            # covers the schema-16 unreadable rotation too -- see ``saveio.rows``.
            "yaw": _yaw(piece.yaw),
        }
        for piece in saverows.iter_structures(st.projection)
    ]
    return {"structures": out, "count": len(out), "tile_m": FOUNDATION_M}
