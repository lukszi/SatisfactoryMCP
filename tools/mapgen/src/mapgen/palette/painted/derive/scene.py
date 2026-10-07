"""What the derivation rules read: the paint store on the 4 m rock grid, its areas and its trees.

The weights, the bake and the area map are taken at every fourth texel, the grid the painter
measures its medians on; the area map is rehomed offshore as the painter's is.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import numpy.typing as npt

from mapgen.colour import srgb_to_linear
from mapgen.gamedata.ground.bake import BAKE_NAME
from mapgen.gamedata.vegetation.crown_sprites import (
    CROWN_RECORD,
    CROWNS_NAME,
    MATERIAL_NONE,
    SPRITE_M,
    SPRITES_NAME,
    CrownSpecies,
    DecodedSprite,
    decode_records,
    decode_sprites,
)
from mapgen.palette.painted.calibration import area_ids, rehome_offshore
from mapgen.palette.painted.ground import ROCK_GRID_M, biome_grid, land_cells
from mapgen.palette.painted.shapes import BiomeGrid, FieldPlanes, PaintFile, PaintMeta
from mapgen.terrain.render_meshes import is_render_only_foliage
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, U8Grid
from satisfactory_mcp.core.gameassets.maparea import NO_MANS_LAND
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "NO_COLOUR",
    "STRIDE",
    "AreaGrid",
    "Scene",
    "Species",
    "area_grid",
    "scene_from_store",
]

#: Texels between samples: the painter's rock grid.
STRIDE = ROCK_GRID_M

#: Layers that carry no colour of their own.
NO_COLOUR = frozenset({"LandscapeVisibilityLayerInfo", "Foliage_Eraser_LayerInfo"})

_FALLBACK_RGB = (0.05, 0.08, 0.03)


class AreaGrid(NamedTuple):
    """The area index on the sample grid, rehomed offshore, and each index's name and asset."""

    cells: U8Grid
    names: list[str]
    assets: list[str | None]

    def digest(self) -> str:
        legend = json.dumps([self.names, self.assets]).encode("utf-8")
        return sha256_hex(np.ascontiguousarray(self.cells).tobytes(), legend)


@dataclass(frozen=True)
class Species:
    """A tree species seen from above: its cover-weighted linear colour and crown area."""

    name: str
    mesh: str
    materials: list[str]
    material_linear: dict[str, list[float]]
    linear: F64Grid
    area_m2: float


@dataclass
class Scene:
    """The store on the sample grid: colour layers, their sum, the lit bake, areas and trees."""

    meta: PaintMeta
    planes: dict[str, U8Grid]
    have: BoolMask
    bake_linear: F32Grid
    areas: AreaGrid
    species: list[Species]
    records: npt.NDArray[np.void]
    meshed: BoolMask

    @property
    def total(self) -> F32Grid:
        total = np.zeros(self.have.shape, np.float32)
        for plane in self.planes.values():
            total += plane
        return total

    def where(self, mask: BoolMask) -> tuple[F64Grid, F64Grid]:
        """World metres of the texels in ``mask``."""
        grid = self.meta["grid"]
        rows, cols = np.nonzero(mask)
        step = grid["spacing_cm"]
        x = (grid["x0_cm"] + (cols * STRIDE + 0.5) * step) / 100.0
        y = (grid["y0_cm"] + (rows * STRIDE + 0.5) * step) / 100.0
        return x.astype(np.float64), y.astype(np.float64)

    def area_mask(self, keys: Collection[str]) -> BoolMask:
        return np.isin(self.areas.cells, area_ids(self.areas.names, self.areas.assets, keys))

    def tree_mask(self, keys: Collection[str]) -> BoolMask:
        """The trees standing in the listed areas."""
        grid, index = self.meta["grid"], self.areas.cells
        cell = grid["spacing_cm"] * STRIDE
        rows = np.clip(((self.records["y"] - grid["y0_cm"]) // cell).astype(np.int64), 0,
                       index.shape[0] - 1)  # fmt: skip
        cols = np.clip(((self.records["x"] - grid["x0_cm"]) // cell).astype(np.int64), 0,
                       index.shape[1] - 1)  # fmt: skip
        wanted = area_ids(self.areas.names, self.areas.assets, keys)
        return np.isin(index[rows, cols], wanted)


def area_grid(
    biome: BiomeGrid, names: list[str], field: FieldPlanes | None, shape: tuple[int, int]
) -> AreaGrid:
    """The area raster under every sample of a ``shape`` store, rehomed when a field is given."""
    coarse = biome_grid(biome, *shape)[::STRIDE, ::STRIDE]
    land = None if field is None else land_cells(field, (coarse.shape[0], coarse.shape[1]))
    if land is not None:
        coarse = rehome_offshore(coarse, names, land, NO_MANS_LAND)
    return AreaGrid(np.ascontiguousarray(coarse), list(names), list(biome["assets_by_index"]))


def _species(entry: CrownSpecies, sprite: DecodedSprite) -> Species:
    """The crown's colour: each visible slot's albedo, weighted by the cover over it."""
    cover = sprite["cover"].astype(np.float64) / 255.0
    colours = [m["linear"] for m in entry["materials"]]
    fallback = next((c for c in colours if c is not None), _FALLBACK_RGB)
    rgb = np.zeros(3)
    for k in map(int, np.unique(sprite["slot"])):
        if k != MATERIAL_NONE:
            known = colours[k] if k < len(colours) else None
            rgb = rgb + cover[sprite["slot"] == k].sum() * np.asarray(known or fallback)
    total = float(cover.sum())
    paths = [m["path"] or "" for m in entry["materials"]]
    linear = {m["path"] or "": m["linear"] for m in entry["materials"] if m["linear"] is not None}
    return Species(entry["name"], entry["mesh"], paths, linear, rgb / max(total, 1e-6),
                   total * SPRITE_M**2)  # fmt: skip


def _trees(paint_dir: Path, meta: PaintMeta) -> tuple[list[Species], npt.NDArray[np.void]]:
    block = meta.get("crowns")
    if not block or CROWNS_NAME not in meta["files"]:
        return [], np.zeros(0, CROWN_RECORD)
    entries = block["species"]
    sprites = decode_sprites(
        (paint_dir / SPRITES_NAME).read_bytes(), [e["sprite"] for e in entries if "sprite" in e]
    )
    species = [_species(e, s) for e, s in zip(entries, sprites, strict=True)]
    return species, decode_records((paint_dir / CROWNS_NAME).read_bytes())


def _sampled(paint_dir: Path, name: str, entry: PaintFile) -> U8Grid:
    """One u8 plane of the store at every ``STRIDE``-th texel."""
    shape = entry["shape"]
    flat = shape[1] * (shape[2] if len(shape) > 2 else 1)
    full = hf.decode_u8((paint_dir / name).read_bytes(), shape[0], flat).reshape(shape)
    return np.ascontiguousarray(full[::STRIDE, ::STRIDE])


def scene_from_store(paint_dir: Path, meta: PaintMeta, areas: AreaGrid) -> Scene:
    """The store read on the sample grid; a plane at a time, so only the samples are kept."""
    files = meta["files"]
    planes: dict[str, U8Grid] = {}
    for name, entry in files.items():
        layer = entry.get("layer")
        if layer and layer not in NO_COLOUR:
            planes[layer] = _sampled(paint_dir, name, entry)
    rgb = _sampled(paint_dir, BAKE_NAME, files[BAKE_NAME])
    have = rgb.astype(np.uint16).sum(-1) >= 3
    species, records = _trees(paint_dir, meta)
    meshed = np.array([is_render_only_foliage(s.mesh) for s in species], bool)
    return Scene(meta, planes, have, srgb_to_linear(rgb), areas, species, records, meshed)
