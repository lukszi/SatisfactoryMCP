"""The textures and numbers a rock's surface is drawn with, read from the install.

The cliff body is the landscape Cliff layer's material, world-projected; the arches wear
their own rock texture; each cliff family's top layer is its root material's top texture.
Tile sizes come from the compiled landscape shader and the materials' parameters, the top
layer's mask from the cliff master's ``SlopeMask`` and ``CheapContrast``. docs/map/painted.md
section 30, "Rock textures" has the measurements.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import NamedTuple

from mapgen.gamedata.ground.landscape_albedo import decode_texture
from mapgen.gamedata.install import GameReader, open_package
from mapgen.gamedata.materials import material_parent, scalar_parameters
from mapgen.gamedata.rocks.families import FAMILIES, FAMILY_ROOTS, MAX_CHAIN, ROOT_DIR
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects

__all__ = [
    "ARCH_TILE_M",
    "CELL_TILE_M",
    "CLIFF_ALBEDO_TILE_M",
    "CLIFF_DETAIL_TILE_M",
    "CLIFF_NORMAL_TILE_M",
    "DESERT_TILE_M",
    "FALLOFF_CONTRAST",
    "FALLOFF_POWER",
    "LOOK_TEXTURES",
    "TOP_FAR_TILING",
    "RockTextures",
    "TopRule",
    "read_rock_textures",
]

#: The body's albedo, normal and detail normal, the anti-tiling cells, and the arches' rock.
LOOK_TEXTURES = {
    "cliff_albedo": "World/Environment/Rock/Cliff/Textures/CliffSediment/Cliff_Sediment_Alb",
    "cliff_normal": "World/Environment/Landscape/Texture/Tiles/Cliff/TX_Cliff_01_Nor",
    "cliff_detail": "World/Environment/Rock/Texture/T_Detail_Rocky_N",
    "cells": "MasterMaterials/Resources/Cell_Bombing_Noise_basecolor",
    "arch_albedo": "World/Environment/Rock/Arc/Texture/Textures/TX_Arc_Rock_BC",
    "arch_normal": "World/Environment/Rock/Arc/Texture/Textures/TX_Arc_Rock_N",
    "desert_albedo": "World/Environment/Rock/DesertRock/Texture/TX_DesertRock_Rough_01_Alb",
    "desert_normal": "World/Environment/Rock/DesertRock/Texture/TX_DesertRock_Rough_01_normal",
}

#: The landscape Cliff layer's albedo: UV0 times its ``Scale``, 0.05.
CLIFF_ALBEDO_TILE_M = 20.0
#: Its normal, world-projected at 1/1024 per cm, and the detail normal at 0.000690229 per cm.
CLIFF_NORMAL_TILE_M = 10.24
CLIFF_DETAIL_TILE_M = 14.4879
#: The rotated cells' noise: UV0 times 0.01.
CELL_TILE_M = 100.0
#: An arch mesh spans about 125 m per UV unit; its instances repeat the rock 13 to 15 times.
ARCH_TILE_M = 125.0 / 14.0
#: A desert rock mesh spans about 75 m per UV unit; ``DesertRock_WA`` repeats its rock 6 times.
DESERT_TILE_M = 75.0 / 6.0

#: ``Rock_WA``'s own ``Falloff_Power``, ``Falloff_Contrast`` and ``TopLayer Tiling Far``.
FALLOFF_POWER = 1.5
FALLOFF_CONTRAST = 1.2
TOP_FAR_TILING = 0.02

#: Decoded at most this wide: the full-size sheet reads a 20 m tile at 23 texels a pixel.
_MAX_PX = 1024


class TopRule(NamedTuple):
    """A cliff family's top layer: its texture, tile in metres, and its mask's power and
    contrast (``CheapContrast(nz ** power, contrast)``)."""

    texture: str
    tile_m: float
    power: float
    contrast: float


class RockTextures(NamedTuple):
    """The decoded textures, RGBA bytes by name, and each family's top layer by code."""

    textures: dict[str, U8Grid]
    tops: dict[int, tuple[TopRule, U8Grid]]
    sources: dict[str, str]


def read_rock_textures(
    store: IoStore, scripts: ScriptObjects, top_textures: Mapping[str, str | None]
) -> RockTextures:
    """Every texture of ``LOOK_TEXTURES``, and the top layer of each family ``top_textures``
    names (the paint store's ``rock_families``), with its root material's numbers."""
    decoder = importlib.import_module("texture2ddecoder")
    index = AssetIndex(store)
    game = GameReader(store, scripts, index, ClassFacts(store, index))
    textures = {
        name: decode_texture(game, decoder, asset, _MAX_PX, channels=4)
        for name, asset in LOOK_TEXTURES.items()
    }
    sources = dict(LOOK_TEXTURES)
    tops: dict[int, tuple[TopRule, U8Grid]] = {}
    roots = {family: leaf for leaf, family in FAMILY_ROOTS.items()}
    for family, path in top_textures.items():
        if family not in roots or not path:
            continue
        asset = path.split(".")[0].removeprefix("/Game/FactoryGame/")
        rule = _top_rule(game, ROOT_DIR + roots[family], asset)
        tops[FAMILIES.index(family)] = (rule, decode_texture(game, decoder, asset, _MAX_PX, 4))
        sources[f"top.{family}"] = asset
    return RockTextures(textures, tops, sources)


def _top_rule(game: GameReader, root: str, texture: str) -> TopRule:
    """A family root's top layer numbers: the nearest override up its chain, else the
    master's own."""
    found: dict[str, float] = {}
    current: str | None = root
    for _ in range(MAX_CHAIN):
        view = (
            None if current is None else open_package(game.store, game.scripts, game.index, current)
        )
        if view is None:
            break
        for name, value in scalar_parameters(view).items():
            found.setdefault(name, value)
        current = material_parent(view)
    tiling = found.get("TopLayer Tiling Far", TOP_FAR_TILING)
    return TopRule(
        texture,
        1.0 / tiling,
        found.get("Falloff_Power", FALLOFF_POWER),
        found.get("Falloff_Contrast", FALLOFF_CONTRAST),
    )
