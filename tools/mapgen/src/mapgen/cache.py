"""The on-disk caches (direct, top, meshes, rivers): their names, stamps and readers.

The names and stamp bytes are what existing caches were written under, so they still hit.
A raster cache's planes are a zstd band store, or the raw memory maps every cache was before
it; the sidecar's ``storage`` says which (docs/spatial-and-map.md section 39).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Self

import numpy as np

from mapgen.bandstore import BandArray, BandWriter
from mapgen.common import RENDERS_DIR_NAME
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS

#: Where the direct raster is kept between the two layers. Rasterising 216 M triangles is
#: twenty minutes and the answer does not depend on which layer is being coloured, so it is
#: done once, kept on disk, and deleted at the end of the run unless ``--keep-direct``.
DIRECT_CACHE_DIR_NAME = "direct.cache"


DIRECT_Z_NAME = "direct.z.f32"


DIRECT_COVERAGE_NAME = "direct.cov.u8"


#: The cliff family of each texel's winning rock (``gamedata.rocks.families.FAMILIES``).
DIRECT_FAMILY_NAME = "direct.family.u8"


#: Every cache's stamp: the sidecar a cache is read back by.
CACHE_SIDECAR_NAME = "meta.json"


#: The arch-and-boulder raster, cached the same way and under the same file names.
TOP_CACHE_DIR_NAME = "top.cache"


MESH_CACHE_DIR_NAME = "meshes.cache"


MESH_Z_NAME = "meshes.z.f32"


MESH_CLASS_NAME = "meshes.class.u8"


#: The rock family of each texel's winning render-only mesh, as the direct cache's.
MESH_FAMILY_NAME = "meshes.family.u8"


#: Every plane a raster cache holds, by its raw file name, and its element type.
PLANE_DTYPES = {
    DIRECT_Z_NAME: np.dtype(np.float32),
    DIRECT_COVERAGE_NAME: np.dtype(np.uint8),
    DIRECT_FAMILY_NAME: np.dtype(np.uint8),
    MESH_Z_NAME: np.dtype(np.float32),
    MESH_CLASS_NAME: np.dtype(np.uint8),
    MESH_FAMILY_NAME: np.dtype(np.uint8),
}

#: A sidecar's ``storage``. One without the field is ``raw``, as every cache before it was.
STORAGE_RAW = "raw"
STORAGE_BANDS = "zstd-bands-v1"
STORAGES = (STORAGE_RAW, STORAGE_BANDS)
BANDS_SUFFIX = ".bands"

#: Rows of the output the direct pass rasterises at a time. A whole 32768 square of float32
#: is 4.3 GB and the render already holds 3.2 GB of output; 256 rows is 34 MB, and a
#: triangle at the 0.48 m median touches one band or two, so a per-placement y-bbox test is
#: all the selection needed. Also the colour bands' size and one row of 256 px tiles.
DIRECT_BAND_ROWS = 256


def direct_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / DIRECT_CACHE_DIR_NAME


def top_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / TOP_CACHE_DIR_NAME


def raster_cache_stamp(
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


def plane_file(directory: Path, name: str, storage: str) -> Path:
    return directory / (name + BANDS_SUFFIX if storage == STORAGE_BANDS else name)


def clear_planes(directory: Path, names) -> None:
    """Remove these planes in either storage, so a cache never holds both."""
    for name in names:
        for storage in STORAGES:
            plane_file(directory, name, storage).unlink(missing_ok=True)


def held_open(paths) -> Path | None:
    """The first file another process holds open, found by renaming it to itself and back.

    Windows refuses to rename an open file; elsewhere a rename always succeeds, and nothing is
    found.
    """
    for path in paths:
        probe = path.with_name(path.name + ".probe")
        try:
            os.replace(path, probe)
        except OSError:
            return path
        os.replace(probe, path)
    return None


class RawWriter:
    """``BandWriter``'s interface over a ``w+`` memory map: the raw storage."""

    def __init__(self, path: Path, shape: tuple[int, int], dtype):
        self._map = np.memmap(path, dtype, "w+", shape=shape)

    def write(self, top: int, band) -> None:
        self._map[top : top + len(band)] = band

    def close(self) -> None:
        if self._map is not None:
            self._map.flush()
            self._map = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def plane_writer(directory: Path, name: str, size: int, storage: str, band_rows: int):
    """A writer for one ``size`` square plane, used as a context manager that commits it."""
    path = plane_file(directory, name, storage)
    if storage == STORAGE_BANDS:
        return BandWriter(path, (size, size), PLANE_DTYPES[name], band_rows)
    return RawWriter(path, (size, size), PLANE_DTYPES[name])


def open_plane(directory: Path, name: str, size: int, storage: str, on_corrupt=None):
    """One plane, read-only: a ``BandArray`` in the band store, else the raw memory map."""
    if storage == STORAGE_BANDS:
        return BandArray(plane_file(directory, name, storage), (size, size), PLANE_DTYPES[name],
                         on_corrupt=on_corrupt)  # fmt: skip
    return np.memmap(directory / name, PLANE_DTYPES[name], "r", shape=(size, size))


def read_sidecar(path: Path, stamp: dict) -> dict | None:
    """The sidecar at ``path`` if it carries ``stamp`` and a storage this reader knows."""
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    return recorded if recorded.get("storage", STORAGE_RAW) in STORAGES else None


def _planes(directory: Path, sidecar: str, stamp: dict, names: tuple[str, ...]):
    """The named planes if the sidecar carries ``stamp``; a corrupt band unstamps the cache."""
    recorded = read_sidecar(directory / sidecar, stamp)
    if recorded is None:
        return None

    def unstamp() -> None:
        (directory / sidecar).unlink(missing_ok=True)

    storage = recorded.get("storage", STORAGE_RAW)
    try:
        return tuple(open_plane(directory, n, stamp["size"], storage, unstamp) for n in names)
    except (OSError, ValueError, ImportError):
        return None


def cached_raster(directory: Path, stamp: dict) -> tuple | None:
    """The cached raster's two planes, read-only, or ``None`` if it is not this one."""
    return _planes(directory, CACHE_SIDECAR_NAME, stamp, (DIRECT_Z_NAME, DIRECT_COVERAGE_NAME))


def cached_family(directory: Path, stamp: dict):
    """The direct cache's family plane, when it is this cache and was written."""
    if "families" not in stamp or cached_raster(directory, stamp) is None:
        return None
    found = _planes(directory, CACHE_SIDECAR_NAME, stamp, (DIRECT_FAMILY_NAME,))
    return None if found is None else found[0]


#: The Titan trees, a mesh raster at ``TITAN_FACTOR`` times the render's pixel.
TITAN_CACHE_DIR_NAME = "titan.cache"
TITAN_FACTOR = 2

#: The river splines and the water boxes they are reconciled with: one small JSON, keyed on
#: the build and the reader version, so a run whose raster caches hit skips the sweep.
RIVER_CACHE_DIR_NAME = "rivers.cache"

#: Every raster cache, the planes ``python -m mapgen compress-cache`` converts.
BAND_STORE_DIRS = (
    DIRECT_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    MESH_CACHE_DIR_NAME,
    TITAN_CACHE_DIR_NAME,
)

#: Every raster cache a run deletes at its end unless ``--keep-direct``.
CACHE_DIR_NAMES = (*BAND_STORE_DIRS, RIVER_CACHE_DIR_NAME)


def mesh_stamp(size: int, build: str | None, reader_version: int) -> dict:
    return {"size": int(size), "game_version_pinned": build, "reader_version": int(reader_version)}


def cached_meshes(directory: Path, stamp: dict):
    """``(z cm, class)`` planes, read-only, if the cache is this one, else ``None``."""
    return _planes(directory, CACHE_SIDECAR_NAME, stamp, (MESH_Z_NAME, MESH_CLASS_NAME))


def cached_mesh_family(directory: Path, stamp: dict):
    """The mesh cache's family plane, when it is this cache and one was written."""
    found = _planes(directory, CACHE_SIDECAR_NAME, stamp, (MESH_FAMILY_NAME,))
    return None if found is None else found[0]


RIVER_CACHE_NAME = "rivers.json"


def river_stamp(build: str | None, reader_version: int) -> dict:
    return {"game_version_pinned": build, "reader_version": int(reader_version)}


def cached_rivers(directory: Path, stamp: dict) -> dict | None:
    """``{"stamp", "rivers", "boxes"}`` if the cache is this one, else ``None``."""
    try:
        recorded = json.loads((directory / RIVER_CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or not isinstance(recorded.get("stamp"), dict):
        return None
    return recorded if {k: recorded["stamp"].get(k) for k in stamp} == stamp else None


def write_rivers(directory: Path, stamp: dict, rivers: list, boxes: list) -> dict:
    payload = {"stamp": stamp, "rivers": rivers, "boxes": [[n, list(b)] for n, b in boxes]}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RIVER_CACHE_NAME).write_text(json.dumps(payload), encoding="utf-8")
    return payload


def missing_caches(
    root: Path, stamp: dict, meshes_stamp: dict, *, top: bool, meshes: bool, titan_stamp=None
) -> list[str]:
    """The cache directories under ``root`` a palette-only run needs and cannot use."""
    wanted = [(DIRECT_CACHE_DIR_NAME, stamp, cached_raster)]
    if top:
        wanted.append((TOP_CACHE_DIR_NAME, stamp, cached_raster))
    if meshes:
        wanted.append((MESH_CACHE_DIR_NAME, meshes_stamp, cached_meshes))
    if titan_stamp is not None:
        wanted.append((TITAN_CACHE_DIR_NAME, titan_stamp, cached_meshes))
    return [name for name, want, read in wanted if read(root / name, want) is None]


def restyle_gaps(root: Path, size: int, subsamples: int, build, *, top: bool, meshes: bool,
                 titan: bool) -> list[str]:  # fmt: skip
    """``missing_caches`` for a run at ``size``; ``titan`` when it draws the Titan trees."""
    mesh_key = mesh_stamp(size, build, READER_VERSIONS["render_meshes"])
    titan_key = mesh_stamp(size // TITAN_FACTOR, build, READER_VERSIONS["titan_trees"])
    stamp = raster_cache_stamp(size, subsamples, build)
    return missing_caches(
        root, stamp, mesh_key, top=top, meshes=meshes, titan_stamp=titan_key if titan else None
    )
