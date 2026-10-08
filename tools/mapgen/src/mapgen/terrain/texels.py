"""Textures read onto a band and sprites stamped over it: the CPU reference of the draw's
texture kernels (``render/gpu/texels.py``), which give its bytes.

A texture is read at texel coordinates, bilinear between texel centres, repeating or clamped
at its edge; an atlas holds many textures as tiles of one array. A sprite is a tile turned,
scaled and laid over the band's colour by its alpha, sprite after sprite in their order.
Every operation is float32 in the order written here. docs/map/renders.md section 41, "The
draw on the GPU".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from satisfactory_mcp.core.arrays import F32Grid, I32Grid, I64Grid

__all__ = ["Atlas", "Sprites", "sample_atlas", "sample_texture", "sprite_boxes", "stamp_sprites"]

_HALF = np.float32(0.5)
_ONE = np.float32(1.0)

#: Texels either side of a position along an axis, and the second one's weight.
_Corners = tuple[I64Grid, I64Grid, F32Grid]


class Atlas(NamedTuple):
    """Textures packed into one ``(h, w, channels)`` float32 array: ``tiles`` holds each
    one's first column, first row, width and height, in texels."""

    texels: F32Grid
    tiles: I32Grid


class Sprites(NamedTuple):
    """Sprites over a band, stamped in this order: each one's centre (column, row) and half
    edge in the band's pixels, the cosine and sine of its turn, its atlas tile and opacity.
    The atlas's last channel is a sprite's alpha, the others its colour."""

    x: F32Grid
    y: F32Grid
    half: F32Grid
    cos: F32Grid
    sin: F32Grid
    tile: I32Grid
    opacity: F32Grid


def _corners(position: F32Grid, size: int | I64Grid, wrap: bool) -> _Corners:
    """The texels either side of each position, a texel's centre at its index plus a half,
    repeated past the edge or clamped to it."""
    at = position - _HALF
    first = np.floor(at)
    fraction = (at - first).astype(np.float32)
    low = first.astype(np.int64)
    if wrap:
        return low % size, (low + 1) % size, fraction
    return np.clip(low, 0, size - 1), np.clip(low + 1, 0, size - 1), fraction


def _bilinear(texels: F32Grid, rows: _Corners, cols: _Corners) -> F32Grid:
    """``(a (1 - fx) + b fx) (1 - fy) + (c (1 - fx) + d fx) fy``, per channel."""
    (y0, y1, fy), (x0, x1, fx) = rows, cols
    gx, gy = (_ONE - fx)[..., None], (_ONE - fy)[..., None]
    top = texels[y0, x0] * gx + texels[y0, x1] * fx[..., None]
    bottom = texels[y1, x0] * gx + texels[y1, x1] * fx[..., None]
    return (top * gy + bottom * fy[..., None]).astype(np.float32)


def sample_texture(texture: F32Grid, u: F32Grid, v: F32Grid, wrap: bool = True) -> F32Grid:
    """``texture`` (rows, columns, channels) at columns ``u`` and rows ``v`` in texels."""
    h, w = texture.shape[:2]
    return _bilinear(texture, _corners(v, h, wrap), _corners(u, w, wrap))


def sample_atlas(
    atlas: Atlas, tile: I32Grid, u: F32Grid, v: F32Grid, wrap: bool = False
) -> F32Grid:
    """Each pixel's ``tile`` of ``atlas`` at ``(u, v)`` texels into the tile, repeated or
    clamped at the tile's edge, never reading its neighbours."""
    x0, y0, w, h = (atlas.tiles[tile, k].astype(np.int64) for k in range(4))
    r0, r1, fy = _corners(v, h, wrap)
    c0, c1, fx = _corners(u, w, wrap)
    return _bilinear(atlas.texels, (y0 + r0, y0 + r1, fy), (x0 + c0, x0 + c1, fx))


def sprite_boxes(sprites: Sprites, shape: tuple[int, int]) -> I64Grid:
    """Each sprite's rows ``[r0, r1)`` and columns ``[c0, c1)`` the band reads it on, as
    ``(n, 4)``, cut to the band: a turned sprite reaches its half diagonal."""
    reach = sprites.half.astype(np.float64) * np.sqrt(2.0) + 1.0
    x, y = sprites.x.astype(np.float64), sprites.y.astype(np.float64)
    box = np.stack([y - reach, y + reach, x - reach, x + reach])
    box[0::2], box[1::2] = np.floor(box[0::2]), np.ceil(box[1::2])
    rows, cols = shape
    limits = np.array([rows, rows, cols, cols], np.float64)[:, None]
    return np.ascontiguousarray(np.clip(box, 0, limits).astype(np.int64).T)


def _sprite_uv(
    sprites: Sprites, k: int, rows: I64Grid, cols: I64Grid, size: tuple[int, int]
) -> tuple[F32Grid, F32Grid]:
    """Where sprite ``k``'s tile, ``size`` (width, height) texels, lies under the pixel
    centres of ``rows`` by ``cols``."""
    dx = (cols.astype(np.float32) + _HALF)[None, :] - sprites.x[k]
    dy = (rows.astype(np.float32) + _HALF)[:, None] - sprites.y[k]
    c, s, half = sprites.cos[k], sprites.sin[k], sprites.half[k]
    lx = dx * c + dy * s
    ly = dy * c - dx * s
    u = (lx / half + _ONE) * (_HALF * np.float32(size[0]))
    v = (ly / half + _ONE) * (_HALF * np.float32(size[1]))
    return u.astype(np.float32), v.astype(np.float32)


def stamp_sprites(colour: F32Grid, cover: F32Grid, sprites: Sprites, atlas: Atlas) -> None:
    """Each sprite laid over ``colour`` by its alpha times its opacity, in order, and
    ``cover`` raised by the same alpha (``cover + a (1 - cover)``), both in place. A pixel
    whose centre falls outside a sprite's tile is left to the next."""
    boxes = sprite_boxes(sprites, (colour.shape[0], colour.shape[1]))
    for k in range(len(boxes)):
        r0, r1, c0, c1 = (int(boxes[k, j]) for j in range(4))
        if r0 >= r1 or c0 >= c1:
            continue
        tile = int(sprites.tile[k])
        width, height = int(atlas.tiles[tile, 2]), int(atlas.tiles[tile, 3])
        u, v = _sprite_uv(sprites, k, np.arange(r0, r1), np.arange(c0, c1), (width, height))
        inside = (u >= 0) & (u < np.float32(width)) & (v >= 0) & (v < np.float32(height))
        texel = sample_atlas(atlas, np.full(u.shape, tile, np.int32), u, v)
        alpha = texel[..., -1] * sprites.opacity[k]
        window = (slice(r0, r1), slice(c0, c1))
        under = colour[window]
        mixed = under * (_ONE - alpha)[..., None] + texel[..., :-1] * alpha[..., None]
        colour[window] = np.where(inside[..., None], mixed, under)
        raised = cover[window] + alpha * (_ONE - cover[window])
        cover[window] = np.where(inside, raised, cover[window])
