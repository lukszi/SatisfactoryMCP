"""The paint layers' albedo: their textures and material parameters, and rock colours."""

from __future__ import annotations

import struct

import numpy as np

from mapgen.gamedata.rocks.families import FAMILIES, family_sources
from satisfactory_mcp.core.gameassets.packages import PackageView, class_name_of, property_tags

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
LAYERS = {
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
OVERLAYS = {"Puddles_LayerInfo": ("TX_Puddles_01_Alb", None)}

ROCK_TEXTURES = ("Cliff_Macro_Alb_02", "Cliff_Detail_Alb")
CANOPY_TEXTURE = "TX_Forest_Far_01_Alb"

PIGMENT_MAX_PX = 2048


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    c = np.asarray(values, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


# ----------------------------------------------------------------------- textures


def material_vectors(view) -> dict[str, tuple[float, float, float]]:
    """The material instance's ``VectorParameterValues`` as ``{name: (r, g, b)}``."""
    payload = view.props(0).get("VectorParameterValues", b"")
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos, out = 4, {}
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        name, value = None, None
        for tag, kind, raw, _v in tags:
            if tag == "ParameterInfo" and kind == "StructProperty":
                inner, _end = property_tags(raw, view.pkg.names, 0)
                for key, inner_kind, inner_raw, _iv in inner:
                    if key == "Name" and inner_kind == "NameProperty":
                        name = view.read_fname(inner_raw)
            elif tag == "ParameterValue" and len(raw) == 16:
                value = struct.unpack("<4f", raw)[:3]
        if name and value:
            out[name] = tuple(float(v) for v in value)
    return out


def decode_texture(
    store, scripts, decoder, asset: str, want_max: int, channels: int = 3
) -> np.ndarray:
    """The largest mip no wider than ``want_max`` as (H, W, channels) uint8 RGB(A). Square."""
    blocks = {
        "PF_DXT1": (8, decoder.decode_bc1),
        "PF_DXT5": (16, decoder.decode_bc3),
        "PF_BC7": (16, decoder.decode_bc7),
        "PF_BC5": (16, decoder.decode_bc5),
        "PF_BC4": (8, decoder.decode_bc4),
    }
    path = GAME_ROOT + asset
    view = PackageView(store.read_path(path + ".uasset"), scripts)
    fmt = next(n for n in view.pkg.names if n.startswith("PF_"))
    export = next(
        e for e in view.exports if class_name_of(view.class_of[e["slot"]]).startswith("Texture")
    )
    ubulk = store.read_path(path + ".ubulk") if path + ".ubulk" in store.by_path else b""
    best = None
    for entry in view.pkg.bulk_entries():
        if fmt in blocks:
            side = round((entry["size"] / blocks[fmt][0]) ** 0.5) * 4
        else:
            side = round((entry["size"] / (4 if fmt == "PF_B8G8R8A8" else 1)) ** 0.5)
        if side > want_max or (best and best[0] >= side):
            continue
        if entry["flags"] & 0x40:
            raw = view.pkg.body(export)[entry["offset"] : entry["offset"] + entry["size"]]
        else:
            raw = ubulk[entry["offset"] : entry["offset"] + entry["size"]]
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


def layer_albedo(layers: dict, means: dict, vectors: dict) -> dict[str, list[float]]:
    """Linear albedo per layer: texture mean times material vector, either alone."""
    table = {}
    for layer, (texture, vector) in layers.items():
        value = np.ones(3)
        if texture is not None:
            value = value * np.asarray(means[texture])
        if vector is not None:
            value = value * np.asarray(vectors[vector])
        table[layer] = [round(float(v), 5) for v in value]
    return table


# ----------------------------------------------------------------------- rock families


def rock_family_colours(store, scripts, index, decoder) -> dict:
    """Each cliff family's ``Color Tint`` and its top layer's mean linear albedo."""
    out = {}
    for family, source in family_sources(store, scripts, index).items():
        top = source["top_texture"]
        mean = None
        if top:
            asset = top.split("/Game/FactoryGame/", 1)[-1]
            mean = srgb_to_linear(decode_texture(store, scripts, decoder, asset, 512))
            mean = [round(float(v), 5) for v in mean.reshape(-1, 3).mean(0)]
        tint = source["tint"]
        out[family] = {
            "code": FAMILIES.index(family),
            "material": source["material"],
            "tint": [round(v, 5) for v in tint] if tint else None,
            "top_texture": top,
            "top": mean,
        }
    return out
