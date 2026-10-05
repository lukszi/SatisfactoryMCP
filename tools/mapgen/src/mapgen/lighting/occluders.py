"""Occluder heights for the horizon pass: the top of every tree crown, on a render grid.

The result is the ``occluder`` argument of ``lighting.horizon``: world metres, ``nan`` where
no crown stands. Shadows stay out of the colour, so they follow whatever sun the viewer
picks. Why the crown has this shape: the README's "Horizons and tree shadows".
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

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


def sheet_crowns(top_dm: np.ndarray, grid: dict, size: int, out: np.ndarray) -> np.ndarray:
    """The paint store's 1 m crown-top plane on a ``size`` px sheet, into ``out``.

    World metres, ``nan`` where no crown stands. A sheet pixel coarser than the plane takes
    the highest crown in its footprint, so no crown drops out of the shadows.
    """
    step_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    grid_m = grid["spacing_cm"] / 100.0
    top = np.where(top_dm == hf.NODATA, -np.inf, top_dm / np.float32(hf.DM_PER_M))
    reach = int(np.ceil(step_m / grid_m))
    if reach > 1:
        top = ndimage.maximum_filter(top.astype(np.float32), size=reach)
    centre = (np.arange(size) + 0.5) * step_m
    cols = np.rint((BOUNDS_M["x_min_m"] + centre - grid["x0_cm"] / 100.0) / grid_m)
    rows = np.rint((BOUNDS_M["y_min_m"] + centre - grid["y0_cm"] / 100.0) / grid_m)
    col_ok = (cols >= 0) & (cols < top.shape[1])
    cols = np.clip(cols, 0, top.shape[1] - 1).astype(np.int64)
    for start in range(0, size, 512):
        band = slice(start, start + 512)
        row = rows[band]
        ok = ((row >= 0) & (row < top.shape[0]))[:, None] & col_ok[None, :]
        cut = top[np.clip(row, 0, top.shape[0] - 1).astype(np.int64)][:, cols]
        out[band] = np.where(ok & np.isfinite(cut), cut, np.nan)
    return out
