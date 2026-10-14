"""A crown's form in the sun: the crown-top surface's slope over ``FORM_M``, lit against flat.

The sprite normals carry a crown's leaves, not its shape: a lobe's sunward side, the dip
between two crowns, the step down to a lower one. This reads that shape off the stamped crown
tops, the mean top over a box of half-width ``FORM_M`` either side of a pixel, raises its slope
by the style's ``crowns.relief`` and lights it as Lambert against flat ground, a factor on the
crown's sun term. Heights are summed as integers, exact in any order, and
``crown_form.cu`` is the CUDA twin under ``--gpu``, the same bits. docs/map/light-and-crowns.md
section 36, "Crown form".
"""

from __future__ import annotations

import numpy as np

from mapgen.jit import cuda_kernel, gpu_on
from mapgen.lighting.sun import Sun, sun_vector
from satisfactory_mcp.core.arrays import F32Grid, I64Grid

__all__ = ["FORM_M", "FORM_STEPS_PER_CM", "crown_form", "form_reach"]

#: Half-width of the box the crown's form is measured over, and its offset either side.
FORM_M = 1.8

#: Crown tops are summed in these steps a centimetre, as integers.
FORM_STEPS_PER_CM = 8

_THREADS = 256

_SOURCE = ("mapgen.palette.painted", "crown_form.cu")


def _radius(spacing_m: float) -> tuple[int, int]:
    """The box's half-width in pixels, and the offset either side the slope is taken over."""
    r = round(FORM_M / spacing_m)
    return r, max(r, 1)


def form_reach(spacing_m: float) -> int:
    """How many pixels away ``crown_form`` reads."""
    r, d = _radius(spacing_m)
    return r + d


def crown_form(
    top_cm: F32Grid, spacing_m: float, relief: float, sun: Sun, gpu: bool | None = None
) -> F32Grid:
    """Each pixel's form factor: 1 where no crown top is, ``relief`` 0 or flat tops; the
    slope's sun term over flat ground's otherwise. ``gpu`` None follows the switch."""
    top_cm = np.ascontiguousarray(top_cm, np.float32)
    light = np.asarray(sun_vector(*sun), np.float32)
    if gpu_on() if gpu is None else gpu:
        try:
            return _on_gpu(top_cm, spacing_m, np.float32(relief), light)
        except MemoryError:  # CuPy's OutOfMemoryError: the reference has the same bits
            pass
    r, d = _radius(spacing_m)
    seen = ~np.isnan(top_cm)
    steps = np.where(seen, np.rint(top_cm * np.float32(FORM_STEPS_PER_CM)), 0).astype(np.int64)
    sums, counts = _box_sums(steps, r), _box_sums(seen.astype(np.int64), r)
    per = counts.astype(np.float64) * np.float64(FORM_STEPS_PER_CM)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(counts > 0, sums.astype(np.float64) / per, np.nan).astype(np.float32)
    step_cm = np.float32(spacing_m * 100.0)
    tilt = np.float32(relief)
    gx = (_slope(mean, d, 1) / step_cm * tilt).astype(np.float32)
    gy = (_slope(mean, d, 0) / step_cm * tilt).astype(np.float32)
    lit = -gx * light[0] - gy * light[1] + light[2]
    lit = np.where(lit > 0, lit, np.float32(0.0))
    form = lit / np.sqrt(gx * gx + gy * gy + np.float32(1.0)) / light[2]
    return np.where(seen, form, np.float32(1.0)).astype(np.float32)


def _box_sums(values: I64Grid, r: int) -> I64Grid:
    """Each pixel's sum over the ``2 r + 1`` square round it, nothing past the plane's edges."""
    h, w = values.shape
    padded = np.pad(values, r)
    across = np.pad(np.cumsum(padded, axis=1), ((0, 0), (1, 0)))
    rows = across[:, 2 * r + 1 : 2 * r + 1 + w] - across[:, :w]
    down = np.pad(np.cumsum(rows, axis=0), ((1, 0), (0, 0)))
    return down[2 * r + 1 : 2 * r + 1 + h] - down[:h]


def _slope(mean: F32Grid, d: int, axis: int) -> F32Grid:
    """The mean's slope in cm a pixel along ``axis``, between the means ``d`` either side; a
    side with no mean, or past the edge, is the pixel's own. 0 where the pixel has none."""
    n = int(mean.shape[axis])
    index = np.arange(n, dtype=np.int64)
    ahead = np.take(mean, np.minimum(index + d, n - 1), axis=axis)
    behind = np.take(mean, np.maximum(index - d, 0), axis=axis)
    shape = [1, 1]
    shape[axis] = n
    ahead = np.where(((index + d) < n).reshape(shape), ahead, np.nan)
    behind = np.where(((index - d) >= 0).reshape(shape), behind, np.nan)
    found_ahead, found_behind = ~np.isnan(ahead), ~np.isnan(behind)
    hi = np.where(found_ahead, ahead, mean)
    lo = np.where(found_behind, behind, mean)
    sides = found_ahead.astype(np.int64) + found_behind.astype(np.int64)
    span = (sides * d).astype(np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (hi - lo) / span
    return np.where((sides > 0) & ~np.isnan(mean), slope, np.float32(0.0)).astype(np.float32)


def _on_gpu(top_cm: F32Grid, spacing_m: float, relief: np.float32, light: F32Grid) -> F32Grid:
    import cupy as cp

    rows, cols = top_cm.shape
    r, d = _radius(spacing_m)
    shape = (np.int32(rows), np.int32(cols))
    try:
        top = cp.asarray(top_cm)
        across = cp.empty((2, rows, cols), np.int64)
        mean = cp.empty((rows, cols), np.float32)
        out = cp.empty((rows, cols), np.float32)
        steps = np.float32(FORM_STEPS_PER_CM)
        _run("across", top.size, (top, steps, *shape, np.int32(r), across))
        _run("mean", top.size, (across, *shape, np.int32(r), np.float64(steps), mean))
        sun = (light[0], light[1], light[2])
        step_cm = np.float32(spacing_m * 100.0)
        _run("form", top.size, (top, mean, *shape, np.int32(d), step_cm, relief, *sun, out))
        found: F32Grid = out.get()
        return found
    finally:
        cp.get_default_memory_pool().free_all_blocks()


def _run(name: str, n: int, args: tuple[object, ...]) -> None:
    """The kernel ``name`` of ``crown_form.cu`` over ``n`` values."""
    cuda_kernel(*_SOURCE, name)((-(-n // _THREADS),), (_THREADS,), (*args, np.int64(n)))
