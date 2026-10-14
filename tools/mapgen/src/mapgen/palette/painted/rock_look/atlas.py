"""A run's rock look: the install's rock textures at the mip level the run's pixel reads them
at, packed into one albedo and one normal atlas, and what each kind of rock reads of them.

docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.gamedata.ground.layer_textures import CELLS_NONE, CELLS_ROTATED
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.gamedata.rocks.looks import (
    ARCH_TILE_M,
    BARNACLE_CUT,
    BARNACLE_FADE,
    BARNACLE_TILE_M,
    CELL_TILE_M,
    CLIFF_ALBEDO_TILE_M,
    CLIFF_DETAIL_TILE_M,
    CLIFF_NORMAL_TILE_M,
    DESERT_TILE_M,
    SHELL_CUT,
    SHELL_DETAIL_TILE_M,
    SHELL_FADE,
    SHELL_TILE_M,
    RockTextures,
    TopRule,
)
from mapgen.terrain.ground_detail.cells import noise_terms
from mapgen.terrain.texels import Atlas
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I32Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "ALBEDO",
    "ALBEDO_ARCH",
    "ALBEDO_BARNACLE",
    "ALBEDO_CLIFF",
    "ALBEDO_DESERT",
    "ALBEDO_LAYER_BASE",
    "ALBEDO_SHELL",
    "BASE",
    "DETAIL",
    "KINDS",
    "KIND_ARCH",
    "KIND_BARNACLE",
    "KIND_CLIFF",
    "KIND_DESERT",
    "KIND_LAYER",
    "KIND_SHELL",
    "LAYER_BASE_M",
    "UNDER",
    "RockLook",
    "mip_level",
    "rock_look",
    "seamless_cut",
]

#: What a pixel is drawn as: a cliff, the landscape's Cliff layer, an arch or boulder, desert
#: rock, a shell or a barnacle. 0 is none.
KIND_CLIFF, KIND_LAYER, KIND_ARCH, KIND_DESERT, KIND_SHELL, KIND_BARNACLE = 1, 2, 3, 4, 5, 6
KINDS = 7
#: A kind's reads, in the table's order: its albedo, what the albedo is measured against (none:
#: the albedo tile's median), its base normal map and the detail laid on it.
ALBEDO, UNDER, BASE, DETAIL = 0, 1, 2, 3

#: The albedo atlas's fixed tiles; the families' top layers follow them.
ALBEDO_CLIFF, ALBEDO_LAYER_BASE, ALBEDO_ARCH, ALBEDO_DESERT, ALBEDO_SHELL = 0, 1, 2, 3, 4
ALBEDO_BARNACLE = 5
#: The texture and span of each normal tile, in the normal atlas's order.
_NORMALS = (
    ("cliff_normal", CLIFF_NORMAL_TILE_M),
    ("cliff_detail", CLIFF_DETAIL_TILE_M),
    ("arch_normal", ARCH_TILE_M),
    ("desert_normal", DESERT_TILE_M),
    ("shell_normal", SHELL_TILE_M),
    ("shell_detail", SHELL_DETAIL_TILE_M),
    ("barnacle_normal", BARNACLE_TILE_M),
)
_N_CLIFF, _N_DETAIL, _N_ARCH, _N_DESERT, _N_SHELL, _N_SHELL_DETAIL, _N_BARNACLE = range(
    len(_NORMALS)
)
#: The textures cut from a mesh's own UV layout: their cut and the run faded into it.
_CUTS = {
    "shell_albedo": (SHELL_CUT, SHELL_FADE),
    "shell_normal": (SHELL_CUT, SHELL_FADE),
    "barnacle_albedo": (BARNACLE_CUT, BARNACLE_FADE),
    "barnacle_normal": (BARNACLE_CUT, BARNACLE_FADE),
}

#: Per kind, ``(tile, cells)`` of each read, -1 for none, and the slope gains of its base
#: and detail normal maps. The cliff and the layer read as the landscape's Cliff layer does;
#: every other texture takes the rotated cells for the meshes' own UVs (painted.md section 30).
_ROT, _NONE = CELLS_ROTATED, CELLS_NONE
_CLIFF_NORMALS = ((_N_CLIFF, _NONE), (_N_DETAIL, _NONE))
_READS: dict[int, tuple[tuple[int, int], ...]] = {
    KIND_CLIFF: ((ALBEDO_CLIFF, _ROT), (-1, _NONE), *_CLIFF_NORMALS),
    KIND_LAYER: ((ALBEDO_CLIFF, _ROT), (ALBEDO_LAYER_BASE, _ROT), *_CLIFF_NORMALS),
    KIND_ARCH: ((ALBEDO_ARCH, _ROT), (-1, _NONE), (_N_ARCH, _ROT), (-1, _NONE)),
    KIND_DESERT: ((ALBEDO_DESERT, _ROT), (-1, _NONE), (-1, _NONE), (_N_DESERT, _ROT)),
    KIND_SHELL: ((ALBEDO_SHELL, _ROT), (-1, _NONE), (_N_SHELL, _ROT), (_N_SHELL_DETAIL, _ROT)),
    KIND_BARNACLE: ((ALBEDO_BARNACLE, _ROT), (-1, _NONE), (_N_BARNACLE, _ROT), (-1, _NONE)),
}
_GAINS = {KIND_CLIFF: (2.0, 0.5), KIND_LAYER: (2.0, 0.5), KIND_ARCH: (2.0, 1.0)}

#: The texel the landscape layer's colour already holds: the ground bake's 1 m.
LAYER_BASE_M = 1.0

#: The fewest texels a level keeps along a side.
_MIN_SIDE = 4


class RockLook(NamedTuple):
    """What the texel kernel reads: the albedo atlas (linear) with each tile's span in metres
    and median, the normal atlas (-1..1) with its tiles' spans, the cell noise's terms, each
    kind's reads (``(KINDS, 4, 2)``: tile and cells), their inverse repeats (``(KINDS, 4)``)
    and slope gains (``(KINDS, 2)``), the tiles of the families' top layers and their rules,
    and the record the sidecar keeps."""

    albedo: Atlas
    albedo_tiles_m: F64Grid
    albedo_median: F32Grid
    normals: Atlas
    normal_tiles_m: F64Grid
    noise: F32Grid
    reads: I32Grid
    inverse: F32Grid
    gains: F32Grid
    tops: dict[int, int]
    rules: dict[int, TopRule]
    record: JsonObject

    def top_inverse(self) -> F32Grid:
        """Each albedo tile's inverse repeat, as a top layer reads it."""
        return (1.0 / self.albedo_tiles_m).astype(np.float32)


def mip_level(side: int, tile_m: float, spacing_m: float) -> int:
    """The level whose texel is nearest a pixel of ``spacing_m``, at most ``_MIN_SIDE`` texels
    a side: the texture is box-filtered to about the pixel, so it never aliases."""
    finest = max(round(math.log2(spacing_m * side / tile_m)), 0)
    return min(finest, int(math.log2(side / _MIN_SIDE)))


def _mip(texels: F32Grid, level: int) -> F32Grid:
    """``texels`` box-filtered ``level`` times, two by two."""
    out = texels
    for _ in range(level):
        h, w, c = out.shape
        out = out.reshape(h // 2, 2, w // 2, 2, c).mean(axis=(1, 3), dtype=np.float32)
    return np.ascontiguousarray(out, np.float32)


def _linear(rgba: U8Grid) -> F32Grid:
    return srgb_unit_to_linear(rgba[..., :3].astype(np.float32) / np.float32(255.0))


def _signed(rgba: U8Grid) -> F32Grid:
    """A normal map's red and green as -1..1; blue is rebuilt from them."""
    return rgba[..., :2].astype(np.float32) / np.float32(255.0) * np.float32(2.0) - np.float32(1.0)


def _pack(tiles: list[F32Grid]) -> Atlas:
    """Tiles side by side from column 0, each in its own columns."""
    height = max(t.shape[0] for t in tiles)
    width = sum(t.shape[1] for t in tiles)
    texels = np.zeros((height, width, tiles[0].shape[2]), np.float32)
    boxes = np.zeros((len(tiles), 4), np.int32)
    x0 = 0
    for k, tile in enumerate(tiles):
        h, w = tile.shape[:2]
        texels[:h, x0 : x0 + w] = tile
        boxes[k] = (x0, 0, w, h)
        x0 += w
    return Atlas(texels, boxes)


def seamless_cut(
    texels: F32Grid,
    cut: tuple[float, float, float] = SHELL_CUT,
    fade: float = SHELL_FADE,
) -> F32Grid:
    """``cut`` of ``texels``, ``(u, v, side)`` in UV units, as a tile that repeats without a
    seam: the ``fade`` after the cut, along each axis, faded into its start, so its last texel
    runs on into the texel after it."""
    rows, cols = texels.shape[:2]
    u0, v0, side = cut
    c0, r0 = round(u0 * cols), round(v0 * rows)
    n, m = round(side * cols), round(fade * cols)
    tile = texels[r0 : r0 + n + m, c0 : c0 + n + m].astype(np.float32)
    ramp: F32Grid = (np.arange(m, dtype=np.float32) + np.float32(0.5)) / np.float32(m)
    for axis in (1, 0):
        head, tail = np.take(tile, range(m), axis), np.take(tile, range(n, n + m), axis)
        weight = ramp.reshape([-1 if k == axis else 1 for k in range(tile.ndim)])
        tile = np.concatenate(
            [tail + weight * (head - tail), np.take(tile, range(m, n), axis)], axis
        )
    return np.ascontiguousarray(tile, np.float32)


def _cut(name: str, texels: F32Grid) -> F32Grid:
    """A texture as the look tiles it: its seamless cut where it has one (``_CUTS``)."""
    return seamless_cut(texels, *_CUTS[name]) if name in _CUTS else texels


def _table(spans: list[float]) -> tuple[I32Grid, F32Grid, F32Grid]:
    """Each kind's reads, their inverse repeats and its slope gains."""
    reads = np.full((KINDS, 4, 2), -1, np.int32)
    reads[..., 1] = CELLS_NONE
    inverse = np.ones((KINDS, 4), np.float32)
    gains = np.ones((KINDS, 2), np.float32)
    normal_spans = [tile_m for _name, tile_m in _NORMALS]
    for kind, kind_reads in _READS.items():
        for slot, (tile, cells) in enumerate(kind_reads):
            reads[kind, slot] = (tile, cells)
            if tile >= 0:
                span = spans[tile] if slot in (ALBEDO, UNDER) else normal_spans[tile]
                inverse[kind, slot] = np.float32(1.0 / span)
        gains[kind] = _GAINS.get(kind, (1.0, 1.0))
    return reads, inverse, gains


def rock_look(textures: RockTextures, spacing_m: float) -> RockLook:
    """The textures at the levels a pixel of ``spacing_m`` reads them at."""
    found = textures.textures
    levels: dict[str, int] = {}

    def level(name: str, side: int, tile_m: float) -> int:
        levels[name] = mip_level(side, tile_m, spacing_m)
        return levels[name]

    cliff = _linear(found["cliff_albedo"])
    side = cliff.shape[0]
    fine = level("cliff_albedo", side, CLIFF_ALBEDO_TILE_M)
    levels["layer_base"] = max(fine, mip_level(side, CLIFF_ALBEDO_TILE_M, LAYER_BASE_M))
    albedo = [_mip(cliff, fine), _mip(cliff, levels["layer_base"])]
    own_tiles = (
        ("arch_albedo", ARCH_TILE_M),
        ("desert_albedo", DESERT_TILE_M),
        ("shell_albedo", SHELL_TILE_M),
        ("barnacle_albedo", BARNACLE_TILE_M),
    )
    spans = [CLIFF_ALBEDO_TILE_M, CLIFF_ALBEDO_TILE_M, *(tile_m for _name, tile_m in own_tiles)]
    for name, tile_m in own_tiles:
        own = _cut(name, _linear(found[name]))
        albedo.append(_mip(own, level(name, own.shape[0], tile_m)))
    tops: dict[int, int] = {}
    rules: dict[int, TopRule] = {}
    for code, (rule, rgba) in sorted(textures.tops.items()):
        top = _linear(rgba)
        name = f"top.{FAMILIES[code]}"
        tops[code], rules[code] = len(albedo), rule
        albedo.append(_mip(top, level(name, top.shape[0], rule.tile_m)))
        spans.append(rule.tile_m)
    normals: list[F32Grid] = []
    for name, tile_m in _NORMALS:
        signed = _cut(name, _signed(found[name]))
        normals.append(_mip(signed, level(name, signed.shape[0], tile_m)))
    medians = np.stack([np.median(t.reshape(-1, 3), axis=0) for t in albedo])
    reads, inverse, gains = _table(spans)
    return RockLook(
        albedo=_pack(albedo),
        albedo_tiles_m=np.asarray(spans, np.float64),
        albedo_median=medians.astype(np.float32),
        normals=_pack(normals),
        normal_tiles_m=np.asarray([tile_m for _name, tile_m in _NORMALS], np.float64),
        noise=noise_terms(found["cells"]),
        reads=reads,
        inverse=inverse,
        gains=gains,
        tops=tops,
        rules=rules,
        record=_record(textures, levels, rules),
    )


def _record(
    textures: RockTextures, levels: dict[str, int], rules: dict[int, TopRule]
) -> JsonObject:
    """What the sidecar says of the look: the textures read, each one's level and span, the
    textures turned in cells, and each family's top layer."""
    return {
        "textures": dict(textures.sources),
        "levels": dict(levels),
        "tiles_m": {
            "cliff_albedo": CLIFF_ALBEDO_TILE_M,
            "cliff_normal": CLIFF_NORMAL_TILE_M,
            "cliff_detail": CLIFF_DETAIL_TILE_M,
            "cells": CELL_TILE_M,
            "arch": round(ARCH_TILE_M, 3),
            "desert": round(DESERT_TILE_M, 3),
            "shell": round(SHELL_TILE_M, 3),
            "shell_detail": round(SHELL_DETAIL_TILE_M, 3),
            "barnacle": round(BARNACLE_TILE_M, 3),
        },
        "shell_cut_uv": list(SHELL_CUT),
        "barnacle_cut_uv": list(BARNACLE_CUT),
        "rotated_cells": ["cliff_albedo", "arch", "desert", "shell", "barnacle"],
        "tops": {
            FAMILIES[code]: {
                "tile_m": round(rule.tile_m, 3),
                "falloff_power": rule.power,
                "falloff_contrast": rule.contrast,
            }
            for code, rule in sorted(rules.items())
        },
    }
