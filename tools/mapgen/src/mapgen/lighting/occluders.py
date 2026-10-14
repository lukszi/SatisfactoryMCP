"""Occluder heights for the horizon pass from the tree table's domes (``canopy_top``).

The result is the ``occluder`` argument of ``lighting.horizon``: world metres, ``nan`` where
no crown stands. Shadows stay out of the colour, so they follow whatever sun the viewer
picks. A render's light casts the crown sprites instead (``render/draw/tree_occluder.py``).
"""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.vegetation.trees import TreeTable
from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["CROWN_RIM", "canopy_top"]

#: The crown's rim, as a share of the tree's height; the dome rises from it to the top.
CROWN_RIM = 0.3


def canopy_top(
    trees: TreeTable, x0_m: float, y0_m: float, step_m: float, shape: tuple[int, int]
) -> F32Grid:
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
