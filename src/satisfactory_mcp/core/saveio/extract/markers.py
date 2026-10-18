"""The player's own map markers, off ``FGMapManager.mMapMarkers`` (``docs/save-projection.md``
§6.17)."""

from __future__ import annotations

from ..schema import MapMarkerRecord, Position
from .census import Drops
from .parser import SaveValue
from .readers import as_sequence, enum_value, optional_int, struct_fields, to_float

__all__ = ["map_markers"]


def _location(raw: SaveValue | None) -> Position | None:
    fields = struct_fields(raw)
    try:
        return [round(to_float(fields[axis]), 1) for axis in ("X", "Y", "Z")]
    except (KeyError, TypeError, ValueError):
        return None


def _colour(raw: SaveValue | None) -> list[float] | None:
    """A ``LinearColor`` as ``[r, g, b, a]``, linear and unconverted, or None."""
    try:
        values = [round(to_float(v), 4) for v in as_sequence(raw)]
    except (TypeError, ValueError):
        return None
    return values if len(values) == 4 else None


def map_markers(raw: SaveValue | None, drops: Drops) -> list[MapMarkerRecord]:
    """Every marker the player placed on the in-game map, in the save's own order.

    A marker whose location will not read costs only itself and is counted into ``warnings``.
    """
    out: list[MapMarkerRecord] = []
    for entry in as_sequence(raw) if isinstance(raw, list) else ():
        fields = struct_fields(entry)
        pos = _location(fields.get("Location"))
        if pos is None:
            drops["map marker(s) dropped: the location would not read"] += 1
            continue
        name = fields.get("Name")
        category = fields.get("CategoryName")
        try:
            icon = optional_int(fields.get("IconID"))
        except (TypeError, ValueError):
            icon = None
        out.append(
            {
                "name": name if isinstance(name, str) else "",
                "pos": pos,
                "colour": _colour(fields.get("Color")),
                "icon_id": icon,
                "type": enum_value(fields.get("MapMarkerType")),
                "category": category if isinstance(category, str) else "",
                "compass": enum_value(fields.get("CompassViewDistance")),
            }
        )
    return out
