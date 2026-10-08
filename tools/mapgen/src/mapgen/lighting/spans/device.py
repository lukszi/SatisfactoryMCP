"""A block's atlas cells on the GPU: what ``bake.horizon_cells`` does to each direction, with
the block's planes kept on the device from the first direction to the last.

Each march, the band rules after it, the holes' fill, the folded horizon and the crown cell's
test run on the device (``gpu.cu``, ``cells.cu``); the arctangent and the degrees, numpy's
transcendentals, run on the host between them, on the pixels that need them. The cells come
back as the host's code makes them, bit for bit. Imported only when ``mapgen.jit.gpu_on()``.
docs/map/renders.md section 41, "On the GPU".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple, TypeAlias

import cupy as cp
import numpy as np

from mapgen.jit import cuda_kernel
from mapgen.lighting import gpu
from mapgen.lighting.horizon import FADE_M, Fade, march_steps
from mapgen.lighting.horizon import kernel_steps as plain_steps
from mapgen.lighting.model import SHADOW_SOFT_DEG
from mapgen.lighting.spans import gpu as span_gpu
from mapgen.lighting.spans.holes import Holes
from mapgen.lighting.spans.march import (
    Bands,
    SpanSurface,
    as_degrees,
    band_target,
    kernel_steps,
    rows_with_spans,
    span_steps,
    step_reach,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = ["DeviceBands", "DeviceCells"]

_SOURCE = ("mapgen.lighting.spans", "cells.cu")

#: Threads of a block over a flat plane.
_THREADS = 256

_Plane: TypeAlias = "cp.ndarray[np.float32]"


class DeviceBands(NamedTuple):
    """``march.Bands`` on the device."""

    horizon: _Plane
    lo: _Plane
    hi: _Plane
    seen: cp.ndarray[np.bool_]


class DeviceCells:
    """``bake.horizon_cells``' operations on the device, for one block: its heights (marched
    plain where no span casts) or its span surfaces, uploaded once, and its holes."""

    def __init__(
        self,
        z_half: F32Grid,
        halo: int,
        spacing_m: float,
        surfaces: Sequence[SpanSurface],
        holes: Holes | None,
    ) -> None:
        self.halo, self.spacing_m, self.z_half = halo, spacing_m, z_half
        self.shape = (z_half.shape[0] - 2 * halo, z_half.shape[1] - 2 * halo)
        self.z: _Plane | None = None
        self.surfaces = {
            id(s): span_gpu.resident((s.z, s.solid, s.lo, s.hi, s.tops), rows_with_spans(s.lo))
            for s in surfaces
        }
        self.holes = holes
        self.nearest: cp.ndarray[np.int64] | None = None
        if holes is not None and holes.nearest is not None:
            rows, cols = holes.nearest
            self.nearest = gpu.device(rows * np.int64(self.shape[1]) + cols)

    def plain(self, az_deg: float) -> _Plane:
        """``fill_holes(march_horizon(...))``: the plain march's horizon, degrees."""
        if self.z is None:
            self.z = gpu.device(np.ascontiguousarray(self.z_half, np.float32))
        best = cp.zeros(self.shape, np.float32)
        steps = plain_steps(march_steps(az_deg, self.spacing_m, FADE_M), self.halo)
        gpu.march_on_device(self.z, self.z, self.halo, steps, best)
        return self._fill(gpu.device(as_degrees(best.get())), 0.0)

    def bands(self, surface: SpanSurface, az_deg: float, fade: Fade) -> DeviceBands:
        """``_filled(march_spans(...))``: the direction's horizon and band, degrees, filled."""
        steps = span_steps(az_deg, self.spacing_m, fade)
        best, lo, hi, seen = span_gpu.march_resident(
            self.surfaces[id(surface)],
            self.halo,
            kernel_steps(steps, self.halo),
            band_target(az_deg),
            step_reach(steps),
        )
        floating = cp.empty(self.shape, np.uint8)
        self._run("finish", (best, lo, hi, floating))
        where = cp.flatnonzero(floating)
        bands = DeviceBands(
            gpu.device(as_degrees(best.get())), _band(lo, where), _band(hi, where), seen
        )
        return self._filled(bands)

    def path(self, bands: DeviceBands, el: float) -> _Plane:
        """``bake.path_horizon`` of filled bands."""
        half = np.float32(SHADOW_SOFT_DEG / 2)
        soft, at = np.float32(SHADOW_SOFT_DEG), np.float32(el)
        cell = cp.empty(self.shape, np.float32)
        self._run("path", (*bands[:3], at, at - half, at + half, soft, cell))
        return cell

    def above(self, over: _Plane, cell: _Plane) -> _Plane:
        """The crown cell: ``np.where(over > cell, over, 0)``."""
        out = cp.empty(self.shape, np.float32)
        self._run("above", (over, cell, out))
        return out

    def band_in(self, stored: _Plane, whole: _Plane, bands: DeviceBands) -> BoolMask:
        """``bake._OnHost.band_in``: where the cell stores the band folded in."""
        out = cp.empty(self.shape, np.bool_)
        self._run("band_in", (stored, whole, bands.horizon, out))
        return out.get()

    def host(self, plane: _Plane) -> F32Grid:
        return plane.get()

    def host_bands(self, bands: DeviceBands) -> Bands:
        return Bands(bands.horizon.get(), bands.lo.get(), bands.hi.get(), bands.seen.get())

    def _filled(self, bands: DeviceBands) -> DeviceBands:
        """``bake._filled``: each hole given its nearest pixel's bands."""
        if self.holes is None:
            return bands
        if self.nearest is None:
            nan = cp.full(self.shape, np.float32(np.nan), np.float32)
            return DeviceBands(
                cp.zeros(self.shape, np.float32), nan, nan.copy(), cp.zeros(self.shape, np.bool_)
            )
        filled = DeviceBands(
            cp.empty_like(bands.horizon),
            cp.empty_like(bands.lo),
            cp.empty_like(bands.hi),
            cp.empty_like(bands.seen),
        )
        self._run("fill_bands", (*bands, self.nearest, *filled))
        return filled

    def _fill(self, plane: _Plane, empty: float) -> _Plane:
        """``holes.fill_holes`` of one plane."""
        if self.holes is None:
            return plane
        if self.nearest is None:
            return cp.full(self.shape, np.float32(empty), np.float32)
        out = cp.empty_like(plane)
        self._run("fill", (plane, self.nearest, out))
        return out

    def _run(self, name: str, args: tuple[object, ...]) -> None:
        """The kernel ``name`` of ``cells.cu`` over the block's pixels."""
        n = self.shape[0] * self.shape[1]
        blocks = (-(-n // _THREADS),)
        cuda_kernel(*_SOURCE, name)(blocks, (_THREADS,), (*args, np.int64(n)))


def _band(tangents: _Plane, where: cp.ndarray[np.int64]) -> _Plane:
    """A band's edge in degrees where it floats, NaN elsewhere: ``march._finish``'s, its
    arctangent taken on the host over those pixels alone."""
    out = cp.full(tangents.shape, np.float32(np.nan), np.float32)
    out.ravel()[where] = gpu.device(as_degrees(tangents.ravel()[where].get()))
    return out
