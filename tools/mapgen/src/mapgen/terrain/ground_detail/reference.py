"""The ground's detail per pixel: the CPU reference of ``render/gpu/ground.cu``, which gives its
bits.

Per pixel, as the landscape material does it: each present layer's albedo, normal and height
read at their repeats, a second read in the noise's cells mixed in by the cell's mask (2A -
0.5), the normal of a turned cell turned back; the layers height-blended,
``clamp(2 w - 1 + h, 1e-4, 1)`` over their sum; the overlay lerped on by its weight. The
detail is the blend over the same blend of the textures' low passes, weighted by the heights'
low passes, so it carries what lies under ``DETAIL_SIGMA_M`` and the layers' mosaic, and its
mean stays 1. Every operation is float32 in the order written here.
docs/map/painted.md section 30, "The layers' own textures".
"""

from __future__ import annotations

from typing import NamedTuple, TypeVar

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.ground.layer_textures import CELLS_NONE, CELLS_ROTATED, NOISE_TILE_M
from mapgen.terrain.ground_detail.textures import NO_LAYER, DetailTextures
from mapgen.terrain.sample import Taps
from mapgen.terrain.texels import Atlas, sample_atlas, sample_texture
from satisfactory_mcp.core.arrays import F32Grid, I32Grid, I64Grid, U8Grid

_N = TypeVar("_N", bound=np.number)

__all__ = [
    "BLEND_FLOOR",
    "NOISE_INVERSE",
    "DetailPiece",
    "GroundDetail",
    "detail_piece",
    "ground_detail",
]

#: The height blend's floor, as the engine's layer blend has it, and the low pass's.
BLEND_FLOOR = np.float32(1e-4)
#: The noise's repeat as a multiplier: the shaders' UV0 x 0.01.
NOISE_INVERSE = np.float32(1.0 / NOISE_TILE_M)

_ZERO, _HALF, _ONE, _TWO = np.float32(0.0), np.float32(0.5), np.float32(1.0), np.float32(2.0)
_BYTE = np.float32(255.0)

#: Two taps along an axis: indices into the piece's window and their weights, ``(2, n)``.
_Taps = tuple[I32Grid, F32Grid]


class DetailPiece(NamedTuple):
    """One piece's inputs: its columns' ``u`` and rows' ``v``, metres from the paint grid's
    corner; its window of the leading layers, their weights and the overlay's weight; the
    rows' and columns' bilinear taps into that window; and the layer ids present in it."""

    u: F32Grid
    v: F32Grid
    ids: U8Grid
    weights: U8Grid
    overlay: U8Grid
    rows: _Taps
    cols: _Taps
    present: I32Grid


class GroundDetail(NamedTuple):
    """A piece's detail: the albedo's ratio to its low pass ``(rows, cols, 3)``, 1 where no
    layer is painted, and the textures' normal, east and south ``(rows, cols, 2)``."""

    ratio: F32Grid
    normal: F32Grid


def detail_piece(
    textures: DetailTextures, taps: Taps, u: F32Grid, v: F32Grid
) -> DetailPiece | None:
    """The piece the linear ``taps`` (rows', then columns' on the paint grid) read, at pixel
    centres ``u`` and ``v``; None where no layer and no overlay is painted under it."""
    (row_index, row_weight), (col_index, col_weight) = taps
    r0, r1 = int(row_index.min()), int(row_index.max()) + 1
    c0, c1 = int(col_index.min()), int(col_index.max()) + 1
    ids = np.ascontiguousarray(textures.ids[:, r0:r1, c0:c1])
    weights = np.ascontiguousarray(textures.weights[:, r0:r1, c0:c1])
    overlay = np.ascontiguousarray(textures.overlay_weight[r0:r1, c0:c1])
    present = np.unique(ids[(ids != NO_LAYER) & (weights > 0)]).astype(np.int32)
    if not len(present) and not overlay.any():
        return None
    return DetailPiece(
        u,
        v,
        ids,
        weights,
        overlay,
        (_contiguous(row_index - r0, np.int32), _contiguous(row_weight, np.float32)),
        (_contiguous(col_index - c0, np.int32), _contiguous(col_weight, np.float32)),
        present,
    )


def _contiguous(array: NDArray[np.number], dtype: type[_N]) -> NDArray[_N]:
    return np.ascontiguousarray(array, dtype)


def _f32(array: NDArray[np.floating]) -> F32Grid:
    """``array`` as the float32 it is: what numpy's stubs widen a float32 sum to."""
    return np.asarray(array, np.float32)


def _weights(piece: DetailPiece) -> tuple[list[F32Grid], F32Grid]:
    """Each present layer's weight and the overlay's, bilinear over the window's texels:
    corner by corner, a layer's leading slots in order."""
    (ri, rw), (ci, cw) = piece.rows, piece.cols
    shape = (len(piece.v), len(piece.u))
    layers = [np.zeros(shape, np.float32) for _ in piece.present]
    overlay = np.zeros(shape, np.float32)
    for a in range(2):
        for b in range(2):
            g = rw[a][:, None] * cw[b][None, :]
            at_rows, at_cols = ri[a][:, None], ci[b][None, :]
            for k in range(piece.ids.shape[0]):
                held = piece.ids[k][at_rows, at_cols]
                share = g * (piece.weights[k][at_rows, at_cols].astype(np.float32) / _BYTE)
                for i, layer in enumerate(piece.present):
                    layers[i] = layers[i] + np.where(held == layer, share, _ZERO)
            overlay = overlay + g * (piece.overlay[at_rows, at_cols].astype(np.float32) / _BYTE)
    return layers, overlay


class _Cells(NamedTuple):
    """The noise's terms at the pixels: shift along u and v, scale, cosine, sine, mask."""

    du: F32Grid
    dv: F32Grid
    scale: F32Grid
    cos: F32Grid
    sin: F32Grid
    mask: F32Grid


def _cells(noise: F32Grid, u: F32Grid, v: F32Grid) -> _Cells:
    """The noise read where the pixels fall in its repeat, its mask as the shaders mix by."""
    side = np.float32(noise.shape[0])
    tu, tv = u * NOISE_INVERSE, v * NOISE_INVERSE
    terms = sample_texture(noise, (tu - np.floor(tu)) * side, (tv - np.floor(tv)) * side)
    mask = np.clip(_TWO * terms[:, 5] - _HALF, _ZERO, _ONE)
    return _Cells(terms[:, 0], terms[:, 1], terms[:, 2], terms[:, 3], terms[:, 4], mask)


class _Pixels(NamedTuple):
    """Pixels to read at: their ``u``, ``v`` and the noise's terms there."""

    u: F32Grid
    v: F32Grid
    cells: _Cells

    @classmethod
    def at(cls, noise: F32Grid, u: F32Grid, v: F32Grid) -> _Pixels:
        return cls(u, v, _cells(noise, u, v))

    def subset(self, at: I64Grid) -> _Pixels:
        return _Pixels(self.u[at], self.v[at], _Cells(*(term[at] for term in self.cells)))


def _read(
    atlas: Atlas, read: tuple[int, int, np.float32], at: _Pixels
) -> tuple[F32Grid, F32Grid | None]:
    """One read of a tile at the pixels, and its read in the cells (None without cells)."""
    tile, mode, inverse = read
    size = (np.float32(atlas.tiles[tile, 2]), np.float32(atlas.tiles[tile, 3]))
    which = np.full(at.u.shape, tile, np.int32)
    tu, tv = at.u * inverse, at.v * inverse
    plain = sample_atlas(atlas, which, *_in_tile(tu, tv, size), True)
    if mode == CELLS_NONE:
        return plain, None
    cells = at.cells
    su, sv = _f32(cells.scale * (tu + cells.du)), _f32(cells.scale * (tv + cells.dv))
    if mode == CELLS_ROTATED:
        su, sv = _f32(cells.cos * su - cells.sin * sv), _f32(cells.sin * su + cells.cos * sv)
    return plain, sample_atlas(atlas, which, *_in_tile(su, sv, size), True)


def _in_tile(
    tu: F32Grid, tv: F32Grid, size: tuple[np.float32, np.float32]
) -> tuple[F32Grid, F32Grid]:
    """Repeats ``tu`` and ``tv`` as texels of a tile ``size`` (width, height): their fraction."""
    return _f32((tu - np.floor(tu)) * size[0]), _f32((tv - np.floor(tv)) * size[1])


def _mixed(plain: F32Grid, cell: F32Grid | None, cells: _Cells) -> F32Grid:
    if cell is None:
        return plain
    return _f32(plain + cells.mask[:, None] * (cell - plain))


def _normal(plain: F32Grid, cell: F32Grid | None, mode: int, cells: _Cells) -> F32Grid:
    """A normal read, the turned cell's turned back before it is mixed in."""
    if cell is not None and mode == CELLS_ROTATED:
        east = cells.cos * cell[:, 0] + cells.sin * cell[:, 1]
        south = cells.cos * cell[:, 1] - cells.sin * cell[:, 0]
        cell = _f32(np.stack([east, south], -1))
    return _mixed(plain, cell, cells)


def _reads(textures: DetailTextures, layer: int) -> list[tuple[int, int, np.float32]]:
    table = textures.table
    return [
        (int(table.tiles[layer, j]), int(table.cells[layer, j]), table.inverse[layer, j])
        for j in range(3)
    ]


def ground_detail(textures: DetailTextures, piece: DetailPiece) -> GroundDetail:
    """The piece's detail, computed only where a layer or the overlay is painted."""
    layers, overlay = _weights(piece)
    total = np.zeros(overlay.shape, np.float32)
    for weight in layers:
        total = total + weight
    shape = (len(piece.v), len(piece.u))
    ratio = np.ones((*shape, 3), np.float32)
    normal = np.zeros((*shape, 2), np.float32)
    on = (total > _ZERO) | (overlay > _ZERO)
    if not on.any():
        return GroundDetail(ratio, normal)
    rows, cols = np.nonzero(on)
    at = _Pixels.at(textures.noise, piece.u[cols], piece.v[rows])
    here_ratio, here_normal = ratio[on], normal[on]
    painted = np.flatnonzero(total[on] > _ZERO)
    if len(painted):
        weights = [w[on][painted] for w in layers]
        mixed = _blend(textures, piece.present, weights, at.subset(painted))
        here_ratio[painted], here_normal[painted] = mixed
    wet = np.flatnonzero(overlay[on] > _ZERO)
    if textures.overlay >= 0 and len(wet):
        under = (here_ratio[wet], here_normal[wet])
        mixed = _overlay(textures, overlay[on][wet], at.subset(wet), under)
        here_ratio[wet], here_normal[wet] = mixed
    ratio[on], normal[on] = here_ratio, here_normal
    return GroundDetail(ratio, normal)


def _blend(
    textures: DetailTextures, present: I32Grid, weights: list[F32Grid], at: _Pixels
) -> tuple[F32Grid, F32Grid]:
    """The present layers height-blended, each where it weighs anything: the albedo's ratio
    to its low pass, and the normal."""
    n = len(at.u)
    blend, low_blend = np.zeros(n, np.float32), np.zeros(n, np.float32)
    albedo, low = np.zeros((n, 3), np.float32), np.zeros((n, 3), np.float32)
    normal = np.zeros((n, 2), np.float32)
    for weight, layer in zip(weights, present, strict=True):
        here = np.flatnonzero(weight > _ZERO)
        if not len(here):
            continue
        px = at.subset(here)
        colour_read, normal_read, height_read = _reads(textures, int(layer))
        colour = _mixed(*_read(textures.colour, colour_read, px), px.cells)
        height = _mixed(*_read(textures.surface, height_read, px), px.cells)
        lift = _TWO * weight[here] - _ONE
        h = np.clip(lift + height[:, 0], BLEND_FLOOR, _ONE)
        h_low = np.clip(lift + height[:, 1], BLEND_FLOOR, _ONE)
        blend[here], low_blend[here] = blend[here] + h, low_blend[here] + h_low
        albedo[here] = albedo[here] + h[:, None] * colour[:, :3]
        low[here] = low[here] + h_low[:, None] * colour[:, 3:]
        if normal_read[0] >= 0:
            plain, cell = _read(textures.surface, normal_read, px)
            turned = _normal(plain, cell, normal_read[1], px.cells)
            normal[here] = normal[here] + h[:, None] * turned
    mean = albedo / blend[:, None]
    mean_low = low / low_blend[:, None]
    return _f32(mean / np.maximum(mean_low, BLEND_FLOOR)), _f32(normal / blend[:, None])


def _overlay(
    textures: DetailTextures, weight: F32Grid, at: _Pixels, under: tuple[F32Grid, F32Grid]
) -> tuple[F32Grid, F32Grid]:
    """The overlay's own ratio and normal lerped over ``under`` by its weight."""
    ratio, normal = under
    colour_read, normal_read, _height = _reads(textures, textures.overlay)
    colour = _mixed(*_read(textures.colour, colour_read, at), at.cells)
    own = colour[:, :3] / np.maximum(colour[:, 3:], BLEND_FLOOR)
    w = weight[:, None]
    ratio = _f32(ratio + w * (own - ratio))
    if normal_read[0] >= 0:
        plain, cell = _read(textures.surface, normal_read, at)
        normal = _f32(normal + w * (_normal(plain, cell, normal_read[1], at.cells) - normal))
    return ratio, normal
