"""FXAA on the arches: their edges antialiased in the drawn colour, nothing else touched.

FXAA 3.11 at quality settings (contrast test, edge direction, an end-of-edge search up to
``FXAA_REACH`` px, a sub-pixel blend), kept only inside the arches' coverage dilated by
``MASK_DILATE_PX``: rocks, crowns and the ground stay as drawn. A band is filtered with
``FXAA_HALO`` rows of its neighbours, so it is filtered as the whole sheet would be.
docs/map/light-and-crowns.md section 29, "FXAA on the arches".
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.terrain.archfill import column_pieces
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid, U8Grid

__all__ = [
    "FXAA_HALO",
    "FXAA_REACH",
    "MASK_DILATE_PX",
    "PIECE_MARGIN",
    "SUBPIX",
    "THRESHOLD",
    "THRESHOLD_MIN",
    "arch_fxaa",
    "arch_mask",
    "fxaa",
]

#: How far the end-of-edge search reads along an edge, and the rows a band reads past its
#: edges: the search, one pixel across it, and the 3 x 3 contrast test.
FXAA_REACH = 12
FXAA_HALO = FXAA_REACH + 2
#: The arches' coverage grown by this many pixels is where FXAA's answer is kept.
MASK_DILATE_PX = 3
#: Columns past an arch a band's filter reads: more than the search and the mask reach.
PIECE_MARGIN = FXAA_HALO + MASK_DILATE_PX

#: FXAA 3.11's contrast thresholds and sub-pixel strength (its "quality" preset).
THRESHOLD = np.float32(0.125)
THRESHOLD_MIN = np.float32(0.0625)
SUBPIX = np.float32(0.75)


def _luma(rgb: F32Grid) -> F32Grid:
    """Rec. 601 luma, summed in one fixed order."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (r * np.float32(0.299) + g * np.float32(0.587) + b * np.float32(0.114)).astype(
        np.float32
    )


def _disk(radius: int) -> BoolMask:
    y, x = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return (x * x + y * y) <= radius * radius


def arch_mask(cover: NDArray[np.generic]) -> BoolMask:
    """Where FXAA's answer is kept: the arches' coverage grown by ``MASK_DILATE_PX``."""
    covered: BoolMask = np.asarray(cover, np.uint8) > 0
    if not covered.any():
        return covered
    grown: BoolMask = ndimage.binary_dilation(covered, _disk(MASK_DILATE_PX))
    return grown


class _Edges:
    """The 3 x 3 neighbourhood of every pixel, edge-padded, and where the contrast test fires."""

    def __init__(self, lum: F32Grid) -> None:
        h, w = lum.shape
        padded = np.pad(lum, 1, mode="edge")
        self.at = {
            (dy, dx): padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
        }
        cross = [
            self.at[(0, 0)],
            self.at[(-1, 0)],
            self.at[(1, 0)],
            self.at[(0, -1)],
            self.at[(0, 1)],
        ]
        high = np.maximum.reduce(cross)
        self.range = high - np.minimum.reduce(cross)
        self.edge = self.range >= np.maximum(THRESHOLD_MIN, high * THRESHOLD)


def _search(
    lum: F32Grid,
    ys: I64Grid,
    xs: I64Grid,
    along: tuple[I64Grid, I64Grid],
    across: tuple[I64Grid, I64Grid],
    level: F32Grid,
    gradient: F32Grid,
) -> tuple[dict[int, F32Grid], dict[int, F32Grid]]:
    """The end-of-edge search both ways: how far each edge runs, and the luma where it ends."""
    h, w = lum.shape
    (ay, ax), (py, px) = along, across
    dist: dict[int, F32Grid] = {}
    end: dict[int, F32Grid] = {}
    for d in (-1, 1):
        k = np.zeros(len(ys), np.float32)
        done = np.zeros(len(ys), bool)
        found = np.zeros(len(ys), np.float32)
        for step in range(1, FXAA_REACH + 1):
            live = ~done
            if not live.any():
                break
            y0 = np.clip(ys + ay * d * step, 0, h - 1)
            x0 = np.clip(xs + ax * d * step, 0, w - 1)
            y1, x1 = np.clip(y0 + py, 0, h - 1), np.clip(x0 + px, 0, w - 1)
            value = np.float32(0.5) * (lum[y0, x0] + lum[y1, x1]) - level
            k[live] = step
            found[live] = value[live]
            done |= np.abs(value) >= gradient
        dist[d], end[d] = k, found
    return dist, end


def fxaa(rgb_u8: U8Grid, rows: slice) -> U8Grid:
    """FXAA 3.11 on ``rgb_u8``, answered for ``rows`` of it; the other rows are only read."""
    rgb = rgb_u8.astype(np.float32) * np.float32(1.0 / 255.0)
    lum = _luma(rgb)
    h, w = lum.shape
    found = _Edges(lum)
    edge = np.zeros(lum.shape, bool)
    edge[rows] = found.edge[rows]
    ys, xs = np.nonzero(edge)
    out = rgb[rows].copy()
    if len(ys):
        n9 = {key: plane[ys, xs] for key, plane in found.at.items()}
        m, n, s, west, east = n9[(0, 0)], n9[(-1, 0)], n9[(1, 0)], n9[(0, -1)], n9[(0, 1)]
        nw, ne, sw, se = n9[(-1, -1)], n9[(-1, 1)], n9[(1, -1)], n9[(1, 1)]
        average = ((n + s + west + east) * np.float32(2.0) + (nw + ne + sw + se)) * np.float32(
            1 / 12
        )
        contrast = np.clip(np.abs(average - m) / np.maximum(found.range[ys, xs], 1e-6), 0, 1)
        sub = ((np.float32(-2.0) * contrast + np.float32(3.0)) * contrast * contrast) ** 2 * SUBPIX
        horizontal = (
            np.abs(-2 * west + nw + sw) + 2 * np.abs(-2 * m + n + s) + np.abs(-2 * east + ne + se)
        ) >= (
            np.abs(-2 * n + nw + ne) + 2 * np.abs(-2 * m + west + east) + np.abs(-2 * s + sw + se)
        )
        l1, l2 = np.where(horizontal, n, west), np.where(horizontal, s, east)
        g1, g2 = l1 - m, l2 - m
        first = np.abs(g1) >= np.abs(g2)
        gradient = np.float32(0.25) * np.maximum(np.abs(g1), np.abs(g2))
        level = np.float32(0.5) * (np.where(first, l1, l2) + m)
        sign = np.where(first, -1, 1)
        along = (np.where(horizontal, 0, 1), np.where(horizontal, 1, 0))
        across = (np.where(horizontal, sign, 0), np.where(horizontal, 0, sign))
        dist, end = _search(lum, ys, xs, along, across, level, gradient)
        below = (m - level) < 0
        before, after = np.not_equal(end[-1] < 0, below), np.not_equal(end[1] < 0, below)
        good = np.where(dist[-1] < dist[1], before, after)
        offset = np.where(good, 0.5 - np.minimum(dist[-1], dist[1]) / (dist[-1] + dist[1]), 0.0)
        f = np.maximum(offset, sub).astype(np.float32)[:, None]
        oy = np.clip(ys + across[0], 0, h - 1)
        ox = np.clip(xs + across[1], 0, w - 1)
        out[ys - rows.start, xs] = rgb[ys, xs] * (1 - f) + rgb[oy, ox] * f
    return np.clip(out * np.float32(255.0) + np.float32(0.5), 0, 255).astype(np.uint8)


def arch_fxaa(rgb: U8Grid, cover: NDArray[np.generic], rows: slice) -> U8Grid:
    """``rows`` of ``rgb`` with FXAA kept inside the arches' grown coverage; ``cover`` is the
    arches' coverage over every row of ``rgb``, which holds ``FXAA_HALO`` rows of the band's
    neighbours either side where the sheet has them."""
    mask = arch_mask(cover)
    core = np.array(rgb[rows])
    keep = mask[rows]
    for c0, c1 in column_pieces(keep, PIECE_MARGIN):
        filtered = fxaa(np.ascontiguousarray(rgb[:, c0:c1]), rows)
        inside = keep[:, c0:c1]
        core[:, c0:c1][inside] = filtered[inside]
    return core
