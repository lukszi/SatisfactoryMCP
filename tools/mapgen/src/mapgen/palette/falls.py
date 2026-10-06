"""Waterfalls in the colour step: a foam streak at the lip and a foam ring where it lands.

Drawn over the finished colour, raise-only: a pixel only ever moves towards the foam colour
and is never darkened. See docs/spatial-and-map.md section 35.
"""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.water.falls import falls_input
from mapgen.palette.shore import OCEAN_LEVEL_M
from mapgen.palette.styles import PAINTED_PALETTE, SATELLITE_PALETTE
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["FALL_STYLES", "MIN_DROP_M", "draw_falls", "load_falls", "prepare_falls"]

FALL_STYLES = {"satellite": SATELLITE_PALETTE.get("falls"), "painted": PAINTED_PALETTE.get("falls")}

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


def _surface_at(field, x_m, y_m) -> tuple[np.ndarray, np.ndarray]:
    """The field's ground and water surface at world points, ``nan`` where it has none."""
    col = np.round((x_m * 100 - field.x0_cm) / field.spacing_cm).astype(np.int64)
    row = np.round((y_m * 100 - field.y0_cm) / field.spacing_cm).astype(np.int64)
    on = (col >= 0) & (col < field.width) & (row >= 0) & (row < field.height)
    col, row = np.clip(col, 0, field.width - 1), np.clip(row, 0, field.height - 1)
    ground = field._height_dm[row, col].astype(np.float64)
    water = field._water_raster()[row, col].astype(np.float64)
    ground = np.where(ground == hf.NODATA, np.nan, ground / hf.DM_PER_M)
    water = np.where(water == hf.NODATA, np.nan, water / hf.DM_PER_M)
    return np.where(on, ground, np.nan), np.where(on, water, np.nan)


def load_falls(cache_root, build, sweep_once, field) -> tuple[np.ndarray, dict]:
    """The drawable falls for this field, and the sidecar's ``waterfalls`` block."""
    records, source = falls_input(cache_root, build, sweep_once)
    falls = prepare_falls(records, field)
    source["waterfalls"]["drawable"] = len(falls)
    return falls, source


def prepare_falls(records: list[dict] | None, field) -> np.ndarray:
    """One row per drawable fall: x, y, lip z, along, out, half width, top, landing, base z.

    The landing is the lowest surface just out from the lip; the drop is measured to it, not
    to the bottom of the tool's curtain, which often runs on underground.
    """
    rows = []
    for f in records or ():
        x, y, z, (ux, uy), (nx, ny) = f["x"], f["y"], f["z"], f["along"], f["out"]
        half = f["width_m"] / 2
        ground, water = _surface_at(field, np.array([x]), np.array([y]))
        if z < OCEAN_LEVEL_M + SUBMERGED_M or water[0] > z + SUBMERGED_M:
            continue
        if not ground[0] <= z + BURIED_M:
            continue
        t = np.repeat(_OUT_M, len(_ACROSS))
        s = np.tile(_ACROSS, len(_OUT_M)) * 2 * half
        z_out = np.fmax(*_surface_at(field, x + s * ux + t * nx, y + s * uy + t * ny))
        if np.isfinite(z_out).mean() < 0.5:
            continue
        base = max(float(np.nanmin(z_out)), z - f["height_m"])
        if z - base < MIN_DROP_M:
            continue
        splash = [p for p in f.get("splash", ()) if p[2] <= z]
        out_m = [(p[0] - x) * nx + (p[1] - y) * ny for p in splash]
        landing = float(np.median(out_m)) if out_m else 0.0
        rows.append((x, y, z, ux, uy, nx, ny, half, f["top_len_m"], max(landing, 0.0), base))
    return np.array(rows, np.float64).reshape(-1, 11)


def _step(value, edge):
    return np.clip(value / edge + 0.5, 0.0, 1.0)


def _fall_alpha(fall, cfg, xs, ys, surface, px_m):
    """Foam and mist opacity of one fall over a window of the band."""
    x, y, z, ux, uy, nx, ny, half, top_len, landing, base = fall
    drop = z - base
    edge = max(px_m, cfg["edge_m"])
    surface = np.where(np.isfinite(surface), surface, z)
    dx, dy = xs[None, :] - x, ys[:, None] - y
    s, t = dx * ux + dy * uy, dx * nx + dy * ny
    k, lo, hi = cfg["spread"]
    spread = float(np.clip(k * drop, lo, hi))
    across = _step(half - np.abs(s), edge)
    along = _step(t + top_len, edge) * _step(spread - t, edge)
    upstream = cfg["top"] * np.clip(1.0 + t / max(top_len, 1e-6), 0.0, 1.0)
    fade = np.where(t < 0, upstream, 1.0 - (1.0 - cfg["streak_end"]) * np.clip(t / spread, 0, 1))
    on_cliff = np.clip((z + cfg["above_m"] - surface) / cfg["above_m"], 0.0, 1.0)
    strands = 1.0 - cfg["strands"] * (0.5 + 0.25 * (np.cos(s * 2.1) + np.cos(s * 0.77 + 1.3)))
    streak = cfg["streak"] * across * along * fade * on_cliff * strands
    k, lo, hi = cfg["pool_radius"]
    r = float(np.clip(k * drop, lo, hi))
    centre = max(landing, 0.5 * spread)
    d = np.hypot(np.maximum(np.abs(s) - half, 0.0), t - centre)
    width = cfg["ring_width"] * r
    rim = cfg["core"] + (1.0 - cfg["core"]) * np.exp(-(((d - r) / width) ** 2))
    pool_shape = rim * np.exp(-((np.maximum(d - r, 0.0) / width) ** 2))
    low = np.clip((z - surface) / (cfg["pool_below"] * drop) - 1.0, 0.0, 1.0)
    pool = cfg["pool"] * pool_shape * low
    mist = cfg["mist"] * np.exp(-((d / (cfg["mist_reach"] * r)) ** 2)) * low
    return np.maximum(streak, pool), mist


def draw_falls(rgb, falls, layer, x_cm, y_cm, surface_m, px_m) -> np.ndarray:
    """``rgb`` (sRGB 0..255, rows ``y_cm`` by columns ``x_cm``) with the falls laid over it."""
    cfg = FALL_STYLES.get(layer)
    if not cfg or falls is None or not len(falls):
        return rgb
    xs, ys = np.asarray(x_cm, np.float64) / 100, np.asarray(y_cm, np.float64) / 100
    reach = falls[:, 7] + falls[:, 8] + cfg["spread"][2] + 3 * cfg["pool_radius"][2]
    near = (
        (falls[:, 0] + reach >= xs.min())
        & (falls[:, 0] - reach <= xs.max())
        & (falls[:, 1] + reach >= ys.min())
        & (falls[:, 1] - reach <= ys.max())
    )
    foam = np.asarray(cfg["foam"], np.float32)
    mist_rgb = np.asarray(cfg["mist_rgb"], np.float32)
    for fall, r in zip(falls[near], reach[near], strict=True):
        cols = np.flatnonzero(np.abs(xs - fall[0]) <= r)
        rows = np.flatnonzero(np.abs(ys - fall[1]) <= r)
        if not len(cols) or not len(rows):
            continue
        win = np.ix_(rows, cols)
        a, m = _fall_alpha(fall, cfg, xs[cols], ys[rows], surface_m[win], px_m)
        part = rgb[win]
        misty = part + m[..., None] * (mist_rgb - part)
        foamy = misty + a[..., None] * (foam - misty)
        rgb[win] = np.maximum(part, foamy)
    return rgb
