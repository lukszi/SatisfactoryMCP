"""``/api/map-markers``: the markers the player placed on the in-game map.

Handler names are operation_ids; wire rules: docs/web-wire.md "Map markers".
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.saveio.schema import MapMarkerRecord
from ...serial import object_rows, require_world, xyz_m

__all__ = ["router"]

router = APIRouter(prefix="/api")


class MapMarkerRow(TypedDict):
    """One in-game map marker.

    ``colour`` is the marker's own colour as ``#rrggbb`` sRGB, converted from the save's linear
    value, and null where the save's would not read. ``icon_id`` is the game's icon library id,
    ``type`` the representation (``RT_MapMarker`` for a marker the player placed) and
    ``compass`` how far away it shows on the compass (``CVD_Near``, ``CVD_Mid``, ``CVD_Far``).
    """

    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    colour: str | None
    icon_id: int | None
    type: str | None
    category: str
    compass: str | None


class MapMarkersResponse(TypedDict):
    """What ``/api/map-markers`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    markers: list[MapMarkerRow]
    count: int


def _srgb_channel(linear: float) -> int:
    """One linear channel as an 8-bit sRGB value, the standard transfer function."""
    c = min(1.0, max(0.0, linear))
    encoded = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return round(encoded * 255)


def srgb_hex(colour: Sequence[float] | None) -> str | None:
    """A linear ``[r, g, b, a]`` as ``#rrggbb``; alpha is dropped, as the game draws it."""
    if not colour or len(colour) < 3:
        return None
    return "#" + "".join(f"{_srgb_channel(float(c)):02x}" for c in colour[:3])


def _marker_row(row: MapMarkerRecord) -> MapMarkerRow:
    return {
        "name": row.get("name") or "",
        **xyz_m(row.get("pos")),
        "colour": srgb_hex(row.get("colour")),
        "icon_id": row.get("icon_id"),
        "type": row.get("type"),
        "category": row.get("category") or "",
        "compass": row.get("compass"),
    }


@router.get("/map-markers", response_model=MapMarkersResponse)
def map_markers(
    request: Request, save: str | None = None, world: str | None = None
) -> MapMarkersResponse:
    """Every marker the player placed on the in-game map, in the save's own order."""
    st = require_world(request, save, world)
    rows = [_marker_row(row) for row in object_rows(st.projection.get("map_markers"))]
    return {"markers": rows, "count": len(rows)}
