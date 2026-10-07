"""The span march and the sky view with spans, for numba.

Each reproduces its numpy reference in ``spans`` bit for bit: per pixel the same float32
operations in the same order, every step's offsets, fractions and scales worked out by that
numpy code beforehand. The ground's steps run a row at a time as ``kernels.march`` does; a row
with no span within reach skips the span work, which then changes nothing, and a step visits
only the columns ``spans.SpanRuns`` holds, where the work does something. Imported only when
``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.jit import helper, kernel
from mapgen.lighting.kernels import Offsets, bilinear_row, raise_nan, raise_row
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid

__all__ = ["march_spans", "sky_view_spans"]

_ZERO = np.float32(0.0)
_HALF = np.float32(0.5)
_ONE = np.float32(1.0)

#: ``spans.SpanSurface`` as the kernels take it: receivers, solid, underside, top, tops.
_Surface: TypeAlias = tuple[F32Grid, F32Grid, F32Grid, F32Grid, F32Grid]
#: ``spans.SpanRuns`` as a plain tuple.
_Runs: TypeAlias = tuple[I64Grid, I64Grid, I64Grid]


@helper
def _fmin(a: np.float32, b: np.float32) -> np.float32:
    """``np.fmin``: the other where one is NaN."""
    if np.isnan(a):
        return b
    if np.isnan(b):
        return a
    return min(a, b)


@helper
def _fmax(a: np.float32, b: np.float32) -> np.float32:
    """``np.fmax``: the other where one is NaN."""
    if np.isnan(a):
        return b
    if np.isnan(b):
        return a
    return max(a, b)


@helper
def _quad(p: F32Grid, r: int, c: int, lowest: bool) -> np.float32:
    """``spans._quad`` at one pixel: the four pixels from ``[r, c]`` down and right."""
    if lowest:
        return _fmin(_fmin(p[r, c], p[r, c + 1]), _fmin(p[r + 1, c], p[r + 1, c + 1]))
    return _fmax(_fmax(p[r, c], p[r, c + 1]), _fmax(p[r + 1, c], p[r + 1, c + 1]))


@helper
def _sample(z: F32Grid, r: int, c: int, smooth: bool, frac: NDArray[np.float32]) -> np.float32:
    """``horizon.bilinear`` or ``nearest`` at one pixel; ``frac`` is ``(fy, fx, gy, gx)``."""
    if not smooth:
        return z[r, c]
    fy, fx, gy, gx = frac[0], frac[1], frac[2], frac[3]
    return (z[r, c] * gx + z[r, c + 1] * fx) * gy + (z[r + 1, c] * gx + z[r + 1, c + 1] * fx) * fy


@helper
def _tangents(low: np.float32, high: np.float32, near: np.float32, near_m: np.float32,
              far_m: np.float32) -> tuple[np.float32, np.float32]:  # fmt: skip
    """``spans._tangents`` at one pixel."""
    dl, dh = low - near, high - near
    tl = dl / far_m if dl > 0 else dl / near_m
    th = dh / near_m if dh > 0 else dh / far_m
    return tl, th


@helper
def _distance(lo: np.float32, hi: np.float32, t: np.float32) -> np.float32:
    """``spans._distance`` at one pixel."""
    if t < lo:
        return lo - t
    if t > hi:
        return t - hi
    return _ZERO


@helper
def _near_spans(counts: I64Grid, r: int, reach: int) -> bool:
    """Whether a row within ``reach`` of row ``r`` holds a span."""
    top = min(r + 1 + reach, counts.shape[0] - 1)
    return counts[top] > counts[max(r - reach, 0)]


@helper
def _into_band(j: int, tl: np.float32, th: np.float32, weight: np.float32,
               band: tuple[F32Grid, F32Grid], target: tuple[np.float32, np.float32]) -> None:  # fmt: skip
    """``spans._span_step``'s band update for a sample that floats, at core column ``j``."""
    lo, hi = band
    te, gap = target
    centre, half = (tl + th) * _HALF, (th - tl) * _HALF * weight
    sl, sh = centre - half, centre + half
    have = np.isfinite(lo[j])
    if have and sl <= hi[j] + gap and sh >= lo[j] - gap:
        lo[j] = min(lo[j], sl)
        hi[j] = max(hi[j], sh)
    elif not have or _distance(sl, sh, te) < _distance(lo[j], hi[j], te):
        lo[j], hi[j] = sl, sh


@helper
def _run_columns(runs: _Runs, qr: int, qc: int, cols: int, run: int) -> tuple[int, int]:
    """Run ``run`` of quad row ``qr`` as core columns, clipped to ``[0, cols)``."""
    _row, start, end = runs
    return max(start[run] - qc, 0), min(end[run] - qc, cols)


@kernel
def march_spans(
    surface: _Surface, halo: int, smooth: BoolMask, offsets: Offsets, quads: tuple[I64Grid, I64Grid],
    per_step: F32Grid, target: tuple[np.float32, np.float32],
    out: tuple[F32Grid, F32Grid, F32Grid, BoolMask], rows: tuple[I64Grid, int], runs: _Runs,
) -> None:  # fmt: skip
    """``spans.march_spans``' loop over every row of the core; ``out`` is raised in place.

    ``per_step`` holds each step's scale, stretch near and far end, and fade weight;
    ``quads`` each step's whole-pixel offsets to the four pixels a span sample reads, and
    ``runs`` the columns where those hold a span, the only ones visited.
    """
    z, solid, lo_p, hi_p, tops = surface
    iy, ix, fy, fx, gy, gx = offsets
    qy, qx = quads
    best, band_lo, band_hi, seen = out
    counts, reach = rows
    cols = best.shape[1]
    sample = np.empty(cols, np.float32)
    frac = np.empty(4, np.float32)
    for i in range(best.shape[0]):
        r = halo + i
        near, top = z[r, halo : halo + cols], best[i]
        spans = _near_spans(counts, r, reach)
        for s in range(per_step.shape[0]):
            scale, near_m, far_m, weight = (
                per_step[s, 0],
                per_step[s, 1],
                per_step[s, 2],
                per_step[s, 3],
            )
            sr, sc = r + iy[s], halo + ix[s]
            if smooth[s]:
                bilinear_row(sample, solid, sr, sc, fy[s], fx[s], gy[s], gx[s])
                raise_row(top, near, sample, scale)
            else:
                raise_row(top, near, solid[sr, sc : sc + cols], scale)
            if not spans:
                continue
            frac[0], frac[1], frac[2], frac[3] = fy[s], fx[s], gy[s], gx[s]
            qr, qc = r + qy[s], halo + qx[s]
            for run in range(runs[0][qr], runs[0][qr + 1]):
                a, b = _run_columns(runs, qr, qc, cols, run)
                for j in range(a, b):
                    low = _quad(lo_p, qr, qc + j, True)
                    high = _quad(hi_p, qr, qc + j, False)
                    tl, th = _tangents(low, high, near[j], near_m, far_m)
                    seen[i, j] = True
                    if tl * weight <= top[j]:
                        exact = (_sample(tops, sr, sc + j, smooth[s], frac) - near[j]) * scale
                        top[j] = raise_nan(top[j], exact)
                    else:
                        _into_band(j, tl, th, weight, (band_lo[i], band_hi[i]), target)


@kernel
def sky_view_spans(
    surface: _Surface, halo: int, offsets: Offsets, quads: tuple[I64Grid, I64Grid],
    per_step: F32Grid, out: F32Grid, rows: tuple[I64Grid, int], runs: _Runs,
) -> None:  # fmt: skip
    """``spans.sky_view_spans``' loop over every row of ``out``: offsets are ``(dirs, steps)``,
    ``per_step`` each step's scale and stretch near and far end; ``runs`` as ``march_spans``."""
    z, solid, lo_p, hi_p, tops = surface
    iy, ix, fy, fx, gy, gx = offsets
    qy, qx = quads
    counts, reach = rows
    cols = out.shape[1]
    dirs, steps = iy.shape
    sample = np.empty(cols, np.float32)
    best = np.empty(cols, np.float32)
    lo = np.empty(cols, np.float32)
    hi = np.empty(cols, np.float32)
    acc = np.empty(cols, np.float32)
    frac = np.empty(4, np.float32)
    for i in range(out.shape[0]):
        r = halo + i
        near = z[r, halo : halo + cols]
        spans = _near_spans(counts, r, reach)
        acc[:] = _ZERO
        for d in range(dirs):
            best[:] = _ZERO
            lo[:] = np.float32(np.inf)
            hi[:] = np.float32(-np.inf)
            for s in range(steps):
                scale, near_m, far_m = per_step[s, 0], per_step[s, 1], per_step[s, 2]
                bilinear_row(sample, solid, r + iy[d, s], halo + ix[d, s],
                             fy[d, s], fx[d, s], gy[d, s], gx[d, s])  # fmt: skip
                raise_row(best, near, sample, scale)
                if not spans:
                    continue
                frac[0], frac[1], frac[2], frac[3] = fy[d, s], fx[d, s], gy[d, s], gx[d, s]
                qr, qc = r + qy[d, s], halo + qx[d, s]
                for run in range(runs[0][qr], runs[0][qr + 1]):
                    a, b = _run_columns(runs, qr, qc, cols, run)
                    for j in range(a, b):
                        low = _quad(lo_p, qr, qc + j, True)
                        high = _quad(hi_p, qr, qc + j, False)
                        tl, th = _tangents(low, high, near[j], near_m, far_m)
                        if tl <= best[j]:
                            exact = _sample(tops, r + iy[d, s], halo + ix[d, s] + j, True, frac)
                            best[j] = raise_nan(best[j], (exact - near[j]) * scale)
                        else:
                            lo[j] = min(lo[j], tl)
                            hi[j] = max(hi[j], th)
            for j in range(cols):
                banded = np.isfinite(lo[j])
                late = banded and lo[j] <= best[j]
                if late:
                    best[j] = raise_nan(best[j], hi[j])
                acc[j] += best[j] / np.sqrt(_ONE + best[j] * best[j])
                if banded and not late:
                    up, down = hi[j], lo[j]
                    acc[j] += up / np.sqrt(_ONE + up * up) - down / np.sqrt(_ONE + down * down)
        for j in range(cols):
            out[i, j] = _ONE - acc[j] / np.float32(dirs)
