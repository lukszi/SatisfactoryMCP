"""Occluder heights for the horizon pass: the top of every tree crown, on a render grid.

The result is the ``occluder`` argument of ``lighting.horizon``: world metres, ``nan`` where
no crown stands. Shadows stay out of the colour, so they follow whatever sun the viewer
picks. Why the crown has this shape: the README's "Horizons and tree shadows".
"""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.mesh import MeshBounds
from mapgen.gamedata.trees import TreeTable, crown_species, tree_table
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "CROWN_RIM",
    "canopy_top",
    "sheet_crowns",
    "sweep_trees",
]

#: The crown's rim, as a share of the tree's height; the dome rises from it to the top.
CROWN_RIM = 0.3


def sweep_trees(sweep: dict, store, scripts, index) -> TreeTable:
    """The tree table for a ``terrain.rasters.sweep_world`` result."""
    trees = sweep.get("trees", {})
    return tree_table(trees, crown_species(MeshBounds(store, scripts, index), sorted(trees)))


def canopy_top(
    trees: TreeTable, x0_m: float, y0_m: float, step_m: float, shape: tuple[int, int]
) -> np.ndarray:
    """Max crown-top Z per texel; texel ``(r, c)`` is centred on ``x0 + (c + 0.5) * step``."""
    rows, cols = shape
    out = np.full(shape, np.nan, np.float32)
    here = trees.within(x0_m, y0_m, x0_m + cols * step_m, y0_m + rows * step_m)
    rim = np.float32(CROWN_RIM)
    for x, y, base, height, radius in zip(
        here.x_m, here.y_m, here.base_m, here.height_m, here.radius_m, strict=True
    ):
        cx, cy = (x - x0_m) / step_m - 0.5, (y - y0_m) / step_m - 0.5
        reach = radius / step_m
        c0, c1 = max(int(np.floor(cx - reach)), 0), min(int(np.ceil(cx + reach)) + 1, cols)
        r0, r1 = max(int(np.floor(cy - reach)), 0), min(int(np.ceil(cy + reach)) + 1, rows)
        if c0 >= c1 or r0 >= r1:
            continue
        dx = (np.arange(c0, c1, dtype=np.float32) - cx) / reach
        dy = (np.arange(r0, r1, dtype=np.float32) - cy) / reach
        inside = 1.0 - dy[:, None] ** 2 - dx[None, :] ** 2
        dome = base + height * (rim + (1 - rim) * np.sqrt(np.maximum(inside, 0.0)))
        dome = np.where(inside >= 0, dome, np.nan).astype(np.float32)
        out[r0:r1, c0:c1] = np.fmax(out[r0:r1, c0:c1], dome)
    return out


def _at(running: np.ndarray, x: np.ndarray, axis: int) -> np.ndarray:
    """A running sum read at fractional corner positions ``x`` along ``axis``, linearly."""
    i = np.minimum(np.floor(x).astype(np.int64), running.shape[axis] - 2)
    t = (x - i).astype(np.float64)
    lo, hi = np.take(running, i, axis), np.take(running, i + 1, axis)
    t = t[:, None] if axis == 0 else t
    return lo * (1.0 - t) + hi * t


def _running(a: np.ndarray, axis: int) -> np.ndarray:
    pad = [(0, 0), (0, 0)]
    pad[axis] = (1, 0)
    return np.pad(np.cumsum(a, axis=axis, dtype=np.float64), pad)


def _box(slab, rows, cols) -> np.ndarray:
    """Each sheet pixel's sum over its box of ``slab``: corners ``(lo, hi)`` per axis."""
    (r_lo, r_hi), (c_lo, c_hi) = rows, cols
    across = _running(np.asarray(slab, np.float64), 1)
    down = _running(_at(across, c_hi, 1) - _at(across, c_lo, 1), 0)
    return _at(down, r_hi, 0) - _at(down, r_lo, 0)


def _corners(position: np.ndarray, width: float, n: int):
    """The texel-edge coordinates of a box ``width`` wide on each centre, inside the plane."""
    edge = position + 0.5
    return np.clip(edge - width / 2, 0, n), np.clip(edge + width / 2, 0, n)


def sheet_crowns(top_dm, grid: dict, size: int, out: np.ndarray, cover=None) -> np.ndarray:
    """The paint store's 1 m crown-top plane on a ``size`` px sheet, into ``out``.

    World metres: the mean crown top over the share of the pixel that crowns cover, ``nan``
    where none does. ``cover`` receives that share as a byte. Each pixel averages a box of
    its own width, or of one texel on a sheet finer than the plane, which is the bilinear
    sample: a crown keeps its area and its round edge, and a small one casts a small shadow.
    """
    step_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    grid_m = grid["spacing_cm"] / 100.0
    width = max(step_m / grid_m, 1.0)
    n_rows, n_cols = top_dm.shape
    centre = (np.arange(size) + 0.5) * step_m
    cols = _corners((BOUNDS_M["x_min_m"] + centre - grid["x0_cm"] / 100.0) / grid_m, width, n_cols)
    rows = (BOUNDS_M["y_min_m"] + centre - grid["y0_cm"] / 100.0) / grid_m
    area = width * width
    for start in range(0, size, 256):
        band = slice(start, start + 256)
        corners = _corners(rows[band], width, n_rows)
        lo = min(int(np.floor(corners[0].min())), n_rows - 1)
        dm = np.asarray(top_dm[lo : max(int(np.ceil(corners[1].max())), lo + 1)])
        have = dm != hf.NODATA
        local = (corners[0] - lo, corners[1] - lo)
        share = _box(have, local, cols) / area
        mean = _box(np.where(have, dm / hf.DM_PER_M, 0.0), local, cols) / area
        seen = share >= 0.5 / 255.0
        out[band] = np.where(seen, mean / np.maximum(share, 1e-9), np.nan)
        if cover is not None:
            cover[band] = np.where(seen, np.round(np.clip(share, 0.0, 1.0) * 255.0), 0)
    return out
