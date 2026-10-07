"""The light's loops for numba: the horizon march and the sky view.

Each one reproduces its numpy reference in ``horizon`` bit for bit: per pixel the same float32
operations in the same order, with every step's offset, fraction and scale worked out by that
numpy code beforehand. A row at a time, so each inner loop is a plain run over contiguous
memory that the compiler vectorises. Imported only when ``mapgen.jit.kernels_on()``.
docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.jit import helper, kernel
from mapgen.terrain.kernels import raise_nan
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid

__all__ = ["Offsets", "bilinear_row", "march", "raise_row", "sky_view"]

_ZERO = np.float32(0.0)
_ONE = np.float32(1.0)

#: Per step: rows and columns to the sample, then ``_bilinear``'s fractions ``fy, fx`` and
#: their complements ``1 - fy, 1 - fx`` (zeros and ones for a nearest step).
Offsets: TypeAlias = tuple[I64Grid, I64Grid, F32Grid, F32Grid, F32Grid, F32Grid]

_Row: TypeAlias = NDArray[np.floating]


@helper
def bilinear_row(
    out: _Row,
    z: _Row,
    r: int,
    c: int,
    fy: np.float32,
    fx: np.float32,
    gy: np.float32,
    gx: np.float32,
) -> None:
    """``horizon._bilinear`` along one row: ``z`` read from ``[r, c]`` down and right."""
    n = out.shape[0]
    a, b = z[r, c : c + n], z[r, c + 1 : c + 1 + n]
    d, e = z[r + 1, c : c + n], z[r + 1, c + 1 : c + 1 + n]
    for j in range(n):
        out[j] = (a[j] * gx + b[j] * fx) * gy + (d[j] * gx + e[j] * fx) * fy


@helper
def raise_row(top: _Row, near: _Row, sample: _Row, k: np.float32) -> None:
    """One step of ``horizon._march`` along a row: ``top = max(top, (sample - near) * k)``."""
    for j in range(top.shape[0]):
        top[j] = raise_nan(top[j], (sample[j] - near[j]) * k)


@kernel
def march(
    solid: F32Grid,
    z: F32Grid,
    halo: int,
    bilinear: BoolMask,
    offsets: Offsets,
    scale: F32Grid,
    best: F32Grid,
) -> None:
    """``horizon._march`` over every row of ``best``, which is raised in place.

    ``z`` holds the receivers, ``solid`` what blocks.
    """
    iy, ix, fy, fx, gy, gx = offsets
    rows, cols = best.shape
    sample = np.empty(cols, np.float32)
    for i in range(rows):
        r = halo + i
        near, top = z[r, halo : halo + cols], best[i]
        for s in range(scale.shape[0]):
            sr, sc = r + iy[s], halo + ix[s]
            if bilinear[s]:
                bilinear_row(sample, solid, sr, sc, fy[s], fx[s], gy[s], gx[s])
                raise_row(top, near, sample, scale[s])
            else:
                raise_row(top, near, solid[sr, sc : sc + cols], scale[s])


@kernel
def sky_view(z: F32Grid, halo: int, offsets: Offsets, scale: F32Grid, out: F32Grid) -> None:
    """``horizon.sky_view``'s loop over every row of ``out``: offsets are ``(dirs, steps)``."""
    iy, ix, fy, fx, gy, gx = offsets
    rows, cols = out.shape
    dirs, steps = iy.shape
    sample = np.empty(cols, np.float32)
    best = np.empty(cols, np.float32)
    acc = np.empty(cols, np.float32)
    for i in range(rows):
        r = halo + i
        near = z[r, halo : halo + cols]
        acc[:] = _ZERO
        for d in range(dirs):
            best[:] = _ZERO
            for s in range(steps):
                bilinear_row(
                    sample, z, r + iy[d, s], halo + ix[d, s], fy[d, s], fx[d, s], gy[d, s], gx[d, s]
                )
                raise_row(best, near, sample, scale[s])
            for j in range(cols):
                acc[j] += best[j] / np.sqrt(_ONE + best[j] * best[j])
        for j in range(cols):
            out[i, j] = _ONE - acc[j] / np.float32(dirs)
