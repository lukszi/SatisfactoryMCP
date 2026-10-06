"""The lighting stage: the surface a render drew, baked into the lighting pyramid.

Per tile, ``{z}/{x}_{y}.nrm.webp`` (lossless RGBA: east and south normal, sky view, land
weight) and ``{z}/{x}_{y}.hz.webp`` (an 8 x 8 grey atlas at half resolution: 32 faded ground
horizons, then 32 crown horizons). Every style reads the ground's; only a style that draws
the crowns adds theirs. docs/spatial-and-map.md section 29.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time
import warnings
from collections import deque
from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.horizon import (
    HORIZON_DIRS,
    SKY_RADIUS_M,
    crown_horizon,
    crown_surface,
    encode_horizon,
    horizon_reach_px,
    march_horizon,
    normals,
    sky_view,
)
from mapgen.lighting.model import DIRECT_SCALE, HZ_CELLS, direct_term, light_axis, sun_cells
from mapgen.lighting.sun import DEFAULT_SUN
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_PX,
    RETIRED_SUFFIX,
    STAGING_SUFFIX,
    TILES_DIR_NAME,
    swap_into_place,
    tile_relpath,
)
from satisfactory_mcp.core.mapprogress import encode_stage

__all__ = [
    "HZ_SUFFIX",
    "LIGHT_DIR_NAME",
    "LIGHT_WORKER_BYTES",
    "LIGHT_WORKER_CAP",
    "NRM_SUFFIX",
    "Surface",
    "bake_light",
    "decode_linear",
    "default_terms",
    "discard",
    "free_ram_bytes",
    "hz_atlas",
    "light_workers",
    "occluder_planes",
]

LIGHT_DIR_NAME = "light"
NRM_SUFFIX = ".nrm.webp"
HZ_SUFFIX = ".hz.webp"
HZ_QUALITY = 75
ATLAS_COLS = 8

#: Native tiles per block edge; a block is computed with its own halo.
BLOCK_TILES = 16

#: Stored horizons for the coarser levels, before encoding: degrees times this, as a byte.
HZ_LINEAR_SCALE = 2.8

#: Strips of a coarser level whose tiles may still be encoding while the next is computed,
#: and the tiles a task of the pool encodes.
LEVEL_AHEAD = 4
LEVEL_TASK_TILES = 4

#: Light processes at most, and the free memory each one needs: its measured commit peak on a
#: full-size block.
LIGHT_WORKER_CAP = 16
LIGHT_WORKER_BYTES = 2_500_000_000

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


class Surface:
    """The drawn surface, band by band: heights in metres and the land weight as a byte."""

    def __init__(self, directory: Path, size: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.size = size
        self.spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
        self.z = np.lib.format.open_memmap(directory / "z.npy", "w+", np.float32, (size, size))
        self.land = np.lib.format.open_memmap(directory / "land.npy", "w+", np.uint8, (size, size))

    def put(
        self, row: int, z_m: np.ndarray, land: np.ndarray, columns: slice = slice(None)
    ) -> None:
        self.z[row : row + z_m.shape[0], columns] = z_m
        self.land[row : row + z_m.shape[0], columns] = np.round(np.clip(land, 0, 1) * 255)

    def flush(self) -> None:
        self.z.flush()
        self.land.flush()

    def path(self, name: str) -> Path:
        return self.directory / f"{name}.npy"

    def close(self) -> None:
        self.flush()
        del self.z, self.land


def hz_atlas(hz_u8: np.ndarray) -> np.ndarray:
    """``(dirs, h, w)`` bytes as one grey image of ``ATLAS_COLS`` cells a row."""
    dirs, h, w = hz_u8.shape
    rows = -(-dirs // ATLAS_COLS)
    out = np.zeros((rows * h, ATLAS_COLS * w), np.uint8)
    for k in range(dirs):
        r, c = divmod(k, ATLAS_COLS)
        out[r * h : (r + 1) * h, c * w : (c + 1) * w] = hz_u8[k]
    return out


def _down(a: np.ndarray, f: int = 2, how=np.mean) -> np.ndarray:
    h, w = a.shape[0] // f, a.shape[1] // f
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN cell is empty, not an error
        return how(a[: h * f, : w * f].reshape(h, f, w, f, *a.shape[2:]), axis=(1, 3))


def _window(arr, r0, r1, c0, c1, fill=None):
    """``arr[r0:r1, c0:c1]`` with whatever falls off the sheet edge-padded, or ``fill``."""
    n = arr.shape[0]
    a0, a1, b0, b1 = max(r0, 0), min(r1, n), max(c0, 0), min(c1, arr.shape[1])
    part = np.asarray(arr[a0:a1, b0:b1], np.float32)
    pads = ((a0 - r0, r1 - a1), (b0 - c0, c1 - b1))
    if fill is None:
        return np.pad(part, pads, mode="edge")
    return np.pad(part, pads, mode="constant", constant_values=fill)


def _encode(jobs: Iterable[tuple[str, np.ndarray, np.ndarray]]) -> int:
    from PIL import Image

    written = 0
    for path, nrm, atlas in jobs:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        Image.fromarray(nrm, "RGBA").save(buf, "WEBP", lossless=True, exact=True, method=4)
        Path(path + NRM_SUFFIX).write_bytes(buf.getvalue())
        buf = io.BytesIO()
        Image.fromarray(atlas, "L").convert("RGB").save(buf, "WEBP", quality=HZ_QUALITY, method=4)
        Path(path + HZ_SUFFIX).write_bytes(buf.getvalue())
        written += len(buf.getvalue())
    return written


def _tile_jobs(dest: Path, z: int, tx0: int, ty0: int, nrm: np.ndarray, hz_u8: np.ndarray):
    """Each tile's stem, normal tile and atlas, made as they are asked for."""
    t, h = PYRAMID_TILE_PX, PYRAMID_TILE_PX // 2
    for j in range(nrm.shape[0] // t):
        for i in range(nrm.shape[1] // t):
            stem = str(dest / tile_relpath(z, tx0 + i, ty0 + j))[: -len(".png")]
            cell = np.ascontiguousarray(nrm[j * t : (j + 1) * t, i * t : (i + 1) * t])
            atlas = hz_atlas(hz_u8[:, j * h : (j + 1) * h, i * h : (i + 1) * h])
            yield stem, cell, atlas


def _open(work: Path, name: str, mode: str = "r+"):
    path = work / f"{name}.npy"
    return np.load(path, mmap_mode=mode) if path.is_file() else None


def _crown_surfaces(work: Path, window, zw, ground_w):
    """The crowns stood on the surface and on the solid ground, at half resolution, or None."""
    occ = _open(work, "occluder", "r")
    if occ is None:
        return None
    top = _window(occ, *window, np.nan)
    cover = _open(work, "occluder_cover", "r")
    share = None if cover is None else _window(cover, *window, 0.0) / np.float32(255.0)
    on_z = _down(crown_surface(zw, top, share))
    return on_z, None if ground_w is None else _down(crown_surface(ground_w, top, share))


def _half_surfaces(work: Path, window, march: bool):
    """The block's heights at half resolution, and what casts on them when it marches."""
    zw = _window(_open(work, "z", "r"), *window)
    zh = _down(zw)
    if not march:
        return zh, None, None
    ground, lo, hi = (_open(work, f"slab_{k}", "r") for k in ("ground", "lo", "hi"))
    slabs = ground_w = None
    if ground is not None and lo is not None and hi is not None:
        ground_w = _window(ground, *window)
        slabs = (_down(ground_w),
                 _down(_window(lo, *window, np.nan), how=np.nanmin),
                 _down(_window(hi, *window, np.nan), how=np.nanmax))  # fmt: skip
    return zh, _crown_surfaces(work, window, zw, ground_w), slabs


def _horizon_cells(zh, halo, sp, crowns, slabs):
    """Each direction's ground cell, then its crown cell where the crowns stand above it."""
    for k in range(HORIZON_DIRS):
        az = k * 360.0 / HORIZON_DIRS
        ground = march_horizon(zh, halo, az, sp, None, slabs)
        yield k, ground
        if crowns is not None:
            over = crown_horizon(crowns[0], halo, az, sp, crowns[1])
            yield HORIZON_DIRS + k, np.where(over > ground, over, np.float32(0.0))


def _bake_horizons(zh, halo, sp, crowns, slabs, m: int, march: bool):
    """The atlas bytes, the coarser levels' source and the default sun's planes, a cell at a time.

    No ``(cells, m, m)`` float stack: each cell is encoded as it is marched.
    """
    hz_u8 = np.zeros((HZ_CELLS, m, m), np.uint8)
    hq = np.zeros((m // 2, m // 2, HZ_CELLS), np.uint8)
    sun = [np.zeros((m, m), np.float32)] * HZ_CELLS
    keep = sun_cells(DEFAULT_SUN[0])
    for k, deg in _horizon_cells(zh, halo, sp, crowns, slabs) if march else ():
        hz_u8[k] = encode_horizon(deg)
        hq[..., k] = np.round(np.clip(_down(deg), 0, 90) * HZ_LINEAR_SCALE)
        if k in keep:
            sun[k] = deg
    return hz_u8, hq, sun


def _bake_block(job: dict) -> dict:
    """One block of native tiles: horizons, sky view, normals, tiles, default-sun terms."""
    started = time.time()
    work, sp, (r0, c0, n) = Path(job["work"]), job["spacing_m"], job["block"]
    halo, m = job["halo_h"], n // 2
    window = (r0 - 2 * halo, r0 + n + 2 * halo, c0 - 2 * halo, c0 + n + 2 * halo)
    land_core = np.asarray(_open(work, "land", "r")[r0 : r0 + n, c0 : c0 + n])
    march = not (job["skip_water"] and not land_core.any())
    zh, crowns, slabs = _half_surfaces(work, window, march)
    hz_u8, hq, sun = _bake_horizons(zh, halo, 2 * sp, crowns, slabs, m, march)
    del crowns, slabs
    sky = job["sky_halo"]
    svf_h = sky_view(zh[halo - sky : zh.shape[0] - halo + sky, halo - sky : zh.shape[1] - halo + sky],
                     sky, 2 * sp)  # fmt: skip
    nx, ny = normals(_window(_open(work, "z", "r"), r0 - 1, r0 + n + 1, c0 - 1, c0 + n + 1), sp)
    svf = np.clip(ndimage.zoom(svf_h, 2, order=1, mode="nearest", grid_mode=True), 0, 1)
    q = lambda v: np.round((v * 0.5 + 0.5) * 255).astype(np.uint8)
    nrm = np.stack([q(nx), q(ny), np.round(svf * 255).astype(np.uint8), land_core], -1)
    del nx, ny, svf
    t = PYRAMID_TILE_PX
    hz_bytes = _encode(_tile_jobs(Path(job["dest"]), job["z"], c0 // t, r0 // t, nrm, hz_u8))
    del hz_u8
    terms = _open(work, "terms")
    terms[r0 : r0 + n, c0 : c0 + n, 0] = nrm[..., 2]
    for k, crowns in ((1, False), (2, True)):
        direct = direct_term(nrm, sun, DEFAULT_SUN, crowns=crowns)
        terms[r0 : r0 + n, c0 : c0 + n, k] = np.clip(np.round(direct * DIRECT_SCALE), 0, 255)
    h0, w0 = r0 // 2, c0 // 2
    _open(work, "zh")[h0 : h0 + m, w0 : w0 + m] = zh[halo:-halo, halo:-halo]
    _open(work, "landh")[h0 : h0 + m, w0 : w0 + m] = np.round(_down(land_core.astype(np.float32)))
    _open(work, "svfh")[h0 : h0 + m, w0 : w0 + m] = np.round(np.clip(svf_h, 0, 1) * 255)
    _open(work, "hzq")[h0 // 2 : (h0 + m) // 2, w0 // 2 : (w0 + m) // 2] = hq
    tiles = (nrm.shape[0] // t) * (nrm.shape[1] // t)
    return {"tiles": tiles, "hz_bytes": hz_bytes, "seconds": time.time() - started}


def _level_strips(work: Path, dest: Path, level: int, sp: float, pool, last: bool) -> int:
    """One coarser level from the sources below it, a strip of tile rows at a time.

    The pool encodes a strip's tiles while this process computes the strips after it.
    """
    z, land, svf, hzq = (_open(work, k, "r") for k in ("zh", "landh", "svfh", "hzq"))
    n = z.shape[0]
    q4 = max(n // 4, 1)
    nxt = None if last else {
        name: np.lib.format.open_memmap(work / f"{name}.next.npy", "w+", dtype, shape)
        for name, dtype, shape in (
            ("zh", np.float32, (n // 2, n // 2)), ("landh", np.uint8, (n // 2, n // 2)),
            ("svfh", np.uint8, (n // 2, n // 2)), ("hzq", np.uint8, (q4, q4, HZ_CELLS)),
        )
    }  # fmt: skip
    t = PYRAMID_TILE_PX
    rows = t
    count = 0
    pending: deque[list[Future]] = deque()
    for r0 in range(0, n, rows):
        zz = _window(z, r0 - 1, r0 + rows + 1, -1, n + 1)
        nx, ny = normals(zz, sp)
        q = lambda v: np.round((v * 0.5 + 0.5) * 255).astype(np.uint8)
        nrm = np.stack([q(nx), q(ny), svf[r0 : r0 + rows], land[r0 : r0 + rows]], -1)
        hz_u8 = np.moveaxis(_LINEAR_TO_HZ[hzq[r0 // 2 : (r0 + rows) // 2]], -1, 0)
        jobs = list(_tile_jobs(dest, level, 0, r0 // t, nrm, hz_u8))
        count += len(jobs)
        tasks = [jobs[i : i + LEVEL_TASK_TILES] for i in range(0, len(jobs), LEVEL_TASK_TILES)]
        pending.append([pool.submit(_encode, task) for task in tasks])
        del jobs, tasks
        if nxt is not None:
            a, b = r0 // 2, (r0 + rows) // 2
            nxt["zh"][a:b] = _down(np.asarray(z[r0 : r0 + rows]))
            nxt["landh"][a:b] = np.round(_down(land[r0 : r0 + rows].astype(np.float32)))
            nxt["svfh"][a:b] = np.round(_down(svf[r0 : r0 + rows].astype(np.float32)))
            hz_rows = hzq[r0 // 2 : (r0 + rows) // 2].astype(np.float32)
            nxt["hzq"][r0 // 4 : (r0 + rows) // 4] = np.round(_down(hz_rows))
        while len(pending) > LEVEL_AHEAD:
            _wait(pending.popleft())
    while pending:
        _wait(pending.popleft())
    del z, land, svf, hzq
    for name in list(nxt or ()):
        nxt.pop(name).flush()  # Windows replaces a file only once its last map is closed
        (work / f"{name}.next.npy").replace(work / f"{name}.npy")
    return count


def decode_linear(q: np.ndarray) -> np.ndarray:
    return np.asarray(q, np.float32) / np.float32(HZ_LINEAR_SCALE)


#: ``encode_horizon(decode_linear(q))`` for every byte: elementwise, so a lookup is exact.
_LINEAR_TO_HZ = encode_horizon(decode_linear(np.arange(256, dtype=np.uint8)))


def _wait(futures: list[Future]) -> None:
    for future in futures:
        future.result()


def _alloc(work: Path, size: int) -> None:
    half, quarter = size // 2, max(size // 4, 1)
    for name, dtype, shape in (
        ("terms", np.uint8, (size, size, 3)),
        ("zh", np.float32, (half, half)),
        ("landh", np.uint8, (half, half)),
        ("svfh", np.uint8, (half, half)),
        ("hzq", np.uint8, (quarter, quarter, HZ_CELLS)),
    ):
        np.lib.format.open_memmap(work / f"{name}.npy", "w+", dtype, shape).flush()


def occluder_planes(work: Path, size: int) -> tuple[np.ndarray, np.ndarray]:
    """The crown tops and cover as ``w+`` memory maps under the names the bake reads."""
    work.mkdir(parents=True, exist_ok=True)
    shape = (size, size)
    top = np.lib.format.open_memmap(work / "occluder.npy", "w+", np.float32, shape)
    return top, np.lib.format.open_memmap(work / "occluder_cover.npy", "w+", np.uint8, shape)


def _in_place(raster, path: Path, dtype) -> bool:
    name = getattr(raster, "filename", None)
    return name is not None and raster.dtype == dtype and Path(name).resolve() == path.resolve()


def _extra(work: Path, name: str, raster, dtype=np.float32) -> None:
    path = work / f"{name}.npy"
    if raster is not None and not _in_place(raster, path, dtype):
        np.save(path, np.asarray(raster, dtype))


def free_ram_bytes() -> int | None:
    """Memory free now, in bytes, or None where the platform does not say.

    On Windows the lesser of the free physical memory and the commit still available.
    """
    if sys.platform == "win32":
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (k, ctypes.c_ulonglong) for k in ("total", "free", "pt", "pf", "vt", "vf", "x")
            ]

        status = _Status(length=ctypes.sizeof(_Status))
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(min(status.free, status.pf)) if ok else None
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None


def light_workers(requested: int | None = None) -> int:
    """``requested``, else one a core up to ``LIGHT_WORKER_CAP`` that the free RAM holds."""
    if requested:
        return max(1, requested)
    free = free_ram_bytes()
    by_ram = LIGHT_WORKER_CAP if free is None else int(free // LIGHT_WORKER_BYTES)
    return max(1, min(os.cpu_count() or 1, LIGHT_WORKER_CAP, by_ram))


def bake_light(surface: Surface, out_dir: Path, workers: int | None, occluder=None, slabs=None,
               progress: bool = True, occluder_layers=()) -> dict:  # fmt: skip
    """Write ``out_dir/light/`` from a captured surface; returns its sidecar's ``_meta``.

    ``workers`` None is ``light_workers()``, counted when the bake starts. ``occluder`` is an
    optional crown-top raster on the sheet's grid, metres, NaN where empty, or ``(top,
    cover)`` with the covered share as a byte; it casts into the crown horizons that
    ``occluder_layers`` read. ``slabs`` is an optional ``(ground, min_z, max_z)`` for geometry
    with open space beneath it (arches): the surface without it, and its underside and top.
    Both only cast. An occluder made by ``occluder_planes`` in the surface's directory is
    read where it is, not copied.
    """
    started = time.time()
    workers = light_workers(workers)
    surface.flush()
    size, work = surface.size, surface.directory
    sp = surface.spacing_m
    _alloc(work, size)
    crown_top, crown_cover = occluder if isinstance(occluder, tuple) else (occluder, None)
    _extra(work, "occluder", crown_top)
    _extra(work, "occluder_cover", crown_cover, np.uint8)
    for name, raster in zip(("ground", "lo", "hi"), slabs or (), strict=False):
        _extra(work, f"slab_{name}", raster)
    top = int(np.log2(size // PYRAMID_TILE_PX))
    root = out_dir / LIGHT_DIR_NAME
    staging = root / (TILES_DIR_NAME + STAGING_SUFFIX)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    block = min(size, BLOCK_TILES * PYRAMID_TILE_PX)
    halo_h = horizon_reach_px(2 * sp)
    common = {"work": str(work), "dest": str(staging), "z": top, "spacing_m": sp, "halo_h": halo_h,
              "sky_halo": int(np.ceil(SKY_RADIUS_M / (2 * sp))) + 2, "skip_water": True}  # fmt: skip
    jobs = [
        {**common, "block": (r, c, block)}
        for r in range(0, size, block)
        for c in range(0, size, block)
    ]
    tiles = hz_bytes = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, done in enumerate(pool.map(_bake_block, jobs), 1):
            tiles += done["tiles"]
            hz_bytes += done["hz_bytes"]
            if progress:
                print(encode_stage("light", k / len(jobs)), flush=True)
        native_s = time.time() - started
        for level in range(top - 1, -1, -1):
            tiles += _level_strips(work, staging, level, sp * 2 ** (top - level), pool, level == 0)
    stats = {
        "tile_px": PYRAMID_TILE_PX,
        "hz_tile_px": PYRAMID_TILE_PX // 2,
        "max_z": top,
        "count": tiles,
        "bytes": sum(p.stat().st_size for p in staging.rglob("*.webp")),
        "hz_bytes": hz_bytes,
    }
    stats["installed_by"] = swap_into_place(
        staging, root / TILES_DIR_NAME, root / (TILES_DIR_NAME + RETIRED_SUFFIX)
    )
    meta = {
        "generator": "tools/gen_map_renders.py",
        "kind": LIGHT_DIR_NAME,
        "light": {
            **light_axis(),
            "occluder_layers": list(occluder_layers) if occluder is not None else [],
        },
        "tiles": stats,
        "render": {
            "size_px": size,
            "metres_per_pixel": round(sp, 4),
            "horizon_res_m": round(2 * sp, 4),
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


def default_terms(surface: Surface):
    """``(svf, direct, direct with crowns)`` at the default sun, bytes on the sheet's grid."""
    return np.load(surface.path("terms"), mmap_mode="r")


def discard(surface: Surface) -> None:
    for name in _FILES:
        try:
            surface.path(name).unlink()
        except OSError:
            pass
