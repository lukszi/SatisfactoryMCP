"""A run's rock look: the install's rock textures at the mip level the run's pixel reads them
at, packed into one albedo and one normal atlas for the texel kernel.

docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.gamedata.rocks.looks import (
    ARCH_TILE_M,
    CELL_TILE_M,
    CLIFF_ALBEDO_TILE_M,
    CLIFF_DETAIL_TILE_M,
    CLIFF_NORMAL_TILE_M,
    DESERT_TILE_M,
    RockTextures,
    TopRule,
)
from mapgen.terrain.texels import Atlas
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "ALBEDO_ARCH",
    "ALBEDO_CLIFF",
    "ALBEDO_DESERT",
    "ALBEDO_LAYER_BASE",
    "LAYER_BASE_M",
    "NORMAL_ARCH",
    "NORMAL_CLIFF",
    "NORMAL_DESERT",
    "NORMAL_DETAIL",
    "RockLook",
    "mip_level",
    "rock_look",
]

#: The albedo atlas's fixed tiles; the families' top layers follow them.
ALBEDO_CLIFF, ALBEDO_LAYER_BASE, ALBEDO_ARCH, ALBEDO_DESERT = 0, 1, 2, 3
#: The normal atlas's tiles.
NORMAL_CLIFF, NORMAL_DETAIL, NORMAL_ARCH, NORMAL_DESERT = 0, 1, 2, 3

#: The texture and span of each normal tile, in that order.
_NORMALS = (
    ("cliff_normal", CLIFF_NORMAL_TILE_M),
    ("cliff_detail", CLIFF_DETAIL_TILE_M),
    ("arch_normal", ARCH_TILE_M),
    ("desert_normal", DESERT_TILE_M),
)

#: The texel the landscape layer's colour already holds: the ground bake's 1 m.
LAYER_BASE_M = 1.0

#: The fewest texels a level keeps along a side.
_MIN_SIDE = 4


class RockLook(NamedTuple):
    """What the texel kernel reads: the albedo atlas (linear) with each tile's span in metres
    and median, the normal atlas (-1..1) with its tiles' spans, the anti-tiling cells (0..1)
    and their span, the cosine and sine of each cell turn, the tiles of the families' top
    layers and their rules, and the record the sidecar keeps."""

    albedo: Atlas
    albedo_tiles_m: F64Grid
    albedo_median: F32Grid
    normals: Atlas
    normal_tiles_m: F64Grid
    cells: F32Grid
    cell_tile_m: float
    turn: F64Grid
    tops: dict[int, int]
    rules: dict[int, TopRule]
    record: JsonObject


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
    spans = [CLIFF_ALBEDO_TILE_M, CLIFF_ALBEDO_TILE_M, ARCH_TILE_M, DESERT_TILE_M]
    for name, tile_m in (("arch_albedo", ARCH_TILE_M), ("desert_albedo", DESERT_TILE_M)):
        own = _linear(found[name])
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
        signed = _signed(found[name])
        normals.append(_mip(signed, level(name, signed.shape[0], tile_m)))
    cells = found["cells"].astype(np.float32) / np.float32(255.0)
    cells = _mip(cells, level("cells", cells.shape[0], CELL_TILE_M))
    turn = 2.0 * np.pi * np.arange(256, dtype=np.float64) / 255.0
    medians = np.stack([np.median(t.reshape(-1, 3), axis=0) for t in albedo])
    return RockLook(
        albedo=_pack(albedo),
        albedo_tiles_m=np.asarray(spans, np.float64),
        albedo_median=medians.astype(np.float32),
        normals=_pack(normals),
        normal_tiles_m=np.asarray([tile_m for _name, tile_m in _NORMALS], np.float64),
        cells=cells,
        cell_tile_m=CELL_TILE_M,
        turn=np.stack([np.cos(turn), np.sin(turn)], -1),
        tops=tops,
        rules=rules,
        record=_record(textures, levels, rules),
    )


def _record(
    textures: RockTextures, levels: dict[str, int], rules: dict[int, TopRule]
) -> JsonObject:
    """What the sidecar says of the look: the textures read, each one's level and span, and
    each family's top layer."""
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
        },
        "tops": {
            FAMILIES[code]: {
                "tile_m": round(rule.tile_m, 3),
                "falloff_power": rule.power,
                "falloff_contrast": rule.contrast,
            }
            for code, rule in sorted(rules.items())
        },
    }
