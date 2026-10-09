"""A piece's ground detail, on the GPU where the run draws there, and where it shows.

The detail is the landscape layers' own textures under the bake's metre
(``terrain/ground_detail``): the painted layer multiplies its ground by the ratio, and the
light adds the normal to its normal tiles wherever the ground is what is drawn.
docs/map/painted.md section 30, "The layers' own textures".
"""

from __future__ import annotations

import numpy as np

from mapgen.jit import gpu_on
from mapgen.lighting.light_tiles import DETAIL_SCALE
from mapgen.terrain.ground_detail.reference import GroundDetail, detail_piece, ground_detail
from mapgen.terrain.ground_detail.textures import DetailTextures
from mapgen.terrain.sample import Taps
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, FloatGrid, I8Grid

__all__ = ["detail_bytes", "drawn_share", "piece_detail"]


def piece_detail(
    textures: DetailTextures | None, taps: Taps, x_cm: F64Grid, y_cm: F64Grid
) -> GroundDetail | None:
    """The detail under a piece whose pixel centres are ``x_cm`` by ``y_cm`` and whose linear
    ``taps`` read the paint grid; None without textures or where nothing is painted."""
    if textures is None:
        return None
    x0_cm, y0_cm = textures.origin_cm
    u = ((x_cm - x0_cm) / 100.0).astype(np.float32)
    v = ((y_cm - y0_cm) / 100.0).astype(np.float32)
    piece = detail_piece(textures, taps, u, v)
    if piece is None:
        return None
    if gpu_on():
        from mapgen.render.gpu.ground import ground_detail as on_gpu

        done = on_gpu(textures, piece)
        if done is not None:
            return done
    return ground_detail(textures, piece)


def drawn_share(
    shape: tuple[int, int],
    rock: FloatGrid | None,
    top: FloatGrid | None,
    mesh: FloatGrid | None,
) -> F32Grid:
    """The share of each pixel the ground is drawn on: what neither a rock, the overlay's lift
    nor a render-only mesh covers, as the painted layer takes them."""
    over = np.zeros(shape, np.float32)
    for plane in (rock, top):
        if plane is not None:
            over = np.maximum(over, plane)
    drawn = np.float32(1.0) - over
    return drawn if mesh is None else drawn * (np.float32(1.0) - mesh)


def detail_bytes(detail: GroundDetail | None, drawn: F32Grid) -> I8Grid:
    """The detail normal as the light stores it, east and south bytes ``(rows, cols, 2)``,
    faded by ``drawn`` (``drawn_share``); 0 without detail."""
    if detail is None:
        return np.zeros((*drawn.shape, 2), np.int8)
    normal = detail.normal * drawn[..., None]
    scaled = np.clip(normal, -1.0, 1.0) * np.float32(DETAIL_SCALE)
    return np.round(scaled).astype(np.int8)
