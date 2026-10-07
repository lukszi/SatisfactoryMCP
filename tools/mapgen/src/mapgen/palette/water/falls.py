"""Waterfalls in the colour step: a foam streak at the lip and a foam ring where it lands.

Drawn over the finished colour, raise-only: a pixel only ever moves towards the foam colour
and is never darkened. See docs/spatial-and-map.md section 35.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np

from mapgen.gamedata.level.sweep import Sweep
from mapgen.gamedata.water.falls import FallRecord, load_or_sweep_falls
from mapgen.palette.scene import FloatGrid, field_heights
from mapgen.palette.schema import FallsStyle
from mapgen.palette.styles import PAINTED_PALETTE, SATELLITE_PALETTE
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.core.arrays import F64Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "FALL_STYLES",
    "MIN_DROP_M",
    "draw_falls",
    "load_falls",
    "prepare_falls",
]

FALL_STYLES: dict[str, FallsStyle] = {
    "satellite": SATELLITE_PALETTE["falls"],
    "painted": PAINTED_PALETTE["falls"],
}

#: A fall that drops less than this is a step in a river, not a waterfall.
MIN_DROP_M = 2.0

#: A lip this far under the field's surface is in a cave or under a rock.
BURIED_M = 4.0

#: A lip this far under standing water, or this close to the sea, is the ocean pouring off
#: the world's edge.
SUBMERGED_M = 1.0

#: Where the landing is looked for: metres out from the lip, and across it as a share of width.
_OUT_M = (0.5, 1.0, 2.0, 4.0, 7.0, 11.0)
_ACROSS = (-0.4, -0.2, 0.0, 0.2, 0.4)

#: The columns of a drawable fall's row (``prepare_falls``), in order: the lip, the unit
#: vectors along and out of it, its half width and top length, the landing and the base.
_X, _Y, _HALF_WIDTH, _TOP_LEN = 0, 1, 7, 8


def _surface_at(field: hf.Field, x_m: F64Grid, y_m: F64Grid) -> tuple[F64Grid, F64Grid]:
    """The field's ground and water surface at world points, ``nan`` where it has none."""
    col = np.round((x_m * 100 - field.x0_cm) / field.spacing_cm).astype(np.int64)
    row = np.round((y_m * 100 - field.y0_cm) / field.spacing_cm).astype(np.int64)
    on = (col >= 0) & (col < field.width) & (row >= 0) & (row < field.height)
    col, row = np.clip(col, 0, field.width - 1), np.clip(row, 0, field.height - 1)
    ground = field_heights(field)[row, col].astype(np.float64)
    level = field.water_raster()
    water = np.full(ground.shape, np.nan) if level is None else level[row, col].astype(np.float64)
    ground = np.where(ground == hf.NODATA, np.nan, ground / hf.DM_PER_M)
    water = np.where(water == hf.NODATA, np.nan, water / hf.DM_PER_M)
    return np.where(on, ground, np.nan), np.where(on, water, np.nan)


def load_falls(
    cache_root: Path, build: str | None, sweep_once: Callable[[], Sweep], field: hf.Field
) -> tuple[F64Grid, dict[str, JsonObject]]:
    """The drawable falls for this field, and the sidecar's ``waterfalls`` block."""
    records, source = load_or_sweep_falls(cache_root, build, sweep_once)
    # Stamped by its reader's version, or just swept: the records have the sweep's shape.
    falls = prepare_falls(cast(list[FallRecord], records), field)
    source["waterfalls"]["drawable"] = len(falls)
    return falls, source


def prepare_falls(records: list[FallRecord] | None, field: hf.Field) -> F64Grid:
    """One row per drawable fall: x, y, lip z, along, out, half width, top, landing, base z.

    The landing is the lowest surface just out from the lip; the drop is measured to it, not
    to the bottom of the tool's curtain, which often runs on underground.
    """
    rows: list[tuple[float, ...]] = []
    for fall in records or ():
        x, y, z, (ux, uy), (nx, ny) = fall["x"], fall["y"], fall["z"], fall["along"], fall["out"]
        half_m = fall["width_m"] / 2
        ground, water = _surface_at(field, np.array([x]), np.array([y]))
        if z < OCEAN_LEVEL_M + SUBMERGED_M or water[0] > z + SUBMERGED_M:
            continue
        if not ground[0] <= z + BURIED_M:
            continue
        out_m = np.repeat(_OUT_M, len(_ACROSS))
        across_m = np.tile(_ACROSS, len(_OUT_M)) * 2 * half_m
        x_m, y_m = x + across_m * ux + out_m * nx, y + across_m * uy + out_m * ny
        z_out = np.fmax(*_surface_at(field, x_m, y_m))
        if np.isfinite(z_out).mean() < 0.5:
            continue
        base = max(float(np.nanmin(z_out)), z - fall["height_m"])
        if z - base < MIN_DROP_M:
            continue
        splash = [point for point in fall.get("splash", ()) if point[2] <= z]
        splash_out_m = [(point[0] - x) * nx + (point[1] - y) * ny for point in splash]
        landing = float(np.median(splash_out_m)) if splash_out_m else 0.0
        top_len_m = fall["top_len_m"]
        rows.append((x, y, z, ux, uy, nx, ny, half_m, top_len_m, max(landing, 0.0), base))
    return np.array(rows, np.float64).reshape(-1, 11)


def _soft_step(value: F64Grid, edge: float) -> F64Grid:
    """0 to 1 as ``value`` crosses 0, antialiased over ``edge``."""
    return np.clip(value / edge + 0.5, 0.0, 1.0)


def _smooth_step(value: F64Grid, edge: float) -> F64Grid:
    """0 to 1 as ``value`` crosses 0, along a smoothstep over ``edge``."""
    t = _soft_step(value, edge)
    return t * t * (3.0 - 2.0 * t)


def _fall_alpha(
    fall: F64Grid, style: FallsStyle, xs: F64Grid, ys: F64Grid, surface: FloatGrid, px_m: float
) -> tuple[F64Grid, F64Grid]:
    """Foam and mist opacity of one fall over a window of the band."""
    x, y, z, ux, uy, nx, ny, half_m, top_len, landing, base = (float(value) for value in fall)
    drop = z - base
    edge = max(px_m, style["edge_m"])
    surface = np.where(np.isfinite(surface), surface, z)
    dx, dy = xs[None, :] - x, ys[:, None] - y
    across_m, out_m = dx * ux + dy * uy, dx * nx + dy * ny
    spread_gain, spread_lo, spread_hi = style["spread"]
    spread = float(np.clip(spread_gain * drop, spread_lo, spread_hi))
    soft = style["soft"]
    across = _smooth_step(half_m - np.abs(across_m), max(edge, soft * half_m))
    end = _smooth_step(spread - out_m, max(edge, soft * spread))
    along = _soft_step(out_m + top_len, edge) * end
    upstream = style["top"] * np.clip(1.0 + out_m / max(top_len, 1e-6), 0.0, 1.0)
    downstream = 1.0 - (1.0 - style["streak_end"]) * np.clip(out_m / spread, 0, 1)
    fade = np.where(out_m < 0, upstream, downstream)
    on_cliff = np.clip((z + style["above_m"] - surface) / style["above_m"], 0.0, 1.0)
    waves = np.cos(across_m * 2.1) + np.cos(across_m * 0.77 + 1.3)
    strands = 1.0 - style["strands"] * (0.5 + 0.25 * waves)
    streak = style["streak"] * across * along * fade * on_cliff * strands
    pool_gain, pool_lo, pool_hi = style["pool_radius"]
    pool_radius_m = float(np.clip(pool_gain * drop, pool_lo, pool_hi))
    centre = max(landing, 0.5 * spread)
    distance_m = np.hypot(np.maximum(np.abs(across_m) - half_m, 0.0), out_m - centre)
    width = style["ring_width"] * pool_radius_m
    ring = np.exp(-(((distance_m - pool_radius_m) / width) ** 2))
    rim = style["core"] + (1.0 - style["core"]) * ring
    pool_shape = rim * np.exp(-((np.maximum(distance_m - pool_radius_m, 0.0) / width) ** 2))
    low = np.clip((z - surface) / (style["pool_below"] * drop) - 1.0, 0.0, 1.0)
    pool = style["pool"] * pool_shape * low
    reach = style["mist_reach"] * pool_radius_m
    mist = style["mist"] * np.exp(-((distance_m / reach) ** 2)) * low
    return np.maximum(streak, pool), mist


def draw_falls(
    rgb: FloatGrid,
    falls: F64Grid | None,
    layer: str,
    x_cm: FloatGrid,
    y_cm: FloatGrid,
    surface_m: FloatGrid,
    px_m: float,
    hidden: Callable[[], FloatGrid | None] | None = None,
) -> FloatGrid:
    """``rgb`` (sRGB 0..255, rows ``y_cm`` by columns ``x_cm``) with the falls laid over it.

    ``hidden``, asked only when a fall comes near, gives how much of each pixel what the
    style draws over the ground hides, such as the crowns; the foam and mist go under it.
    """
    style = FALL_STYLES.get(layer)
    if not style or falls is None or not len(falls):
        return rgb
    xs, ys = np.asarray(x_cm, np.float64) / 100, np.asarray(y_cm, np.float64) / 100
    reach = (
        falls[:, _HALF_WIDTH] * (1.0 + style["soft"]) + falls[:, _TOP_LEN] + style["spread"][2]
        + 3 * style["pool_radius"][2]
    )  # fmt: skip
    near = (
        (falls[:, _X] + reach >= xs.min())
        & (falls[:, _X] - reach <= xs.max())
        & (falls[:, _Y] + reach >= ys.min())
        & (falls[:, _Y] - reach <= ys.max())
    )
    foam = np.asarray(style["foam"], np.float32)
    mist_rgb = np.asarray(style["mist_rgb"], np.float32)
    over = hidden() if hidden is not None and near.any() else None
    for fall, fall_reach in zip(falls[near], reach[near], strict=True):
        cols = np.flatnonzero(np.abs(xs - fall[_X]) <= fall_reach)
        rows = np.flatnonzero(np.abs(ys - fall[_Y]) <= fall_reach)
        if not len(cols) or not len(rows):
            continue
        window = np.ix_(rows, cols)
        foam_alpha, mist_alpha = _fall_alpha(
            fall, style, xs[cols], ys[rows], surface_m[window], px_m
        )
        if over is not None:
            seen = 1.0 - np.clip(over[window], 0.0, 1.0)
            foam_alpha, mist_alpha = foam_alpha * seen, mist_alpha * seen
        part = rgb[window]
        misty = part + mist_alpha[..., None] * (mist_rgb - part)
        foamy = misty + foam_alpha[..., None] * (foam - misty)
        rgb[window] = np.maximum(part, foamy)
    return rgb
