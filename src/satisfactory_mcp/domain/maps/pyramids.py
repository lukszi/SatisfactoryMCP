"""What a base-map layer's sidecars say about its tile pyramid: depth, density, corners, build
tags and the live light, for the tile routes and for the static map export alike.

Read only. Every directory comes from the registry, whose ``dir`` resolves inside ``data/local``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typing_extensions import TypedDict

from ...core.gameassets.pyramid import PYRAMID_TILE_2X_PX, PYRAMID_TILE_PX
from ...core.jsontypes import JsonObject, JsonValue
from ..spatial import geo
from . import registry

__all__ = [
    "COLOUR_SUFFIXES",
    "DEFAULT_MAP_BOUNDS_M",
    "LIGHT_KINDS",
    "MAP_LAYER_DEFAULT",
    "MAP_RENDERS_DIR_NAME",
    "MAP_RENDER_SIDECAR_NAME",
    "MAP_TILE_MAX_Z",
    "Light",
    "LightHeader",
    "Pyramid",
    "layer_dir",
    "layer_sidecar",
    "light",
    "map_bounds",
    "map_pyramid",
    "pyramid_headers",
]


def json_object(value: JsonValue | None) -> JsonObject:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def sidecar_meta_block(path: Path | None) -> JsonObject:
    """The ``_meta`` block of a generator's JSON sidecar; ``{}`` when absent or unreadable."""
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    except (OSError, ValueError):
        return {}
    return json_object(raw.get("_meta")) if isinstance(raw, dict) else {}


#: The type ``/api/maptiles/{z}/{x}/{y}`` serves: the game's own artwork under ``local/tiles/``.
#: Every other type is a registry id, each with its own sidecar naming its depth and build.
MAP_LAYER_DEFAULT = "map"
MAP_RENDERS_DIR_NAME = "renders"
MAP_RENDER_SIDECAR_NAME = "meta.json"

#: A pyramid's depth when its sidecar does not say: z0 (the world in one tile) through z5
#: (the full 8192 px sheet in 32x32).
MAP_TILE_MAX_Z = 5

#: The corners of the in-game map square, metres, game axes. The playable content is strictly
#: inside it -- ``geo.CONTENT_BBOX`` is x [-2988.4, 4065.6], y [-3141.0, 3042.0] -- so an
#: image pinned here cannot clip anything the map draws. Also the frame the map-area raster
#: is pinned on.
_X0, _Y0, _X1, _Y1 = geo.MAP_SQUARE_M
DEFAULT_MAP_BOUNDS_M = {"x_min_m": _X0, "x_max_m": _X1, "y_min_m": _Y0, "y_max_m": _Y1}


def layer_dir(layer: str) -> Path | None:
    """Where one ready type's ``tiles/`` tree and sidecar live, or ``None``."""
    return registry.directory(layer)


def layer_sidecar(layer: str) -> Path | None:
    """The JSON beside one type's pyramid, named by its registry entry: ``map.json`` for the
    artwork, whose corners a reader may have written by hand, and ``meta.json`` for a render."""
    entry, directory = registry.lookup(layer)
    if entry is None or directory is None:
        return None
    return directory / entry["sidecar"]


def map_bounds(layer: str = MAP_LAYER_DEFAULT) -> dict[str, float]:
    """Where to pin a layer, defaults overridden by its own sidecar if present.

    A malformed override is ignored rather than fatal: the picture is decoration, and a typo
    in an optional sidecar must not take down the endpoint that serves it. Per layer, because
    the corners are a property of a picture and a reader who drops in their own ``map.png``
    pinned somewhere else must not thereby move the renders.
    """
    bounds = dict(DEFAULT_MAP_BOUNDS_M)
    path = layer_sidecar(layer)
    if path is None:
        return bounds
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
        override = json_object(raw)
        bounds.update({k: _number(override[k]) for k in DEFAULT_MAP_BOUNDS_M if k in override})
    except (OSError, ValueError, TypeError):
        return bounds
    return bounds


def _number(value: JsonValue) -> float:
    """``float(value)``, with its ``TypeError`` for a list, an object or a null."""
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"not a number: {value!r}")


def _whole(source: JsonObject, key: str, default: int, floor: int) -> int:
    """An integer sidecar field at or above ``floor``, else ``default``."""
    value = source.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= floor:
        return value
    return default


class Pyramid(TypedDict):
    """A layer's tile pyramid as its sidecar describes it; the @2x pair is null without one."""

    tile_px: int
    max_z: int
    tile_2x_px: int | None
    max_2x_z: int | None
    build: str


def map_pyramid(layer: str = MAP_LAYER_DEFAULT) -> Pyramid:
    """What a layer's sidecar says about its pyramid: tile size, depth, build.

    An absent or malformed sidecar describes a default pyramid, and ``build`` is only a cache
    tag, so saying nothing still yields a stable tag. The layer's name and both trees' numbers
    are in its digest: a tag shared between two pictures, or two cuts of one, is how an
    ``immutable`` tile of one gets cached as a tile of the other.
    """
    block = sidecar_meta_block(layer_sidecar(layer))
    tiles = json_object(block.get("tiles"))
    found = block.get("tiles_2x")
    dense = found if isinstance(found, dict) else None
    stamp = "|".join(
        [
            layer,
            *(str(tiles.get(key)) for key in ("game_version_pinned", "count", "bytes", "max_z")),
            *(str((dense or {}).get(key)) for key in ("count", "bytes", "max_z")),
        ]
    )
    return {
        "tile_px": _whole(tiles, "tile_px", PYRAMID_TILE_PX, 1),
        "max_z": _whole(tiles, "max_z", MAP_TILE_MAX_Z, 0),
        "tile_2x_px": _whole(dense, "tile_px", PYRAMID_TILE_2X_PX, 1) if dense else None,
        "max_2x_z": _whole(dense, "max_z", 0, 0) if dense else None,
        "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
    }


#: Each lighting tree's tile suffix, unless its sidecar's ``layout`` names another.
LIGHT_KINDS = {"unlit": ".png", "trees": ".webp", "nrm": ".nrm.webp", "hz": ".hz.webp"}
#: The suffixes a colour tree's sidecar ``layout`` may name; any other keeps the kind's own.
COLOUR_SUFFIXES = {".png": "image/png", ".webp": "image/webp"}


class LightHeader(TypedDict):
    """``X-Map-Light``: what the page needs to relight a layer live, and its parts besides the
    light: ``{"trees": {max_z, sparse}}`` on a layer drawn apart at its trees."""

    build: str
    max_z: int
    unlit_max_z: int
    params: JsonValue
    baked_sun: JsonValue
    model: JsonObject
    parts: JsonObject


class Light(TypedDict):
    """A lit layer's trees, their depths and suffixes, and the header that describes them."""

    root: Path
    unlit: Path
    max_z: int
    unlit_max_z: int
    trees: Path | None
    trees_max_z: int
    suffix: dict[str, str]
    header: LightHeader


def _layout_suffix(tree: JsonObject, kind: str) -> str:
    """The suffix a colour tree's ``layout`` names, as long as it is one this side serves."""
    layout = tree.get("layout")
    found = Path(layout).suffix if isinstance(layout, str) else ""
    return found if found in COLOUR_SUFFIXES else LIGHT_KINDS[kind]


def light(layer: str) -> Light | None:
    """A lit layer's lighting: its unlit tree and trees, the light pyramid, the shader's numbers.

    ``None`` for a layer drawn lit. The light pyramid's folder comes from the sidecar and is
    refused unless it resolves inside ``data/local``, like a registry ``dir``; the colour
    trees' names and suffixes are checked against the two each can be.
    """
    directory = layer_dir(layer)
    block = sidecar_meta_block(layer_sidecar(layer)).get("light")
    if directory is None or not isinstance(block, dict):
        return None
    rel = block.get("dir")
    if not isinstance(rel, str):
        return None
    root = (directory / rel).resolve()
    if not root.is_relative_to(registry.local_dir().resolve()):
        return None
    meta = sidecar_meta_block(root / MAP_RENDER_SIDECAR_NAME)
    tiles = json_object(meta.get("tiles"))
    unlit = json_object(block.get("unlit_tiles"))
    trees = json_object(block.get("trees_tiles"))
    model = json_object(meta.get("light"))
    max_z, unlit_max_z = tiles.get("max_z"), unlit.get("max_z")
    if not isinstance(max_z, int) or not isinstance(unlit_max_z, int):
        return None
    trees_max_z = trees.get("max_z")
    has_trees = isinstance(trees_max_z, int) and block.get("trees_dir") == "trees"
    stamp = "|".join(
        [
            layer,
            str(model.get("digest")),
            *(str(tiles.get(key)) for key in ("count", "bytes")),
            *(str(tree.get(key)) for tree in (unlit, trees) for key in ("count", "bytes")),
        ]
    )
    parts: JsonObject = {}
    if has_trees:
        parts["trees"] = {"max_z": trees_max_z, "sparse": trees.get("sparse") is True}
    return {
        "root": root,
        "unlit": directory / str(block.get("unlit_dir") or "unlit"),
        "max_z": max_z,
        "unlit_max_z": unlit_max_z,
        "trees": directory / "trees" if has_trees else None,
        "trees_max_z": trees_max_z if isinstance(trees_max_z, int) else -1,
        "suffix": {
            "unlit": _layout_suffix(unlit, "unlit"),
            "trees": _layout_suffix(trees, "trees"),
        },
        "header": {
            "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
            "max_z": max_z,
            "unlit_max_z": unlit_max_z,
            "params": block.get("params"),
            "baked_sun": block.get("baked_sun"),
            "model": {k: v for k, v in model.items() if k not in ("label", "digest")},
            "parts": parts,
        },
    }


def pyramid_headers(layer: str, pyramid: Pyramid) -> dict[str, str]:
    """The layer's own corners, grid and build, so the page configures its tile layer from
    the probe it already makes; the @2x pair is absent, not zero, without a denser tree."""
    bounds = map_bounds(layer)
    headers = {
        "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**bounds),
        "X-Map-Layer": layer,
        "X-Map-Tile-Px": str(pyramid["tile_px"]),
        "X-Map-Tile-Max-Z": str(pyramid["max_z"]),
        "X-Map-Build": pyramid["build"],
    }
    if pyramid["max_2x_z"] is not None:
        headers["X-Map-Tile-2x-Px"] = str(pyramid["tile_2x_px"])
        headers["X-Map-Tile-2x-Max-Z"] = str(pyramid["max_2x_z"])
    return headers
