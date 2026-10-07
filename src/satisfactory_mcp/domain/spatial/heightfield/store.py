"""A field directory's planes: each opened on first use and decoded once into a mapped cache.

``PlaneStore`` also holds the georeference and the files beside the planes (cave masks, the
collision pack). The cache and its stamp: docs/map/heightfield.md section 22.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Generic, TypeVar

import numpy as np

from ....core.jsontypes import JsonObject, JsonValue
from . import cave_masks, collision_pack
from .codec import DECODERS, RasterGrid, sha256_of
from .meta import (
    json_float,
    json_int,
    json_object,
    layer_accuracies,
    pinned_build,
    read_terrain_grid,
)
from .planes import (
    CACHE_DIR_NAME,
    DENSITY_NAME,
    HEIGHT_NAME,
    PROV_NAME,
    TERRAIN_NAME,
    TOP_NAME,
    WATER_NAME,
    WATER_QUALITY_NAME,
)

__all__ = ["SIDECAR_RECHECK_S", "PlaneStore"]

#: A sidecar beside the field is stat'ed at most this often, so a point read rarely pays a stat.
SIDECAR_RECHECK_S = 1.0

_Loaded = TypeVar("_Loaded")


class _ReloadingSidecar(Generic[_Loaded]):
    """What ``load`` made of the files beside ``sidecar``, loaded again once it is rewritten."""

    def __init__(self, sidecar: Path, load: Callable[[], _Loaded | None]) -> None:
        self.sidecar = sidecar
        self.load = load
        self._loaded: tuple[int, _Loaded | None] | None = None
        self._checked = 0.0

    def get(self) -> _Loaded | None:
        now = time.monotonic()
        if self._loaded is not None and now - self._checked < SIDECAR_RECHECK_S:
            return self._loaded[1]
        self._checked = now
        try:
            stamp = self.sidecar.stat().st_mtime_ns
        except OSError:
            self._loaded = (0, None)
            return None
        if self._loaded is None or self._loaded[0] != stamp:
            self._loaded = (stamp, self.load())
        return self._loaded[1]


class PlaneStore:
    """A field's planes and georeference, read from ``meta.json`` and the files beside it.

    Height and provenance open with the store, since nothing answers without both; every
    other plane on first use, and ``None`` for a plane this field was written without.
    """

    def __init__(
        self,
        meta: JsonObject,
        directory: Path,
        *,
        cache: bool = True,
        caves_dir: Path | None = None,
    ) -> None:
        self.meta = meta
        self.directory = directory
        self.cache = cache
        self.caves_dir = caves_dir
        grid = json_object(meta["grid"])
        self.width = json_int(grid["width"])
        self.height = json_int(grid["height"])
        self.x0_cm = json_float(grid["x0_cm"])
        self.y0_cm = json_float(grid["y0_cm"])
        self.spacing_cm = json_float(grid["spacing_cm"])
        self._planes: dict[str, RasterGrid | None] = {}
        #: How each plane was opened: ``mapped``, ``written``, ``decoded`` or ``failed, decoded``.
        self.cache_events: dict[str, str] = {}
        self.terrain_grid = read_terrain_grid(
            meta.get("terrain_grid"), self.x0_cm, self.y0_cm, self.spacing_cm
        )
        height_dm, provenance = self.plane(HEIGHT_NAME), self.plane(PROV_NAME)
        if height_dm is None or provenance is None:
            raise FileNotFoundError("height or provenance plane missing")
        self.height_dm = height_dm
        self.provenance_plane = provenance
        self._accuracy = layer_accuracies(meta.get("provenance"))
        # The loaders hold no reference to the store: a cycle would keep its mapped planes
        # open past its last use, and Windows refuses to replace a mapped cache file.
        self._caves = (
            None
            if caves_dir is None
            else _ReloadingSidecar(
                caves_dir / cave_masks.META_NAME, partial(cave_masks.load_caves, caves_dir)
            )
        )
        self._rocks = _ReloadingSidecar(
            directory / collision_pack.META_NAME,
            partial(collision_pack.load_rocks, directory, self.build),
        )

    def plane(self, name: str) -> RasterGrid | None:
        """A decoded plane, or ``None`` when this field was written without it."""
        if name in self._planes:
            return self._planes[name]
        path = self.directory / name
        if not path.is_file() or (name == TERRAIN_NAME and self.terrain_grid is None):
            self._planes[name] = None
            return None
        height, width, kind = self._plane_shape(name)
        array = self._cached(path, height, width, kind) if self.cache else None
        if array is None:
            array = DECODERS[kind](path.read_bytes(), height, width)
            failed = self.cache_events.get(name)
            self.cache_events[name] = f"{failed}, decoded" if failed else "decoded"
        self._planes[name] = array
        return array

    def _plane_shape(self, name: str) -> tuple[int, int, str]:
        if name == TERRAIN_NAME and self.terrain_grid is not None:
            return self.terrain_grid["height"], self.terrain_grid["width"], "u16"
        kind = "u8" if name.endswith(".u8.z") else "i16"
        return self.height, self.width, kind

    def _cached(self, path: Path, height: int, width: int, kind: str) -> RasterGrid | None:
        """The plane mapped from its ``.npy`` cache, written first if missing or stale.

        Any failure (a read-only medium, a cache another process holds mapped) is ``None``,
        and the caller decodes in memory.
        """
        cache_dir = self.directory / CACHE_DIR_NAME
        npy = cache_dir / (path.name + ".npy")
        stamp_path = cache_dir / (path.name + ".stamp.json")
        try:
            source = path.stat()
            want: JsonObject = {
                "source": path.name,
                "bytes": source.st_size,
                "shape": [height, width],
                "dtype": kind,
                "generator_version": self.meta.get("generator_version"),
            }
            if npy.is_file() and _stamp_matches(stamp_path, want, path, source.st_mtime_ns):
                array: RasterGrid = np.load(npy, mmap_mode="r", allow_pickle=False)
                if array.shape == (height, width):
                    self.cache_events[path.name] = "mapped"
                    return array
            stamp: JsonObject = {**want, "mtime_ns": source.st_mtime_ns, "sha256": sha256_of(path)}
            _write_cache(npy, DECODERS[kind](path.read_bytes(), height, width), stamp_path, stamp)
            self.cache_events[path.name] = "written"
            return np.load(npy, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError):
            self.cache_events[path.name] = "failed"
            return None

    def accuracy_m(self, provenance: int) -> float | None:
        """What the generator measured for one layer, or ``None`` where it recorded nothing."""
        return self._accuracy.get(provenance)

    @property
    def has_terrain(self) -> bool:
        return self.plane(TERRAIN_NAME) is not None

    @property
    def has_top(self) -> bool:
        return self.plane(TOP_NAME) is not None

    def water_raster(self) -> RasterGrid | None:
        return self.plane(WATER_NAME)

    def water_quality_raster(self) -> RasterGrid | None:
        """``waterq.u8.z``, or ``None`` for a field written before it existed."""
        return self.plane(WATER_QUALITY_NAME)

    def density_raster(self) -> RasterGrid | None:
        """``density.u8.z``: source vertices per texel, clamped at 255, zero off the cliff layer.

        ``None`` is not zero: a field written before the plane knows nothing of its density.
        """
        return self.plane(DENSITY_NAME)

    def caves(self) -> cave_masks.Caves | None:
        """The cave masks beside this field, loaded on first use and again when rewritten."""
        return None if self._caves is None else self._caves.get()

    def rocks(self) -> collision_pack.RockIndex | None:
        """The collision pack beside the planes, loaded on first use and again when rewritten."""
        return self._rocks.get()

    @property
    def build(self) -> str | None:
        """The game build this field was cut from, for the staleness the project announces."""
        return pinned_build(self.meta)

    def texel(self, x_cm: float, y_cm: float) -> tuple[int, int] | None:
        """``(row, col)`` for a world coordinate, or ``None`` if it is off the grid.

        Rounded, never floored: the grid is vertex-aligned, and flooring would answer with
        the vertex up to a metre south-west, which on a cliff edge is another cliff.
        """
        col = round((x_cm - self.x0_cm) / self.spacing_cm)
        row = round((y_cm - self.y0_cm) / self.spacing_cm)
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        return row, col


def _stamp_matches(stamp_path: Path, want: JsonObject, source: Path, mtime_ns: int) -> bool:
    """Whether a cache stamp describes this source: every ``want`` key, and its mtime or bytes."""
    try:
        have: JsonValue = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(have, dict)
        and all(have.get(key) == value for key, value in want.items())
        and (have.get("mtime_ns") == mtime_ns or have.get("sha256") == sha256_of(source))
    )


def _write_cache(npy: Path, array: RasterGrid, stamp_path: Path, stamp: JsonObject) -> None:
    """``array`` and its stamp, each written aside and swapped in."""
    npy.parent.mkdir(exist_ok=True)
    tmp = npy.with_name(npy.name + f".{os.getpid()}.tmp")
    with open(tmp, "wb") as handle:
        np.save(handle, array, allow_pickle=False)
    os.replace(tmp, npy)
    tmp_stamp = stamp_path.with_name(stamp_path.name + f".{os.getpid()}.tmp")
    tmp_stamp.write_text(json.dumps(stamp), encoding="utf-8")
    os.replace(tmp_stamp, stamp_path)
