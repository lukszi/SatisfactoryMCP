"""The parts of a sidecar the readers use, narrowed out of the JSON with one rule for all of them.

A value of the wrong JSON type raises ``TypeError`` and a malformed number ``ValueError``,
which every loader in this package reads as "no data here".
"""

from __future__ import annotations

from typing_extensions import TypedDict

from ....core.jsontypes import JsonObject, JsonValue, as_float, as_int, require_object

__all__ = [
    "TerrainGrid",
    "layer_accuracies",
    "pinned_build",
    "read_terrain_grid",
]

#: The cook's uint16 encoding, z_m = (raw - zero) / units_per_m + offset_m, for a sidecar
#: written before ``terrain_grid`` recorded it.
LANDSCAPE_ZERO = 32768.0
LANDSCAPE_UNITS_PER_M = 128.0
LANDSCAPE_OFFSET_M = 1.0


class TerrainGrid(TypedDict):
    """``meta.json``'s ``terrain_grid``, with its whole-texel offset into the field's grid."""

    width: int
    height: int
    x0_cm: float
    y0_cm: float
    spacing_cm: float
    zero: float
    units_per_m: float
    offset_m: float
    #: ``None`` when the two grids do not share vertices.
    col_off: int | None
    row_off: int | None


def pinned_build(meta: JsonObject) -> str | None:
    """The game build a generator recorded under ``sources.game``, or ``None``."""
    sources = meta.get("sources")
    game = sources.get("game") if isinstance(sources, dict) else None
    pinned = game.get("game_version_pinned") if isinstance(game, dict) else None
    return pinned if isinstance(pinned, str) else None


def layer_accuracies(provenance: JsonValue) -> dict[int, float | None]:
    """``accuracy_m`` per provenance code, from the sidecar's ``provenance`` block."""
    if not provenance:
        return {}
    accuracies: dict[int, float | None] = {}
    for key, entry in require_object(provenance).items():
        if not key.lstrip("-").isdigit():
            continue
        accuracy = require_object(entry).get("accuracy_m")
        accuracies[int(key)] = accuracy if isinstance(accuracy, (int, float)) else None
    return accuracies


def read_terrain_grid(
    raw: JsonValue, x0_cm: float, y0_cm: float, spacing_cm: float
) -> TerrainGrid | None:
    """``terrain_grid`` placed on the field's grid at ``x0_cm``/``y0_cm``, or ``None`` if unusable."""
    if not isinstance(raw, dict):
        return None
    try:
        width, height = as_int(raw["width"]), as_int(raw["height"])
        tx0_cm, ty0_cm = as_float(raw["x0_cm"]), as_float(raw["y0_cm"])
        tspacing_cm = as_float(raw["spacing_cm"])
        zero = as_float(raw.get("zero", LANDSCAPE_ZERO))
        units_per_m = as_float(raw.get("units_per_m", LANDSCAPE_UNITS_PER_M))
        offset_m = as_float(raw.get("offset_m", LANDSCAPE_OFFSET_M))
    except (KeyError, TypeError, ValueError):
        return None
    dc = (tx0_cm - x0_cm) / spacing_cm
    dr = (ty0_cm - y0_cm) / spacing_cm
    aligned = tspacing_cm == spacing_cm and dc == round(dc) and dr == round(dr)
    return TerrainGrid(
        width=width,
        height=height,
        x0_cm=tx0_cm,
        y0_cm=ty0_cm,
        spacing_cm=tspacing_cm,
        zero=zero,
        units_per_m=units_per_m,
        offset_m=offset_m,
        col_off=round(dc) if aligned else None,
        row_off=round(dr) if aligned else None,
    )
