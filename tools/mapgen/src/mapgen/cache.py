"""The on-disk caches (direct, top, meshes, rivers): their names, stamps, readers and writers.

The names and stamp bytes are what existing caches were written under, so they still hit.
A raster cache's planes are written as a zstd band store; the raw memory maps every cache was
before it are still read, and the sidecar's ``storage`` says which (docs/spatial-and-map.md
section 39).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import NamedTuple, Protocol, TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.bandstore import BandArray, BandWriter
from mapgen.common import RENDERS_DIR_NAME
from satisfactory_mcp.core.arrays import F32Grid
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

#: Where the direct raster is kept between the two layers. Rasterising 216 M triangles is
#: twenty minutes and the answer does not depend on which layer is being coloured, so it is
#: done once, kept on disk, and deleted at the end of the run unless ``--keep-direct``.
DIRECT_CACHE_DIR_NAME = "direct.cache"
DIRECT_Z_NAME = "direct.z.f32"
DIRECT_COVERAGE_NAME = "direct.cov.u8"
#: The cliff family of each texel's winning rock (``gamedata.rocks.families.FAMILIES``).
DIRECT_FAMILY_NAME = "direct.family.u8"
DIRECT_PLANE_NAMES = (DIRECT_Z_NAME, DIRECT_COVERAGE_NAME, DIRECT_FAMILY_NAME)

#: Every cache's stamp: the sidecar a cache is read back by.
CACHE_SIDECAR_NAME = "meta.json"

#: The arch-and-boulder raster, cached the same way and under the same file names.
TOP_CACHE_DIR_NAME = "top.cache"

MESH_CACHE_DIR_NAME = "meshes.cache"
MESH_Z_NAME = "meshes.z.f32"
MESH_CLASS_NAME = "meshes.class.u8"
#: The rock family of each texel's winning render-only mesh, as the direct cache's.
MESH_FAMILY_NAME = "meshes.family.u8"
MESH_PLANE_NAMES = (MESH_Z_NAME, MESH_CLASS_NAME, MESH_FAMILY_NAME)

#: Every plane a raster cache holds, by its raw file name, and its element type.
PLANE_DTYPES: dict[str, np.dtype[np.generic]] = {
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

#: The Titan trees, a mesh raster at ``TITAN_FACTOR`` times the render's pixel.
TITAN_CACHE_DIR_NAME = "titan.cache"
TITAN_FACTOR = 2

#: The river splines and the water boxes they are reconciled with: one small JSON, keyed on
#: the build and the reader version, so a run whose raster caches hit skips the sweep.
RIVER_CACHE_DIR_NAME = "rivers.cache"
RIVER_CACHE_NAME = "rivers.json"

#: Every raster cache, the planes ``python -m mapgen compress-cache`` converts.
BAND_STORE_DIRS = (
    DIRECT_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    MESH_CACHE_DIR_NAME,
    TITAN_CACHE_DIR_NAME,
)

#: Every raster cache a run deletes at its end unless ``--keep-direct``.
CACHE_DIR_NAMES = (*BAND_STORE_DIRS, RIVER_CACHE_DIR_NAME)


#: A raster plane as a cache hands it out: a raw memory map, or the band store's reader.
Plane: TypeAlias = NDArray[np.generic] | BandArray


class ReadPlane(Protocol):
    """What a drawer reads from a plane: its shape, and rows and columns cut out of it."""

    @property
    def shape(self) -> tuple[int, ...]: ...

    def __getitem__(self, key: slice | tuple[slice, slice], /) -> NDArray[np.generic]: ...


class DirectPlanes(NamedTuple):
    """The direct raster as the band loop takes it, with the ground it composes over."""

    z: Plane
    coverage: Plane
    ground: F32Grid
    subsamples: int


class TopPlanes(NamedTuple):
    """The arch-and-boulder overlay: its max-Z, its coverage, and the samples per texel."""

    z: Plane
    coverage: Plane
    subsamples: int


class MeshPlanes(NamedTuple):
    """The render-only meshes: z in cm, the class code, and the rock family where written."""

    z_cm: Plane
    cls: Plane
    family: Plane | None = None


class TitanPlanes(NamedTuple):
    """The Titan trees' mesh raster, ``factor`` render pixels to its texel, from an origin."""

    z_cm: Plane
    cls: Plane
    factor: int
    row0: int
    col0: int


class DirectStamp(TypedDict):
    """``raster_cache_stamp``: what a direct or top cache must agree with to be read."""

    size: int
    subsamples: int
    game_version_pinned: str | None
    families: int


class MeshStamp(TypedDict):
    """``mesh_stamp``: the mesh and Titan caches' key."""

    size: int
    game_version_pinned: str | None
    reader_version: int


class RiverStamp(TypedDict):
    """``river_stamp``: the river cache's key."""

    game_version_pinned: str | None
    reader_version: int


#: A mesh raster as ``mesh_pass`` hands it on: ``(z cm, class)``, and the family plane when
#: the cache has one. ``MeshPlanes`` names the same once its readers take it.
MeshMaps: TypeAlias = tuple[Plane, Plane] | tuple[Plane, Plane, Plane]


def direct_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / DIRECT_CACHE_DIR_NAME


def top_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / TOP_CACHE_DIR_NAME


def raster_cache_stamp(
    size: int, subsamples: int, build: str | None, families: int | None = None
) -> DirectStamp:
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


def mesh_stamp(size: int, build: str | None, reader_version: int) -> MeshStamp:
    return {"size": int(size), "game_version_pinned": build, "reader_version": int(reader_version)}


def river_stamp(build: str | None, reader_version: int) -> RiverStamp:
    return {"game_version_pinned": build, "reader_version": int(reader_version)}


def plane_file(directory: Path, name: str, storage: str) -> Path:
    return directory / (name + BANDS_SUFFIX if storage == STORAGE_BANDS else name)


def clear_planes(directory: Path, names: Iterable[str]) -> None:
    """Remove these planes in either storage, so a cache never holds both."""
    for name in names:
        for storage in STORAGES:
            plane_file(directory, name, storage).unlink(missing_ok=True)


def held_open(paths: Iterable[Path]) -> Path | None:
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


# --------------------------------------------------------------------------- writing


def plane_writer(directory: Path, name: str, size: int, band_rows: int) -> BandWriter:
    """A band store writer for one ``size`` square plane; leaving its block commits it."""
    path = plane_file(directory, name, STORAGE_BANDS)
    return BandWriter(path, (size, size), PLANE_DTYPES[name], band_rows)


def band_spans(size: int, band_rows: int) -> Iterator[tuple[int, int]]:
    """``(top, bottom)`` of each band of ``band_rows`` rows, top to bottom; the last may be short."""
    for top in range(0, size, band_rows):
        yield top, min(top + band_rows, size)


@contextmanager
def rewrite_planes(directory: Path, names: Sequence[str], size: int, band_rows: int, *,
                   clear: Iterable[str] = ()) -> Generator[list[BandWriter]]:  # fmt: skip
    """Writers for a cache's ``names``, committed together when the block ends cleanly.

    The sidecar goes first, and every plane named in ``names`` or ``clear`` in either storage,
    so a cache cut short is a miss rather than old planes under a new stamp.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CACHE_SIDECAR_NAME).unlink(missing_ok=True)
    clear_planes(directory, dict.fromkeys([*names, *clear]))
    with ExitStack() as planes:
        yield [planes.enter_context(plane_writer(directory, n, size, band_rows)) for n in names]


def write_sidecar(path: Path, recorded: Mapping[str, object], indent: int | None = 1) -> None:
    """``recorded`` as the JSON at ``path``, replaced whole: a reader sees the old or the new."""
    staging = path.with_name(path.name + ".tmp")
    try:
        with open(staging, "w", encoding="utf-8") as f:
            f.write(json.dumps(recorded, indent=indent))
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def write_rivers(directory: Path, stamp: RiverStamp, rivers: list[dict[str, object]],
                 boxes: Iterable[tuple[str, Iterable[float]]]) -> dict[str, object]:  # fmt: skip
    """``rivers.json``: the river splines and the water boxes, as ``[name, [x0..z1]]`` pairs."""
    payload: dict[str, object] = {
        "stamp": stamp,
        "rivers": rivers,
        "boxes": [(name, list(box)) for name, box in boxes],
    }
    directory.mkdir(parents=True, exist_ok=True)
    write_sidecar(directory / RIVER_CACHE_NAME, payload, indent=None)
    return payload


# --------------------------------------------------------------------------- reading


def open_plane(directory: Path, name: str, size: int, storage: str,
               on_corrupt: Callable[[], None] | None = None) -> Plane:  # fmt: skip
    """One plane, read-only: a ``BandArray`` in the band store, else the raw memory map."""
    if storage == STORAGE_BANDS:
        return BandArray(plane_file(directory, name, storage), (size, size), PLANE_DTYPES[name],
                         on_corrupt=on_corrupt)  # fmt: skip
    return np.memmap(directory / name, PLANE_DTYPES[name], "r", shape=(size, size))


def _read_json(path: Path) -> JsonValue:
    try:
        recorded: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return recorded


def _carries(recorded: Mapping[str, JsonValue], stamp: Mapping[str, object]) -> bool:
    return {key: recorded.get(key) for key in stamp} == stamp


def read_sidecar(path: Path, stamp: Mapping[str, object]) -> JsonObject | None:
    """The sidecar at ``path`` if it carries ``stamp`` and a storage this reader knows."""
    recorded = _read_json(path)
    if not isinstance(recorded, dict) or not _carries(recorded, stamp):
        return None
    return recorded if recorded.get("storage", STORAGE_RAW) in STORAGES else None


def _planes(directory: Path, stamp: DirectStamp | MeshStamp,
            names: tuple[str, ...]) -> tuple[Plane, ...] | None:  # fmt: skip
    """The named planes if the sidecar carries ``stamp``; a corrupt band unstamps the cache."""
    recorded = read_sidecar(directory / CACHE_SIDECAR_NAME, stamp)
    if recorded is None:
        return None

    def unstamp() -> None:
        (directory / CACHE_SIDECAR_NAME).unlink(missing_ok=True)

    storage = str(recorded.get("storage", STORAGE_RAW))
    try:
        return tuple(open_plane(directory, n, stamp["size"], storage, unstamp) for n in names)
    except (OSError, ValueError, ImportError):
        return None


def _pair(found: tuple[Plane, ...] | None) -> tuple[Plane, Plane] | None:
    return None if found is None else (found[0], found[1])


def _single(found: tuple[Plane, ...] | None) -> Plane | None:
    return None if found is None else found[0]


def cached_raster(directory: Path, stamp: DirectStamp) -> tuple[Plane, Plane] | None:
    """The cached raster's ``(z, coverage)`` planes, read-only, or ``None`` if it is not this one."""
    return _pair(_planes(directory, stamp, (DIRECT_Z_NAME, DIRECT_COVERAGE_NAME)))


def cached_family(directory: Path, stamp: DirectStamp) -> Plane | None:
    """The direct cache's family plane, when it is this cache and was written."""
    if cached_raster(directory, stamp) is None:
        return None
    return _single(_planes(directory, stamp, (DIRECT_FAMILY_NAME,)))


def cached_meshes(directory: Path, stamp: MeshStamp) -> tuple[Plane, Plane] | None:
    """``(z cm, class)`` planes, read-only, if the cache is this one, else ``None``."""
    return _pair(_planes(directory, stamp, (MESH_Z_NAME, MESH_CLASS_NAME)))


def cached_mesh_family(directory: Path, stamp: MeshStamp) -> Plane | None:
    """The mesh cache's family plane, when it is this cache and one was written."""
    return _single(_planes(directory, stamp, (MESH_FAMILY_NAME,)))


def cached_rivers(directory: Path, stamp: RiverStamp) -> JsonObject | None:
    """``write_rivers``' ``{"stamp", "rivers", "boxes"}`` if the cache is this one, else ``None``."""
    recorded = _read_json(directory / RIVER_CACHE_NAME)
    if not isinstance(recorded, dict):
        return None
    seen = recorded.get("stamp")
    if not isinstance(seen, dict) or not _carries(seen, stamp):
        return None
    return recorded


def missing_caches(root: Path, stamp: DirectStamp, meshes_stamp: MeshStamp, *, top: bool,
                   meshes: bool, titan_stamp: MeshStamp | None = None) -> list[str]:  # fmt: skip
    """The cache directories under ``root`` a palette-only run needs and cannot use."""
    found = {DIRECT_CACHE_DIR_NAME: cached_raster(root / DIRECT_CACHE_DIR_NAME, stamp)}
    if top:
        found[TOP_CACHE_DIR_NAME] = cached_raster(root / TOP_CACHE_DIR_NAME, stamp)
    if meshes:
        found[MESH_CACHE_DIR_NAME] = cached_meshes(root / MESH_CACHE_DIR_NAME, meshes_stamp)
    if titan_stamp is not None:
        found[TITAN_CACHE_DIR_NAME] = cached_meshes(root / TITAN_CACHE_DIR_NAME, titan_stamp)
    return [name for name, planes in found.items() if planes is None]


def restyle_gaps(root: Path, size: int, subsamples: int, build: str | None, *, top: bool,
                 meshes: bool, titan: bool) -> list[str]:  # fmt: skip
    """``missing_caches`` for a run at ``size``; ``titan`` when it draws the Titan trees."""
    mesh_key = mesh_stamp(size, build, READER_VERSIONS["render_meshes"])
    titan_key = mesh_stamp(size // TITAN_FACTOR, build, READER_VERSIONS["titan_trees"])
    stamp = raster_cache_stamp(size, subsamples, build)
    return missing_caches(
        root, stamp, mesh_key, top=top, meshes=meshes, titan_stamp=titan_key if titan else None
    )
