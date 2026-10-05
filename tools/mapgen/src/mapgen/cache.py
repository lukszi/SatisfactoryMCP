"""The on-disk raster caches (direct, top, meshes): their names, stamps and readers.

The names and stamp bytes are what existing caches were written under, so they still hit.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mapgen.common import RENDERS_DIR_NAME
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS

#: Where the direct raster is kept between the two layers. Rasterising 216 M triangles is
#: twenty minutes and the answer does not depend on which layer is being coloured, so it is
#: done once, memory-mapped, and deleted at the end of the run unless ``--keep-direct``.
DIRECT_CACHE_DIR_NAME = "direct.cache"


DIRECT_Z_NAME = "direct.z.f32"


DIRECT_COVERAGE_NAME = "direct.cov.u8"


#: The cliff family of each texel's winning rock (``gamedata.rockfamily.FAMILIES``).
DIRECT_FAMILY_NAME = "direct.family.u8"


DIRECT_CACHE_SIDECAR = "meta.json"


#: The arch-and-boulder raster, cached the same way and under the same file names.
TOP_CACHE_DIR_NAME = "top.cache"


def direct_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / DIRECT_CACHE_DIR_NAME


def top_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / TOP_CACHE_DIR_NAME


def direct_cache_stamp(
    size: int, subsamples: int, build: str | None, families: int | None = None
) -> dict:
    """What a cached direct raster has to agree with before it is drawn from.

    Three things, each of which is a different picture if it moves: the grid it was
    rasterised onto, how finely it sampled each texel of that grid, and the build of the game
    whose rocks it is. Everything else in the sidecar is a record rather than a key.
    The fourth, ``families``, is the rock family reader that wrote the family plane beside
    them; it defaults to the current one.
    """
    families = READER_VERSIONS["rock_families"] if families is None else families
    return {
        "size": int(size),
        "subsamples": int(subsamples),
        "game_version_pinned": build,
        "families": int(families),
    }


def cached_direct(directory: Path, stamp: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """The cached raster as two read-only memory maps, or ``None`` if it is not this one."""
    try:
        recorded = json.loads((directory / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    size = stamp["size"]
    try:
        return (
            np.memmap(directory / DIRECT_Z_NAME, np.float32, "r", shape=(size, size)),
            np.memmap(directory / DIRECT_COVERAGE_NAME, np.uint8, "r", shape=(size, size)),
        )
    except (OSError, ValueError):
        return None


def cached_family(directory: Path, stamp: dict) -> np.ndarray | None:
    """The direct cache's family plane, when it is this cache and was written."""
    if "families" not in stamp or cached_direct(directory, stamp) is None:
        return None
    size = stamp["size"]
    try:
        return np.memmap(directory / DIRECT_FAMILY_NAME, np.uint8, "r", shape=(size, size))
    except (OSError, ValueError):
        return None


MESH_CACHE_DIR_NAME = "meshes.cache"


MESH_Z_NAME = "meshes.z.f32"


MESH_CLASS_NAME = "meshes.class.u8"


MESH_CACHE_SIDECAR = "meta.json"


#: The Titan trees, a mesh raster at ``TITAN_FACTOR`` times the render's pixel.
TITAN_CACHE_DIR_NAME = "titan.cache"
TITAN_FACTOR = 2

#: Every raster cache a run deletes at its end unless ``--keep-direct``.
RASTER_CACHE_DIRS = (
    DIRECT_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    MESH_CACHE_DIR_NAME,
    TITAN_CACHE_DIR_NAME,
)


def mesh_stamp(size: int, build: str | None, reader_version: int) -> dict:
    return {"size": int(size), "game_version_pinned": build, "reader_version": int(reader_version)}


def cached_meshes(directory: Path, stamp: dict):
    """``(z cm, class)`` memory maps if the cache is this one, else ``None``."""
    try:
        recorded = json.loads((directory / MESH_CACHE_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    size = stamp["size"]
    try:
        return (
            np.memmap(directory / MESH_Z_NAME, np.float32, "r", shape=(size, size)),
            np.memmap(directory / MESH_CLASS_NAME, np.uint8, "r", shape=(size, size)),
        )
    except (OSError, ValueError):
        return None
