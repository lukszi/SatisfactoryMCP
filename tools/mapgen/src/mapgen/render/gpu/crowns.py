"""The crown stamps on the GPU: ``terrain/crown_stamp.py``'s, a thread a pixel, to its bits.

The atlas goes up once a process, and again only after the colour calibration moved it; per
band the placements go up, binned by cell as ``texels.stamp_sprites`` bins its sprites, and
the four planes come back. Imported only when ``mapgen.jit.gpu_on()``. docs/map/renders.md
section 41, "The draw on the GPU".
"""

from __future__ import annotations

import threading
import weakref

import cupy as cp
import numpy as np

from mapgen.render.gpu.device import kernel, on_device, row_grid
from mapgen.render.gpu.texels import CELL_PX, DeviceAtlas, sprite_cells, upload_atlas
from mapgen.terrain.crown_atlas import CHANNELS, CrownAtlas
from mapgen.terrain.crown_stamp import COVER_TOP_MIN, CrownBand, Placements, stamp_placed
from satisfactory_mcp.core.arrays import F64Grid

__all__ = ["stamp_crowns"]

_SOURCE = "texels.cu"

#: Each atlas on the device, with the version it was uploaded at, for as long as it lives.
_held: weakref.WeakKeyDictionary[CrownAtlas, tuple[int, DeviceAtlas]] = weakref.WeakKeyDictionary()
_holding = threading.Lock()


def stamp_crowns(
    atlas: CrownAtlas, placed: Placements, centres: tuple[F64Grid, F64Grid]
) -> CrownBand | None:
    """``crown_stamp.stamp_placed`` on the device; None for the CPU to stamp them where the
    device has no memory for the band."""
    if not len(placed.tile):
        return stamp_placed(atlas.atlas, placed, centres)
    return on_device(lambda: _stamp(atlas, placed, centres))


def _device_atlas(atlas: CrownAtlas) -> DeviceAtlas:
    with _holding:
        held = _held.get(atlas)
        if held is None or held[0] != atlas.version:
            held = (atlas.version, upload_atlas(atlas.atlas))
            _held[atlas] = held
        return held[1]


def _stamp(atlas: CrownAtlas, placed: Placements, centres: tuple[F64Grid, F64Grid]) -> CrownBand:
    x_cm, y_cm = centres
    rows, cols = len(y_cm), len(x_cm)
    on = _device_atlas(atlas)
    starts, ids, cells_x = sprite_cells(placed.box, rows, cols)
    cover = cp.empty((rows, cols), np.float32)
    rgb = cp.empty((rows, cols, 3), np.float32)
    normal = cp.empty((rows, cols, 3), np.float32)
    top = cp.empty((rows, cols), np.float32)
    grid = (np.int32(rows), np.int32(cols), cp.asarray(np.ascontiguousarray(x_cm, np.float64)))
    grid += (cp.asarray(np.ascontiguousarray(y_cm, np.float64)),)
    trees = (cp.asarray(np.ascontiguousarray(placed.centre, np.float64)),)
    trees += (cp.asarray(placed.pose), cp.asarray(placed.tile), cp.asarray(placed.box))
    bins = (cp.asarray(starts), cp.asarray(ids), np.int32(CELL_PX), np.int32(cells_x))
    look = (on.texels, np.int32(on.texels.shape[1]), on.on_tiles, COVER_TOP_MIN)
    if on.texels.shape[2] != CHANNELS:
        raise ValueError(f"a crown atlas of {on.texels.shape[2]} channels, not {CHANNELS}")
    args = (cover, rgb, normal, top, *grid, *trees, *bins, *look)
    kernel(_SOURCE, "stamp_crowns")(*row_grid(rows, cols), args)
    high = top.get()
    found = np.where(high > -np.inf, high, np.nan).astype(np.float32)
    return {"cover": cover.get(), "rgb": rgb.get(), "normal": normal.get(), "top_cm": found}
