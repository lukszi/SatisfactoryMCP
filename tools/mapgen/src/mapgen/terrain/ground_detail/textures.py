"""The layer textures a run reads, made ready once for the detail kernel.

Each texture is mipped to about half the run's pixel and kept with its low pass beside it,
packed into two atlases: the colour (linear albedo, then its low pass) and the surface (a
normal's east and south, or a height and its low pass). The cell noise is kept as the terms
the cells read, and the paint weights as each texel's leading layers.
docs/map/painted.md section 30, "The layers' own textures".
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import NamedTuple

import numpy as np
from scipy import ndimage

from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.gamedata.ground.layer_textures import (
    GROUND_TEXTURES_NAME,
    LAYERS,
    NOISE,
    OVERLAYS,
    LayerTextures,
    Read,
    decode_ground_textures,
)
from mapgen.gamedata.ground.paint_store import META_NAME
from mapgen.terrain.texels import Atlas
from satisfactory_mcp.core.arrays import F32Grid, I32Grid, I64Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "DETAIL_MAX_SPACING_M",
    "DETAIL_SIGMA_M",
    "NOISE_TERMS",
    "NO_LAYER",
    "TOP_LAYERS",
    "DetailTextures",
    "LayerTable",
    "load_detail_textures",
]

#: The low pass the detail is measured against: about the blur the 1 m bake is drawn with,
#: its box and the bilinear read, so the textures add only what the bake cannot hold.
DETAIL_SIGMA_M = 0.5
#: Layers kept per texel; the fifth and later carry 0.006 of a texel's weight on average.
TOP_LAYERS = 4
NO_LAYER = 255
#: A pixel at least this wide draws no detail: the 1 m bake holds what it shows.
DETAIL_MAX_SPACING_M = 1.0
#: The noise's terms a texel: its cell's shift along u and v in repeats, its scale, the
#: cosine and sine of its turn, and its mask.
NOISE_TERMS = 6
#: Weight texels merged into the leading layers at a time.
_MERGE_TEXELS = 1 << 21


class LayerTable(NamedTuple):
    """Per layer id, its albedo, normal and height read, ``(layers, 3)`` each: the tile in the
    colour atlas (albedo) or the surface atlas (the others), -1 for none; the cells; and the
    inverse of the metres a repeat spans."""

    tiles: I32Grid
    cells: I32Grid
    inverse: F32Grid


@dataclass(frozen=True)
class DetailTextures:
    """What the detail kernel reads, built once a run: the two atlases, the noise's terms, the
    layer table, the layers by id (the overlay's id ``overlay``, -1 for none), each texel's
    ``TOP_LAYERS`` leading layers and their weights, the overlay's weight, the paint grid's
    corner in centimetres and the run's pixel in metres."""

    colour: Atlas
    surface: Atlas
    noise: F32Grid
    table: LayerTable
    layers: tuple[str, ...]
    overlay: int
    ids: U8Grid
    weights: U8Grid
    overlay_weight: U8Grid
    origin_cm: tuple[float, float]
    spacing_m: float

    def provenance(self, seconds: float) -> JsonObject:
        """What a sidecar records of the detail."""
        names: list[JsonValue] = list(self.layers)
        return {
            "layers": names,
            "tiles": int(len(self.colour.tiles) + len(self.surface.tiles)),
            "low_pass_sigma_m": DETAIL_SIGMA_M,
            "leading_layers": TOP_LAYERS,
            "spacing_m": round(self.spacing_m, 4),
            "seconds_to_prepare": round(seconds, 1),
        }


class _Packer:
    """Tiles stacked down one column of an atlas, each kept once by its key."""

    def __init__(self, channels: int) -> None:
        self.channels = channels
        self.tiles: list[F32Grid] = []
        self.index: dict[tuple[str, float, float], int] = {}

    def add(self, key: tuple[str, float, float], make: Callable[[], F32Grid]) -> int:
        if key not in self.index:
            tile = make()
            pad = self.channels - tile.shape[2]
            self.tiles.append(np.pad(tile, ((0, 0), (0, 0), (0, pad))) if pad else tile)
            self.index[key] = len(self.tiles) - 1
        return self.index[key]

    def atlas(self) -> Atlas:
        width = max(t.shape[1] for t in self.tiles)
        height = sum(t.shape[0] for t in self.tiles)
        texels = np.zeros((height, width, self.channels), np.float32)
        tiles = np.zeros((len(self.tiles), 4), np.int32)
        row = 0
        for k, tile in enumerate(self.tiles):
            h, w = tile.shape[:2]
            texels[row : row + h, :w] = tile
            tiles[k] = (0, row, w, h)
            row += h
        return Atlas(texels, tiles)


def load_detail_textures(paint_dir: Path, spacing_m: float) -> DetailTextures | None:
    """The run's detail textures from the paint store; None where its pixel is too wide for
    any detail, or the store predates the layer textures (paint generator 5)."""
    if spacing_m >= DETAIL_MAX_SPACING_M:
        return None
    try:
        meta: JsonValue = json.loads((paint_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    index, files, grid = meta.get("ground_textures"), meta.get("files"), meta.get("grid")
    if not (isinstance(index, dict) and isinstance(files, dict) and isinstance(grid, dict)):
        return None
    textures = decode_ground_textures(paint_dir / GROUND_TEXTURES_NAME, index)
    planes = {
        str(entry["layer"]): name
        for name, entry in files.items()
        if isinstance(entry, dict) and "layer" in entry
    }
    layers = tuple(name for name in sorted(planes) if name in LAYERS)
    overlays = tuple(name for name in sorted(planes) if name in OVERLAYS)[:1]
    shape = (_int(grid["height"]), _int(grid["width"]))
    weights = ((i, _plane(paint_dir / planes[name], shape)) for i, name in enumerate(layers))
    ids, kept = _leading(weights, shape)
    cover = np.zeros(shape, np.uint8)
    if overlays:
        cover = _plane(paint_dir / planes[overlays[0]], shape)
    colour, surface = _Packer(6), _Packer(2)
    recipes = [LAYERS[name] for name in layers] + [OVERLAYS[name] for name in overlays]
    table = _table(recipes, textures, spacing_m, (colour, surface))
    return DetailTextures(
        colour=colour.atlas(),
        surface=surface.atlas(),
        noise=_noise_terms(textures[NOISE]),
        table=table,
        layers=layers + overlays,
        overlay=len(layers) if overlays else -1,
        ids=ids,
        weights=kept,
        overlay_weight=cover,
        origin_cm=(float(_number(grid["x0_cm"])), float(_number(grid["y0_cm"]))),
        spacing_m=spacing_m,
    )


def _table(
    recipes: list[LayerTextures],
    textures: dict[str, U8Grid],
    spacing_m: float,
    packers: tuple[_Packer, _Packer],
) -> LayerTable:
    """Each recipe's three reads as tiles of the two atlases, with their cells and repeats."""
    colour, surface = packers
    tiles = np.full((len(recipes), 3), -1, np.int32)
    cells = np.zeros((len(recipes), 3), np.int32)
    inverse = np.ones((len(recipes), 3), np.float32)
    for i, recipe in enumerate(recipes):
        for j, read in enumerate(recipe):
            if read is None:
                continue
            texels, key = textures[read.texture], (read.texture, read.tile_m, read.strength)
            make = (_colour_tile, _normal_tile, _height_tile)[j]
            packer = colour if j == 0 else surface
            tiles[i, j] = packer.add(key, partial(make, texels, read, spacing_m))
            cells[i, j], inverse[i, j] = read.cells, np.float32(1.0 / read.tile_m)
    return LayerTable(tiles, cells, inverse)


def _levels(side: int, tile_m: float, spacing_m: float) -> int:
    """The mips below a ``side`` texture that bring its texel nearest half the run's pixel,
    keeping at least two texels a side."""
    ratio = (spacing_m / 2.0) / (tile_m / side)
    levels = round(float(np.log2(ratio))) if ratio > 1.0 else 0
    return min(max(levels, 0), side.bit_length() - 2)


def _mipped(plane: F32Grid, levels: int) -> F32Grid:
    """``plane`` averaged over 2 x 2 blocks ``levels`` times."""
    for _ in range(levels):
        h, w = plane.shape[:2]
        plane = plane.reshape(h // 2, 2, w // 2, 2, plane.shape[2]).mean((1, 3))
    return np.ascontiguousarray(plane, np.float32)


def _low(plane: F32Grid, tile_m: float) -> F32Grid:
    """``plane`` blurred by ``DETAIL_SIGMA_M``, wrapped as it repeats."""
    sigma = DETAIL_SIGMA_M / (tile_m / plane.shape[0])
    blur = [
        ndimage.gaussian_filter(plane[..., k], sigma, mode="wrap") for k in range(plane.shape[2])
    ]
    return np.stack(blur, -1).astype(np.float32)


def _colour_tile(texels: U8Grid, read: Read, spacing_m: float) -> F32Grid:
    """Linear albedo at the run's texel, its detail scaled by the read's strength about its
    low pass, then the low pass: six channels."""
    linear = srgb_unit_to_linear(texels[..., :3].astype(np.float32) / np.float32(255.0))
    mipped = _mipped(linear, _levels(texels.shape[0], read.tile_m, spacing_m))
    low = _low(mipped, read.tile_m)
    detail = low + np.float32(read.strength) * (mipped - low)
    return np.concatenate([detail, low], -1).astype(np.float32)


def _normal_tile(texels: U8Grid, read: Read, spacing_m: float) -> F32Grid:
    """The normal's east and south at the run's texel, times the read's strength."""
    unit = texels[..., :2].astype(np.float32) / np.float32(127.5) - np.float32(1.0)
    mipped = _mipped(unit, _levels(texels.shape[0], read.tile_m, spacing_m))
    return mipped * np.float32(read.strength)


def _height_tile(texels: U8Grid, read: Read, spacing_m: float) -> F32Grid:
    """The height at the run's texel, then its low pass."""
    height = texels[..., :1].astype(np.float32) / np.float32(255.0)
    mipped = _mipped(height, _levels(texels.shape[0], read.tile_m, spacing_m))
    return np.concatenate([mipped, _low(mipped, read.tile_m)], -1)


def _noise_terms(rgba: U8Grid) -> F32Grid:
    """The cell noise as the terms a cell reads (``NOISE_TERMS``): R shifts u and scales by
    0.9 to 1.1, G shifts v, B turns a whole circle, A is the cell's mask."""
    c = rgba.astype(np.float64) / 255.0
    turn = 2.0 * np.pi * c[..., 2]
    terms = [2.0 * c[..., 0] - 1.0, 2.0 * c[..., 1] - 1.0, 0.9 + 0.2 * c[..., 0]]
    terms += [np.cos(turn), np.sin(turn), c[..., 3]]
    return np.ascontiguousarray(np.stack(terms, -1), np.float32)


def _leading(planes: Iterator[tuple[int, U8Grid]], shape: tuple[int, int]) -> tuple[U8Grid, U8Grid]:
    """Each texel's ``TOP_LAYERS`` heaviest layers and their weights, heaviest first; a layer
    weighing what a held one does goes after it. Empty slots hold ``NO_LAYER`` at 0."""
    ids = np.full((TOP_LAYERS, shape[0] * shape[1]), NO_LAYER, np.uint8)
    weights = np.zeros((TOP_LAYERS, shape[0] * shape[1]), np.uint8)
    for layer, plane in planes:
        flat = plane.ravel()
        texels = np.flatnonzero(flat)
        for start in range(0, len(texels), _MERGE_TEXELS):
            at = texels[start : start + _MERGE_TEXELS]
            _insert(ids, weights, at, layer, flat[at])
    return ids.reshape(TOP_LAYERS, *shape), weights.reshape(TOP_LAYERS, *shape)


def _insert(ids: U8Grid, weights: U8Grid, at: I64Grid, layer: int, weight: U8Grid) -> None:
    """``layer`` at ``weight`` placed among the held layers of the texels ``at``, in place."""
    held_w, held_i = weights[:, at], ids[:, at]
    slot = (held_w >= weight).sum(0)
    new_w, new_i = held_w.copy(), held_i.copy()
    for k in range(1, TOP_LAYERS):
        moved = k > slot
        new_w[k] = np.where(moved, held_w[k - 1], new_w[k])
        new_i[k] = np.where(moved, held_i[k - 1], new_i[k])
    for k in range(TOP_LAYERS):
        new_w[k] = np.where(slot == k, weight, new_w[k])
        new_i[k] = np.where(slot == k, np.uint8(layer), new_i[k])
    weights[:, at], ids[:, at] = new_w, new_i


def _plane(path: Path, shape: tuple[int, int]) -> U8Grid:
    return hf.decode_u8(path.read_bytes(), shape[0], shape[1])


def _int(value: JsonValue) -> int:
    if type(value) is not int:
        raise TypeError(f"{value!r} is not an integer")
    return value


def _number(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{value!r} is not a number")
    return float(value)
