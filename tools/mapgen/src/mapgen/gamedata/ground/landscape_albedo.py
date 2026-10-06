"""The paint layers' albedo: their textures and material parameters, and rock colours."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import TypeVar

import numpy as np
import numpy.typing as npt

from mapgen.gamedata.install import GameReader
from mapgen.gamedata.materials import material_parameters
from mapgen.gamedata.rocks.families import FAMILIES, family_sources
from satisfactory_mcp.core.arrays import F64Grid, U8Grid
from satisfactory_mcp.core.gameassets.packages import PackageView, class_name_of
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "CANOPY_TEXTURE",
    "GAME_ROOT",
    "LAYERS",
    "MATERIAL",
    "OVERLAYS",
    "PIGMENT",
    "PIGMENT_MAX_PX",
    "ROCK_TEXTURES",
    "TEXTURES",
    "TILES",
    "decode_texture",
    "layer_albedo",
    "material_vectors",
    "rock_family_colours",
    "srgb_to_linear",
    "srgb_unit_to_linear",
]


GAME_ROOT = "../../../FactoryGame/Content/FactoryGame/"
MATERIAL = "World/Environment/Landscape/Material/FG_Landscape_Inst"
TILES = "World/Environment/Landscape/Texture/Tiles/"
PIGMENT = "-Shared/Texture/PigmentMap"

#: Every texture whose mean albedo the table below reads, by short name.
TEXTURES = {
    "TX_Grass_Far_01_Alb": TILES + "Grass/TX_Grass_Far_01_Alb",
    "TX_Forest_Far_01_Alb": TILES + "Forest/TX_Forest_Far_01_Alb",
    "TX_GrassRed_01_Alb": TILES + "GrassRed/TX_GrassRed_01_Alb",
    "TX_Grass_RedJungle_01_Alb": TILES + "RedJungle/TX_Grass_RedJungle_01_Alb",
    "TX_SandRock_Alb_01": TILES + "SandRock/TX_SandRock_Alb_01",
    "TX_SandPebbles_01_Alb": TILES + "Pebbels/TX_SandPebbles_01_Alb",
    "Sand_Dry_02_Alb": TILES + "Sand/Sand_Dry_02_Alb",
    "Gravel_Alb": TILES + "Stones/Gravel_Alb",
    "TX_Soil_01_Alb": TILES + "Soil/TX_Soil_01_Alb",
    "TX_Puddles_01_Alb": TILES + "Soil/TX_Puddles_01_Alb",
    "TX_SeaRocks_01_Alb": TILES + "SeaRocks/TX_SeaRocks_01_Alb",
    "Cliff_Macro_Alb_02": TILES + "Cliff/Cliff_Macro_Alb_02",
    "Cliff_Detail_Alb": TILES + "Cliff/Cliff_Detail_Alb",
    "Cliff_Sediment_Alb": "World/Environment/Rock/Cliff/Textures/CliffSediment/Cliff_Sediment_Alb",
}

#: Paint layer -> (texture or None, material vector parameter or None); the albedo is their
#: product. Matched by name: the cooked layer functions that wire them are stripped.
LAYERS: dict[str, tuple[str | None, str | None]] = {
    "Grass_LayerInfo": ("TX_Grass_Far_01_Alb", None),
    "Forest_LayerInfo": ("TX_Forest_Far_01_Alb", None),
    "PurpleForest_LayerInfo": ("TX_Forest_Far_01_Alb", None),
    "GrassRed_LayerInfo": ("TX_GrassRed_01_Alb", None),
    "RedJungle_LayerInfo": ("TX_Grass_RedJungle_01_Alb", None),
    "Sand_LayerInfo": (None, "Sand Far Color"),
    "SandRipples_LayerInfo": (None, "SandRipples Far Color"),
    "WetSand_LayerInfo": (None, "WetSand_Color"),
    "SandRock_LayerInfo": ("TX_SandRock_Alb_01", "Sand Rock BaseColor"),
    "DesertRock_LayerInfo": ("TX_SandRock_Alb_01", "Sand Rock BaseColor"),
    "SandPebbles_LayerInfo": ("TX_SandPebbles_01_Alb", None),
    "SandCracks_LayerInfo": ("Sand_Dry_02_Alb", None),
    "Gravel_WeightLayerInfo": ("Gravel_Alb", None),
    "Soil_LayerInfo": ("TX_Soil_01_Alb", None),
    "Cliff_LayerInfo": ("Cliff_Sediment_Alb", None),
    "CoralRock_LayerInfo": ("TX_SeaRocks_01_Alb", None),
}

#: Not weight-blended: lerped over the blend by its own weight.
OVERLAYS: dict[str, tuple[str | None, str | None]] = {
    "Puddles_LayerInfo": ("TX_Puddles_01_Alb", None)
}

ROCK_TEXTURES = ("Cliff_Macro_Alb_02", "Cliff_Detail_Alb")
CANOPY_TEXTURE = "TX_Forest_Far_01_Alb"

PIGMENT_MAX_PX = 2048

#: A bulk entry with this flag lives inline in the export body rather than in the ``.ubulk``.
_INLINE_BULK = 0x40

_Float = TypeVar("_Float", np.float32, np.float64)


def srgb_unit_to_linear(c: npt.NDArray[_Float]) -> npt.NDArray[_Float]:
    """The sRGB transfer function undone, on values in 0-1, in their own dtype."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def srgb_to_linear(values: npt.ArrayLike) -> F64Grid:
    """0-255 sRGB values as linear light in 0-1, float64."""
    return srgb_unit_to_linear(np.asarray(values, np.float64) / 255.0)


def material_vectors(view: PackageView) -> dict[str, tuple[float, ...]]:
    """The material instance's ``VectorParameterValues`` as ``{name: (r, g, b)}``."""
    vectors: Mapping[str, Sequence[float]] = material_parameters(view)["vector"]
    return {name: tuple(value[:3]) for name, value in vectors.items()}


def _texture_view(game: GameReader, path: str) -> tuple[PackageView, bytes]:
    """A texture asset's package view and its ``.ubulk`` (empty when it has none)."""
    view = PackageView(game.store.read_path(path + ".uasset"), game.scripts)
    has_bulk = path + ".ubulk" in game.store.by_path
    return view, game.store.read_path(path + ".ubulk") if has_bulk else b""


def decode_texture(
    game: GameReader, decoder: ModuleType, asset: str, want_max: int, channels: int = 3
) -> U8Grid:
    """The largest mip no wider than ``want_max`` as (H, W, channels) uint8 RGB(A). Square."""
    blocks: dict[str, tuple[int, Callable[[bytes, int, int], bytes]]] = {
        "PF_DXT1": (8, decoder.decode_bc1),
        "PF_DXT5": (16, decoder.decode_bc3),
        "PF_BC7": (16, decoder.decode_bc7),
        "PF_BC5": (16, decoder.decode_bc5),
        "PF_BC4": (8, decoder.decode_bc4),
    }
    view, ubulk = _texture_view(game, GAME_ROOT + asset)
    fmt = next(n for n in view.pkg.names if n.startswith("PF_"))
    export = next(
        e for e in view.exports if class_name_of(view.class_of[e["slot"]]).startswith("Texture")
    )
    best: tuple[int, bytes] | None = None
    for entry in view.pkg.bulk_entries():
        if fmt in blocks:
            side = round((entry["size"] / blocks[fmt][0]) ** 0.5) * 4
        else:
            side = round((entry["size"] / (4 if fmt == "PF_B8G8R8A8" else 1)) ** 0.5)
        if side > want_max or (best and best[0] >= side):
            continue
        source = view.pkg.body(export) if entry["flags"] & _INLINE_BULK else ubulk
        raw = source[entry["offset"] : entry["offset"] + entry["size"]]
        if len(raw) == entry["size"]:
            best = (side, raw)
    if best is None:
        raise ValueError(f"{asset}: no mip at or under {want_max} px")
    side, raw = best
    if fmt == "PF_B8G8R8A8":
        rgba = np.frombuffer(raw, np.uint8).reshape(side, side, 4)[..., [2, 1, 0, 3]]
    elif fmt == "PF_G8":
        grey = np.frombuffer(raw, np.uint8).reshape(side, side)
        rgba = np.dstack([grey, grey, grey, grey])
    else:
        out = blocks[fmt][1](raw, side, side)
        rgba = np.frombuffer(out, np.uint8).reshape(side, side, 4)[..., [2, 1, 0, 3]]
    return np.ascontiguousarray(rgba[..., :channels])


def layer_albedo(
    layers: Mapping[str, tuple[str | None, str | None]],
    means: Mapping[str, Sequence[float]],
    vectors: Mapping[str, Sequence[float]],
) -> dict[str, list[float]]:
    """Linear albedo per layer: texture mean times material vector, either alone."""
    table: dict[str, list[float]] = {}
    for layer, (texture, vector) in layers.items():
        value = np.ones(3)
        if texture is not None:
            value = value * np.asarray(means[texture])
        if vector is not None:
            value = value * np.asarray(vectors[vector])
        table[layer] = [round(float(v), 5) for v in value]
    return table


def rock_family_colours(game: GameReader, decoder: ModuleType) -> dict[str, JsonObject]:
    """Each cliff family's ``Color Tint`` and its top layer's mean linear albedo."""
    out: dict[str, JsonObject] = {}
    sources = family_sources(game.store, game.scripts, game.index)
    for family, source in sources.items():
        material: str = source["material"]
        top: str | None = source["top_texture"]
        mean: list[JsonValue] | None = None
        if top:
            asset = top.split("/Game/FactoryGame/", 1)[-1]
            linear = srgb_to_linear(decode_texture(game, decoder, asset, 512))
            mean = [round(float(v), 5) for v in linear.reshape(-1, 3).mean(0)]
        tint: Sequence[float] | None = source["tint"]
        tint_rounded: list[JsonValue] | None = [round(v, 5) for v in tint] if tint else None
        out[family] = {
            "code": FAMILIES.index(family),
            "material": material,
            "tint": tint_rounded,
            "top_texture": top,
            "top": mean,
        }
    return out
