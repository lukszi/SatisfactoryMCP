"""``/api/storage``: every container and fluid buffer, and what is inside each one.

Two row models told apart by ``kind``, each declared whole; why, and what each field means:
docs/web-wire.md "Storage". Handler names are operation_ids.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.saveio.schema import StorageRecord
from .....domain.world.state import WorldState
from ...serial import StoredItem, contents_json, placement_fields, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


class StorageSolid(TypedDict):
    """A storage container: what is in it, and how much of the box that is.

    The coordinates are null where the transform did not decode, ``w_m``/``l_m`` for the
    HUB's and the Blueprint Designer's containers, which the dump gives no clearance, and
    ``slots`` where the projection wrote none. ``more`` is always 0 from this server, which
    sends every box whole.
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

    ``fluid`` and ``fluid_name`` are null for a buffer no pipe network claims, ``stored_m3``
    where the save's float would not read, ``capacity_m3`` for a class the dump does not
    carry, and ``fill`` whenever either of those two is missing.
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

    ``filled`` and ``items_total`` count the solid rows only.
    """

    storage: list[StorageSolid | StorageFluid]
    count: int
    filled: int
    items_total: int


def _storage_row(st: WorldState, row: StorageRecord) -> StorageSolid | StorageFluid:
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
    """Every storage container and fluid buffer the player built, and what is inside each
    one; never the splitters and mergers, whose few items are in transit."""
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
