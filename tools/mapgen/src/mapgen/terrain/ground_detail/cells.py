"""The landscape's rotated cells against tiling, for the ground's detail and the rock look.

A texture is read at its repeat and read again in the pixel's cell of the noise, shifted,
scaled and turned, and the two are mixed by the cell's mask; a turned normal is turned back.
Float32 operations in the order written here; ``render/gpu/cells.cu`` is the CUDA twin.
docs/map/painted.md section 30, "The layers' own textures".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.ground.layer_textures import CELLS_NONE, CELLS_ROTATED, NOISE_TILE_M
from mapgen.terrain.texels import Atlas, sample_atlas, sample_texture
from satisfactory_mcp.core.arrays import F32Grid, I64Grid, U8Grid

__all__ = [
    "NOISE_INVERSE",
    "NOISE_TERMS",
    "CellPixels",
    "Cells",
    "Read",
    "cells_at",
    "mixed",
    "noise_terms",
    "read",
    "turned_back",
]

#: The noise's repeat as a multiplier: the shaders' UV0 x 0.01.
NOISE_INVERSE = np.float32(1.0 / NOISE_TILE_M)
#: The noise's terms a texel: its cell's shift along u and v in repeats, its scale, the
#: cosine and sine of its turn, and its mask.
NOISE_TERMS = 6

#: One read of a tile: the tile, its cells (``CELLS_*``) and the inverse of its repeat in metres.
Read = tuple[int, int, np.float32]

_ZERO, _HALF, _ONE, _TWO = np.float32(0.0), np.float32(0.5), np.float32(1.0), np.float32(2.0)


def _f32(array: NDArray[np.floating]) -> F32Grid:
    """``array`` as the float32 it is: what numpy's stubs widen a float32 sum to."""
    return np.asarray(array, np.float32)


def noise_terms(rgba: U8Grid) -> F32Grid:
    """The cell noise as the terms a cell reads (``NOISE_TERMS``), as the landscape shader
    takes them: R shifts u, G shifts v and scales by 0.9 to 1.1, B turns a whole circle, A is
    the cell's mask."""
    c = rgba.astype(np.float64) / 255.0
    turn = 2.0 * np.pi * c[..., 2]
    terms = [2.0 * c[..., 0] - 1.0, 2.0 * c[..., 1] - 1.0, 0.9 + 0.2 * c[..., 1]]
    terms += [np.cos(turn), np.sin(turn), c[..., 3]]
    return np.ascontiguousarray(np.stack(terms, -1), np.float32)


class Cells(NamedTuple):
    """The noise's terms at the pixels: shift along u and v, scale, cosine, sine, mask."""

    du: F32Grid
    dv: F32Grid
    scale: F32Grid
    cos: F32Grid
    sin: F32Grid
    mask: F32Grid


def cells_at(noise: F32Grid, u: F32Grid, v: F32Grid) -> Cells:
    """The noise read where the pixels fall in its repeat, its mask as the shaders mix by."""
    side = np.float32(noise.shape[0])
    tu, tv = u * NOISE_INVERSE, v * NOISE_INVERSE
    terms = sample_texture(noise, (tu - np.floor(tu)) * side, (tv - np.floor(tv)) * side)
    mask = np.clip(_TWO * terms[:, 5] - _HALF, _ZERO, _ONE)
    return Cells(terms[:, 0], terms[:, 1], terms[:, 2], terms[:, 3], terms[:, 4], mask)


class CellPixels(NamedTuple):
    """Pixels to read at: their ``u``, ``v`` in metres and the noise's terms there."""

    u: F32Grid
    v: F32Grid
    cells: Cells

    @classmethod
    def at(cls, noise: F32Grid, u: F32Grid, v: F32Grid) -> CellPixels:
        return cls(u, v, cells_at(noise, u, v))

    def subset(self, at: I64Grid) -> CellPixels:
        return CellPixels(self.u[at], self.v[at], Cells(*(term[at] for term in self.cells)))


def _in_tile(
    tu: F32Grid, tv: F32Grid, size: tuple[np.float32, np.float32]
) -> tuple[F32Grid, F32Grid]:
    """Repeats ``tu`` and ``tv`` as texels of a tile ``size`` (width, height): their fraction."""
    return _f32((tu - np.floor(tu)) * size[0]), _f32((tv - np.floor(tv)) * size[1])


def read(atlas: Atlas, which: Read, at: CellPixels) -> tuple[F32Grid, F32Grid | None]:
    """One read of a tile at the pixels, and its read in the cells (None without cells)."""
    tile, mode, inverse = which
    size = (np.float32(atlas.tiles[tile, 2]), np.float32(atlas.tiles[tile, 3]))
    tiles = np.full(at.u.shape, tile, np.int32)
    tu, tv = at.u * inverse, at.v * inverse
    plain = sample_atlas(atlas, tiles, *_in_tile(tu, tv, size), True)
    if mode == CELLS_NONE:
        return plain, None
    cells = at.cells
    su, sv = _f32(cells.scale * (tu + cells.du)), _f32(cells.scale * (tv + cells.dv))
    if mode == CELLS_ROTATED:
        su, sv = _f32(cells.cos * su - cells.sin * sv), _f32(cells.sin * su + cells.cos * sv)
    return plain, sample_atlas(atlas, tiles, *_in_tile(su, sv, size), True)


def mixed(plain: F32Grid, cell: F32Grid | None, cells: Cells) -> F32Grid:
    """The plain read with the cell's mixed in by its mask."""
    if cell is None:
        return plain
    return _f32(plain + cells.mask[:, None] * (cell - plain))


def turned_back(plain: F32Grid, cell: F32Grid | None, mode: int, cells: Cells) -> F32Grid:
    """A normal read (east, south), the turned cell's turned back before it is mixed in."""
    if cell is not None and mode == CELLS_ROTATED:
        east = cells.cos * cell[:, 0] + cells.sin * cell[:, 1]
        south = cells.cos * cell[:, 1] - cells.sin * cell[:, 0]
        cell = _f32(np.stack([east, south], -1))
    return mixed(plain, cell, cells)
