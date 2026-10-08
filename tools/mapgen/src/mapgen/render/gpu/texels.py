"""Textures and sprites on the GPU: ``terrain/texels.py``'s reads and stamps, a thread a
pixel, to its bytes, on planes that stay on the device.

What a band's look samples, its ground textures by world position and its sprites by tile,
is uploaded once (``device.DeviceBand``, ``upload_atlas``) and read there by every kernel;
only what is asked for comes back. The sprites are binned by cell on the host, each cell's
in their order, so every pixel walks the sprites that may reach it as the reference walks
them all. Imported only when ``mapgen.jit.gpu_on()``. docs/map/renders.md section 41, "The
draw on the GPU".
"""

from __future__ import annotations

from typing import NamedTuple

import cupy as cp
import numpy as np

from mapgen.render.gpu.device import flat_grid, kernel, row_grid
from mapgen.terrain.texels import Atlas, Sprites, sprite_boxes
from satisfactory_mcp.core.arrays import I32Grid, I64Grid

__all__ = [
    "CELL_PX",
    "MAX_CHANNELS",
    "DeviceAtlas",
    "sample_atlas",
    "sample_texture",
    "sprite_cells",
    "stamp_sprites",
    "upload_atlas",
]

_SOURCE = "texels.cu"

#: The side of the cells the sprites are binned into, in pixels.
CELL_PX = 16

#: Channels an atlas texel may have, its alpha included.
MAX_CHANNELS = 8


class DeviceAtlas(NamedTuple):
    """An ``Atlas`` on the device: its texels, and its tiles on both sides."""

    texels: cp.ndarray[np.float32]
    tiles: I32Grid
    on_tiles: cp.ndarray[np.int32]


def upload_atlas(atlas: Atlas) -> DeviceAtlas:
    """``atlas`` on the device, for any number of reads and stamps."""
    tiles = np.ascontiguousarray(atlas.tiles, np.int32)
    texels = np.ascontiguousarray(atlas.texels, np.float32)
    return DeviceAtlas(cp.asarray(texels), tiles, cp.asarray(tiles))


def sample_texture(
    texture: cp.ndarray[np.float32],
    u: cp.ndarray[np.float32],
    v: cp.ndarray[np.float32],
    wrap: bool = True,
) -> cp.ndarray[np.float32]:
    """``texels.sample_texture`` on the device: ``texture`` (rows, columns, channels) at
    ``(u, v)``, all C-ordered device arrays."""
    h, w, channels = texture.shape
    out = cp.empty((*u.shape, channels), np.float32)
    head = (texture, np.int32(h), np.int32(w), np.int32(channels), u, v)
    tail = (np.int64(u.size), np.int32(wrap), out)
    kernel(_SOURCE, "sample_texture")(*flat_grid(u.size), (*head, *tail))
    return out


def sample_atlas(
    atlas: DeviceAtlas,
    tile: cp.ndarray[np.int32],
    u: cp.ndarray[np.float32],
    v: cp.ndarray[np.float32],
    wrap: bool = False,
) -> cp.ndarray[np.float32]:
    """``texels.sample_atlas`` on the device."""
    channels = atlas.texels.shape[2]
    out = cp.empty((*u.shape, channels), np.float32)
    head = (atlas.texels, np.int32(atlas.texels.shape[1]), np.int32(channels), atlas.on_tiles)
    tail = (tile, u, v, np.int64(u.size), np.int32(wrap), out)
    kernel(_SOURCE, "sample_atlas")(*flat_grid(u.size), (*head, *tail))
    return out


def stamp_sprites(
    colour: cp.ndarray[np.float32],
    cover: cp.ndarray[np.float32],
    sprites: Sprites,
    atlas: DeviceAtlas,
) -> None:
    """``texels.stamp_sprites`` over ``colour`` and ``cover``, in place on the device."""
    channels = atlas.texels.shape[2]
    if channels > MAX_CHANNELS:
        raise ValueError(f"an atlas of {channels} channels: at most {MAX_CHANNELS}")
    rows, cols = cover.shape
    boxes = sprite_boxes(sprites, (rows, cols))
    starts, ids, cells_x = sprite_cells(boxes, rows, cols)
    per_sprite = (sprites.x, sprites.y, sprites.half, sprites.cos, sprites.sin, sprites.opacity)
    table = np.concatenate([np.asarray(p, np.float32) for p in per_sprite])
    count = np.int64(len(sprites.x))
    tiles = cp.asarray(np.ascontiguousarray(sprites.tile, np.int32))
    bins = (cp.asarray(boxes), cp.asarray(starts), cp.asarray(ids), np.int32(CELL_PX))
    head = (colour, cover, np.int32(rows), np.int32(cols), cp.asarray(table), count, tiles)
    look = (atlas.texels, np.int32(atlas.texels.shape[1]), np.int32(channels), atlas.on_tiles)
    kernel(_SOURCE, "stamp")(*row_grid(rows, cols), (*head, *bins, np.int32(cells_x), *look))


def sprite_cells(boxes: I64Grid, rows: int, cols: int) -> tuple[I32Grid, I32Grid, int]:
    """Each cell's sprites, in their order: the cells' first entries (one more than the
    cells, the last the count) and the sprites, and the cells along a row."""
    cells_x, cells_y = -(-cols // CELL_PX), -(-rows // CELL_PX)
    r0, r1, c0, c1 = boxes.T
    live = (r1 > r0) & (c1 > c0)
    cy0, cx0 = r0 // CELL_PX, c0 // CELL_PX
    ny = np.where(live, (r1 - 1) // CELL_PX + 1 - cy0, 0)
    nx = np.where(live, (c1 - 1) // CELL_PX + 1 - cx0, 0)
    counts = ny * nx
    sprite = np.repeat(np.arange(len(boxes)), counts)
    local = np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
    width = np.maximum(nx[sprite], 1)
    cell = (cy0[sprite] + local // width) * cells_x + cx0[sprite] + local % width
    order = np.argsort(cell, kind="stable")
    per_cell = np.bincount(cell, minlength=cells_x * cells_y)
    starts = np.concatenate([[0], np.cumsum(per_cell)]).astype(np.int32)
    return starts, sprite[order].astype(np.int32), cells_x
