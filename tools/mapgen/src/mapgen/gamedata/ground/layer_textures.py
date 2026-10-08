"""The landscape layers' own textures, read as the game's material reads them from above.

Each paint layer's albedo, normal and height texture, with the metres one repeat spans at
the scale a top-down view shows and the layer's anti-tiling, as the compiled landscape
shaders state them; and the cell-bombing noise those shaders turn their cells by. The paint
command keeps every texture at the size a full-size render needs, in one blob of the paint
store. docs/map/painted.md section 30, "The layers' own textures".
"""

from __future__ import annotations

import time
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import numpy as np

from mapgen.gamedata.frame import Z7_TEXEL_M
from mapgen.gamedata.ground.landscape_albedo import TEXTURES as MEAN_TEXTURES
from mapgen.gamedata.ground.landscape_albedo import TILES
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "CELLS_NONE",
    "CELLS_OFFSET",
    "CELLS_ROTATED",
    "GROUND_TEXTURES_NAME",
    "LAYERS",
    "NOISE",
    "NOISE_TILE_M",
    "OVERLAYS",
    "TEXTURES",
    "LayerTextures",
    "Read",
    "decode_ground_textures",
    "encode_ground_textures",
]

GROUND_TEXTURES_NAME = "ground.tex.z"

#: A read's anti-tiling: none, a second read in turned, shifted and scaled cells, or in
#: cells shifted and scaled only, so the ripples keep their direction.
CELLS_NONE, CELLS_ROTATED, CELLS_OFFSET = 0, 1, 2

#: The shaders' UV0 multipliers as metres a repeat (0.25, 0.22, 0.07, 0.05, 0.02, 0.01),
#: and the cliff's world projection at 1/1024 a centimetre.
NEAR_M = 4.0
NEAR_WIDE_M = 1.0 / 0.22
FOREST_FAR_M = 1.0 / 0.07
FAR_M = 20.0
GRASS_FAR_M = 50.0
CLIFF_WORLD_M = 10.24
NOISE_TILE_M = 100.0

#: The cell-bombing noise: R and G a cell's shift and scale, B its turn, A its mask.
NOISE = "Cell_Bombing_Noise_basecolor"
_NOISE_ASSET = "MasterMaterials/Resources/Cell_Bombing_Noise_basecolor"
_NOISE_PX = 1024

_RGB, _RG, _R, _B = (0, 1, 2), (0, 1), (0,), (2,)

#: Texture -> (asset, the channels kept): RGB albedo, RG normal, or the height channel the
#: shaders read (R of an HRA texture and of the cracks' Refl, B of the sand rock's ORMA).
TEXTURES: dict[str, tuple[str, tuple[int, ...]]] = {
    "TX_Grass_Far_01_Alb": (TILES + "Grass/TX_Grass_Far_01_Alb", _RGB),
    "TX_Grass_01_Nor": (TILES + "Grass/TX_Grass_01_Nor", _RG),
    "TX_Grass_01_HRA": (TILES + "Grass/TX_Grass_01_HRA", _R),
    "TX_Forest_Far_01_Alb": (TILES + "Forest/TX_Forest_Far_01_Alb", _RGB),
    "TX_Forest_01_HRA": (TILES + "Forest/TX_Forest_01_HRA", _R),
    "TX_GrassRed_01_Alb": (TILES + "GrassRed/TX_GrassRed_01_Alb", _RGB),
    "TX_GrassRed_01_Nor": (TILES + "GrassRed/TX_GrassRed_01_Nor", _RG),
    "TX_GrassRed_01_HRA": (TILES + "GrassRed/TX_GrassRed_01_HRA", _R),
    "TX_Grass_RedJungle_01_Alb": (TILES + "RedJungle/TX_Grass_RedJungle_01_Alb", _RGB),
    "TX_Soil_01_Alb": (TILES + "Soil/TX_Soil_01_Alb", _RGB),
    "TX_Soil_01_Nor": (TILES + "Soil/TX_Soil_01_Nor", _RG),
    "TX_Soil_01_HRA": (TILES + "Soil/TX_Soil_01_HRA", _R),
    "Gravel_Alb": (TILES + "Stones/Gravel_Alb", _RGB),
    "Gravel_Nor": (TILES + "Stones/Gravel_Nor", _RG),
    "Gravel_HRA": (TILES + "Stones/Gravel_HRA", _R),
    "TX_Sand_BC": (TILES + "Sand/TX_Sand_BC", _RGB),
    "TX_Sand_Normal": (TILES + "Sand/TX_Sand_Normal", _RG),
    "TX_Sand_Nor_Wet": (TILES + "Sand/TX_Sand_Nor_Wet", _RG),
    "TX_Sand_HRA": (TILES + "Sand/TX_Sand_HRA", _R),
    "TX_SandRipples_BC": (TILES + "Sand/TX_SandRipples_BC", _RGB),
    "TX_SandRipples_HRA": (TILES + "Sand/TX_SandRipples_HRA", _R),
    "Sand_Dry_02_Alb": (TILES + "Sand/Sand_Dry_02_Alb", _RGB),
    "Sand_Dry_02_Nor": (TILES + "Sand/Sand_Dry_02_Nor", _RG),
    "Sand_Dry_02_Refl": (TILES + "Sand/Sand_Dry_02_Refl", _R),
    "TX_SandPebbles_01_Alb": (TILES + "Pebbels/TX_SandPebbles_01_Alb", _RGB),
    "TX_SandPebbles_01_Nor": (TILES + "Pebbels/TX_SandPebbles_01_Nor", _RG),
    "TX_SandPebbles_01_HRA": (TILES + "Pebbels/TX_SandPebbles_01_HRA", _R),
    "TX_SandRock_Alb_01": (TILES + "SandRock/TX_SandRock_Alb_01", _RGB),
    "TX_SandRock_Nor_01": (TILES + "SandRock/TX_SandRock_Nor_01", _RG),
    "TX_SandRock_ORMA_01": (TILES + "SandRock/TX_SandRock_ORMA_01", _B),
    "TX_SeaRocks_01_Alb": (TILES + "SeaRocks/TX_SeaRocks_01_Alb", _RGB),
    "TX_SeaRocks_01_Nor": (TILES + "SeaRocks/TX_SeaRocks_01_Nor", _RG),
    "TX_SeaRocks_01_HRA": (TILES + "SeaRocks/TX_SeaRocks_01_HRA", _R),
    "Cliff_Sediment_Alb": (MEAN_TEXTURES["Cliff_Sediment_Alb"], _RGB),
    "TX_Cliff_01_Nor": (TILES + "Cliff/TX_Cliff_01_Nor", _RG),
    "TX_Cliff_01_HRA": (TILES + "Cliff/TX_Cliff_01_HRA", _R),
    "TX_Puddles_01_Alb": (TILES + "Soil/TX_Puddles_01_Alb", _RGB),
    "TX_Puddles_01_Nor": (TILES + "Soil/TX_Puddles_01_Nor", _RG),
}


class Read(NamedTuple):
    """One texture read of a layer: the texture, the metres one repeat spans, its cells, and
    the share of its detail that shows from above, where the far blend mixes it with a flat
    colour or a flat normal."""

    texture: str
    tile_m: float
    cells: int = CELLS_NONE
    strength: float = 1.0


class LayerTextures(NamedTuple):
    """A layer's albedo, its normal (None: flat from above) and the height its blend reads
    (None: not height-blended)."""

    albedo: Read
    normal: Read | None
    height: Read | None


def _near(albedo: str, normal: str, height: str, tile_m: float, cells: int) -> LayerTextures:
    """A layer whose three reads share one repeat and one anti-tiling."""
    reads = (Read(name, tile_m, cells) for name in (albedo, normal, height))
    return LayerTextures(*reads)


_ROT, _OFF = CELLS_ROTATED, CELLS_OFFSET
_FOREST = LayerTextures(
    Read("TX_Forest_Far_01_Alb", FOREST_FAR_M), None, Read("TX_Forest_01_HRA", NEAR_WIDE_M)
)
_SAND_ROCK = _near(
    "TX_SandRock_Alb_01", "TX_SandRock_Nor_01", "TX_SandRock_ORMA_01", NEAR_WIDE_M, CELLS_NONE
)

#: Paint layer -> its reads. Top-down the far blends show: grass's albedo at 50 m, the
#: forest's at 14.29 m, the sand's, cracks' and coral rock's at 20 m. The sand's far albedo
#: is mixed half and half with its far colour and its far normal taken at 0.2; the coral
#: rock's normal goes far with its albedo; the forest's and the ripples' normals fade to
#: flat, and the ripples' albedo to a flat far colour. The heights the blend reads stay at
#: the near scale. Red jungle has no normal or height of its own and borrows the grass's.
LAYERS: dict[str, LayerTextures] = {
    "Grass_LayerInfo": LayerTextures(
        Read("TX_Grass_Far_01_Alb", GRASS_FAR_M),
        Read("TX_Grass_01_Nor", NEAR_M),
        Read("TX_Grass_01_HRA", NEAR_M),
    ),
    "Forest_LayerInfo": _FOREST,
    "PurpleForest_LayerInfo": _FOREST,
    "GrassRed_LayerInfo": _near(
        "TX_GrassRed_01_Alb", "TX_GrassRed_01_Nor", "TX_GrassRed_01_HRA", NEAR_M, _ROT
    ),
    "RedJungle_LayerInfo": _near(
        "TX_Grass_RedJungle_01_Alb", "TX_Grass_01_Nor", "TX_Grass_01_HRA", NEAR_M, CELLS_NONE
    ),
    "Soil_LayerInfo": _near(
        "TX_Soil_01_Alb", "TX_Soil_01_Nor", "TX_Soil_01_HRA", NEAR_M, CELLS_NONE
    ),
    "Gravel_WeightLayerInfo": _near("Gravel_Alb", "Gravel_Nor", "Gravel_HRA", NEAR_M, _ROT),
    "Sand_LayerInfo": LayerTextures(
        Read("TX_Sand_BC", FAR_M, CELLS_NONE, 0.5),
        Read("TX_Sand_Normal", FAR_M, CELLS_NONE, 0.2),
        Read("TX_Sand_HRA", NEAR_M, _ROT),
    ),
    "WetSand_LayerInfo": _near("TX_Sand_BC", "TX_Sand_Nor_Wet", "TX_Sand_HRA", NEAR_M, _ROT),
    "SandRipples_LayerInfo": LayerTextures(
        Read("TX_SandRipples_BC", NEAR_M, _OFF, 0.0), None, Read("TX_SandRipples_HRA", NEAR_M, _OFF)
    ),
    "SandCracks_LayerInfo": LayerTextures(
        Read("Sand_Dry_02_Alb", FAR_M),
        Read("Sand_Dry_02_Nor", NEAR_WIDE_M),
        Read("Sand_Dry_02_Refl", NEAR_WIDE_M),
    ),
    "SandPebbles_LayerInfo": _near(
        "TX_SandPebbles_01_Alb", "TX_SandPebbles_01_Nor", "TX_SandPebbles_01_HRA", NEAR_M, _ROT
    ),
    "SandRock_LayerInfo": _SAND_ROCK,
    "DesertRock_LayerInfo": _SAND_ROCK,
    "CoralRock_LayerInfo": LayerTextures(
        Read("TX_SeaRocks_01_Alb", FAR_M),
        Read("TX_SeaRocks_01_Nor", FAR_M),
        Read("TX_SeaRocks_01_HRA", NEAR_WIDE_M),
    ),
    "Cliff_LayerInfo": LayerTextures(
        Read("Cliff_Sediment_Alb", FAR_M, _ROT),
        Read("TX_Cliff_01_Nor", CLIFF_WORLD_M),
        Read("TX_Cliff_01_HRA", CLIFF_WORLD_M),
    ),
}

#: Not weight-blended: lerped over the blend by its own weight.
OVERLAYS: dict[str, LayerTextures] = {
    "Puddles_LayerInfo": LayerTextures(
        Read("TX_Puddles_01_Alb", NEAR_M, _ROT), Read("TX_Puddles_01_Nor", NEAR_M, _ROT), None
    ),
}


def _stored_px(name: str) -> int:
    """The side a texture is kept at: a power of two, at least a texel per half a full-size
    pixel at the largest repeat any layer reads it at, 64 to 512."""
    layers = (*LAYERS.values(), *OVERLAYS.values())
    tile = max(r.tile_m for layer in layers for r in layer if r is not None and r.texture == name)
    return int(np.clip(2 ** np.ceil(np.log2(tile / (Z7_TEXEL_M / 2))), 64, 512))


def encode_ground_textures(rgba: Callable[[str, int], U8Grid]) -> tuple[bytes, JsonObject]:
    """Every texture and the noise, read by ``rgba(asset, max side)``, as one zlib blob of
    their kept channels, and the index the paint store's meta records."""
    started = time.time()
    wanted = {name: (asset, kept, _stored_px(name)) for name, (asset, kept) in TEXTURES.items()}
    wanted[NOISE] = (_NOISE_ASSET, (0, 1, 2, 3), _NOISE_PX)
    parts: list[bytes] = []
    index: JsonObject = {}
    offset = 0
    for name, (asset, kept, side) in sorted(wanted.items()):
        texels = np.ascontiguousarray(rgba(asset, side)[..., list(kept)], np.uint8)
        shape: list[JsonValue] = [int(n) for n in texels.shape]
        index[name] = {"asset": asset, "shape": shape, "offset": offset}
        parts.append(texels.tobytes())
        offset += texels.nbytes
    blob = zlib.compress(b"".join(parts), 6)
    return blob, {"textures": index, "seconds": round(time.time() - started, 1)}


def decode_ground_textures(path: Path, index: JsonObject) -> dict[str, U8Grid]:
    """The blob at ``path`` back into its textures by name, as ``index`` lays them out."""
    raw = zlib.decompress(path.read_bytes())
    entries = index.get("textures")
    if not isinstance(entries, dict):
        raise TypeError(f"{path}: the ground textures' index lists no textures")
    out: dict[str, U8Grid] = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            raise TypeError(f"{path}: texture {name} has no entry")
        shape = tuple(_ints(entry.get("shape")))
        (start,) = _ints([entry.get("offset")])
        out[name] = np.frombuffer(raw, np.uint8, int(np.prod(shape)), start).reshape(shape)
    return out


def _ints(value: JsonValue) -> list[int]:
    if not isinstance(value, list) or not all(type(v) is int for v in value):
        raise TypeError(f"{value!r} is not a list of integers")
    return [v for v in value if type(v) is int]
