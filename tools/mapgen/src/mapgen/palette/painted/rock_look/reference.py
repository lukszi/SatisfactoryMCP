"""What a rock pixel reads of the look: the CPU reference of the texel kernel (``gpu.py``),
which gives its bits.

Per pixel, every texture projected on the ground plane, the one a view from straight above
sees undistorted: the body's albedo blended with its copy in a rotated cell, its family's top
layer, and the normal maps laid on the surface. Positions are float64 until they are wrapped
into a tile, float32 after; every operation in the order written here. docs/map/painted.md
section 30, "Rock textures".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.palette.painted.rock_look.atlas import (
    ALBEDO_ARCH,
    ALBEDO_CLIFF,
    ALBEDO_DESERT,
    ALBEDO_LAYER_BASE,
    NORMAL_ARCH,
    NORMAL_CLIFF,
    NORMAL_DESERT,
    NORMAL_DETAIL,
    RockLook,
)
from mapgen.terrain.texels import sample_atlas, sample_texture
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, FloatGrid, I32Grid, U8Grid

__all__ = [
    "KIND_ARCH",
    "KIND_CLIFF",
    "KIND_DESERT",
    "KIND_LAYER",
    "LookPixels",
    "LookTexels",
    "look_texels",
]

#: What a pixel is drawn as: a cliff, the landscape's Cliff layer, an arch or boulder, or
#: desert rock. The cliff and the layer wear the Cliff layer's material; the arches and the
#: desert rock their own texture, without the rotated cells or the detail normal.
KIND_CLIFF, KIND_LAYER, KIND_ARCH, KIND_DESERT = 1, 2, 3, 4

_F = np.float32
_ZERO, _HALF, _ONE, _TWO = _F(0.0), _F(0.5), _F(1.0), _F(2.0)


class LookPixels(NamedTuple):
    """The pixels to read, flat: position in metres from the frame's corner (east, south), the
    surface's unit normal (east, south, up; ``(n, 3)``), kind, and the albedo tile of the
    family's top layer, -1 for none."""

    x_m: F64Grid
    y_m: F64Grid
    normal: F32Grid
    kind: U8Grid
    top: I32Grid


class LookTexels(NamedTuple):
    """Per pixel, ``(n, 3)`` each: the body's albedo over its median (the landscape layer's
    over its 1 m base), the top layer's over its median, and the normal with the maps laid on."""

    body: F32Grid
    top: F32Grid
    normal: F32Grid


class _Cells(NamedTuple):
    """Each pixel's anti-tiling cell: the copy's weight, its offset in tiles along both axes,
    its scale, and its turn's cosine and sine."""

    weight: F32Grid
    offset_a: F64Grid
    offset_b: F64Grid
    scale: F64Grid
    cos: F64Grid
    sin: F64Grid


def _wrapped(texel: F64Grid, side: F64Grid | np.float64) -> F32Grid:
    """A position in texels brought into ``[0, side)``."""
    return (texel - np.floor(texel / side) * side).astype(np.float32)


def _at(
    look_tiles: F64Grid, sides: F64Grid, tile: I32Grid, a: F64Grid, b: F64Grid
) -> tuple[F32Grid, F32Grid]:
    """Where ``(a, b)`` metres fall in each pixel's ``tile``, in its texels, wrapped."""
    span, side = look_tiles[tile], sides[tile]
    return _wrapped(a / span * side, side), _wrapped(b / span * side, side)


def _cells(look: RockLook, a: F64Grid, b: F64Grid) -> _Cells:
    """Each pixel's cell, read nearest, and the copy's weight, read bilinear."""
    cells = look.cells
    side = np.float64(cells.shape[0])
    u = _wrapped(a / look.cell_tile_m * side, side)
    v = _wrapped(b / look.cell_tile_m * side, side)
    col = np.floor(u).astype(np.int64) % cells.shape[1]
    row = np.floor(v).astype(np.int64) % cells.shape[0]
    red, green, blue = (cells[row, col, k] for k in range(3))
    alpha = sample_texture(cells[..., 3:], u, v, wrap=True)[:, 0]
    weight = np.clip(alpha * _TWO - _HALF, _ZERO, _ONE)
    turn = np.clip(np.floor(blue * _F(255.0) + _HALF), 0, 255).astype(np.int64)
    offset_a = red.astype(np.float64) * 2.0 - 1.0
    offset_b = green.astype(np.float64) * 2.0 - 1.0
    scale = 0.9 + 0.2 * green.astype(np.float64)
    return _Cells(weight, offset_a, offset_b, scale, look.turn[turn, 0], look.turn[turn, 1])


def _albedo(look: RockLook, tile: I32Grid, a: F64Grid, b: F64Grid, cells: _Cells) -> F32Grid:
    """The tile at ``(a, b)``, blended by the cell's weight with its copy in the cell."""
    atlas = look.albedo
    sides = atlas.tiles[:, 2].astype(np.float64)
    u, v = _at(look.albedo_tiles_m, sides, tile, a, b)
    plain = sample_atlas(atlas, tile, u, v, wrap=True)
    span, side, c = look.albedo_tiles_m[tile], sides[tile], cells
    ua = (a / span + c.offset_a) * c.scale
    vb = (b / span + c.offset_b) * c.scale
    ru = _wrapped((c.cos * ua - c.sin * vb) * side, side)
    rv = _wrapped((c.sin * ua + c.cos * vb) * side, side)
    turned = sample_atlas(atlas, tile, ru, rv, wrap=True)
    return (plain + c.weight[:, None] * (turned - plain)).astype(np.float32, copy=False)


def _normal(look: RockLook, kind: U8Grid, a: F64Grid, b: F64Grid) -> F32Grid:
    """The base normal map, the detail one laid on it at half strength (reoriented normal
    mapping, as the landscape's Cliff layer does), unit length, in the map's own axes."""
    atlas = look.normals
    sides = atlas.tiles[:, 2].astype(np.float64)
    own = (kind == KIND_ARCH) | (kind == KIND_DESERT)
    base = np.where(kind == KIND_ARCH, NORMAL_ARCH, NORMAL_CLIFF)
    base = np.where(kind == KIND_DESERT, NORMAL_DESERT, base).astype(np.int32)
    u, v = _at(look.normal_tiles_m, sides, base, a, b)
    bxy = sample_atlas(atlas, base, u, v, wrap=True)
    detail = np.full(base.shape, NORMAL_DETAIL, np.int32)
    u, v = _at(look.normal_tiles_m, sides, detail, a, b)
    dxy = np.where(own[:, None], _ZERO, sample_atlas(atlas, detail, u, v, wrap=True))
    bx, by, dx, dy = bxy[:, 0], bxy[:, 1], dxy[:, 0], dxy[:, 1]
    bz = np.sqrt(np.maximum(_ONE - bx * bx - by * by, _ZERO))
    dz = np.sqrt(np.maximum(_ONE - dx * dx - dy * dy, _ZERO))
    t = (bx * _TWO, by * _TWO, bz + _ONE)
    w = (dx * -_HALF, dy * -_HALF, dz)
    dot = t[0] * w[0] + t[1] * w[1] + t[2] * w[2]
    r = [t[k] * dot - w[k] * t[2] for k in range(3)]
    length = np.sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2])
    return np.stack([c / length for c in r], -1).astype(np.float32, copy=False)


def _onto(r: F32Grid, normal: F32Grid) -> F32Grid:
    """The map's normal laid on the surface: its x east, its y south, its z along the surface
    normal, unit length."""
    out: list[FloatGrid] = [r[:, 0] + r[:, 2] * normal[:, 0], r[:, 1] + r[:, 2] * normal[:, 1]]
    out.append(r[:, 2] * normal[:, 2])
    length = np.sqrt(out[0] * out[0] + out[1] * out[1] + out[2] * out[2])
    return np.stack([c / length for c in out], -1).astype(np.float32, copy=False)


def look_texels(look: RockLook, px: LookPixels) -> LookTexels:
    """Every pixel of ``px``'s body, top layer and normal."""
    a, b = px.x_m, px.y_m
    own = (px.kind == KIND_ARCH) | (px.kind == KIND_DESERT)
    cells = _cells(look, a, b)
    cells = cells._replace(weight=np.where(own, _ZERO, cells.weight))
    tile = np.where(px.kind == KIND_ARCH, ALBEDO_ARCH, ALBEDO_CLIFF)
    tile = np.where(px.kind == KIND_DESERT, ALBEDO_DESERT, tile).astype(np.int32)
    albedo = _albedo(look, tile, a, b, cells)
    layer = (px.kind == KIND_LAYER)[:, None]
    under = look.albedo_median[tile]
    if layer.any():
        base = np.full(tile.shape, ALBEDO_LAYER_BASE, np.int32)
        under = np.where(layer, _albedo(look, base, a, b, cells), under)
    body = albedo / under
    has_top = px.top >= 0
    top_tile = np.where(has_top, px.top, 0).astype(np.int32)
    sides = look.albedo.tiles[:, 2].astype(np.float64)
    u, v = _at(look.albedo_tiles_m, sides, top_tile, a, b)
    layer_top = sample_atlas(look.albedo, top_tile, u, v, wrap=True) / look.albedo_median[top_tile]
    top_ratio = np.where(has_top[:, None], layer_top, _ONE)
    normal = _onto(_normal(look, px.kind, a, b), px.normal)
    return LookTexels(body.astype(np.float32), top_ratio.astype(np.float32), normal)
