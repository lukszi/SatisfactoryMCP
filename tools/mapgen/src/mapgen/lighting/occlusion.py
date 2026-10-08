"""Ambient occlusion: how much sky a pixel's close neighbourhood takes, over a height plane.

At each of ``model.AO_SCALES_M`` a pixel is occluded by how far the mean height of the box
around it stands above it, against ``AO_DEPTH`` times the box's half-width; the occlusion is
the weighted sum over the scales times ``AO_STRENGTH``: the smallest scale darkens where a rock
or a cliff meets the ground, the largest the floor of a gully. The box sums add heights in steps of
1 / ``AO_STEPS_PER_M`` m as integers, so they are exact whatever window a block reads, and
``occlusion.cu`` is the CUDA twin under ``--gpu``, the same bits. docs/map/light-and-crowns.md
section 29, "Ambient occlusion".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.jit import cuda_kernel, gpu_on
from mapgen.lighting.model import AO_DEPTH, AO_SCALES_M, AO_STRENGTH, AO_WEIGHTS
from mapgen.lighting.spans.holes import OPEN_M
from satisfactory_mcp.core.arrays import F32Grid, I64Grid

__all__ = [
    "AO_STEPS_PER_M",
    "Scale",
    "ao_margin",
    "ao_scales",
    "occlusion",
    "relative_occlusion",
]

#: Heights are summed in these steps a metre, as integers.
AO_STEPS_PER_M = 1024

#: Threads of a block over a flat plane.
_THREADS = 256

_SOURCE = ("mapgen.lighting", "occlusion.cu")


class Scale(NamedTuple):
    """One scale on a sheet: its half-width in pixels, what divides a box's sum into a mean
    in metres, the height over a pixel that occludes it fully, and its weight."""

    radius: int
    divisor: np.float64
    depth: np.float32
    weight: np.float32


def ao_scales(spacing_m: float) -> list[Scale]:
    """The scales a sheet of ``spacing_m`` resolves: none under half a pixel."""
    out: list[Scale] = []
    for size_m, weight in zip(AO_SCALES_M, AO_WEIGHTS, strict=True):
        r = round(size_m / spacing_m)
        if r > 0:
            n = 2 * r + 1
            divisor = np.float64(n * n * AO_STEPS_PER_M)
            out.append(Scale(r, divisor, np.float32(AO_DEPTH * r * spacing_m), np.float32(weight)))
    return out


def ao_margin(spacing_m: float) -> int:
    """The pixels past each edge of its core that ``occlusion`` reads."""
    return max((scale.radius for scale in ao_scales(spacing_m)), default=0)


def _steps(plane: F32Grid) -> I64Grid:
    """``plane`` in integer steps, a pixel with no height at ``holes.OPEN_M``."""
    opened = np.where(np.isnan(plane), OPEN_M, plane).astype(np.float32)
    return np.rint(opened * np.float32(AO_STEPS_PER_M)).astype(np.int64)


def _box_sums(steps: I64Grid, r: int, m: int) -> I64Grid:
    """Each core pixel's sum over the ``2 r + 1`` square around it, the core ``m`` in."""
    h, w = steps.shape[0] - 2 * m, steps.shape[1] - 2 * m
    across = np.pad(np.cumsum(steps, axis=1), ((0, 0), (1, 0)))
    rows = across[:, m + r + 1 : m + r + 1 + w] - across[:, m - r : m - r + w]
    down = np.pad(np.cumsum(rows, axis=0), ((1, 0), (0, 0)))
    return down[m + r + 1 : m + r + 1 + h] - down[m - r : m - r + h]


def occlusion(
    plane: F32Grid,
    spacing_m: float,
    receivers: F32Grid | None = None,
    gpu: bool | None = None,
) -> F32Grid:
    """The occlusion of ``plane``'s core, 0 to 1, a pixel with no height 0. ``plane`` is in
    metres with ``ao_margin`` pixels past each edge, NaN where no height is; ``receivers``
    the core's own heights where they stand off it, as an arch's top does over the ground
    that occludes. ``gpu`` None follows the switch."""
    plane = np.ascontiguousarray(plane, np.float32)
    m = ao_margin(spacing_m)
    core = plane[m : plane.shape[0] - m, m : plane.shape[1] - m]
    core = np.ascontiguousarray(core if receivers is None else receivers, np.float32)
    if gpu_on() if gpu is None else gpu:
        try:
            return _on_gpu(plane, core, spacing_m, m)
        except MemoryError:  # CuPy's OutOfMemoryError: the reference has the same bits
            pass
    steps = _steps(plane)
    out = np.zeros(core.shape, np.float32)
    for scale in ao_scales(spacing_m):
        sums = _box_sums(steps, scale.radius, m)
        mean = (sums.astype(np.float64) / scale.divisor).astype(np.float32)
        part = np.clip((mean - core) / scale.depth, np.float32(0.0), np.float32(1.0))
        out = (out + scale.weight * part).astype(np.float32)
    found = (out * np.float32(AO_STRENGTH)).astype(np.float32)
    return np.nan_to_num(found, nan=0.0).astype(np.float32)


def relative_occlusion(over: F32Grid, under: F32Grid) -> F32Grid:
    """What ``over`` occludes beyond ``under``, as a factor on what is left:
    ``1 - (1 - over) / (1 - under)``, 0 to 1."""
    one = np.float32(1.0)
    return np.clip(one - (one - over) / (one - under), np.float32(0.0), one).astype(np.float32)


def _on_gpu(plane: F32Grid, core: F32Grid, spacing_m: float, m: int) -> F32Grid:
    import cupy as cp

    rows, cols = plane.shape
    h, w = rows - 2 * m, cols - 2 * m
    shape = (np.int32(rows), np.int32(cols), np.int32(m))
    try:
        source, receivers = cp.asarray(plane), cp.asarray(core)
        steps = cp.empty(plane.shape, np.int64)
        _run("steps", steps.size, (source, np.float32(OPEN_M), np.float32(AO_STEPS_PER_M), steps))
        across = cp.empty((rows, w), np.int64)
        box = cp.empty((h, w), np.int64)
        out = cp.zeros((h, w), np.float32)
        for scale in ao_scales(spacing_m):
            radius = np.int32(scale.radius)
            _run("across", across.size, (steps, *shape, radius, across))
            _run("down", box.size, (across, *shape, radius, box))
            args = (box, receivers, scale.divisor, scale.depth, scale.weight, out)
            _run("add_scale", box.size, args)
        _run("finish", out.size, (out, np.float32(AO_STRENGTH)))
        found: F32Grid = out.get()
        return found
    finally:
        cp.get_default_memory_pool().free_all_blocks()


def _run(name: str, n: int, args: tuple[object, ...]) -> None:
    """The kernel ``name`` of ``occlusion.cu`` over ``n`` values."""
    cuda_kernel(*_SOURCE, name)((-(-n // _THREADS),), (_THREADS,), (*args, np.int64(n)))
