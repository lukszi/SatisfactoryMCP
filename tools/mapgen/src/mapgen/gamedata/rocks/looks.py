"""The textures and numbers a rock's surface is drawn with, read from the install.

The cliff body is the landscape Cliff layer's material, world-projected; the arches, the
desert rock, the shells and the barnacles wear their own textures; each cliff family's top layer is its
root material's top texture. Tile sizes come from the compiled shaders and
the materials' parameters, the top layer's mask from the cliff master's ``SlopeMask`` and
``CheapContrast``. docs/map/painted.md section 30, "Rock textures" has the measurements.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import NamedTuple

from mapgen.gamedata.ground.landscape_albedo import decode_texture
from mapgen.gamedata.install import GameReader, open_package
from mapgen.gamedata.materials import material_parent, scalar_parameters, switch_parameters
from mapgen.gamedata.rocks.families import FAMILIES, FAMILY_ROOTS, MAX_CHAIN, ROOT_DIR
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects

__all__ = [
    "ARCH_TILE_M",
    "BARNACLE_CUT",
    "BARNACLE_FADE",
    "BARNACLE_TILE_M",
    "BARNACLE_UV_M",
    "CELL_TILE_M",
    "CLIFF_ALBEDO_TILE_M",
    "CLIFF_DETAIL_TILE_M",
    "CLIFF_NORMAL_TILE_M",
    "DESERT_TILE_M",
    "FALLOFF_CONTRAST",
    "FALLOFF_POWER",
    "LOOK_TEXTURES",
    "SHELL_CUT",
    "SHELL_DETAIL_TILE_M",
    "SHELL_FADE",
    "SHELL_TILE_M",
    "SHELL_UV_M",
    "TERRACE_CUT",
    "TERRACE_FADE",
    "TERRACE_TILE_M",
    "TERRACE_UV_M",
    "TOP_FAR_SWITCH",
    "TOP_FAR_TILING",
    "RockTextures",
    "TopRule",
    "read_rock_textures",
    "top_tile_m",
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
    "shell_albedo": "World/Environment/Foliage/Coral/SmallShell/Textures/TX_SmallShell_BC",
    "shell_normal": "World/Environment/Foliage/Coral/SmallShell/Textures/TX_SmallShell_N",
    "shell_detail": "World/Environment/Rock/SeaRock/Texture/SeaRock_Detail_Nor",
    "barnacle_albedo": "World/Environment/Foliage/Coral/Barnacle/Textures/TX_Barnacle_01_Alb",
    "barnacle_normal": "World/Environment/Foliage/Coral/Barnacle/Textures/TX_Barnacle_01_Nor",
    "terrace_albedo": "World/Environment/HotSpring/Texture/HotSpring_Plateau1And3_Alb",
    "terrace_normal": "World/Environment/HotSpring/Texture/HotSpring_Plateau1And3_Nor",
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
#: ``DesertRock_WA`` repeats its rock 6 times a UV unit, scaled with the mesh; the desert
#: seen from above is mostly ``SM_DesertRock_05``, whose up faces span 102.9 m a UV unit.
DESERT_TILE_M = 102.9 / 6.0
#: A shell's UV unit: ``SmallShell`` (4,028 of 4,223 shells) spans 58.7 m a UV unit and is
#: placed at a median 0.545, which its material does not undo.
SHELL_UV_M = 58.7 * 0.545
#: The square of its texture's first island the look tiles, ``(u, v, side)`` in UV units, the
#: run after it faded into its start, and the tile's span.
SHELL_CUT = (0.15, 0.13, 0.3125)
SHELL_FADE = 0.0625
SHELL_TILE_M = SHELL_CUT[2] * SHELL_UV_M
#: ``SmallShell_Inst`` repeats its detail normal 22.8 times a UV unit.
SHELL_DETAIL_TILE_M = SHELL_UV_M / 22.8
#: A barnacle's UV unit: ``SM_Barnacle_01`` (12,404 of 13,087 barnacles) spans 5.09 m a UV
#: unit over its up faces and is placed at a median 0.647, which ``Asset_Master`` keeps.
BARNACLE_UV_M = 5.09 * 0.647
#: The square of its texture the look tiles, and the run after it, as for the shells.
BARNACLE_CUT = (0.0625, 0.375, 0.5)
BARNACLE_FADE = 0.125
BARNACLE_TILE_M = BARNACLE_CUT[2] * BARNACLE_UV_M
#: A terrace's UV unit: ``Plateau1and3``'s meshes, ``Hotspring_Plateau_01`` and ``_03``, span
#: 27.1 and 25.1 m a UV unit over their up faces; their 45 instances are placed at a median 1.04.
TERRACE_UV_M = 26.2 * 1.04
#: The square of the island their up faces mostly read, and the run after it, as for the shells.
TERRACE_CUT = (0.03, 0.5, 0.25)
TERRACE_FADE = 0.03125
TERRACE_TILE_M = TERRACE_CUT[2] * TERRACE_UV_M

#: ``Rock_WA``'s own ``Falloff_Power``, ``Falloff_Contrast`` and ``TopLayer Tiling Far``.
FALLOFF_POWER = 1.5
FALLOFF_CONTRAST = 1.2
TOP_FAR_TILING = 0.02
#: The static switch that reads the top at ``TopLayer Tiling Far``; a family without it
#: reads its top at ``TopLayer Tiling``, the near repeat.
TOP_FAR_SWITCH = "Use Far Albedo"

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


def top_tile_m(scalars: Mapping[str, float], switches: Mapping[str, bool]) -> float:
    """A family's top layer tile in metres: the far repeat where ``TOP_FAR_SWITCH`` is on,
    else the near ``TopLayer Tiling`` (the far one when the chain sets none)."""
    far = scalars.get("TopLayer Tiling Far", TOP_FAR_TILING)
    tiling = far if switches.get(TOP_FAR_SWITCH, False) else scalars.get("TopLayer Tiling", far)
    return 1.0 / tiling


def _top_rule(game: GameReader, root: str, texture: str) -> TopRule:
    """A family root's top layer numbers: the nearest override up its chain, else the
    master's own. The repeat is the far one where the chain switches ``TOP_FAR_SWITCH`` on,
    else the near one."""
    found: dict[str, float] = {}
    switches: dict[str, bool] = {}
    current: str | None = root
    for _ in range(MAX_CHAIN):
        view = (
            None if current is None else open_package(game.store, game.scripts, game.index, current)
        )
        if view is None:
            break
        for name, value in scalar_parameters(view).items():
            found.setdefault(name, value)
        for name, on in switch_parameters(view).items():
            switches.setdefault(name, on)
        current = material_parent(view)
    return TopRule(
        texture,
        top_tile_m(found, switches),
        found.get("Falloff_Power", FALLOFF_POWER),
        found.get("Falloff_Contrast", FALLOFF_CONTRAST),
    )
