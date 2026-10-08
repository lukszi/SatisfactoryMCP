"""Occluder heights for the horizon pass: the paint store's crown tops on a render grid
(``sheet_crowns``), and the tree table's domes (``canopy_top``).

The result is the ``occluder`` argument of ``lighting.horizon``: world metres, ``nan`` where
no crown stands. Shadows stay out of the colour, so they follow whatever sun the viewer
picks. Why the crown has this shape: the README's "Horizons and tree shadows".
"""

from __future__ import annotations

from typing import TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.vegetation.trees import TreeTable
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "CROWN_RIM",
    "UNDER_SCALE",
    "UNDER_TITAN",
    "CrownGrid",
    "canopy_top",
    "sheet_crowns",
]

#: The crown's rim, as a share of the tree's height; the dome rises from it to the top.
CROWN_RIM = 0.3

#: An underside byte: a crown's underside as a share of its top's height times
#: ``UNDER_SCALE``, or ``UNDER_TITAN`` where the top is a Titan tree's (``undersides``).
UNDER_SCALE = 254
UNDER_TITAN = 255

#: Sheet rows ``sheet_crowns`` reads the plane for at a time.
_BAND_ROWS = 256


class CrownGrid(TypedDict):
    """Where the paint store's crown plane lies: its texel size and its west and north edges."""

    spacing_cm: float
    x0_cm: float
    y0_cm: float


#: Float64 at run time; numpy's stubs only know they are floating.

#: A box's texel edges along one axis: where each sheet pixel's box starts, and where it ends.
_BoxEdges: TypeAlias = tuple[FloatGrid, FloatGrid]


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


def _running_sum_at(running: FloatGrid, x: FloatGrid, axis: int) -> FloatGrid:
    """A running sum read at fractional corner positions ``x`` along ``axis``, linearly."""
    i = np.minimum(np.floor(x).astype(np.int64), running.shape[axis] - 2)
    t = (x - i).astype(np.float64)
    lo, hi = np.take(running, i, axis), np.take(running, i + 1, axis)
    t = t[:, None] if axis == 0 else t
    return lo * (1.0 - t) + hi * t


def _running_sum(a: FloatGrid, axis: int) -> FloatGrid:
    pad = [(0, 0), (0, 0)]
    pad[axis] = (1, 0)
    return np.pad(np.cumsum(a, axis=axis, dtype=np.float64), pad)


def _box_sums(slab: NDArray[np.floating] | BoolMask, rows: _BoxEdges, cols: _BoxEdges) -> FloatGrid:
    """Each sheet pixel's sum over its box of ``slab``: texel edges ``(lo, hi)`` per axis."""
    (r_lo, r_hi), (c_lo, c_hi) = rows, cols
    across = _running_sum(np.asarray(slab, np.float64), 1)
    down = _running_sum(_running_sum_at(across, c_hi, 1) - _running_sum_at(across, c_lo, 1), 0)
    return _running_sum_at(down, r_hi, 0) - _running_sum_at(down, r_lo, 0)


def _box_edges(position: FloatGrid, width: float, n: int) -> _BoxEdges:
    """The texel-edge coordinates of a box ``width`` wide on each centre, inside the plane."""
    edge = position + 0.5
    return np.clip(edge - width / 2, 0, n), np.clip(edge + width / 2, 0, n)


def sheet_crowns(
    top_dm: NDArray[np.integer],
    grid: CrownGrid,
    size: int,
    out: F32Grid,
    cover: U8Grid | None = None,
    under: tuple[U8Grid, U8Grid] | None = None,
) -> F32Grid:
    """The paint store's 1 m crown-top plane on a ``size`` px sheet, into ``out``.

    World metres: the mean crown top over the share of the pixel that crowns cover, ``nan``
    where none does. ``cover`` receives that share as a byte. Each pixel averages a box of
    its own width, or of one texel on a sheet finer than the plane, which is the bilinear
    sample: a crown keeps its area and its round edge, and a small one casts a small shadow.
    ``under`` is the plane's underside bytes and the sheet's, which take their mean the same way.
    """
    step_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    grid_m = grid["spacing_cm"] / 100.0
    width = max(step_m / grid_m, 1.0)
    n_rows, n_cols = top_dm.shape
    centre = (np.arange(size) + 0.5) * step_m
    columns = (BOUNDS_M["x_min_m"] + centre - grid["x0_cm"] / 100.0) / grid_m
    cols = _box_edges(columns, width, n_cols)
    rows = (BOUNDS_M["y_min_m"] + centre - grid["y0_cm"] / 100.0) / grid_m
    area = width * width
    for start in range(0, size, _BAND_ROWS):
        band = slice(start, start + _BAND_ROWS)
        edges = _box_edges(rows[band], width, n_rows)
        lo = min(int(np.floor(edges[0].min())), n_rows - 1)
        dm = np.asarray(top_dm[lo : max(int(np.ceil(edges[1].max())), lo + 1)])
        have = dm != hf.NODATA
        local = (edges[0] - lo, edges[1] - lo)
        share = _box_sums(have, local, cols) / area
        mean = _box_sums(np.where(have, dm / hf.DM_PER_M, 0.0), local, cols) / area
        seen = share >= 0.5 / 255.0
        out[band] = np.where(seen, mean / np.maximum(share, 1e-9), np.nan)
        if cover is not None:
            cover[band] = np.where(seen, np.round(np.clip(share, 0.0, 1.0) * 255.0), 0)
        if under is not None:
            byte = np.where(have, under[0][lo : lo + dm.shape[0]], 0)
            low = _box_sums(byte, local, cols) / area / np.maximum(share, 1e-9)
            under[1][band] = np.where(seen, np.round(np.clip(low, 0.0, UNDER_SCALE)), 0)
    return out
