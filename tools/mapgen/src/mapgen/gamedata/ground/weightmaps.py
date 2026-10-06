"""The landscape's paint weightmaps, placed on the 1 m grid."""

from __future__ import annotations

import struct

import numpy as np

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.level.landscape import LANDSCAPE_SECTION_ORIGIN
from satisfactory_mcp.core.gameassets.packages import class_name_of, property_tags

__all__ = [
    "IGNORED",
    "WEIGHTMAP_BYTES",
    "WEIGHTMAP_PX",
    "allocations",
    "component_layers",
    "component_origin",
    "place",
    "weightmap_channels",
    "weightmap_textures",
]


#: Present on components and carrying no colour.
IGNORED = frozenset({"LandscapeVisibilityLayerInfo", "Foliage_Eraser_LayerInfo"})

WEIGHTMAP_PX = 128
WEIGHTMAP_BYTES = WEIGHTMAP_PX * WEIGHTMAP_PX * 4


def weightmap_channels(bgra: bytes) -> np.ndarray:
    """A 128x128 BGRA8 weightmap mip as (128, 128, 4) in R, G, B, A channel order."""
    pixels = np.frombuffer(bgra, np.uint8, count=WEIGHTMAP_BYTES).reshape(
        WEIGHTMAP_PX, WEIGHTMAP_PX, 4
    )
    return pixels[..., [2, 1, 0, 3]]


def allocations(view, payload: bytes) -> list[dict]:
    """``WeightmapLayerAllocations``: layer name, texture index and channel per entry."""
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos, out = 4, []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        entry: dict = {}
        for name, kind, raw, _value in tags:
            if kind == "ObjectProperty":
                entry[name] = view.import_path(raw)
            elif kind == "ByteProperty":
                entry[name] = raw[0]
        out.append(entry)
    return out


def weightmap_textures(view, ubulk: bytes) -> dict[int, np.ndarray]:
    """``{export slot: (128,128,4)}`` for every Weightmap texture in a level package.

    Bulk entries are grouped per owning export (an inline entry continues the group before
    it); owners and groups pair up in export offset order, which is checked by count.
    """
    groups: list[list[dict]] = []
    for entry in view.pkg.bulk_entries():
        if not entry["flags"] & 0x40:
            groups.append([entry])
        elif groups:
            groups[-1].append(entry)
    owners = sorted(
        (
            e
            for e in view.exports
            if class_name_of(view.class_of[e["slot"]])
            in ("Texture2D", "LandscapeTextureStorageProviderFactory")
            and e["name"].split("_")[0] in ("Weightmap", "LandscapeTextureStorageProviderFactory")
        ),
        key=lambda e: e["offset"],
    )
    if len(owners) != len(groups):
        raise ValueError(f"{len(owners)} weightmap owners against {len(groups)} bulk groups")
    out = {}
    for export, group in zip(owners, groups, strict=True):
        if not export["name"].startswith("Weightmap"):
            continue
        if group[0]["size"] != WEIGHTMAP_BYTES:
            raise ValueError(f"{export['name']}: top mip is {group[0]['size']} bytes")
        start = group[0]["offset"]
        out[export["slot"]] = weightmap_channels(ubulk[start : start + WEIGHTMAP_BYTES])
    return out


def component_layers(view, ubulk: bytes) -> list[tuple[int, int, dict[str, np.ndarray]]]:
    """``(SectionBaseX, SectionBaseY, {layer: 128x128 uint8})`` per LandscapeComponent."""
    textures = weightmap_textures(view, ubulk)
    found = []
    for slot, class_path in view.class_of.items():
        if class_name_of(class_path) != "LandscapeComponent":
            continue
        props = view.props(slot)
        base_x = struct.unpack("<i", props.get("SectionBaseX", b"\0\0\0\0"))[0]
        base_y = struct.unpack("<i", props.get("SectionBaseY", b"\0\0\0\0"))[0]
        refs_raw = props.get("WeightmapTextures", b"\0\0\0\0")
        refs = [
            struct.unpack_from("<i", refs_raw, 4 + 4 * i)[0] - 1
            for i in range(struct.unpack_from("<I", refs_raw)[0])
        ]
        layers = {}
        for entry in allocations(view, props.get("WeightmapLayerAllocations", b"")):
            name = (entry.get("LayerInfo") or "None").rsplit("/", 1)[-1].split(".")[0]
            index = entry.get("WeightmapTextureIndex", 0)
            channel = entry.get("WeightmapTextureChannel", 0)
            if index < len(refs) and refs[index] in textures:
                layers[name] = textures[refs[index]][..., channel].copy()
        found.append((base_x, base_y, layers))
    return found


def component_origin(base_x: int, base_y: int) -> tuple[int, int]:
    """Where a component's first sample lands on the 1 m grid, as (row, col)."""
    origin = int(LANDSCAPE_SECTION_ORIGIN)
    col = base_x - origin + round(-ORIGIN_X_CM / SPACING_CM)
    row = base_y - origin + round(-ORIGIN_Y_CM / SPACING_CM)
    return row, col


def place(planes: dict[str, np.ndarray], row: int, col: int, layers: dict, grid: int) -> None:
    """Write one component's layers into the grid planes, clipped to the grid."""
    r0, c0 = max(row, 0), max(col, 0)
    r1, c1 = min(row + WEIGHTMAP_PX, grid), min(col + WEIGHTMAP_PX, grid)
    if r0 >= r1 or c0 >= c1:
        return
    source = (slice(r0 - row, r1 - row), slice(c0 - col, c1 - col))
    for name, weight in layers.items():
        if name in IGNORED:
            continue
        plane = planes.get(name)
        if plane is None:
            plane = planes[name] = np.zeros((grid, grid), np.uint8)
        plane[r0:r1, c0:c1] = weight[source]
