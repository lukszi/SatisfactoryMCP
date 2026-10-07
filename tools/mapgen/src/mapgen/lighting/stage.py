"""The lighting stage: the surface a render drew, baked into the lighting pyramid.

Native tiles are baked in blocks with their own halo, on a pool of light processes; the
coarser levels and the tile format are ``light_tiles``. A bake is keyed on what it reads
(``light_key``), so a run that draws the same surface reuses it (``render/kept_light.py``).
docs/spatial-and-map.md section 29.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple, TypeAlias

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.jit import gpu_on
from mapgen.lighting.horizon import (
    HORIZON_DIRS,
    SKY_RADIUS_M,
    Slabs,
    crown_horizon,
    crown_surface,
    encode_horizon,
    horizon_reach_px,
    march_horizon,
    normals,
    sky_view,
)
from mapgen.lighting.light_tiles import (
    HZ_LINEAR_SCALE,
    downsample,
    encode_tiles,
    level_layout,
    level_strips,
    normal_byte,
    optional_array,
    padded_window,
    tile_jobs,
    work_array,
)
from mapgen.lighting.model import DIRECT_SCALE, HZ_CELLS, direct_term, light_axis, sun_cells
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.pools import free_ram_bytes, one_blas_thread
from satisfactory_mcp.core.arrays import F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_PX,
    RETIRED_SUFFIX,
    STAGING_SUFFIX,
    TILES_DIR_NAME,
    swap_into_place,
)
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.core.mapprogress import encode_stage

__all__ = [
    "LIGHT_DIR_NAME",
    "LIGHT_GPU_BYTES",
    "LIGHT_VERSION",
    "LIGHT_WORKER_BYTES",
    "LIGHT_WORKER_CAP",
    "Occluder",
    "Surface",
    "bake_light",
    "cast_digests",
    "default_terms",
    "discard",
    "light_key",
    "light_workers",
    "occluder_planes",
    "plane_digest",
]

LIGHT_DIR_NAME = "light"

#: The bake's own version. Bump it when the bake writes other bytes from the same surface,
#: casters and model, so no run reuses a light the old bake wrote.
LIGHT_VERSION = 1

#: Rows of a plane hashed at a time.
DIGEST_ROWS = 1024

#: Native tiles per block edge; a block is computed with its own halo.
BLOCK_TILES = 16

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
class _BlockJob:
    """One block of native tiles for a light worker: where it reads, writes, and how far."""

    work: str
    dest: str
    z: int
    spacing_m: float
    halo_h: int
    sky_halo: int
    skip_water: bool
    block: tuple[int, int, int] = (0, 0, 0)


class _BlockDone(NamedTuple):
    tiles: int
    hz_bytes: int
    seconds: float


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
        self._puts: dict[tuple[int, int, int, int], bytes] = {}
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
            self._puts[(row, z.shape[0], c0, c1)] = digest.digest()

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
    on_z = downsample(crown_surface(z_window, top, share))
    if ground_window is None:
        return on_z, None
    return on_z, downsample(crown_surface(ground_window, top, share))


def _half_surfaces(
    work: Path, window: _Window, march: bool
) -> tuple[F32Grid, _CrownSurfaces | None, Slabs | None]:
    """The block's heights at half resolution, and what casts on them when it marches."""
    z_window = padded_window(work_array(work, "z", np.float32, "r"), *window)
    z_half = downsample(z_window)
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


def _horizon_cells(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    crowns: _CrownSurfaces | None,
    slabs: Slabs | None,
) -> Iterator[tuple[int, F32Grid]]:
    """Each direction's ground cell, then its crown cell where the crowns stand above it."""
    for k in range(HORIZON_DIRS):
        az = k * 360.0 / HORIZON_DIRS
        ground = march_horizon(z_half, halo, az, spacing_m, None, slabs)
        yield k, ground
        if crowns is not None:
            over = crown_horizon(crowns[0], halo, az, spacing_m, crowns[1])
            yield HORIZON_DIRS + k, np.where(over > ground, over, np.float32(0.0))


def _bake_horizons(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    crowns: _CrownSurfaces | None,
    slabs: Slabs | None,
    half_px: int,
    march: bool,
) -> tuple[U8Grid, U8Grid, list[F32Grid]]:
    """The atlas bytes, the coarser levels' source and the default sun's planes, a cell at a time.

    No ``(cells, half_px, half_px)`` float stack: each cell is encoded as it is marched.
    """
    hz_u8 = np.zeros((HZ_CELLS, half_px, half_px), np.uint8)
    horizon_quarter = np.zeros((half_px // 2, half_px // 2, HZ_CELLS), np.uint8)
    sun = [np.zeros((half_px, half_px), np.float32)] * HZ_CELLS
    keep = sun_cells(DEFAULT_SUN[0])
    cells = _horizon_cells(z_half, halo, spacing_m, crowns, slabs) if march else iter(())
    for k, deg in cells:
        hz_u8[k] = encode_horizon(deg)
        horizon_quarter[..., k] = np.round(np.clip(downsample(deg), 0, 90) * HZ_LINEAR_SCALE)
        if k in keep:
            sun[k] = deg
    return hz_u8, horizon_quarter, sun


def _bake_block(job: _BlockJob) -> _BlockDone:
    """One block of native tiles: horizons, sky view, normals, tiles, default-sun terms."""
    started = time.time()
    work, spacing_m, (r0, c0, block_px) = Path(job.work), job.spacing_m, job.block
    halo, half_px, half_m = job.halo_h, block_px // 2, 2 * spacing_m
    window = (r0 - 2 * halo, r0 + block_px + 2 * halo, c0 - 2 * halo, c0 + block_px + 2 * halo)
    land = work_array(work, "land", np.uint8, "r")
    land_core = np.asarray(land[r0 : r0 + block_px, c0 : c0 + block_px])
    march = not (job.skip_water and not land_core.any())
    z_half, crowns, slabs = _half_surfaces(work, window, march)
    hz_u8, horizon_quarter, sun = _bake_horizons(
        z_half, halo, half_m, crowns, slabs, half_px, march
    )
    del crowns, slabs
    sky = job.sky_halo
    rows, cols = (slice(halo - sky, side - halo + sky) for side in z_half.shape[:2])
    sky_view_half = sky_view(z_half[rows, cols], sky, half_m)
    ring = (r0 - 1, r0 + block_px + 1, c0 - 1, c0 + block_px + 1)
    z_ring = padded_window(work_array(work, "z", np.float32, "r"), *ring)
    nx, ny = normals(z_ring, spacing_m)
    svf = np.clip(ndimage.zoom(sky_view_half, 2, order=1, mode="nearest", grid_mode=True), 0, 1)
    nrm = np.stack(
        [normal_byte(nx), normal_byte(ny), np.round(svf * 255).astype(np.uint8), land_core], -1
    )
    del nx, ny, svf
    t = PYRAMID_TILE_PX
    hz_bytes = encode_tiles(tile_jobs(Path(job.dest), job.z, c0 // t, r0 // t, nrm, hz_u8))
    del hz_u8
    terms = work_array(work, "terms", np.uint8)
    terms[r0 : r0 + block_px, c0 : c0 + block_px, 0] = nrm[..., 2]
    for k, crowned in ((1, False), (2, True)):
        direct = direct_term(nrm, sun, DEFAULT_SUN, crowns=crowned)
        terms[r0 : r0 + block_px, c0 : c0 + block_px, k] = np.clip(
            np.round(direct * DIRECT_SCALE), 0, 255
        )
    h0, w0 = r0 // 2, c0 // 2
    core = z_half[halo:-halo, halo:-halo]
    half = (slice(h0, h0 + half_px), slice(w0, w0 + half_px))
    work_array(work, "zh", np.float32)[half] = core
    work_array(work, "landh", np.uint8)[half] = np.round(downsample(land_core.astype(np.float32)))
    work_array(work, "svfh", np.uint8)[half] = np.round(np.clip(sky_view_half, 0, 1) * 255)
    quarter = (slice(h0 // 2, (h0 + half_px) // 2), slice(w0 // 2, (w0 + half_px) // 2))
    work_array(work, "hzq", np.uint8)[quarter] = horizon_quarter
    tiles = (nrm.shape[0] // t) * (nrm.shape[1] // t)
    return _BlockDone(tiles, hz_bytes, time.time() - started)


def _allocate_work_arrays(work: Path, size: int) -> None:
    layout = (("terms", np.uint8, (size, size, 3)), *level_layout(size // 2))
    for name, dtype, shape in layout:
        np.lib.format.open_memmap(work / f"{name}.npy", "w+", dtype, shape).flush()


def occluder_planes(work: Path, size: int) -> tuple[F32Grid, U8Grid]:
    """The crown tops and cover as ``w+`` memory maps under the names the bake reads."""
    work.mkdir(parents=True, exist_ok=True)
    shape = (size, size)
    top = np.lib.format.open_memmap(work / "occluder.npy", "w+", np.float32, shape)
    return top, np.lib.format.open_memmap(work / "occluder_cover.npy", "w+", np.uint8, shape)


def _in_place(raster: NDArray[np.number], path: Path, dtype: type[np.number]) -> bool:
    name = getattr(raster, "filename", None)
    return name is not None and raster.dtype == dtype and Path(name).resolve() == path.resolve()


def _save_optional_array(
    work: Path, name: str, raster: NDArray[np.number] | None, dtype: type[np.number] = np.float32
) -> None:
    path = work / f"{name}.npy"
    if raster is not None and not _in_place(raster, path, dtype):
        np.save(path, np.asarray(raster, dtype))


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


def light_workers(requested: int | None = None) -> int:
    """``requested``, else one a core up to ``LIGHT_WORKER_CAP`` that the free RAM holds."""
    if requested:
        return max(1, requested)
    free = free_ram_bytes()
    each = LIGHT_WORKER_BYTES + (LIGHT_GPU_BYTES if gpu_on() else 0)
    by_ram = LIGHT_WORKER_CAP if free is None else int(free // each)
    return max(1, min(os.cpu_count() or 1, LIGHT_WORKER_CAP, by_ram))


def bake_light(
    surface: Surface,
    out_dir: Path,
    workers: int | None,
    occluder: Occluder | None = None,
    slabs: Slabs | None = None,
    progress: bool = True,
    occluder_layers: Sequence[str] = (),
    key: JsonObject | None = None,
) -> JsonObject:
    """Write ``out_dir/light/`` from a captured surface; returns its sidecar's ``_meta``.

    ``workers`` None is ``light_workers()``, counted when the bake starts. ``occluder`` is an
    optional crown-top raster on the sheet's grid, metres, NaN where empty, or ``(top,
    cover)`` with the covered share as a byte; it casts into the crown horizons that
    ``occluder_layers`` read. ``slabs`` is an optional ``(ground, min_z, max_z)`` for geometry
    with open space beneath it (arches): the surface without it, and its underside and top.
    Both only cast. An occluder made by ``occluder_planes`` in the surface's directory is
    read where it is, not copied. ``key`` is the bake's ``light_key``, made here when None.
    """
    started = time.time()
    workers = light_workers(workers)
    if key is None:
        key = light_key(surface, cast_digests(occluder, slabs), occluder_layers)
    surface.flush()
    size, work = surface.size, surface.directory
    spacing_m = surface.spacing_m
    _allocate_work_arrays(work, size)
    crown_top, crown_cover = occluder if isinstance(occluder, tuple) else (occluder, None)
    _save_optional_array(work, "occluder", crown_top)
    _save_optional_array(work, "occluder_cover", crown_cover, np.uint8)
    for name, raster in zip(("ground", "lo", "hi"), slabs or (), strict=False):
        _save_optional_array(work, f"slab_{name}", raster)
    top = int(np.log2(size // PYRAMID_TILE_PX))
    root = out_dir / LIGHT_DIR_NAME
    staging = root / (TILES_DIR_NAME + STAGING_SUFFIX)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    block = min(size, BLOCK_TILES * PYRAMID_TILE_PX)
    common = _BlockJob(
        work=str(work),
        dest=str(staging),
        z=top,
        spacing_m=spacing_m,
        halo_h=horizon_reach_px(2 * spacing_m),
        sky_halo=int(np.ceil(SKY_RADIUS_M / (2 * spacing_m))) + 2,
        skip_water=True,
    )
    jobs = [
        replace(common, block=(r, c, block))
        for r in range(0, size, block)
        for c in range(0, size, block)
    ]
    tiles = hz_bytes = 0
    with one_blas_thread(), ProcessPoolExecutor(max_workers=workers) as pool:
        for k, done in enumerate(pool.map(_bake_block, jobs), 1):
            tiles += done.tiles
            hz_bytes += done.hz_bytes
            if progress:
                print(encode_stage("light", k / len(jobs)), flush=True)
        native_s = time.time() - started
        for level in range(top - 1, -1, -1):
            tiles += level_strips(
                work, staging, level, spacing_m * 2 ** (top - level), pool, level == 0
            )
    written = sum(p.stat().st_size for p in staging.rglob("*.webp"))
    installed_by = swap_into_place(
        staging, root / TILES_DIR_NAME, root / (TILES_DIR_NAME + RETIRED_SUFFIX)
    )
    meta: JsonObject = {
        "generator": "tools/gen_map_renders.py",
        "kind": LIGHT_DIR_NAME,
        "light": {
            **light_axis(),
            "occluder_layers": list(occluder_layers) if occluder is not None else [],
        },
        "key": key,
        "tiles": {
            "tile_px": PYRAMID_TILE_PX,
            "hz_tile_px": PYRAMID_TILE_PX // 2,
            "max_z": top,
            "count": tiles,
            "bytes": written,
            "hz_bytes": hz_bytes,
            "installed_by": installed_by,
        },
        "render": {
            "size_px": size,
            "metres_per_pixel": round(spacing_m, 4),
            "horizon_res_m": round(2 * spacing_m, 4),
            "blocks": len(jobs),
            "workers": workers,
            "seconds_native": round(native_s, 1),
            "seconds": round(time.time() - started, 1),
            "occluder": occluder is not None,
            "slabs": slabs is not None,
        },
    }
    (root / "meta.json").write_text(json.dumps({"_meta": meta}, indent=1), encoding="utf-8")
    return meta


def default_terms(surface: Surface) -> U8Grid:
    """``(svf, direct, direct with crowns)`` at the default sun, bytes on the sheet's grid."""
    return work_array(surface.terms.parent, surface.terms.stem, np.uint8, "r")


def discard(surface: Surface) -> None:
    for name in _FILES:
        try:
            surface.path(name).unlink()
        except OSError:
            pass
