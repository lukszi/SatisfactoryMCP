"""The landscape's paint weightmaps, placed on the 1 m grid."""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import TypeAlias, TypedDict

import numpy as np

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.level.landscape import LANDSCAPE_SECTION_ORIGIN
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.packages import PackageView, class_name_of, property_tags

__all__ = [
    "IGNORED",
    "WEIGHTMAP_BYTES",
    "WEIGHTMAP_PX",
    "ComponentLayers",
    "WeightmapAllocation",
    "component_layers",
    "component_origin",
    "place_component_layers",
    "weightmap_allocations",
    "weightmap_channels",
    "weightmap_textures",
]


#: Present on components and carrying no colour.
IGNORED = frozenset({"LandscapeVisibilityLayerInfo", "Foliage_Eraser_LayerInfo"})

WEIGHTMAP_PX = 128
WEIGHTMAP_BYTES = WEIGHTMAP_PX * WEIGHTMAP_PX * 4

#: A bulk entry with this flag continues the group of the entry before it.
_INLINE_BULK = 0x40

#: ``(SectionBaseX, SectionBaseY, {layer: 128x128 weights})`` of one LandscapeComponent.
ComponentLayers: TypeAlias = tuple[int, int, dict[str, U8Grid]]


class WeightmapAllocation(TypedDict, total=False):
    """One ``WeightmapLayerAllocations`` entry: the layer, its texture and its channel."""

    LayerInfo: str | None
    WeightmapTextureIndex: int
    WeightmapTextureChannel: int


def weightmap_channels(bgra: bytes) -> U8Grid:
    """A 128x128 BGRA8 weightmap mip as (128, 128, 4) in R, G, B, A channel order."""
    pixels = np.frombuffer(bgra, np.uint8, count=WEIGHTMAP_BYTES).reshape(
        WEIGHTMAP_PX, WEIGHTMAP_PX, 4
    )
    return pixels[..., [2, 1, 0, 3]]


def weightmap_allocations(view: PackageView, payload: bytes) -> list[WeightmapAllocation]:
    """``WeightmapLayerAllocations``: layer name, texture index and channel per entry."""
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos = 4
    out: list[WeightmapAllocation] = []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        entry: WeightmapAllocation = {}
        for name, kind, raw, _value in tags:
            if kind == "ObjectProperty" and name == "LayerInfo":
                entry["LayerInfo"] = view.import_path(raw)
            elif kind == "ByteProperty" and name == "WeightmapTextureIndex":
                entry["WeightmapTextureIndex"] = raw[0]
            elif kind == "ByteProperty" and name == "WeightmapTextureChannel":
                entry["WeightmapTextureChannel"] = raw[0]
        out.append(entry)
    return out


def weightmap_textures(view: PackageView, ubulk: bytes) -> dict[int, U8Grid]:
    """``{export slot: (128,128,4)}`` for every Weightmap texture in a level package.

    Bulk entries are grouped per owning export (an inline entry continues the group before
    it); owners and groups pair up in export offset order, which is checked by count.
    """
    groups: list[list[dict[str, int]]] = []
    for entry in view.pkg.bulk_entries():
        if not entry["flags"] & _INLINE_BULK:
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
    out: dict[int, U8Grid] = {}
    for export, group in zip(owners, groups, strict=True):
        if not export["name"].startswith("Weightmap"):
            continue
        if group[0]["size"] != WEIGHTMAP_BYTES:
            raise ValueError(f"{export['name']}: top mip is {group[0]['size']} bytes")
        start = group[0]["offset"]
        out[export["slot"]] = weightmap_channels(ubulk[start : start + WEIGHTMAP_BYTES])
    return out


def _texture_refs(payload: bytes) -> list[int]:
    """``WeightmapTextures``: each entry's export slot (an ``FPackageIndex`` less one)."""
    count = struct.unpack_from("<I", payload)[0]
    return [struct.unpack_from("<i", payload, 4 + 4 * i)[0] - 1 for i in range(count)]


def component_layers(view: PackageView, ubulk: bytes) -> list[ComponentLayers]:
    """``(SectionBaseX, SectionBaseY, {layer: 128x128 uint8})`` per LandscapeComponent."""
    textures = weightmap_textures(view, ubulk)
    found: list[ComponentLayers] = []
    for slot, class_path in view.class_of.items():
        if class_name_of(class_path) != "LandscapeComponent":
            continue
        props = view.props(slot)
        base_x = struct.unpack("<i", props.get("SectionBaseX", b"\0\0\0\0"))[0]
        base_y = struct.unpack("<i", props.get("SectionBaseY", b"\0\0\0\0"))[0]
        refs = _texture_refs(props.get("WeightmapTextures", b"\0\0\0\0"))
        layers: dict[str, U8Grid] = {}
        for entry in weightmap_allocations(view, props.get("WeightmapLayerAllocations", b"")):
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


def place_component_layers(
    planes: dict[str, U8Grid], row: int, col: int, layers: Mapping[str, U8Grid], grid: int
) -> None:
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
