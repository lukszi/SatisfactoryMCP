"""``/api/storage``: every container and fluid buffer, and what is inside each one.

The endpoint that answers "where did I put the steel" rather than "how much steel have I
got", which is what the projection's inventory totals already answer.

**TWO ROW MODELS, NOT ONE WITH OPTIONAL HALVES.** ``_storage_row`` builds a common prefix
and then ``update``s it with one of two tails: the other side's fields are ABSENT rather
than null, because a container does not have an empty fluid level -- it has no fluid level.
A single TypedDict with ``total=False`` on the tails would describe that and also DESTROY
it: pydantic serialises in declaration order and drops absent keys, so a solid row validated
against a model declaring the fluid tail first comes back with its own five fields RE-KEYED
into the gaps the missing ones left. So each variant is declared whole, in its own emission
order, and ``kind`` is the discriminator a reader branches on.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....domain.world.state import WorldState
from ..serial import contents_json, placement_fields, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


# -------------------------------------------------------------------- storage


class StoredItem(TypedDict):
    """One kind of thing in a container, resolved to a display name by the server."""

    cls: str
    name: str
    count: int


class StorageSolid(TypedDict):
    """A storage container: what is in it, and how much of the box that is.

    The nine fields above ``kind`` are the prefix ``_storage_row`` builds first; the five
    below are its solid tail. Both halves are spelled out here rather than inherited from a
    shared base with ``StorageFluid``, because inheritance decides field order somewhere
    other than where the emission is.

    ``cls`` and ``name`` are not nullable: a container is an ACTOR record and its class is
    written out. The coordinates ARE nullable, because an actor whose transform did not
    decode has no ``pos``. ``w_m``/``l_m`` are null for the classes the docs dump carries no
    clearance for -- the HUB's built-in container and the Blueprint Designer's -- because a
    size invented here would arrive looking measured.

    ``slots`` is the inventory component's own slot count forwarded whole, and null rather
    than 0 for a row the projection wrote none for. ``more`` is always 0 from this server,
    which sends every box whole; the field stays because it is the row's own statement that
    nothing was left off, and because a client's "+N more" tile must keep working against a
    server that truncates.
    """

    instance_leaf: str
    cls: str
    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    yaw: float | None
    w_m: float | None
    l_m: float | None
    kind: Literal["solid"]
    items: list[StoredItem]
    more: int
    item_kinds: int
    total: int
    slots: int | None


class StorageFluid(TypedDict):
    """A fluid buffer: what is in it, how much it holds, and the fraction those two make.

    The same nine-field prefix as ``StorageSolid`` and then the fluid tail, declared whole
    for the reason the module docstring gives.

    ``fluid`` comes off the ``FGPipeNetwork`` that claims the buffer rather than off the
    buffer itself, so it is null for a buffer no network claims and ``fluid_name`` with it.
    ``stored_m3`` is null where the ``mFluidBox`` float would not read; ``capacity_m3`` is
    the docs dump's ``mStorageCapacity`` and null for a class the dump does not carry; and
    ``fill`` is the two divided, REFUSING rather than dividing by a missing one of them --
    which is why all three are nullable independently.
    """

    instance_leaf: str
    cls: str
    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    yaw: float | None
    w_m: float | None
    l_m: float | None
    kind: Literal["fluid"]
    fluid: str | None
    fluid_name: str | None
    stored_m3: float | None
    capacity_m3: float | None
    fill: float | None


class StorageResponse(TypedDict):
    """What ``/api/storage`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``filled`` and ``items_total`` are about the SOLID rows only: a fluid buffer has no item
    count to add.
    """

    storage: list[StorageSolid | StorageFluid]
    count: int
    filled: int
    items_total: int


def _storage_row(st: WorldState, row: dict) -> StorageSolid | StorageFluid:
    """One container or fluid buffer: where it stands, how big it is, and what is in it."""
    if "stored_m3" not in row:
        return {**placement_fields(st.game, row), "kind": "solid", **contents_json(st.game, row)}
    building = st.game.buildings.get(row.get("cls") or "")
    fluid = row.get("fluid")
    # A buffer stores a bare float of cubic metres; the class's capacity makes it a reading.
    capacity = getattr(building, "storage_capacity_m3", 0.0) if building else 0.0
    stored = row.get("stored_m3")
    return {
        **placement_fields(st.game, row),
        "kind": "fluid",
        "fluid": fluid,
        "fluid_name": st.game.item_name(fluid) if fluid else None,
        "stored_m3": stored,
        "capacity_m3": round(capacity, 1) if capacity else None,
        "fill": (
            round(float(stored) / capacity, 4)
            if capacity and isinstance(stored, (int, float))
            else None
        ),
    }


@router.get("/storage", response_model=StorageResponse)
def storage(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every storage container and fluid buffer, and what is inside each one.

    The containers the player built: Storage Containers and Industrial ones, Personal Storage
    Boxes, Dimensional Depot uploaders, the HUB's built-in container and the Blueprint
    Designer's, and the fluid buffers. **NOT the splitters and mergers** -- every one of them
    owns a component literally named ``StorageInventory``, holding the one to three items
    physically inside the junction, so a payload built by matching that name would report
    hundreds of phantom containers, draw them a second time over the belt layer that already
    has them, and count items in transit as stock. Machine input and output buffers are
    excluded on the same principle, and are on their own machine's row under ``buffers``,
    where they mean "this smelter is starved" rather than "the player owns this".

    **Two record shapes, told apart by ``kind``.** A solid container reports ``items``
    (biggest first, resolved to display names, and the whole box), ``slots`` and ``total``; a
    fluid buffer reports ``fluid``, ``stored_m3``, ``capacity_m3`` and ``fill``.

    **The fluid's identity comes off the plumbing, not off the buffer.** A buffer stores a
    bare ``mFluidBox`` float and never names its contents, so the name is taken from the
    ``FGPipeNetwork`` that claims it -- the same join ``/api/pipes`` uses -- and is ``null``
    for a buffer no network claims.

    Sent in one payload, ungrouped, the posture every placement endpoint here takes.
    """
    st = require_world(request, save, world)

    rows = [
        _storage_row(st, row) for row in st.projection.get("storage") or () if isinstance(row, dict)
    ]
    solids = [r for r in rows if r["kind"] == "solid"]
    return {
        "storage": rows,
        "count": len(rows),
        "filled": sum(1 for r in solids if r["total"]),
        "items_total": sum(r["total"] for r in solids),
    }
