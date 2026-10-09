"""The ground's detail on the GPU: ``terrain/ground_detail/reference.py``, a thread a pixel,
to its bits.

The run's textures go up once a process (``texels.upload_atlas``) and stay; a piece's
window of the leading layers, its taps and its pixels go up each call, and only the ratio
and the normal come back. Imported only when ``mapgen.jit.gpu_on()``.
docs/map/painted.md section 30, "The layers' own textures".
"""

from __future__ import annotations

import threading
from functools import partial
from typing import NamedTuple

import cupy as cp
import numpy as np

from mapgen.render.gpu.device import kernel, on_device, row_grid
from mapgen.render.gpu.texels import DeviceAtlas, upload_atlas
from mapgen.terrain.ground_detail.reference import DetailPiece, GroundDetail
from mapgen.terrain.ground_detail.textures import DetailTextures

__all__ = ["ground_detail"]

_SOURCE = "ground.cu"


class _OnDevice(NamedTuple):
    """A run's detail textures on the device: both atlases, the noise and the layer table."""

    colour: DeviceAtlas
    surface: DeviceAtlas
    noise: cp.ndarray[np.float32]
    tiles: cp.ndarray[np.int32]
    cells: cp.ndarray[np.int32]
    inverse: cp.ndarray[np.float32]


_held: dict[int, tuple[DetailTextures, _OnDevice]] = {}
_holding = threading.Lock()


def _uploaded(textures: DetailTextures) -> _OnDevice:
    """``textures`` on the device, uploaded on the first piece; a new run's replace a past one's."""
    with _holding:
        found = _held.get(id(textures))
        if found is None or found[0] is not textures:
            _held.clear()
            table = textures.table
            device = _OnDevice(
                upload_atlas(textures.colour),
                upload_atlas(textures.surface),
                cp.asarray(np.ascontiguousarray(textures.noise, np.float32)),
                cp.asarray(np.ascontiguousarray(table.tiles, np.int32)),
                cp.asarray(np.ascontiguousarray(table.cells, np.int32)),
                cp.asarray(np.ascontiguousarray(table.inverse, np.float32)),
            )
            found = (textures, device)
            _held[id(textures)] = found
        return found[1]


def ground_detail(textures: DetailTextures, piece: DetailPiece) -> GroundDetail | None:
    """``reference.ground_detail`` on the device; None where the device has no memory for it."""
    return on_device(partial(_detail, textures, piece))


def _detail(textures: DetailTextures, piece: DetailPiece) -> GroundDetail:
    on = _uploaded(textures)
    rows, cols = len(piece.v), len(piece.u)
    ratio = cp.empty((rows, cols, 3), np.float32)
    normal = cp.empty((rows, cols, 2), np.float32)
    _layers, window_rows, window_cols = piece.ids.shape

    def up(array: np.ndarray[tuple[int, ...], np.dtype[np.generic]]) -> cp.ndarray[np.generic]:
        return cp.asarray(np.ascontiguousarray(array))

    pixels = (np.int32(rows), np.int32(cols), up(piece.u), up(piece.v))
    window = (up(piece.ids), up(piece.weights), up(piece.overlay), np.int32(window_cols))
    taps = (up(piece.rows[0]), up(piece.rows[1]), up(piece.cols[0]), up(piece.cols[1]))
    layers = (up(piece.present), np.int32(len(piece.present)), on.tiles, on.cells, on.inverse)
    colour = (on.colour.texels, np.int32(on.colour.texels.shape[1]), on.colour.on_tiles)
    surface = (on.surface.texels, np.int32(on.surface.texels.shape[1]), on.surface.on_tiles)
    noise = (on.noise, np.int32(on.noise.shape[0]))
    args = (
        *pixels,
        *window,
        np.int64(window_rows * window_cols),
        *taps,
        *layers,
        np.int32(textures.overlay),
        *colour,
        *surface,
        *noise,
        ratio,
        normal,
    )
    kernel(_SOURCE, "ground_detail")(*row_grid(rows, cols), args)
    return GroundDetail(ratio.get(), normal.get())
