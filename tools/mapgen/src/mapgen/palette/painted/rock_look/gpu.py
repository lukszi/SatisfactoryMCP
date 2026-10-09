"""The texel kernel on the GPU: ``reference.look_texels``, a thread a pixel, to its bits.

``render/gpu/cells.cu``'s ``look_texels`` does the reference's operations in its order, with
the landscape's cells the ground's detail reads through, compiled without fused
multiply-adds (``mapgen.jit``). The look's atlases go to the device once a run and stay; a
call the device has no memory for runs the reference instead. Imported only when
``mapgen.jit.gpu_on()``. docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

import threading
from typing import NamedTuple

import cupy as cp
import numpy as np
from numpy.typing import NDArray

from mapgen.jit import cuda_kernel
from mapgen.palette.painted.rock_look import reference
from mapgen.palette.painted.rock_look.atlas import RockLook
from mapgen.palette.painted.rock_look.reference import LookPixels, LookTexels

__all__ = ["FLAT_THREADS", "look_texels"]

#: Threads of a block over the pixels laid flat.
FLAT_THREADS = 256

_SOURCE = ("mapgen.render.gpu", "cells.cu")

_uploading = threading.Lock()


class _OnDevice(NamedTuple):
    """A look's arrays on the device, in the kernel's argument order."""

    albedo: tuple[object, ...]
    normals: tuple[object, ...]
    table: tuple[object, ...]


_uploaded: dict[int, tuple[RockLook, _OnDevice]] = {}


def _up(array: NDArray[np.generic], kind: type[np.generic]) -> cp.ndarray[np.generic]:
    return cp.asarray(np.ascontiguousarray(array, kind))


def _on_device(look: RockLook) -> _OnDevice:
    """``look`` on the device, uploaded on its first call."""
    with _uploading:
        held = _uploaded.get(id(look))
        if held is not None and held[0] is look:
            return held[1]
        albedo = (
            _up(look.albedo.texels, np.float32),
            np.int32(look.albedo.texels.shape[1]),
            _up(look.albedo.tiles, np.int32),
            _up(look.albedo_median, np.float32),
            _up(look.top_inverse(), np.float32),
        )
        normals = (
            _up(look.normals.texels, np.float32),
            np.int32(look.normals.texels.shape[1]),
            _up(look.normals.tiles, np.int32),
        )
        table = (
            _up(look.noise, np.float32),
            np.int32(look.noise.shape[0]),
            _up(look.reads, np.int32),
            _up(look.inverse, np.float32),
            _up(look.gains, np.float32),
        )
        found = _OnDevice(albedo, normals, table)
        _uploaded.clear()
        _uploaded[id(look)] = (look, found)
        return found


def look_texels(look: RockLook, px: LookPixels) -> LookTexels:
    """``reference.look_texels`` on the device; the reference where it has no memory."""
    try:
        return _look_texels(look, px)
    except MemoryError:  # CuPy's OutOfMemoryError
        return reference.look_texels(look, px)


def _look_texels(look: RockLook, px: LookPixels) -> LookTexels:
    n = len(px.kind)
    if n == 0:
        return reference.look_texels(look, px)
    held = _on_device(look)
    outs = [cp.empty((n, 3), np.float32) for _ in range(3)]
    args = (
        _up(px.u, np.float32),
        _up(px.v, np.float32),
        _up(px.normal, np.float32),
        _up(px.kind, np.uint8),
        _up(px.top, np.int32),
        np.int64(n),
        *held.albedo,
        *held.normals,
        *held.table,
        *outs,
    )
    blocks = (max(1, -(-n // FLAT_THREADS)),)
    cuda_kernel(*_SOURCE, "look_texels")(blocks, (FLAT_THREADS,), args)
    body, top, normal = (out.get() for out in outs)
    return LookTexels(body, top, normal)
