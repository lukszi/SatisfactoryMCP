"""The span march and the sky view with spans on the GPU: ``kernels.march_spans`` and
``kernels.sky_view_spans``, a thread a pixel.

Each takes its numba twin's arguments and gives its bits (``gpu.cu``). A call the device has
no memory for runs the twin instead; each call is counted where it ran (``lighting.gpu.ran``).
``Resident`` keeps a surface's planes on the device for the many marches of a block
(``spans/device.py``). Imported only when ``mapgen.jit.gpu_on()``. docs/map/renders.md
section 41, "On the GPU".
"""

from __future__ import annotations

from typing import NamedTuple, TypeAlias

import cupy as cp
import numpy as np

from mapgen.jit import cuda_kernel
from mapgen.lighting import gpu
from mapgen.lighting.kernels import Offsets
from mapgen.lighting.spans import kernels
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid

__all__ = ["Resident", "march_resident", "march_spans", "resident", "sky_view_spans"]

_SOURCE = ("mapgen.lighting.spans", "gpu.cu")

_Surface = tuple[F32Grid, F32Grid, F32Grid, F32Grid, F32Grid]
_Runs = tuple[I64Grid, I64Grid, I64Grid]
#: ``march.KernelSteps`` as a plain tuple: smooth, offsets, quads, per step.
_Steps = tuple[BoolMask, Offsets, tuple[I64Grid, I64Grid], F32Grid]
_Plane: TypeAlias = "cp.ndarray[np.float32]"


class Resident(NamedTuple):
    """A surface on the device: its five planes, their row length, and per row how many rows
    up to it hold a span (``march.rows_with_spans``), on the host."""

    planes: tuple[cp.ndarray[np.float32], ...]
    width: int
    counts: I64Grid


def resident(surface: _Surface, counts: I64Grid) -> Resident:
    """``surface``'s planes uploaded once."""
    planes = tuple(gpu.device(np.ascontiguousarray(p, np.float32)) for p in surface)
    return Resident(planes, surface[0].shape[1], counts)


def march_spans(
    surface: _Surface,
    halo: int,
    smooth: BoolMask,
    offsets: Offsets,
    quads: tuple[I64Grid, I64Grid],
    per_step: F32Grid,
    target: tuple[np.float32, np.float32],
    out: tuple[F32Grid, F32Grid, F32Grid, BoolMask],
    rows: tuple[I64Grid, int],
    runs: _Runs,
) -> None:
    """``kernels.march_spans``: ``out`` written whole, the bits it raises in place."""
    args = (surface, halo, smooth, offsets, quads, per_step, target, out, rows)
    gpu.on_device(_march_spans, kernels.march_spans, args, runs)


def sky_view_spans(
    surface: _Surface,
    halo: int,
    offsets: Offsets,
    quads: tuple[I64Grid, I64Grid],
    per_step: F32Grid,
    out: F32Grid,
    rows: tuple[I64Grid, int],
    runs: _Runs,
) -> None:
    """``kernels.sky_view_spans``: ``out`` written in place."""
    args = (surface, halo, offsets, quads, per_step, out, rows)
    gpu.on_device(_sky_view_spans, kernels.sky_view_spans, args, runs)


def march_resident(
    surface: Resident,
    halo: int,
    steps: _Steps,
    target: tuple[np.float32, np.float32],
    reach: int,
) -> tuple[_Plane, _Plane, _Plane, cp.ndarray[np.bool_]]:
    """``kernels.march_spans`` over a ``Resident``: the core's best, band and seen, on the
    device, written whole."""
    smooth, offsets, quads, per_step = steps
    n, cols = (side - 2 * halo for side in surface.planes[0].shape)
    found = (
        cp.empty((n, cols), np.float32),
        cp.empty((n, cols), np.float32),
        cp.empty((n, cols), np.float32),
        cp.empty((n, cols), np.bool_),
    )
    args = (
        *surface.planes,
        np.int64(surface.width),
        np.int32(halo),
        _near_spans((surface.counts, reach), halo, n),
        cp.asarray(np.ascontiguousarray(smooth, np.bool_)),
        *_steps(offsets, quads),
        cp.asarray(np.ascontiguousarray(per_step, np.float32)),
        np.int32(per_step.shape[0]),
        np.float32(target[0]),
        np.float32(target[1]),
        *found,
        np.int32(n),
        np.int32(cols),
    )
    cuda_kernel(*_SOURCE, "march_spans")(*gpu.grid(n, cols), args)
    return found


def _near_spans(rows: tuple[I64Grid, int], halo: int, count: int) -> cp.ndarray[np.uint8]:
    """Per output row, ``kernels._near_spans``: whether a row within reach holds a span."""
    counts, reach = rows
    r = np.arange(halo, halo + count)
    top = counts[np.minimum(r + 1 + reach, counts.shape[0] - 1)]
    return cp.asarray((top > counts[np.maximum(r - reach, 0)]).astype(np.uint8))


def _steps(
    offsets: Offsets, quads: tuple[I64Grid, I64Grid]
) -> tuple[cp.ndarray[np.int32] | _Plane, ...]:
    """The steps' whole and quad offsets as int32, then their fractions, on the device."""
    iy, ix, fy, fx, gy, gx = offsets
    ints = tuple(cp.asarray(np.ascontiguousarray(part, np.int32)) for part in (iy, ix, *quads))
    return (*ints, *(cp.asarray(np.ascontiguousarray(f, np.float32)) for f in (fy, fx, gy, gx)))


def _march_spans(
    surface: _Surface,
    halo: int,
    smooth: BoolMask,
    offsets: Offsets,
    quads: tuple[I64Grid, I64Grid],
    per_step: F32Grid,
    target: tuple[np.float32, np.float32],
    out: tuple[F32Grid, F32Grid, F32Grid, BoolMask],
    rows: tuple[I64Grid, int],
) -> None:
    counts, reach = rows
    on = resident(surface, counts)
    found = march_resident(on, halo, (smooth, offsets, quads, per_step), target, reach)
    for host, device in zip(out, found, strict=True):
        host[...] = device.get()


def _sky_view_spans(
    surface: _Surface,
    halo: int,
    offsets: Offsets,
    quads: tuple[I64Grid, I64Grid],
    per_step: F32Grid,
    out: F32Grid,
    rows: tuple[I64Grid, int],
) -> None:
    dirs, steps = offsets[0].shape
    n, cols = out.shape
    found = cp.empty((n, cols), np.float32)
    on = resident(surface, rows[0])
    args = (
        *on.planes,
        np.int64(on.width),
        np.int32(halo),
        _near_spans(rows, halo, n),
        *_steps(offsets, quads),
        cp.asarray(np.ascontiguousarray(per_step, np.float32)),
        np.int32(dirs),
        np.int32(steps),
        found,
        np.int32(n),
        np.int32(cols),
    )
    cuda_kernel(*_SOURCE, "sky_view_spans")(*gpu.grid(n, cols), args)
    out[...] = found.get()
