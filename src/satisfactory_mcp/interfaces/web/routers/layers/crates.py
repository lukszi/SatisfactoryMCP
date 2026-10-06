"""``/api/crates``: the death and dismantle crates on the ground, and what is in each one.

A crate is an event rather than a place: it exists only from the moment somebody died or
dismantled with a full inventory until the moment it is emptied, which is why these rows
are not among the containers ``/api/storage`` lists.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.saveio.records import instance_leaf
from .....domain.world.inventory import CRATE_KIND_TEXT
from .....domain.world.state import WorldState
from ...serial import StoredItem, contents_json, require_world, xyz_m, yaw_deg

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- crates


class CrateRow(TypedDict):
    """One crate: what kind it is, where it is, and what is inside it.

    ``kind`` is a plain ``str`` and not a ``Literal``. The value comes from the projection,
    which is versioned and read off disk, so a projection cut by a later extractor that
    learned a fourth ``EFGCrateType`` is still served -- with the word it used, and
    ``kind_text`` null. A closed union would make that a 500 instead.
    """

    instance_leaf: str
    cls: str
    kind: str
    kind_text: str | None
    x_m: float | None
    y_m: float | None
    z_m: float | None
    yaw: float | None
    items: list[StoredItem]
    more: int
    item_kinds: int
    total: int
    slots: int | None


class CratesResponse(TypedDict):
    """The list and the three numbers a header wants.

    ``deaths`` is not a length of anything: a world whose crates all predate ``mCrateType``
    reports 0 deaths against a non-zero ``count``.
    """

    crates: list[CrateRow]
    count: int
    deaths: int
    items_total: int


def _crate_row(st: WorldState, row: dict) -> CrateRow:
    """One crate row, its contents resolved to display names and sent whole."""
    kind = str(row.get("kind") or "none")
    return {
        "instance_leaf": instance_leaf(row.get("instance", "")),
        "cls": row.get("cls"),
        "kind": kind,
        # ``.get``, not ``[]`` -- see ``CrateRow`` for the kind this build has not heard of.
        "kind_text": CRATE_KIND_TEXT.get(kind),
        **xyz_m(row.get("pos")),
        "yaw": yaw_deg(row.get("yaw")),
        **contents_json(st.game, row),
    }


@router.get("/crates", response_model=CratesResponse)
def crates(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every crate lying on the ground, what kind it is, and what is inside it.

    Whose crate it is, the save does not say: ``mCrateType`` is the actor's only saved
    property, so there is no owning player, no timestamp and no cause to report. Rows are
    sorted by kind, and contents are the whole crate.
    """
    st = require_world(request, save, world)

    rows = [
        _crate_row(st, row) for row in st.projection.get("crates") or () if isinstance(row, dict)
    ]
    return {
        "crates": rows,
        "count": len(rows),
        "deaths": sum(1 for r in rows if r["kind"] == "death"),
        "items_total": sum(r["total"] for r in rows),
    }
