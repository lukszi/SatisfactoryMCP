"""The sprite raster's per-sample shading, in numpy: the reference ``gpu.shade`` equals.

A hit sample's normal is its triangle's interpolated tangent basis turned by the slot's
normal map, the basis's normal flipped on a card seen from its back, then bent toward the
sphere about the slot's pivot as far as the material bends it, and laid flat where it still
points down. Its colour is the slot's albedo, with the moss a bark wears where that normal
turns up. Every float32 operation is one IEEE operation, in the order ``shade.cu`` writes
it; no transcendental function is used, so the twin gives the same bits.
docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from mapgen.gamedata.vegetation.tree_surface import SlotTexture, SurfaceMesh
from mapgen.sprites import fill
from mapgen.sprites.fill import Hits, Triangles
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I32Grid, I64Grid

__all__ = [
    "ATTR_WIDTH",
    "SLOT_FLOATS",
    "SLOT_INTS",
    "Shading",
    "ShadingTables",
    "shade",
    "shading_tables",
]

#: A triangle's attribute row: normal, tangent at its first vertex and their steps along
#: its two edges, then the bitangent's sign.
N0, DN1, DN2, T0, DT1, DT2 = 0, 3, 6, 9, 12, 15
SIGN = 18
ATTR_WIDTH = 19
#: A slot's integer row: its albedo's offset (in texels), width and height, then its normal
#: map's, the offset -1 for none.
SLOT_INTS = 6
#: A slot's float row: moss colour, the moss ramp's start and steepness, the spherical
#: normals' share and pivot.
MOSS, MOSS_LOW, MOSS_GAIN, SPHERE, PIVOT = 0, 3, 4, 5, 6
SLOT_FLOATS = 9

_ZERO = np.float32(0.0)
_ONE = np.float32(1.0)
_HALF = np.float32(0.5)


@dataclass(frozen=True)
class ShadingTables:
    """Per triangle its attributes; every slot's albedo and normal map texels (flat, three
    floats a texel); per slot its integer and float rows."""

    attrs: F32Grid
    albedo: F32Grid
    normals: F32Grid
    ints: I32Grid
    floats: F32Grid


@dataclass(frozen=True)
class Shading:
    """Per sample its linear colour and unit normal (z up or flat), zero where nothing hit."""

    colour: F32Grid
    normal: F32Grid


def shading_tables(
    surface: SurfaceMesh, tris: Triangles, textures: Sequence[SlotTexture]
) -> ShadingTables:
    """The tables ``shade`` reads, for ``tris`` (``raster.triangle_setup``'s) of ``surface``."""
    corners = surface.tris[tris.source]
    columns: list[F32Grid] = []
    n = len(surface.verts)
    for values in (
        surface.normals,
        surface.tangents if surface.tangents is not None else np.zeros((n, 3), np.float32),
    ):
        v = values[corners].astype(np.float64)
        columns += [_f32(v[:, 0]), _f32(v[:, 1] - v[:, 0]), _f32(v[:, 2] - v[:, 0])]
    signs = surface.signs if surface.signs is not None else np.ones(n, np.float32)
    attrs = np.concatenate([*columns, _f32(signs[corners[:, 0]][:, None])], axis=1)
    ints = np.zeros((max(len(textures), 1), SLOT_INTS), np.int32)
    floats = np.zeros((max(len(textures), 1), SLOT_FLOATS), np.float32)
    albedo: list[F32Grid] = []
    normals: list[F32Grid] = [np.zeros(3, np.float32)]
    at, nat = 0, 1
    for k, texture in enumerate(textures):
        h, w = texture.albedo.shape[:2]
        maps = texture.shading.normal_map
        nh, nw = maps.shape[:2] if maps is not None else (1, 1)
        ints[k] = (at, w, h, nat if maps is not None else -1, nw, nh)
        albedo.append(_f32(texture.albedo.reshape(-1)))
        at += h * w
        if maps is not None:
            normals.append(_f32(maps.reshape(-1)))
            nat += nh * nw
        s = texture.shading
        floats[k] = (*(s.moss or (0.0, 0.0, 0.0)), s.moss_low, s.moss_gain, s.sphere, *s.pivot_cm)
    return ShadingTables(
        attrs=np.ascontiguousarray(attrs),
        albedo=np.ascontiguousarray(np.concatenate(albedo) if albedo else np.zeros(3), np.float32),
        normals=np.ascontiguousarray(np.concatenate(normals), np.float32),
        ints=ints,
        floats=floats,
    )


def _bilinear(
    texels: F32Grid, offset: I64Grid, w: I64Grid, h: I64Grid, u: F32Grid, v: F32Grid, wrap: BoolMask
) -> list[F32Grid]:
    """Three-channel texels at UVs, per sample its own texture, as ``fill.mask_at`` reads."""
    fu = u * w.astype(np.float32) - _HALF
    fv = v * h.astype(np.float32) - _HALF
    x0, y0 = np.floor(fu), np.floor(fv)
    fx, fy = fu - x0, fv - y0
    gx, gy = _ONE - fx, _ONE - fy
    i0, i1 = fill.taps(x0, w, wrap)
    j0, j1 = fill.taps(y0, h, wrap)
    out: list[F32Grid] = []
    for k in range(3):
        a00 = texels[(offset + j0 * w + i0) * 3 + k]
        a01 = texels[(offset + j0 * w + i1) * 3 + k]
        a10 = texels[(offset + j1 * w + i0) * 3 + k]
        a11 = texels[(offset + j1 * w + i1) * 3 + k]
        top = a00 * gx + a01 * fx
        bottom = a10 * gx + a11 * fx
        out.append(np.asarray(top * gy + bottom * fy, np.float32))
    return out


def _f32(values: npt.ArrayLike) -> F32Grid:
    """``values`` as float32: the same array where it already is one, so the same bits."""
    return np.asarray(values, np.float32)


def _lerp3(a: F32Grid, start: int, b1: F32Grid, b2: F32Grid) -> list[F32Grid]:
    """An attribute of three columns from ``start``, interpolated at the hit's weights."""
    return [
        _f32((a[:, start + k] + b1 * a[:, start + 3 + k]) + b2 * a[:, start + 6 + k])
        for k in range(3)
    ]


def _unit(x: F32Grid, y: F32Grid, z: F32Grid) -> list[F32Grid]:
    """``(x, y, z)`` over its length; (0, 0, 1) where it has none."""
    length = np.sqrt((x * x + y * y) + z * z)
    some = length > _ZERO
    safe = np.where(some, length, _ONE)
    return [
        np.where(some, x / safe, _ZERO),
        np.where(some, y / safe, _ZERO),
        np.where(some, z / safe, _ONE),
    ]


def shade(
    hits: Hits,
    tris: Triangles,
    tables: ShadingTables,
    origin_cm: tuple[float, float],
    sample_cm: float,
) -> Shading:
    """Every hit sample's colour and normal; sample ``(r, c)`` sits at ``origin + (c + 0.5,
    r + 0.5) * sample_cm`` in mesh cm, at its hit's depth."""
    rows, cols = hits.tri.shape
    colour = np.zeros((rows, cols, 3), np.float32)
    normal = np.zeros((rows, cols, 3), np.float32)
    at = np.flatnonzero(hits.tri.ravel() >= 0)
    if not len(at):
        return Shading(colour, normal)
    t = hits.tri.ravel()[at]
    b1, b2 = hits.b1.ravel()[at], hits.b2.ravel()[at]
    s = tris.setup[t]
    u = _f32((s[:, fill.U0] + b1 * s[:, fill.DU1]) + b2 * s[:, fill.DU2])
    v = _f32((s[:, fill.V0] + b1 * s[:, fill.DV1]) + b2 * s[:, fill.DV2])
    slot = tris.bounds[t, fill.SLOT].astype(np.int64)
    wrap = tris.bounds[t, fill.WRAP] > 0
    a = tables.attrs[t]
    nx, ny, nz = _lerp3(a, N0, b1, b2)
    tx, ty, tz = _lerp3(a, T0, b1, b2)
    sign = a[:, SIGN]
    bx = _f32((ny * tz - nz * ty) * sign)
    by = _f32((nz * tx - nx * tz) * sign)
    bz = _f32((nx * ty - ny * tx) * sign)
    back = nz < _ZERO
    nx, ny, nz = np.where(back, -nx, nx), np.where(back, -ny, ny), np.where(back, -nz, nz)
    ints, floats = tables.ints[slot].astype(np.int64), tables.floats[slot]
    has_map = ints[:, 3] >= 0
    mapped = _bilinear(
        tables.normals, np.maximum(ints[:, 3], 0), ints[:, 4], ints[:, 5], u, v, wrap
    )
    mx = np.where(has_map, mapped[0], _ZERO)
    my = np.where(has_map, mapped[1], _ZERO)
    mz = np.where(has_map, mapped[2], _ONE)
    wx, wy, wz = _unit(
        _f32((mx * tx + my * bx) + mz * nx),
        _f32((mx * ty + my * by) + mz * ny),
        _f32((mx * tz + my * bz) + mz * nz),
    )
    r, c = at // cols, at % cols
    px = np.float32(origin_cm[0]) + (c.astype(np.float32) + _HALF) * np.float32(sample_cm)
    py = np.float32(origin_cm[1]) + (r.astype(np.float32) + _HALF) * np.float32(sample_cm)
    pz = hits.z.ravel()[at]
    dx, dy, dz = _unit(
        _f32(px - floats[:, PIVOT]),
        _f32(py - floats[:, PIVOT + 1]),
        _f32(pz - floats[:, PIVOT + 2]),
    )
    k = floats[:, SPHERE]
    bent = _unit(_f32(wx + (dx - wx) * k), _f32(wy + (dy - wy) * k), _f32(wz + (dz - wz) * k))
    bend = k > _ZERO
    wx, wy, wz = (np.where(bend, b, w) for b, w in zip(bent, (wx, wy, wz), strict=True))
    flat = _unit(wx, wy, np.maximum(wz, _ZERO))
    down = wz < _ZERO
    wx, wy, wz = (np.where(down, f, w) for f, w in zip(flat, (wx, wy, wz), strict=True))
    base = _bilinear(tables.albedo, ints[:, 0], ints[:, 1], ints[:, 2], u, v, wrap)
    moss = np.minimum(np.maximum((wz - floats[:, MOSS_LOW]) * floats[:, MOSS_GAIN], _ZERO), _ONE)
    for k3 in range(3):
        colour.reshape(-1, 3)[at, k3] = base[k3] + (floats[:, MOSS + k3] - base[k3]) * moss
    for k3, w in enumerate((wx, wy, wz)):
        normal.reshape(-1, 3)[at, k3] = w
    return Shading(colour, normal)
