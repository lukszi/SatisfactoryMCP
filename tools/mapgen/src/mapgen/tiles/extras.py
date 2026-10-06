"""What a render draws beside the field: render-only meshes, waterfalls, Titan trees, rivers.

Each comes from its own cache or from the shared level sweep, and each names the reader the
sidecar records. docs/spatial-and-map.md sections 27, 30, 34 and 35.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mapgen.cache import (
    MESH_CACHE_DIR_NAME,
    RASTER_CACHE_DIRS,
    TITAN_CACHE_DIR_NAME,
    TITAN_FACTOR,
)
from mapgen.gamedata.waterfalls import FALLS_CACHE_DIR_NAME
from mapgen.palette.falls import load_falls
from mapgen.palette.rivers import load_rivers
from mapgen.terrain.rasters import mesh_items, mesh_pass, titan_items

__all__ = ["KEPT_CACHE_DIRS", "Extras", "load_extras"]

#: Every cache a run deletes at its end unless ``--keep-direct``.
KEPT_CACHE_DIRS = (*RASTER_CACHE_DIRS, FALLS_CACHE_DIR_NAME)


@dataclass
class Extras:
    meshes: object = None
    falls: object = None
    titan: tuple | None = None
    rivers: object = None
    mesh_source: dict = field(default_factory=dict)
    titan_source: dict = field(default_factory=dict)
    river_meta: dict = field(default_factory=dict)
    readers: list[str] = field(default_factory=list)


def load_extras(cache_root: Path, size: int, build, store, scripts, sweep_once, field_, *,
                meshes: bool, titan: bool, rivers: bool, quiet: bool) -> Extras:  # fmt: skip
    """The extras a run asked for; ``sweep_once()`` returns ``(index, sweep)``."""
    out = Extras()
    sweep = lambda: sweep_once()[1]
    if meshes:
        out.meshes, out.mesh_source = mesh_pass(
            cache_root / MESH_CACHE_DIR_NAME, size, build, "render_meshes",
            lambda: mesh_items(store, scripts, *sweep_once()), "render-only meshes", quiet,
        )  # fmt: skip
        out.falls, falls_source = load_falls(cache_root, build, sweep, field_)
        out.mesh_source.update(falls_source)
        out.readers += ["render_meshes", "waterfalls"]
    if titan:
        maps, out.titan_source = mesh_pass(
            cache_root / TITAN_CACHE_DIR_NAME, size // TITAN_FACTOR, build, "titan_trees",
            lambda: titan_items(store, scripts, *sweep_once()), "Titan trees", quiet,
        )  # fmt: skip
        out.titan = (*maps, TITAN_FACTOR, 0, 0)
    if rivers:
        out.rivers, out.river_meta = load_rivers(cache_root, build, sweep, field_)
        out.readers.append("river_splines")
    return out
