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
import shutil
import struct
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TypeAlias

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.jit import gpu_on
from mapgen.lighting.horizon import encode_horizon, normals, sky_view
from mapgen.lighting.light_tiles import (
    HZ_LINEAR_SCALE,
    downsample,
    encode_tiles,
    level_layout,
    normal_byte,
    padded_window,
    tile_jobs,
    work_array,
)
from mapgen.lighting.model import DIRECT_SCALE, HZ_CELLS, direct_term, light_axis, sun_cells
from mapgen.lighting.slabs import SLAB_DIR_NAME, SlabPlanes, SlabStore
from mapgen.lighting.span_bake import (
    BlockSpans,
    block_spans,
    default_shade,
    full_resolution,
    horizon_cells,
    plain_bands,
    shade_cells,
)
from mapgen.lighting.spans import Bands, sky_view_spans, span_surface
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

#: Light processes at most, and the free memory each one needs: its measured peak on a
#: full-size block with crowns or arches, started with one BLAS thread.
LIGHT_WORKER_CAP = 16
LIGHT_WORKER_BYTES = 2_000_000_000
#: What a light process adds with the CUDA kernels: CuPy and its context, 0.65 GB measured.
LIGHT_GPU_BYTES = 700_000_000

#: The work files a bake leaves in the surface's directory, beside its ``SlabStore``.
_FILES = (
    "z",
    "land",
    "occluder",
    "occluder_cover",
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
    """The drawn surface, band by band: heights in metres and the land weight as a byte, and
    the floating geometry over it in a ``SlabStore``.

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
        self.slabs = SlabStore(directory / SLAB_DIR_NAME)
        self.terms = self.path("terms")
        self._puts: dict[Place, bytes] = {}
        self._lock = threading.Lock()

    def put(
        self,
        row: int,
        z_m: NDArray[np.floating],
        land: NDArray[np.floating],
        columns: slice = slice(None),
        slabs: SlabPlanes | None = None,
    ) -> None:
        z = np.ascontiguousarray(z_m, np.float32)
        dry = np.ascontiguousarray(np.round(np.clip(land, 0, 1) * 255), np.uint8)
        self.z[row : row + z.shape[0], columns] = z
        self.land[row : row + z.shape[0], columns] = dry
        digest = hashlib.sha256(z)
        digest.update(dry)
        c0, c1, _step = columns.indices(self.size)
        if slabs is not None:
            digest.update(self.slabs.put(row, c0, slabs))
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


def _half_surfaces(work: Path, window: _Window, march: bool) -> tuple[F32Grid, BlockSpans]:
    """The block's heights at half resolution, and what casts on them when it marches."""
    z_window = padded_window(work_array(work, "z", np.float32, "r"), *window)
    z_half = downsample(z_window)
    if not march:
        return z_half, BlockSpans(None, None)
    return z_half, block_spans(work, window, z_window, z_half, SlabStore(work / SLAB_DIR_NAME))


class _Horizons(NamedTuple):
    """A block's horizons as baked: the atlas bytes, the coarser levels' source, the default
    sun's cells in degrees, and the bands those cells were marched with."""

    atlas: U8Grid
    quarter: U8Grid
    sun: list[F32Grid]
    bands: dict[int, Bands]


def _bake_horizons(
    z_half: F32Grid, halo: int, spacing_m: float, spans: BlockSpans, half_px: int, march: bool
) -> _Horizons:
    """The atlas bytes, the coarser levels' source and the default sun's planes, a cell at a time.

    No ``(cells, half_px, half_px)`` float stack: each cell is encoded as it is marched.
    """
    hz_u8 = np.zeros((HZ_CELLS, half_px, half_px), np.uint8)
    horizon_quarter = np.zeros((half_px // 2, half_px // 2, HZ_CELLS), np.uint8)
    sun = [np.zeros((half_px, half_px), np.float32)] * HZ_CELLS
    bands: dict[int, Bands] = {}
    keep, shaded = sun_cells(DEFAULT_SUN[0]), shade_cells(DEFAULT_SUN[0])
    cells = horizon_cells(z_half, halo, spacing_m, spans) if march else iter(())
    for k, deg, marched in cells:
        hz_u8[k] = encode_horizon(deg)
        horizon_quarter[..., k] = np.round(np.clip(downsample(deg), 0, 90) * HZ_LINEAR_SCALE)
        if k in keep:
            sun[k] = deg
        if k in shaded and marched is not None:
            bands[k] = marched
    if bands:
        bands.update({k: plain_bands(sun[k]) for k in shaded if k not in bands and march})
    return _Horizons(hz_u8, horizon_quarter, sun, bands)


def _sky_view(z_half: F32Grid, halo: int, sky: int, spacing_m: float, spans: BlockSpans) -> F32Grid:
    """The block's sky view at half resolution, with the arches' and overhangs' spans."""
    rows, cols = (slice(halo - sky, side - halo + sky) for side in z_half.shape[:2])
    if spans.ground is None:
        return sky_view(z_half[rows, cols], sky, spacing_m)
    g = spans.ground
    cut = span_surface(*(plane[rows, cols] for plane in (g.z, g.solid, g.lo, g.hi)))
    return sky_view_spans(cut, sky, spacing_m)


def _normals(work: Path, ring: _Window, spacing_m: float) -> tuple[F32Grid, F32Grid]:
    """The block's normals; on the ground beside and beneath a span, the solid surface's, so
    the span's edge draws no rim on it. A pixel at or above a neighbouring span's underside
    is the span's own, and keeps the drawn surface's."""
    z_ring = padded_window(work_array(work, "z", np.float32, "r"), *ring)
    nx, ny = normals(z_ring, spacing_m)
    found = SlabStore(work / SLAB_DIR_NAME).full(ring, z_ring)
    if found is None:
        return nx, ny
    solid, lo = found
    nearest_under = ndimage.minimum_filter(np.where(np.isfinite(lo), lo, np.inf), size=3)
    beneath = (~np.isfinite(lo) & (z_ring < nearest_under))[1:-1, 1:-1]
    if not beneath.any():
        return nx, ny
    sx, sy = normals(solid, spacing_m)
    return np.where(beneath, sx, nx), np.where(beneath, sy, ny)


def _default_terms(nrm: U8Grid, horizons: _Horizons, crowned: bool) -> F32Grid:
    """The direct term at the default sun; beside a span, shaded per cell (``default_shade``)."""
    filtered = None
    found = default_shade(horizons.bands, DEFAULT_SUN, crowned) if horizons.bands else None
    if found is not None:
        shape = (nrm.shape[0], nrm.shape[1])
        use, shade = found
        filtered = (full_resolution(use.astype(np.float32), shape) > 0,
                    full_resolution(shade, shape))  # fmt: skip
    return direct_term(nrm, horizons.sun, DEFAULT_SUN, crowns=crowned, filtered=filtered)


def bake_block(job: BlockJob) -> BlockDone:
    """One block of native tiles: horizons, sky view, normals, tiles, default-sun terms."""
    started = time.time()
    work, spacing_m, (r0, c0, block_px) = Path(job.work), job.spacing_m, job.block
    halo, half_px, half_m = job.halo_h, block_px // 2, 2 * spacing_m
    window = (r0 - 2 * halo, r0 + block_px + 2 * halo, c0 - 2 * halo, c0 + block_px + 2 * halo)
    land = work_array(work, "land", np.uint8, "r")
    land_core = np.asarray(land[r0 : r0 + block_px, c0 : c0 + block_px])
    march = not (job.skip_water and not land_core.any())
    z_half, spans = _half_surfaces(work, window, march)
    horizons = _bake_horizons(z_half, halo, half_m, spans, half_px, march)
    sky_view_half = _sky_view(z_half, halo, job.sky_halo, half_m, spans)
    del spans
    nx, ny = _normals(work, (r0 - 1, r0 + block_px + 1, c0 - 1, c0 + block_px + 1), spacing_m)
    svf = np.clip(ndimage.zoom(sky_view_half, 2, order=1, mode="nearest", grid_mode=True), 0, 1)
    nrm = np.stack(
        [normal_byte(nx), normal_byte(ny), np.round(svf * 255).astype(np.uint8), land_core], -1
    )
    del nx, ny, svf
    t = PYRAMID_TILE_PX
    hz_bytes = encode_tiles(tile_jobs(Path(job.dest), job.z, c0 // t, r0 // t, nrm, horizons.atlas))
    terms = work_array(work, "terms", np.uint8)
    terms[r0 : r0 + block_px, c0 : c0 + block_px, 0] = nrm[..., 2]
    for k, crowned in ((1, False), (2, True)):
        direct = _default_terms(nrm, horizons, crowned)
        terms[r0 : r0 + block_px, c0 : c0 + block_px, k] = np.clip(
            np.round(direct * DIRECT_SCALE), 0, 255
        )
    horizon_quarter = horizons.quarter
    del horizons
    h0, w0 = r0 // 2, c0 // 2
    core = z_half[halo:-halo, halo:-halo]
    half = (slice(h0, h0 + half_px), slice(w0, w0 + half_px))
    work_array(work, "zh", np.float32)[half] = core
    work_array(work, "landh", np.uint8)[half] = np.round(downsample(land_core.astype(np.float32)))
    work_array(work, "svfh", np.uint8)[half] = np.round(np.clip(sky_view_half, 0, 1) * 255)
    quarter = (slice(h0 // 2, (h0 + half_px) // 2), slice(w0 // 2, (w0 + half_px) // 2))
    work_array(work, "hzq", np.uint8)[quarter] = horizon_quarter
    tiles = (nrm.shape[0] // t) * (nrm.shape[1] // t)
    return BlockDone(tiles, hz_bytes, time.time() - started)


def allocate_work_arrays(work: Path, size: int) -> None:
    """The bake's work files in ``work``: the terms and the coarser levels' sources."""
    layout = (("terms", np.uint8, (size, size, 3)), *level_layout(size // 2))
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


def cast_digests(occluder: Occluder | None) -> JsonObject:
    """What casts on the surface in a bake besides it, digested: the crown tops and cover.
    The slabs are the surface's own, digested with it."""
    top, cover = occluder if isinstance(occluder, tuple) else (occluder, None)
    return {"occluder": plane_digest(top), "occluder_cover": plane_digest(cover)}


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
    """``(svf, direct, direct with crowns)`` at the default sun, bytes on the sheet's grid."""
    return work_array(surface.terms.parent, surface.terms.stem, np.uint8, "r")


def discard(surface: Surface) -> None:
    for name in _FILES:
        try:
            surface.path(name).unlink()
        except OSError:
            pass
    shutil.rmtree(surface.slabs.directory, ignore_errors=True)
