"""The lighting stage: the surface a render drew, and one block of it baked into native tiles.

A block is baked with its own halo on a light process; ``lighting/bake.py`` queues the blocks
and bakes the coarser levels, whose tile format is ``light_tiles``. A bake is keyed on what it
reads (``light_key``), so a run that draws the same surface reuses it
(``render/kept_light.py``). docs/spatial-and-map.md section 29.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TypeAlias

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.jit import gpu_on
from mapgen.lighting.canopy import (
    CanopyLight,
    blend_canopy,
    canopy_normal_bytes,
    smoothing_margin,
)
from mapgen.lighting.holes import Holes, fill_holes, find_holes, half_heights, opened
from mapgen.lighting.horizon import (
    HORIZON_DIRS,
    Slabs,
    crown_horizon,
    crown_surface,
    encode_horizon,
    march_horizon,
    normals,
    sky_view,
)
from mapgen.lighting.light_tiles import (
    HZ_LINEAR_SCALE,
    downsample,
    encode_tiles,
    level_layout,
    normal_byte,
    optional_array,
    padded_window,
    tile_jobs,
    work_array,
)
from mapgen.lighting.model import (
    DIRECT_SCALE,
    HZ_CELLS,
    light_axis,
    shaded_direct,
    sun_cells,
    sun_horizon,
)
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.pools import free_ram_bytes
from satisfactory_mcp.core.arrays import F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "BLOCK_TILES",
    "LIGHT_DIR_NAME",
    "LIGHT_GPU_BYTES",
    "LIGHT_VERSION",
    "LIGHT_WORKER_BYTES",
    "LIGHT_WORKER_CAP",
    "TERMS",
    "BlockDone",
    "BlockJob",
    "Occluder",
    "Place",
    "Surface",
    "allocate_work_arrays",
    "bake_block",
    "cast_digests",
    "default_terms",
    "discard",
    "light_key",
    "light_workers",
    "occluder_planes",
    "plane_digest",
    "save_work_array",
]

LIGHT_DIR_NAME = "light"

#: The bake's own version. Bump it when the bake writes other bytes from the same surface,
#: casters and model, so no run reuses a light the old bake wrote.
LIGHT_VERSION = 2

#: Rows of a plane hashed at a time.
DIGEST_ROWS = 1024

#: Native tiles per block edge; a block is computed with its own halo.
BLOCK_TILES = 16

#: The default sun's terms a pixel: sky view, the ground's direct term, then the painted
#: layer's direct term and sky view, crowned and with its canopy's own light.
TERMS = 4

#: A block's rows whose default-sun terms are made at a time, so its full-resolution planes
#: stay small beside the block's own.
TERM_ROWS = 512

#: Light processes at most, and the free memory each one needs: its measured peak on a
#: full-size block with crowns or arches, started with one BLAS thread.
LIGHT_WORKER_CAP = 16
LIGHT_WORKER_BYTES = 1_500_000_000
#: What a light process adds with the CUDA kernels: CuPy and its context, 0.65 GB measured.
LIGHT_GPU_BYTES = 700_000_000

#: The work files a bake leaves in the surface's directory.
_FILES = (
    "z",
    "land",
    "occluder",
    "occluder_cover",
    "slab_ground",
    "slab_lo",
    "slab_hi",
    "terms",
    "zh",
    "landh",
    "svfh",
    "hzq",
)

#: The crown tops on the sheet's grid, metres, or the tops and the covered share as a byte.
Occluder: TypeAlias = F32Grid | tuple[F32Grid, U8Grid | None]

#: Rows, then columns, of the sheet: ``(r0, r1, c0, c1)``.
_Window: TypeAlias = tuple[int, int, int, int]

#: The crowns stood on the drawn surface and on the solid ground, at half resolution.
_CrownSurfaces: TypeAlias = tuple[F32Grid, F32Grid | None]


@dataclass(frozen=True)
class BlockJob:
    """One block of native tiles for a light worker: where it reads, writes, and how far."""

    work: str
    dest: str
    z: int
    spacing_m: float
    halo_h: int
    sky_halo: int
    skip_water: bool
    block: tuple[int, int, int] = (0, 0, 0)


class BlockDone(NamedTuple):
    tiles: int
    hz_bytes: int
    seconds: float


class Place(NamedTuple):
    """Where a ``put`` went: its first row, how many rows, and its columns."""

    row: int
    rows: int
    c0: int
    c1: int


class Surface:
    """The drawn surface, band by band: heights in metres and the land weight as a byte.

    Each ``put`` is hashed as it is stored, on the thread that drew it; ``digest`` folds those
    in row order. ``terms`` is where the bake's default-sun terms for this surface are.
    """

    def __init__(self, directory: Path, size: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.size = size
        self.spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
        self.z: np.memmap[tuple[int, ...], np.dtype[np.float32]] = np.lib.format.open_memmap(
            directory / "z.npy", "w+", np.float32, (size, size)
        )
        self.land: np.memmap[tuple[int, ...], np.dtype[np.uint8]] = np.lib.format.open_memmap(
            directory / "land.npy", "w+", np.uint8, (size, size)
        )
        self.terms = self.path("terms")
        self._puts: dict[Place, bytes] = {}
        self._lock = threading.Lock()

    def put(
        self,
        row: int,
        z_m: NDArray[np.floating],
        land: NDArray[np.floating],
        columns: slice = slice(None),
    ) -> None:
        z = np.ascontiguousarray(z_m, np.float32)
        dry = np.ascontiguousarray(np.round(np.clip(land, 0, 1) * 255), np.uint8)
        self.z[row : row + z.shape[0], columns] = z
        self.land[row : row + z.shape[0], columns] = dry
        digest = hashlib.sha256(z)
        digest.update(dry)
        c0, c1, _step = columns.indices(self.size)
        with self._lock:
            self._puts[Place(row, z.shape[0], c0, c1)] = digest.digest()

    def puts(self) -> list[tuple[Place, str]]:
        """Every ``put`` so far in row order: where it went, and its bytes' digest."""
        with self._lock:
            return [(place, self._puts[place].hex()) for place in sorted(self._puts)]

    def digest(self) -> str:
        """Every ``put`` so far, its place and its bytes, in row order."""
        fold = hashlib.sha256(struct.pack("<q", self.size))
        with self._lock:
            for place in sorted(self._puts):
                fold.update(struct.pack("<4q", *place) + self._puts[place])
        return "sha256:" + fold.hexdigest()

    def flush(self) -> None:
        self.z.flush()
        self.land.flush()

    def path(self, name: str) -> Path:
        return self.directory / f"{name}.npy"

    def close(self) -> None:
        self.flush()
        del self.z, self.land


def _crown_surfaces(
    work: Path, window: _Window, z_window: F32Grid, ground_window: F32Grid | None
) -> _CrownSurfaces | None:
    """The crowns stood on the surface and on the solid ground, at half resolution, or None."""
    occluder = optional_array(work, "occluder", np.float32)
    if occluder is None:
        return None
    top = padded_window(occluder, *window, np.nan)
    cover = optional_array(work, "occluder_cover", np.uint8)
    share = None if cover is None else padded_window(cover, *window, 0.0) / np.float32(255.0)
    on_z = half_heights(crown_surface(z_window, top, share))
    if ground_window is None:
        return on_z, None
    return on_z, half_heights(crown_surface(ground_window, top, share))


def _half_surfaces(
    work: Path, window: _Window, march: bool
) -> tuple[F32Grid, _CrownSurfaces | None, Slabs | None]:
    """The block's heights at half resolution, and what casts on them when it marches."""
    z_window = padded_window(work_array(work, "z", np.float32, "r"), *window)
    z_half = half_heights(z_window)
    if not march:
        return z_half, None, None
    ground, lo, hi = (optional_array(work, f"slab_{k}", np.float32) for k in ("ground", "lo", "hi"))
    slabs = ground_window = None
    if ground is not None and lo is not None and hi is not None:
        ground_window = padded_window(ground, *window)
        slabs = (downsample(ground_window),
                 downsample(padded_window(lo, *window, np.nan), how=np.nanmin),
                 downsample(padded_window(hi, *window, np.nan), how=np.nanmax))  # fmt: skip
    return z_half, _crown_surfaces(work, window, z_window, ground_window), slabs


def _opened_surfaces(
    z_half: F32Grid, crowns: _CrownSurfaces | None, slabs: Slabs | None
) -> tuple[F32Grid, _CrownSurfaces | None, Slabs | None]:
    """What the march and the sky view read with every hole open (``holes.opened``)."""
    open_crowns = None if crowns is None else (
        opened(crowns[0]), None if crowns[1] is None else opened(crowns[1])
    )  # fmt: skip
    open_slabs = None if slabs is None else (opened(slabs[0]), slabs[1], slabs[2])
    return opened(z_half), open_crowns, open_slabs


def _horizon_cells(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    casts: tuple[_CrownSurfaces | None, Slabs | None],
    holes: Holes | None,
) -> Iterator[tuple[int, F32Grid, F32Grid]]:
    """Each direction's ground cell, then its crown cell where the crowns stand above it, each
    with the horizon it was cut from: the crowns' whole, received on the canopy top.

    ``casts`` is the crowns and the slabs; a hole takes the cells of the pixel nearest it.
    """
    crowns, slabs = casts
    for k in range(HORIZON_DIRS):
        az = k * 360.0 / HORIZON_DIRS
        ground = fill_holes(march_horizon(z_half, halo, az, spacing_m, None, slabs), holes, 0.0)
        yield k, ground, ground
        if crowns is not None:
            over = crown_horizon(crowns[0], halo, az, spacing_m, crowns[1])
            over = fill_holes(over, holes, 0.0)
            yield HORIZON_DIRS + k, np.where(over > ground, over, np.float32(0.0)), over


class BakedHorizons(NamedTuple):
    """A block's horizons: the atlas bytes, the coarser levels' source, the default sun's
    cells and, by direction, the canopy's own horizon toward it; the last two with a ring."""

    atlas: U8Grid
    quarter: U8Grid
    sun: list[F32Grid]
    canopy: list[F32Grid]


def _bake_horizons(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    casts: tuple[_CrownSurfaces | None, Slabs | None],
    half_px: int,
    march: bool,
    holes: Holes | None = None,
) -> BakedHorizons:
    """The atlas bytes, the coarser levels' source and the default sun's planes, a cell at a time.

    No ``(cells, half_px, half_px)`` float stack: each cell is encoded as it is marched. The
    march takes a pixel more on each side, which only the default sun's planes keep, so they
    upsample from their neighbours past the block's edge (``_upsampled``).
    """
    hz_u8 = np.zeros((HZ_CELLS, half_px, half_px), np.uint8)
    horizon_quarter = np.zeros((half_px // 2, half_px // 2, HZ_CELLS), np.uint8)
    sun = [np.zeros((half_px + 2, half_px + 2), np.float32)] * HZ_CELLS
    canopy = sun[:HORIZON_DIRS]
    keep = sun_cells(DEFAULT_SUN[0])
    cells = _horizon_cells(z_half, halo - 1, spacing_m, casts, holes) if march else iter(())
    for k, ringed, whole in cells:
        deg = ringed[1:-1, 1:-1]
        hz_u8[k] = encode_horizon(deg)
        horizon_quarter[..., k] = np.round(np.clip(downsample(deg), 0, 90) * HZ_LINEAR_SCALE)
        if k in keep:
            sun[k] = ringed
            if k >= HORIZON_DIRS:
                canopy[k - HORIZON_DIRS] = whole
    return BakedHorizons(hz_u8, horizon_quarter, sun, canopy)


def _upsampled(ringed: F32Grid) -> F32Grid:
    """A half-resolution plane with a ring of one pixel past each edge at full resolution, each
    pixel interpolated from the four half-resolution pixels around its centre."""
    full: F32Grid = ndimage.zoom(ringed, 2, order=1, mode="nearest", grid_mode=True)
    return full[2:-2, 2:-2]


def bake_block(job: BlockJob) -> BlockDone:
    """One block of native tiles: horizons, sky view, normals, tiles, default-sun terms."""
    started = time.time()
    work, spacing_m, (r0, c0, block_px) = Path(job.work), job.spacing_m, job.block
    halo, half_px, half_m = job.halo_h, block_px // 2, 2 * spacing_m
    window = (r0 - 2 * halo, r0 + block_px + 2 * halo, c0 - 2 * halo, c0 + block_px + 2 * halo)
    land = work_array(work, "land", np.uint8, "r")
    land_core = np.asarray(land[r0 : r0 + block_px, c0 : c0 + block_px])
    march = not (job.skip_water and not land_core.any())
    z_half, crowns, slabs = _half_surfaces(work, window, march)
    holes = find_holes(z_half[halo - 1 : 1 - halo, halo - 1 : 1 - halo])
    if holes is not None:
        z_half, crowns, slabs = _opened_surfaces(z_half, crowns, slabs)
    atlas, horizon_quarter, sun, canopy_cells = _bake_horizons(
        z_half, halo, half_m, (crowns, slabs), half_px, march, holes
    )
    sky = job.sky_halo
    rows, cols = (slice(halo - sky - 1, side - halo + sky + 1) for side in z_half.shape[:2])
    sky_ringed = fill_holes(sky_view(z_half[rows, cols], sky, half_m), holes, 1.0)
    canopy_sky = None
    if crowns is not None:
        canopy_sky = fill_holes(sky_view(crowns[0][rows, cols], sky, half_m), holes, 1.0)
    del crowns, slabs
    ring = (r0 - 1, r0 + block_px + 1, c0 - 1, c0 + block_px + 1)
    z_ring = padded_window(work_array(work, "z", np.float32, "r"), *ring)
    nx, ny = normals(z_ring, spacing_m)
    svf = np.clip(_upsampled(sky_ringed), 0, 1)
    nrm = np.stack(
        [normal_byte(nx), normal_byte(ny), np.round(svf * 255).astype(np.uint8), land_core], -1
    )
    del nx, ny, svf
    t = PYRAMID_TILE_PX
    hz_bytes = encode_tiles(tile_jobs(Path(job.dest), job.z, c0 // t, r0 // t, nrm, atlas))
    del atlas
    canopy = None if canopy_sky is None else _canopy(work, job, canopy_cells, canopy_sky)
    _default_terms(work, job, nrm, sun, canopy)
    del canopy
    h0, w0 = r0 // 2, c0 // 2
    ringed = fill_holes(z_half[halo - 1 : 1 - halo, halo - 1 : 1 - halo], holes, 0.0)
    half = (slice(h0, h0 + half_px), slice(w0, w0 + half_px))
    work_array(work, "zh", np.float32)[half] = ringed[1:-1, 1:-1]
    work_array(work, "landh", np.uint8)[half] = np.round(downsample(land_core.astype(np.float32)))
    work_array(work, "svfh", np.uint8)[half] = np.round(np.clip(sky_ringed[1:-1, 1:-1], 0, 1) * 255)
    quarter = (slice(h0 // 2, (h0 + half_px) // 2), slice(w0 // 2, (w0 + half_px) // 2))
    work_array(work, "hzq", np.uint8)[quarter] = horizon_quarter
    tiles = (nrm.shape[0] // t) * (nrm.shape[1] // t)
    return BlockDone(tiles, hz_bytes, time.time() - started)


class _Canopy(NamedTuple):
    """What the canopy's own light reads: its top and cover planes, the surface, and its
    horizon toward the default sun and its sky view at half resolution, with a ring."""

    top: NDArray[np.float32]
    cover: NDArray[np.uint8] | None
    surface: NDArray[np.float32]
    horizon: F32Grid
    sky: F32Grid


def _canopy(work: Path, job: BlockJob, cells: list[F32Grid], sky: F32Grid) -> _Canopy | None:
    """The block's canopy, None without an occluder or where none stands in the block;
    ``cells`` are its horizons by direction, as ``_bake_horizons`` keeps them."""
    r0, c0, n = job.block
    top = optional_array(work, "occluder", np.float32)
    if top is None:
        return None
    cover = optional_array(work, "occluder_cover", np.uint8)
    core = (slice(r0, r0 + n), slice(c0, c0 + n))
    there = np.isfinite(top[core]) if cover is None else np.asarray(cover[core]) > 0
    if not there.any():
        return None
    surface = work_array(work, "z", np.float32, "r")
    return _Canopy(top, cover, surface, sun_horizon(cells, DEFAULT_SUN[0]), sky)


def _canopy_rows(canopy: _Canopy, job: BlockJob, rows: slice) -> CanopyLight:
    """The canopy's light on ``rows`` of the block: its share of each pixel where it stands
    above the surface, its direct term under its own smoothed top and horizons, and its sky
    view."""
    r0, c0, n = job.block
    a, b = r0 + rows.start, r0 + rows.stop
    top = np.asarray(canopy.top[a:b, c0 : c0 + n])
    if canopy.cover is None:
        share = np.isfinite(top).astype(np.float32)
    else:
        share = np.asarray(canopy.cover[a:b, c0 : c0 + n], np.float32) / np.float32(255.0)
    share *= top > np.asarray(canopy.surface[a:b, c0 : c0 + n])
    margin = smoothing_margin(job.spacing_m)
    reach = margin + 1
    top = padded_window(canopy.top, a - reach, b + reach, c0 - reach, c0 + n + reach, np.nan)
    slope = canopy_normal_bytes(top, margin, job.spacing_m)
    direct = shaded_direct(slope, _upsampled(_ring_rows(canopy.horizon, rows)), DEFAULT_SUN)
    sky = np.clip(_upsampled(_ring_rows(canopy.sky, rows)), 0, 1)
    return CanopyLight(share, direct, sky)


def _ring_rows(ringed: F32Grid, rows: slice) -> F32Grid:
    """The half-resolution rows, ring included, that ``_upsampled`` reads for ``rows``."""
    return ringed[rows.start // 2 : rows.stop // 2 + 2]


def _default_terms(
    work: Path, job: BlockJob, nrm: U8Grid, sun: list[F32Grid], canopy: _Canopy | None
) -> None:
    """The block's terms at the default sun, ``TERM_ROWS`` at a time: the sky view and the
    ground's direct term, then the painted layer's, crowned and with the canopy's own light,
    and its sky view."""
    r0, c0, n = job.block
    terms = work_array(work, "terms", np.uint8)
    horizons = {crowned: sun_horizon(sun, DEFAULT_SUN[0], crowned) for crowned in (False, True)}
    for start in range(0, n, TERM_ROWS):
        rows = slice(start, min(start + TERM_ROWS, n))
        out = terms[r0 + rows.start : r0 + rows.stop, c0 : c0 + n]
        part = nrm[rows]
        out[..., 0] = out[..., 3] = part[..., 2]
        for k, crowned in ((1, False), (2, True)):
            horizon = _upsampled(_ring_rows(horizons[crowned], rows))
            direct = shaded_direct(part, horizon, DEFAULT_SUN)
            if crowned and canopy is not None:
                open_sky = part[..., 2].astype(np.float32) / np.float32(255.0)
                direct, open_sky = blend_canopy(direct, open_sky, _canopy_rows(canopy, job, rows))
                out[..., 3] = np.round(np.clip(open_sky, 0, 1) * 255)
            out[..., k] = np.clip(np.round(direct * DIRECT_SCALE), 0, 255)


def allocate_work_arrays(work: Path, size: int) -> None:
    """The bake's work files in ``work``: the terms and the coarser levels' sources."""
    layout = (("terms", np.uint8, (size, size, TERMS)), *level_layout(size // 2))
    for name, dtype, shape in layout:
        np.lib.format.open_memmap(work / f"{name}.npy", "w+", dtype, shape).flush()


def _in_place(raster: NDArray[np.number], path: Path, dtype: type[np.number]) -> bool:
    name = getattr(raster, "filename", None)
    return name is not None and raster.dtype == dtype and Path(name).resolve() == path.resolve()


def save_work_array(
    work: Path, name: str, raster: NDArray[np.number] | None, dtype: type[np.number] = np.float32
) -> None:
    """``raster`` as the work file ``name``, unless it is that file already or None."""
    path = work / f"{name}.npy"
    if raster is not None and not _in_place(raster, path, dtype):
        np.save(path, np.asarray(raster, dtype))


def light_workers(requested: int | None = None) -> int:
    """``requested``, else one a core up to ``LIGHT_WORKER_CAP`` that the free RAM holds."""
    if requested:
        return max(1, requested)
    free = free_ram_bytes()
    each = LIGHT_WORKER_BYTES + (LIGHT_GPU_BYTES if gpu_on() else 0)
    by_ram = LIGHT_WORKER_CAP if free is None else int(free // each)
    return max(1, min(os.cpu_count() or 1, LIGHT_WORKER_CAP, by_ram))


def occluder_planes(work: Path, size: int) -> tuple[F32Grid, U8Grid]:
    """The crown tops and cover as ``w+`` memory maps under the names the bake reads."""
    work.mkdir(parents=True, exist_ok=True)
    shape = (size, size)
    top = np.lib.format.open_memmap(work / "occluder.npy", "w+", np.float32, shape)
    return top, np.lib.format.open_memmap(work / "occluder_cover.npy", "w+", np.uint8, shape)


def plane_digest(plane: NDArray[np.number] | None) -> str | None:
    """A plane's type, shape and bytes, read ``DIGEST_ROWS`` rows at a time; None for None."""
    if plane is None:
        return None
    digest = hashlib.sha256(f"{plane.dtype.str}{plane.shape}".encode())
    for top in range(0, plane.shape[0], DIGEST_ROWS):
        digest.update(np.ascontiguousarray(plane[top : top + DIGEST_ROWS]))
    return "sha256:" + digest.hexdigest()


def cast_digests(occluder: Occluder | None, slabs: Slabs | None) -> JsonObject:
    """What casts on the surface in a bake, digested: the crown tops and cover, the slabs."""
    top, cover = occluder if isinstance(occluder, tuple) else (occluder, None)
    return {
        "occluder": plane_digest(top),
        "occluder_cover": plane_digest(cover),
        "slabs": None if slabs is None else [plane_digest(plane) for plane in slabs],
    }


def light_key(
    surface: Surface, casts: JsonObject, occluder_layers: Sequence[str] = ()
) -> JsonObject:
    """What a bake reads: the drawn surface, ``cast_digests``, the size, the light model and
    ``LIGHT_VERSION``. Two bakes under one ``digest`` write the same pyramid and terms."""
    key: JsonObject = {
        "light_version": LIGHT_VERSION,
        "model": _json_digest(light_axis()),
        "size_px": surface.size,
        "occluder_layers": list(occluder_layers) if casts["occluder"] is not None else [],
        "surface": surface.digest(),
        **casts,
    }
    return {**key, "digest": _json_digest(key)}


def _json_digest(value: JsonObject) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def default_terms(surface: Surface) -> U8Grid:
    """The ``TERMS`` at the default sun, bytes on the sheet's grid."""
    return work_array(surface.terms.parent, surface.terms.stem, np.uint8, "r")


def discard(surface: Surface) -> None:
    for name in _FILES:
        try:
            surface.path(name).unlink()
        except OSError:
            pass
