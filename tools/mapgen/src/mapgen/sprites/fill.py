"""The sprite raster's per-sample fill, in numpy: the reference ``gpu.top_hits`` equals.

Every sample keeps the highest triangle over it whose mask passes there, the triangles taken
in index order and a later one winning only when strictly higher, so a tie keeps the first.
A triangle is visited over its integer bounds, which is exactly the set of samples a kernel
thread tests it at; each float32 operation is one IEEE operation, in the order ``fill.cu``
writes it. docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I32Grid, I64Grid, U8Grid

__all__ = [
    "ALPHA_CUT",
    "BOUNDS_WIDTH",
    "DU1",
    "DU2",
    "DV1",
    "DV2",
    "DZ1",
    "DZ2",
    "INV",
    "P0X",
    "P0Y",
    "Q1X",
    "Q1Y",
    "Q2X",
    "Q2Y",
    "SETUP_WIDTH",
    "SLOT",
    "U0",
    "V0",
    "WRAP",
    "Z0",
    "Hits",
    "Triangles",
    "mask_at",
    "taps",
    "top_hits",
]

#: A triangle's setup row: its first vertex and edges in samples, the reciprocal of their
#: cross product, and depth, u and v at the first vertex with their steps along each edge.
P0X, P0Y, Q1X, Q1Y, Q2X, Q2Y, INV = range(7)
Z0, DZ1, DZ2, U0, DU1, DU2, V0, DV1, DV2 = range(7, 16)
SETUP_WIDTH = 16
#: A bounds row: first and last column, first and last row, material slot, and whether the
#: triangle's UVs leave the texture, so its reads wrap.
SLOT, WRAP = 4, 5
BOUNDS_WIDTH = 6

#: A masked slot's leaf passes from here, a third of 255: the clip value of the game's masks.
ALPHA_CUT = np.float32(85.0)

_HALF = np.float32(0.5)
_ONE = np.float32(1.0)
_ZERO = np.float32(0.0)


@dataclass(frozen=True)
class Triangles:
    """The raster's triangles: ``setup`` (n, 16) float32, ``bounds`` (n, 5) int32, each
    one's index in the mesh, and the bins: each bin's first entry in ``bin_tris``, and the
    bins across a row."""

    setup: F32Grid
    bounds: I32Grid
    source: I64Grid
    bin_start: I32Grid
    bin_tris: I32Grid
    bins_across: int


@dataclass(frozen=True)
class Hits:
    """Per sample: the winning depth (-inf for none), triangle (-1), and its weights."""

    z: F32Grid
    tri: I32Grid
    b1: F32Grid
    b2: F32Grid


def taps(first: F32Grid, n: int, wrap: bool | BoolMask) -> tuple[I64Grid, I64Grid]:
    """The two texels a bilinear read takes along one axis from the floored coordinate
    ``first``: wrapped round ``n``, or held at the edges where ``wrap`` is false."""
    i = first.astype(np.int64)
    low = np.where(wrap, i % n, np.clip(i, 0, n - 1))
    high = np.where(wrap, (i + 1) % n, np.clip(i + 1, 0, n - 1))
    return low, high


def mask_at(texels: U8Grid, row: I32Grid, u: F32Grid, v: F32Grid, wrap: bool) -> F32Grid:
    """A slot's mask, bilinear, at UVs; ``row`` is its ``(offset, w, h, masked)``. A card
    whose UVs stay inside the texture holds its edges, so its border does not read the far
    side's texels; a tiled one wraps."""
    offset, w, h = int(row[0]), int(row[1]), int(row[2])
    fu = u * np.float32(w) - _HALF
    fv = v * np.float32(h) - _HALF
    x0, y0 = np.floor(fu), np.floor(fv)
    fx, fy = fu - x0, fv - y0
    gx, gy = _ONE - fx, _ONE - fy
    i0, i1 = taps(x0, w, wrap)
    j0, j1 = taps(y0, h, wrap)
    a00 = texels[offset + j0 * w + i0].astype(np.float32)
    a01 = texels[offset + j0 * w + i1].astype(np.float32)
    a10 = texels[offset + j1 * w + i0].astype(np.float32)
    a11 = texels[offset + j1 * w + i1].astype(np.float32)
    top = a00 * gx + a01 * fx
    bottom = a10 * gx + a11 * fx
    return np.asarray(top * gy + bottom * fy, np.float32)


def top_hits(tris: Triangles, texels: U8Grid, table: I32Grid, rows: int, cols: int) -> Hits:
    """Every sample's highest hit whose mask passes; ``table`` holds each slot's texture."""
    best = np.full((rows, cols), -np.inf, np.float32)
    which = np.full((rows, cols), -1, np.int32)
    w1 = np.zeros((rows, cols), np.float32)
    w2 = np.zeros((rows, cols), np.float32)
    for t in range(len(tris.setup)):
        s = tris.setup[t]
        c0, c1, r0, r1, slot, wrap = (int(b) for b in tris.bounds[t])
        px = np.arange(c0, c1 + 1).astype(np.float32) + _HALF
        py = np.arange(r0, r1 + 1).astype(np.float32) + _HALF
        dx = (px - s[P0X])[None, :]
        dy = (py - s[P0Y])[:, None]
        b1 = (dx * s[Q2Y] - dy * s[Q2X]) * s[INV]
        b2 = (dy * s[Q1X] - dx * s[Q1Y]) * s[INV]
        inside = (b1 >= _ZERO) & (b2 >= _ZERO) & (b1 + b2 <= _ONE)
        z = (s[Z0] + b1 * s[DZ1]) + b2 * s[DZ2]
        window = (slice(r0, r1 + 1), slice(c0, c1 + 1))
        take = inside & (z > best[window])
        if not take.any():
            continue
        if table[slot, 3]:
            at = np.nonzero(take)
            u = (s[U0] + b1[at] * s[DU1]) + b2[at] * s[DU2]
            v = (s[V0] + b1[at] * s[DV1]) + b2[at] * s[DV2]
            take[at] = mask_at(texels, table[slot], u, v, bool(wrap)) >= ALPHA_CUT
        best[window] = np.where(take, z, best[window])
        which[window] = np.where(take, np.int32(t), which[window])
        w1[window] = np.where(take, b1, w1[window])
        w2[window] = np.where(take, b2, w2[window])
    return Hits(best, which, w1, w2)
