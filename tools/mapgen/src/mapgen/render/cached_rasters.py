"""The rocks and the arches rasterised into the render's grid, or read back from their caches.

A raster cache is reused by any run whose stamp it carries; a miss sweeps the game's levels
once, for every raster that needs them (docs/spatial-and-map.md sections 20, 25 and 39).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property, partial
from pathlib import Path
from typing import TypeAlias, cast

from mapgen.cache import CACHE_SIDECAR_NAME, DirectStamp, Plane, cached_raster, raster_cache_stamp
from mapgen.common import Refusal
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.level.sweep import Sweep
from mapgen.gamedata.rocks.families import placement_families
from mapgen.terrain.rasters import (
    BandRaster,
    CliffGeometry,
    RasterStats,
    direct_placements,
    rasterise_direct_band,
    rasterise_top_band,
    read_cliff_geometry,
    sweep_world,
    top_items,
    write_banded_raster,
)
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "UNREADABLE_RASTER",
    "LevelSweep",
    "RasterGrid",
    "RasterPlanes",
    "direct_raster",
    "stamped_raster",
    "top_raster",
]

#: Exit code of a run whose raster was written and then could not be read back.
UNREADABLE_RASTER = 7

#: A raster cache's two planes: max-Z in cm and the coverage of each pixel.
RasterPlanes: TypeAlias = tuple[Plane, Plane]

_GEOMETRY_LICENCE = (
    "Coffee Stain Studios' own cooked assets, read out of the reader's installed copy of the "
    "game. Nothing is committed, redistributed or served past localhost."
)


class LevelSweep:
    """The game's levels swept once, on first use, and the rocks' geometry decoded from them.

    Each stage gets class facts of its own, so the packages the sweep read are let go before
    the meshes are decoded.
    """

    def __init__(self, store: IoStore, scripts: ScriptObjects, progress: bool) -> None:
        self.store, self.scripts, self.progress = store, scripts, progress

    @cached_property
    def index(self) -> AssetIndex:
        return AssetIndex(self.store)

    @cached_property
    def sweep(self) -> Sweep:
        """The placements, foliage, trees and water actors of every level."""
        classes = ClassFacts(self.store, self.index)
        return sweep_world(self.store, self.scripts, self.index, classes, self.progress)

    @cached_property
    def geometry(self) -> CliffGeometry:
        """The cliff geometry the field's rocks are rasterised from, read off the sweep."""
        classes = ClassFacts(self.store, self.index)
        got = read_cliff_geometry(
            self.store, self.scripts, self.index, classes, self.progress, self.sweep
        )
        print(
            f"  {got['meshes']} rock meshes, {got['tris'] / 1e6:.2f} M triangles "
            f"{got['by_source']}, swept in {got['seconds_sweep']}s and decoded "
            f"in {got['seconds_decode']}s"
        )
        return got


@dataclass(frozen=True)
class RasterGrid:
    """The grid a run's rasters are drawn onto, and the stamp their caches carry."""

    size: int
    subsamples: int
    build: str | None
    progress: bool

    @property
    def stamp(self) -> DirectStamp:
        return raster_cache_stamp(self.size, self.subsamples, self.build)

    @property
    def spacing_m(self) -> float:
        return (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / self.size

    def write(self, rasterise_band: BandRaster, directory: Path) -> RasterStats:
        """Every band rasterised into ``directory`` under this grid's stamp; the stats."""
        return write_banded_raster(
            rasterise_band, directory, self.size, self.subsamples, self.stamp, self.progress
        )


def stamped_raster(
    cache: Path, grid: RasterGrid, label: str, key: str, rasterise: Callable[[], JsonObject]
) -> tuple[RasterPlanes, JsonObject]:
    """The cache's planes, ``rasterise``d into it first on a miss, and the sidecar block.

    On a hit the block quotes the cache's own sidecar under ``key``; on a miss it is what
    ``rasterise`` returns. A cache that will not read back refuses the run.
    """
    maps = cached_raster(cache, grid.stamp)
    if maps is None:
        source = rasterise()
        maps = cached_raster(cache, grid.stamp)
    else:
        print(f"reusing the {label} raster already in {cache}")
        reused: JsonValue = json.loads((cache / CACHE_SIDECAR_NAME).read_text(encoding="utf-8"))
        source: JsonObject = {key: {"reused": reused}}
    if maps is None:
        message = f"the {label} raster in {cache} could not be read back after writing it"
        raise Refusal(UNREADABLE_RASTER, message)
    return (maps[0], maps[1]), source


def direct_raster(
    level: LevelSweep, cache: Path, grid: RasterGrid, pyooz_version: str
) -> tuple[RasterPlanes, JsonObject]:
    """The cliff geometry rasterised at the render's spacing: ``stamped_raster``'s answer."""

    def rasterise() -> JsonObject:
        sampled = grid.subsamples
        print(
            f"decoding the cliff geometry and rasterising it at {grid.spacing_m:.4f} m"
            + (f" with {sampled}x{sampled} sub-samples" if sampled > 1 else "")
        )
        geometry = level.geometry
        families = placement_families(level.store, level.scripts, level.index, geometry["sweep"])
        prepared, dropped = direct_placements(geometry["sweep"], geometry["geometry"], families)
        print(f"  {len(prepared)} placements rasterised, dropped {dropped}")
        band = partial(rasterise_direct_band, prepared, geometry["geometry"], with_source=True)
        stats = grid.write(band, cache)
        print(
            f"  direct raster: {stats['texels_with_geometry'] / 1e6:.1f} M texels "
            f"({stats['share_of_the_sheet']}% of the sheet) in {stats['seconds']}s"
        )
        return {
            "cliff_geometry": {
                "name": "the same placed rock meshes tools/gen_world_heightmap.py folds "
                "into the 1 m field, decoded here a second time",
                "licence": _GEOMETRY_LICENCE,
                "decoder": (
                    "tools/gen_world_heightmap.py's own sweep_levels, read_mesh_geometry, "
                    "rotation_matrix, winding_sign and MaxZRaster, imported and called. "
                    "The grid they are pointed at is the only thing this file changes."
                ),
                "meshes": geometry["meshes"],
                "by_source": cast(JsonValue, geometry["by_source"]),
                "source_triangles": geometry["tris"],
                "triangles_out_of_bounds": geometry["triangles_out_of_bounds"],
                "placements_rasterised": len(prepared),
                "placements_dropped": cast(JsonValue, dropped),
                "raster": cast(JsonValue, stats),
                "pyooz_version": pyooz_version,
            }
        }

    return stamped_raster(cache, grid, "direct", "cliff_geometry", rasterise)


def top_raster(level: LevelSweep, cache: Path, grid: RasterGrid) -> tuple[RasterPlanes, JsonObject]:
    """The arches and foliage boulders rasterised like the rocks: ``stamped_raster``'s answer."""

    def rasterise() -> JsonObject:
        print(f"rasterising the arches and foliage boulders at {grid.spacing_m:.4f} m")
        geometry = level.geometry
        items, top_meta = top_items(
            level.store, level.scripts, level.index, geometry["sweep"], geometry["geometry"]
        )
        print(
            f"  {top_meta['arch_placements']} arches, "
            f"{top_meta['foliage_instances']} boulders {top_meta['foliage_sources']}"
        )
        stats = grid.write(partial(rasterise_top_band, items), cache)
        print(
            f"  top raster: {stats['texels_with_geometry'] / 1e6:.1f} M texels in "
            f"{stats['seconds']}s"
        )
        return {"top_overlay": cast(JsonValue, {**top_meta, "raster": stats})}

    return stamped_raster(cache, grid, "top", "top_overlay", rasterise)
