"""A loaded field: its planes, the decode cache, and the point lookups."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import cave_masks, collision_pack
from .areas import FieldAreas
from .codec import DECODERS, sha256_of
from .planes import (
    CACHE_DIR_NAME,
    DENSITY_NAME,
    DM_PER_M,
    HEIGHT_NAME,
    NODATA,
    PROV_CLIFF_VALUES,
    PROV_LANDSCAPE,
    PROV_NAME,
    TERRAIN_NAME,
    TOP_NAME,
    WATER_DRY,
    WATER_NAME,
    WATER_QUALITY_NAME,
)
from .readings import AMBIGUOUS_M, SURFACES, Reading, Surfaces

__all__ = ["Field"]

#: Four vertices spanning more than this are a cliff edge, not a slope: the point reads the
#: nearest one instead of a blend of rock top and the ground below it.
BLEND_MAX_STEP_M = 2.0

#: A landscape ground reading this close to the terrain plane is that plane rounded to
#: decimetres, so the terrain's 7.8 mm value answers instead.
REFINE_M = 0.1

CAVES_RECHECK_S = 1.0

#: Under ``inside``, a collision surface at most this far under the hint is the cave floor.
CAVE_FLOOR_REACH_M = 3.0

#: What a caller is told when the sidecar records no measured accuracy for a layer. Only
#: reached by a hand-written or truncated ``meta.json``; the generator always measures.
UNKNOWN_ACCURACY_M = None


class Field(FieldAreas):
    """A loaded heightmap: its rasters, a georeference, and the accuracy it measured.

    Constructed by ``load_field``. Height and provenance are opened when the object is
    built, since there is no answer without both; every other plane on first use. With
    ``cache`` on, a plane is decoded once into ``cache/<name>.npy`` and memory-mapped after.
    """

    def __init__(
        self,
        meta: dict[str, Any],
        directory: Path,
        *,
        cache: bool = True,
        caves_dir: Path | None = None,
    ) -> None:
        self.meta = meta
        self.directory = directory
        self.cache = cache
        self.caves_dir = caves_dir
        self._caves: tuple[int, cave_masks.Caves | None] | None = None
        self._caves_checked = 0.0
        self._rocks: tuple[int, collision_pack.RockIndex | None] | None = None
        self._rocks_checked = 0.0
        grid = meta["grid"]
        self.width = int(grid["width"])
        self.height = int(grid["height"])
        self.x0_cm = float(grid["x0_cm"])
        self.y0_cm = float(grid["y0_cm"])
        self.spacing_cm = float(grid["spacing_cm"])
        self._planes: dict[str, np.ndarray | None] = {}
        self.cache_events: dict[str, str] = {}
        self.height_dm = self.plane(HEIGHT_NAME)
        self.provenance_plane = self.plane(PROV_NAME)
        if self.height_dm is None or self.provenance_plane is None:
            raise FileNotFoundError("height or provenance plane missing")
        self._accuracy = {
            int(key): value.get("accuracy_m")
            for key, value in (meta.get("provenance") or {}).items()
            if str(key).lstrip("-").isdigit()
        }
        self.terrain_grid = self._read_terrain_grid(meta.get("terrain_grid"))

    # -- planes ------------------------------------------------------------------------

    def _plane_shape(self, name: str) -> tuple[int, int, str]:
        if name == TERRAIN_NAME:
            tg = self.terrain_grid
            return tg["height"], tg["width"], "u16"
        kind = "u8" if name.endswith(".u8.z") else "i16"
        return self.height, self.width, kind

    def plane(self, name: str) -> np.ndarray | None:
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

    def _cached(self, path: Path, height: int, width: int, kind: str) -> np.ndarray | None:
        """The plane from its ``.npy`` cache, writing the cache first if it is missing or stale.

        Any failure -- a read-only medium, a cache file another process holds mapped --
        returns ``None`` and the caller decodes in memory.
        """
        cache_dir = self.directory / CACHE_DIR_NAME
        npy = cache_dir / (path.name + ".npy")
        stamp_path = cache_dir / (path.name + ".stamp.json")
        try:
            source = path.stat()
            want = {
                "source": path.name,
                "bytes": source.st_size,
                "shape": [height, width],
                "dtype": kind,
                "generator_version": self.meta.get("generator_version"),
            }
            try:
                have = json.loads(stamp_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                have = {}
            current = (
                isinstance(have, dict)
                and npy.is_file()
                and all(have.get(k) == v for k, v in want.items())
                and (
                    have.get("mtime_ns") == source.st_mtime_ns
                    or have.get("sha256") == sha256_of(path)
                )
            )
            if current:
                array = np.load(npy, mmap_mode="r", allow_pickle=False)
                if array.shape == (height, width):
                    self.cache_events[path.name] = "mapped"
                    return array
            array = DECODERS[kind](path.read_bytes(), height, width)
            cache_dir.mkdir(exist_ok=True)
            tmp = npy.with_name(npy.name + f".{os.getpid()}.tmp")
            with open(tmp, "wb") as handle:
                np.save(handle, array, allow_pickle=False)
            os.replace(tmp, npy)
            stamp = {**want, "mtime_ns": source.st_mtime_ns, "sha256": sha256_of(path)}
            tmp_stamp = stamp_path.with_name(stamp_path.name + f".{os.getpid()}.tmp")
            tmp_stamp.write_text(json.dumps(stamp), encoding="utf-8")
            os.replace(tmp_stamp, stamp_path)
            self.cache_events[path.name] = "written"
            del array
            return np.load(npy, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError):
            self.cache_events[path.name] = "failed"
            return None

    def _read_terrain_grid(self, raw: Any) -> dict[str, Any] | None:
        """``terrain_grid`` with its offset into the main grid, or ``None`` if unusable."""
        if not isinstance(raw, dict):
            return None
        try:
            tg = {
                "width": int(raw["width"]),
                "height": int(raw["height"]),
                "x0_cm": float(raw["x0_cm"]),
                "y0_cm": float(raw["y0_cm"]),
                "spacing_cm": float(raw["spacing_cm"]),
                "zero": float(raw.get("zero", 32768.0)),
                "units_per_m": float(raw.get("units_per_m", 128.0)),
                "offset_m": float(raw.get("offset_m", 1.0)),
            }
        except (KeyError, TypeError, ValueError):
            return None
        dc = (tg["x0_cm"] - self.x0_cm) / self.spacing_cm
        dr = (tg["y0_cm"] - self.y0_cm) / self.spacing_cm
        aligned = tg["spacing_cm"] == self.spacing_cm and dc == round(dc) and dr == round(dr)
        tg["col_off"] = round(dc) if aligned else None
        tg["row_off"] = round(dr) if aligned else None
        return tg

    def accuracy_m(self, provenance: int) -> float | None:
        """What the generator measured for one layer, or ``None`` where it recorded nothing."""
        return self._accuracy.get(provenance, UNKNOWN_ACCURACY_M)

    @property
    def has_terrain(self) -> bool:
        return self.plane(TERRAIN_NAME) is not None

    @property
    def has_top(self) -> bool:
        return self.plane(TOP_NAME) is not None

    def water_raster(self) -> np.ndarray | None:
        return self.plane(WATER_NAME)

    def water_quality_raster(self) -> np.ndarray | None:
        """``waterq.u8.z``, or ``None`` for a field written before it existed."""
        return self.plane(WATER_QUALITY_NAME)

    def caves(self) -> cave_masks.Caves | None:
        """The cave masks beside this field, loaded on first use and again when rewritten.

        The sidecar is stat'ed at most once per ``CAVES_RECHECK_S``, so a point read does not
        pay for a stat.
        """
        if self.caves_dir is None:
            return None
        now = time.monotonic()
        if self._caves is not None and now - self._caves_checked < CAVES_RECHECK_S:
            return self._caves[1]
        self._caves_checked = now
        try:
            stamp = (self.caves_dir / cave_masks.META_NAME).stat().st_mtime_ns
        except OSError:
            self._caves = (0, None)
            return None
        if self._caves is None or self._caves[0] != stamp:
            self._caves = (stamp, cave_masks.load_caves(self.caves_dir))
        return self._caves[1]

    def rocks(self) -> collision_pack.RockIndex | None:
        """The collision pack beside the planes, loaded on first use and again when rewritten."""
        now = time.monotonic()
        if self._rocks is not None and now - self._rocks_checked < CAVES_RECHECK_S:
            return self._rocks[1]
        self._rocks_checked = now
        try:
            stamp = (self.directory / collision_pack.META_NAME).stat().st_mtime_ns
        except OSError:
            self._rocks = (0, None)
            return None
        if self._rocks is None or self._rocks[0] != stamp:
            self._rocks = (stamp, collision_pack.load_rocks(self.directory, self.build))
        return self._rocks[1]

    def near_rock(self, x_cm: float, y_cm: float) -> bool:
        """Whether any vertex of the quad holding a point is cliff."""
        c0 = math.floor((x_cm - self.x0_cm) / self.spacing_cm)
        r0 = math.floor((y_cm - self.y0_cm) / self.spacing_cm)
        block = self.provenance_plane[max(r0, 0) : r0 + 2, max(c0, 0) : c0 + 2]
        return bool(np.isin(block, PROV_CLIFF_VALUES).any())

    def collision(self, x_cm: float, y_cm: float, found: Surfaces) -> Surfaces | None:
        """``found`` with its rock surfaces read off the collision pack, or ``None``.

        ``ground`` is the highest of the landscape and the cliff set, ``top`` the highest of
        everything, and every other up-facing surface on the line is a floor. ``None`` where
        there is no pack, or neither a hit nor landscape under the point.
        """
        index = self.rocks()
        if index is None:
            return None
        hits = index.hits(float(x_cm), float(y_cm))
        base = [] if found.terrain_m is None else [found.terrain_m]
        ground_hits = hits.standing(collision_pack.GROUND_KINDS)
        every = hits.standing()
        if not base and not every:
            return None
        ground = max(base + ground_hits) if base or ground_hits else found.ground_m
        top = max(v for v in (ground, *every) if v is not None)
        named = {round(v, 2) for v in (ground, top, found.terrain_m) if v is not None}
        return Surfaces(
            ground_m=None if ground is None else round(ground, 3),
            terrain_m=found.terrain_m,
            top_m=round(top, 3),
            provenance=found.provenance,
            floors=tuple(round(z, 3) for z in every if round(z, 2) not in named),
        )

    def cave_at(
        self,
        x_cm: float,
        y_cm: float,
        hint_z_cm: float | None = None,
        lowest_m: float | None = None,
    ) -> str:
        """``caves.CAVE_VALUES`` at a point; ``none`` wherever no cave mask was generated."""
        found = self.caves()
        if found is None:
            return cave_masks.NONE
        return found.classify(x_cm, y_cm, hint_z_cm, lowest_m)

    def density_raster(self) -> np.ndarray | None:
        """``density.u8.z``, or ``None`` for a field written before it existed.

        How many source vertices landed in each texel, clamped at 255 and zero wherever the
        cliff layer did not answer. ``None`` is not zero: a field predating the plane knows
        nothing about its own density.
        """
        return self.plane(DENSITY_NAME)

    # -- geometry ----------------------------------------------------------------------

    @property
    def build(self) -> str | None:
        """The game build this field was cut from, for the staleness the project announces."""
        source = (self.meta.get("sources") or {}).get("game") or {}
        pinned = source.get("game_version_pinned")
        return pinned if isinstance(pinned, str) else None

    def texel(self, x_cm: float, y_cm: float) -> tuple[int, int] | None:
        """``(row, col)`` for a world coordinate, or ``None`` if it is off the grid.

        Rounded, never floored: the grid is **vertex-aligned**, so a texel's height belongs
        to the point ``x0 + col*spacing`` exactly. Flooring would answer with the vertex up
        to a metre south-west of the question, which on a cliff edge is a different cliff.
        """
        col = round((x_cm - self.x0_cm) / self.spacing_cm)
        row = round((y_cm - self.y0_cm) / self.spacing_cm)
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        return row, col

    def at(self, x_cm: float, y_cm: float) -> Reading | None:
        """The terrain at one world coordinate, or ``None`` where the field knows nothing.

        ``None`` covers both silences -- off the grid, and a no-data texel inside it --
        because they are one answer to the caller: nothing is known about that spot.
        """
        where = self.texel(x_cm, y_cm)
        if where is None:
            return None
        row, col = where
        raw = int(self.height_dm[row, col])
        if raw == NODATA:
            return None
        provenance = int(self.provenance_plane[row, col])
        water = self.water_raster()
        water_m = None
        quality = WATER_DRY
        if water is not None:
            wet = int(water[row, col])
            if wet != NODATA:
                water_m = wet / DM_PER_M
                grades = self.water_quality_raster()
                if grades is not None:
                    quality = int(grades[row, col])
        return Reading(
            z_m=raw / DM_PER_M,
            provenance=provenance,
            accuracy_m=self._accuracy.get(provenance, UNKNOWN_ACCURACY_M),
            water_m=water_m,
            water_quality=quality,
            cave=self.cave_at(x_cm, y_cm),
        )

    def _sample(
        self,
        plane: np.ndarray,
        nodata: int,
        grid: tuple[float, float, float],
        x_cm: float,
        y_cm: float,
        bilinear: bool,
        max_step: float = math.inf,
        triangles: bool = False,
    ) -> tuple[float, tuple[int, int]] | None:
        """Raw value at a point and the vertex that dominated it, or ``None``.

        Bilinear over the vertices with non-zero weight; if any of those is no data, or they
        span more than ``max_step`` (a cliff edge), the heaviest valid one answers alone.
        ``triangles`` reads the engine's landscape surface instead: each quad is two flat
        triangles split on the (r, c)-(r+1, c+1) diagonal (docs/spatial-and-map.md).
        """
        x0, y0, spacing = grid
        fx, fy = (x_cm - x0) / spacing, (y_cm - y0) / spacing
        h, w = plane.shape
        if not (0 <= round(fx) < w and 0 <= round(fy) < h):
            return None
        if not bilinear:
            r, c = round(fy), round(fx)
            v = int(plane[r, c])
            return None if v == nodata else (float(v), (r, c))
        c0, r0 = math.floor(fx), math.floor(fy)
        tx, ty = fx - c0, fy - r0
        block = np.asarray(plane[max(r0, 0) : r0 + 2, max(c0, 0) : c0 + 2]).tolist()
        if not triangles:
            weights = [
                (dr, dc, wy * wx)
                for dr, wy in ((0, 1.0 - ty), (1, ty))
                for dc, wx in ((0, 1.0 - tx), (1, tx))
            ]
        elif tx >= ty:
            weights = [(0, 0, 1.0 - tx), (0, 1, tx - ty), (1, 1, ty)]
        else:
            weights = [(0, 0, 1.0 - ty), (1, 0, ty - tx), (1, 1, tx)]
        taps = []
        for dr, dc, weight in weights:
            if weight <= 0.0:
                continue
            r, c = r0 + dr, c0 + dc
            inside = 0 <= r < h and 0 <= c < w
            v = block[r - max(r0, 0)][c - max(c0, 0)] if inside else nodata
            taps.append((weight, r, c, v))
        good = [t for t in taps if t[3] != nodata]
        if not good:
            return None
        lead = max(good, key=lambda t: t[0])
        values = [t[3] for t in taps]
        if len(good) < len(taps) or max(values) - min(values) > max_step:
            return float(lead[3]), (lead[1], lead[2])
        return sum(t[0] * t[3] for t in taps), (lead[1], lead[2])

    def _ground_m(
        self, plane: np.ndarray | None, x_cm: float, y_cm: float, bilinear: bool
    ) -> tuple[float, tuple[int, int]] | None:
        if plane is None:
            return None
        got = self._sample(
            plane,
            NODATA,
            (self.x0_cm, self.y0_cm, self.spacing_cm),
            x_cm,
            y_cm,
            bilinear,
            BLEND_MAX_STEP_M * DM_PER_M,
        )
        return None if got is None else (got[0] / DM_PER_M, got[1])

    def _terrain_m(self, x_cm: float, y_cm: float, bilinear: bool) -> float | None:
        plane = self.plane(TERRAIN_NAME)
        tg = self.terrain_grid
        if plane is None or tg is None:
            return None
        got = self._sample(
            plane,
            0,
            (tg["x0_cm"], tg["y0_cm"], tg["spacing_cm"]),
            x_cm,
            y_cm,
            bilinear,
            triangles=True,
        )
        if got is None:
            return None
        return (got[0] - tg["zero"]) / tg["units_per_m"] + tg["offset_m"]

    def surfaces(
        self, x_cm: float, y_cm: float, *, bilinear: bool = True, top: bool = True
    ) -> Surfaces | None:
        """Every surface at a point, or ``None`` off the grid or where none has data."""
        x_cm, y_cm = float(x_cm), float(y_cm)
        where = self.texel(x_cm, y_cm)
        if where is None:
            return None
        ground = self._ground_m(self.height_dm, x_cm, y_cm, bilinear)
        top_plane = self.plane(TOP_NAME) if top else None
        top = self._ground_m(top_plane, x_cm, y_cm, bilinear)
        terrain = self._terrain_m(x_cm, y_cm, bilinear)
        if ground is None and top is None and terrain is None:
            return None
        row, col = ground[1] if ground is not None else where
        return Surfaces(
            ground_m=None if ground is None else round(ground[0], 3),
            terrain_m=None if terrain is None else round(terrain, 3),
            top_m=None if top is None else round(top[0], 3),
            provenance=int(self.provenance_plane[row, col]),
        )

    def z(
        self,
        x_cm: float,
        y_cm: float,
        *,
        surface: str = "ground",
        hint_z_cm: float | None = None,
        bilinear: bool = True,
    ) -> Reading | None:
        """The height at a point on one surface, or ``None`` where that surface knows nothing.

        With ``hint_z_cm`` the surface is chosen by ``Surfaces.pick`` instead of by
        ``surface``, and ``Reading.surface`` says which answered.
        """
        if surface not in SURFACES:
            raise ValueError(f"surface {surface!r} is not one of {', '.join(SURFACES)}")
        x_cm, y_cm = float(x_cm), float(y_cm)
        found = self.surfaces(
            x_cm, y_cm, bilinear=bilinear, top=surface == "top" or hint_z_cm is not None
        )
        if found is None:
            return None
        lowest = min(v for _, v in found.candidates())
        cave = self.cave_at(x_cm, y_cm, hint_z_cm, lowest)
        exact = None
        if surface != "terrain" and (
            hint_z_cm is not None or surface == "top" or self.near_rock(x_cm, y_cm)
        ):
            exact = self.collision(x_cm, y_cm, found)
        cave_floor = False
        if hint_z_cm is not None:
            hint_m = hint_z_cm / 100.0
            picked = (exact or found).pick(hint_m)
            if cave == cave_masks.INSIDE:
                cave_floor = (
                    exact is not None
                    and picked is not None
                    and -AMBIGUOUS_M <= hint_m - picked[1] <= CAVE_FLOOR_REACH_M
                )
                if not cave_floor:
                    picked, exact = found.pick(hint_m), None
            if picked is None:
                return None
            surface, z_m = picked
        else:
            value = {"ground": "ground_m", "terrain": "terrain_m", "top": "top_m"}[surface]
            z_m = getattr(exact or found, value)
            if z_m is None:
                return None
        found = exact or found
        if (
            surface == "ground"
            and found.provenance == PROV_LANDSCAPE
            and found.terrain_m is not None
            and abs(z_m - found.terrain_m) <= REFINE_M
        ):
            z_m = found.terrain_m
        provenance = PROV_LANDSCAPE if surface == "terrain" else found.provenance
        if self.has_terrain:
            ambiguous = found.ground_m is not None and (
                found.terrain_m is None
                and found.provenance in PROV_CLIFF_VALUES
                or found.terrain_m is not None
                and found.ground_m - found.terrain_m > AMBIGUOUS_M
            )
        else:
            ambiguous = found.provenance in PROV_CLIFF_VALUES
        row, col = self.texel(x_cm, y_cm)  # type: ignore[misc]
        water_m, quality = None, WATER_DRY
        water = self.water_raster()
        if water is not None and int(water[row, col]) != NODATA:
            water_m = int(water[row, col]) / DM_PER_M
            grades = self.water_quality_raster()
            if grades is not None:
                quality = int(grades[row, col])
        return Reading(
            z_m=z_m,
            provenance=provenance,
            accuracy_m=self._accuracy.get(provenance, UNKNOWN_ACCURACY_M),
            water_m=water_m,
            water_quality=quality,
            surface=surface,
            terrain_z_m=found.terrain_m,
            ambiguous=bool(ambiguous),
            cave=cave,
            cave_floor=cave_floor,
        )
