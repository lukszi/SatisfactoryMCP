"""The ground's detail per pixel: the CPU reference of ``render/gpu/cells.cu``, which gives its
bits.

Per pixel, as the landscape material does it: each present layer's albedo, normal and height
read at their repeats and in the noise's cells (``cells.py``); the layers height-blended,
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

from mapgen.terrain.ground_detail.cells import CellPixels, Read, mixed, read, turned_back
from mapgen.terrain.ground_detail.textures import NO_LAYER, DetailTextures
from mapgen.terrain.sample import Taps
from satisfactory_mcp.core.arrays import F32Grid, I32Grid, U8Grid

_N = TypeVar("_N", bound=np.number)

__all__ = [
    "BLEND_FLOOR",
    "DetailPiece",
    "GroundDetail",
    "detail_piece",
    "ground_detail",
]

#: The height blend's floor, as the engine's layer blend has it, and the low pass's.
BLEND_FLOOR = np.float32(1e-4)

_ZERO, _ONE, _TWO = np.float32(0.0), np.float32(1.0), np.float32(2.0)
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


def _reads(textures: DetailTextures, layer: int) -> list[Read]:
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
    at = CellPixels.at(textures.noise, piece.u[cols], piece.v[rows])
    here_ratio, here_normal = ratio[on], normal[on]
    painted = np.flatnonzero(total[on] > _ZERO)
    if len(painted):
        weights = [w[on][painted] for w in layers]
        found = _blend(textures, piece.present, weights, at.subset(painted))
        here_ratio[painted], here_normal[painted] = found
    wet = np.flatnonzero(overlay[on] > _ZERO)
    if textures.overlay >= 0 and len(wet):
        under = (here_ratio[wet], here_normal[wet])
        found = _overlay(textures, overlay[on][wet], at.subset(wet), under)
        here_ratio[wet], here_normal[wet] = found
    ratio[on], normal[on] = here_ratio, here_normal
    return GroundDetail(ratio, normal)


def _blend(
    textures: DetailTextures, present: I32Grid, weights: list[F32Grid], at: CellPixels
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
        colour = mixed(*read(textures.colour, colour_read, px), px.cells)
        height = mixed(*read(textures.surface, height_read, px), px.cells)
        lift = _TWO * weight[here] - _ONE
        h = np.clip(lift + height[:, 0], BLEND_FLOOR, _ONE)
        h_low = np.clip(lift + height[:, 1], BLEND_FLOOR, _ONE)
        blend[here], low_blend[here] = blend[here] + h, low_blend[here] + h_low
        albedo[here] = albedo[here] + h[:, None] * colour[:, :3]
        low[here] = low[here] + h_low[:, None] * colour[:, 3:]
        if normal_read[0] >= 0:
            plain, cell = read(textures.surface, normal_read, px)
            turned = turned_back(plain, cell, normal_read[1], px.cells)
            normal[here] = normal[here] + h[:, None] * turned
    mean = albedo / blend[:, None]
    mean_low = low / low_blend[:, None]
    return _f32(mean / np.maximum(mean_low, BLEND_FLOOR)), _f32(normal / blend[:, None])


def _overlay(
    textures: DetailTextures, weight: F32Grid, at: CellPixels, under: tuple[F32Grid, F32Grid]
) -> tuple[F32Grid, F32Grid]:
    """The overlay's own ratio and normal lerped over ``under`` by its weight."""
    ratio, normal = under
    colour_read, normal_read, _height = _reads(textures, textures.overlay)
    colour = mixed(*read(textures.colour, colour_read, at), at.cells)
    own = colour[:, :3] / np.maximum(colour[:, 3:], BLEND_FLOOR)
    w = weight[:, None]
    ratio = _f32(ratio + w * (own - ratio))
    if normal_read[0] >= 0:
        plain, cell = read(textures.surface, normal_read, at)
        normal = _f32(normal + w * (turned_back(plain, cell, normal_read[1], at.cells) - normal))
    return ratio, normal
