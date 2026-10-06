"""What a render draws beside the field: render-only meshes, waterfalls, Titan trees, rivers.

Each comes from its own cache or from the shared level sweep, and each names the reader the
sidecar records. docs/spatial-and-map.md sections 27, 30, 34 and 35.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mapgen.cache import (
    CACHE_DIR_NAMES,
    MESH_CACHE_DIR_NAME,
    TITAN_CACHE_DIR_NAME,
    TITAN_FACTOR,
    MeshPlanes,
    TitanPlanes,
)
from mapgen.common import Refusal
from mapgen.gamedata.water.falls import FALLS_CACHE_DIR_NAME
from mapgen.palette.water.falls import load_falls
from mapgen.palette.water.rivers import RiverWater, load_rivers
from mapgen.render.cached_rasters import UNREADABLE_RASTER, LevelSweep
from mapgen.terrain.render_meshes import mesh_items, mesh_pass, titan_items
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["RUN_CACHE_DIRS", "RenderExtras", "load_extras", "remove_run_caches"]

#: Every cache a run deletes at its end unless ``--keep-direct``.
RUN_CACHE_DIRS = (*CACHE_DIR_NAMES, FALLS_CACHE_DIR_NAME)


@dataclass
class RenderExtras:
    """The extras a run draws, each with the block its sidecar records, and their readers."""

    meshes: MeshPlanes | None = None
    falls: np.ndarray | None = None
    titan: TitanPlanes | None = None
    rivers: RiverWater | None = None
    mesh_source: JsonObject = field(default_factory=dict)
    titan_source: JsonObject = field(default_factory=dict)
    river_meta: JsonObject = field(default_factory=dict)
    readers: list[str] = field(default_factory=list)


def load_extras(
    cache_root: Path,
    size: int,
    build: str | None,
    level: LevelSweep,
    heightfield: hf.Field,
    *,
    meshes: bool,
    titan: bool,
    rivers: bool,
    quiet: bool,
) -> RenderExtras:
    """The extras a run asked for, from their caches or from ``level``'s one sweep."""
    store, scripts = level.store, level.scripts
    out = RenderExtras()

    def swept_levels() -> dict:
        return level.sweep

    if meshes:
        maps, mesh_source = mesh_pass(
            cache_root / MESH_CACHE_DIR_NAME, size, build, "render_meshes",
            lambda: mesh_items(store, scripts, level.index, level.sweep), "render-only meshes",
            quiet,
        )  # fmt: skip
        out.meshes = None if maps is None else MeshPlanes(*maps)
        out.mesh_source = {**mesh_source}
        out.falls, falls_source = load_falls(cache_root, build, swept_levels, heightfield)
        out.mesh_source.update(falls_source)
        out.readers += ["render_meshes", "waterfalls"]
    if titan:
        titan_cache = cache_root / TITAN_CACHE_DIR_NAME
        maps, titan_source = mesh_pass(
            titan_cache, size // TITAN_FACTOR, build, "titan_trees",
            lambda: titan_items(store, scripts, level.index, level.sweep), "Titan trees", quiet,
        )  # fmt: skip
        if maps is None:
            message = f"the Titan trees raster in {titan_cache} could not be read back"
            raise Refusal(UNREADABLE_RASTER, message + " after writing it")
        out.titan = TitanPlanes(maps[0], maps[1], TITAN_FACTOR, 0, 0)
        out.titan_source = {**titan_source}
    if rivers:
        out.rivers, out.river_meta = load_rivers(cache_root, build, swept_levels, heightfield)
        out.readers.append("river_splines")
    return out


def remove_run_caches(cache_root: Path) -> list[Path]:
    """Delete the run's caches under ``cache_root``; the directories that would not go.

    A cache a memory map still holds open stays on Windows, so every map is let go first.
    """
    left: list[Path] = []
    for name in RUN_CACHE_DIRS:
        directory = cache_root / name
        shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():
            left.append(directory)
    return left
