"""What a rock pixel reads of the look: the CPU reference of the texel kernel (``gpu.py``),
which gives its bits.

Per pixel, every texture projected on the ground plane, the one a view from straight above
sees undistorted, through the landscape's cells (``terrain/ground_detail/cells.py``): the
kind's albedo over its median or its 1 m base, its family's top layer, and its normal maps
laid on the surface. Float32 throughout, every operation in the order written here.
docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.gamedata.ground.layer_textures import CELLS_NONE
from mapgen.palette.painted.rock_look.atlas import ALBEDO, BASE, DETAIL, UNDER, RockLook
from mapgen.terrain.ground_detail.cells import CellPixels, Read, mixed, read, turned_back
from mapgen.terrain.texels import Atlas
from satisfactory_mcp.core.arrays import F32Grid, FloatGrid, I32Grid, U8Grid

__all__ = ["LookPixels", "LookTexels", "look_texels"]

_F = np.float32
_ZERO, _ONE = _F(0.0), _F(1.0)


class LookPixels(NamedTuple):
    """The pixels to read, flat: position in metres from the frame's corner (east, south), the
    surface's unit normal (east, south, up; ``(n, 3)``), kind, and the albedo tile of the
    family's top layer, -1 for none."""

    u: F32Grid
    v: F32Grid
    normal: F32Grid
    kind: U8Grid
    top: I32Grid


class LookTexels(NamedTuple):
    """Per pixel, ``(n, 3)`` each: the body's albedo over its median (the landscape layer's
    over its 1 m base), the top layer's over its median, and the normal with the maps laid on."""

    body: F32Grid
    top: F32Grid
    normal: F32Grid


def _slot(look: RockLook, kind: int, slot: int) -> Read:
    tile, cells = look.reads[kind, slot]
    return int(tile), int(cells), look.inverse[kind, slot]


def _read(atlas: Atlas, which: Read, at: CellPixels) -> F32Grid:
    """A read mixed with its read in the cells."""
    return mixed(*read(atlas, which, at), at.cells)


def _body(look: RockLook, kind: int, at: CellPixels) -> F32Grid:
    """The kind's albedo over what it is measured against; one where it has none."""
    which = _slot(look, kind, ALBEDO)
    if which[0] < 0:
        return np.ones((len(at.u), 3), np.float32)
    fine = _read(look.albedo, which, at)
    base = _slot(look, kind, UNDER)
    under = look.albedo_median[which[0]] if base[0] < 0 else _read(look.albedo, base, at)
    return (fine / under).astype(np.float32, copy=False)


def _map(look: RockLook, which: Read, at: CellPixels) -> F32Grid:
    """A normal map's east and south, flat where the kind has none."""
    if which[0] < 0:
        return np.zeros((len(at.u), 2), np.float32)
    plain, cell = read(look.normals, which, at)
    return turned_back(plain, cell, which[1], at.cells)


def _mapped(look: RockLook, kind: int, at: CellPixels) -> F32Grid:
    """The base normal map with the detail laid on it (reoriented normal mapping), each slope
    times its gain, unit length, in the map's own axes."""
    b, d = _map(look, _slot(look, kind, BASE), at), _map(look, _slot(look, kind, DETAIL), at)
    gain, detail_gain = look.gains[kind, 0], -look.gains[kind, 1]
    bx, by, dx, dy = b[:, 0], b[:, 1], d[:, 0], d[:, 1]
    bz = np.sqrt(np.maximum(_ONE - bx * bx - by * by, _ZERO))
    dz = np.sqrt(np.maximum(_ONE - dx * dx - dy * dy, _ZERO))
    t = (bx * gain, by * gain, bz + _ONE)
    w = (dx * detail_gain, dy * detail_gain, dz)
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


def _tops(look: RockLook, px: LookPixels, at: CellPixels) -> F32Grid:
    """Each pixel's top layer over its median, plain; one where its family has none."""
    out = np.ones((len(px.top), 3), np.float32)
    inverse = look.top_inverse()
    for tile in (int(t) for t in np.unique(px.top[px.top >= 0])):
        here = np.flatnonzero(px.top == tile)
        which = (tile, CELLS_NONE, np.float32(inverse[tile]))
        plain, _cell = read(look.albedo, which, at.subset(here))
        out[here] = plain / look.albedo_median[tile]
    return out


def look_texels(look: RockLook, px: LookPixels) -> LookTexels:
    """Every pixel of ``px``'s body, top layer and normal."""
    n = len(px.kind)
    at = CellPixels.at(look.noise, px.u, px.v)
    body = np.ones((n, 3), np.float32)
    mapped = np.zeros((n, 3), np.float32)
    for kind in np.unique(px.kind):
        here = np.flatnonzero(px.kind == kind)
        part = at.subset(here)
        body[here] = _body(look, int(kind), part)
        mapped[here] = _mapped(look, int(kind), part)
    return LookTexels(body, _tops(look, px, at), _onto(mapped, px.normal))
