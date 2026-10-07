"""The bake of a captured surface into the lighting pyramid, a block row at a time.

``lighting/stage.py`` bakes one block; this queues a row's blocks on the light processes once
the surface holds every row they read, then bakes the coarser levels and writes the meta.
docs/map/light-and-crowns.md section 29 and docs/map/renders.md section 42.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.horizon import SKY_RADIUS_M, horizon_reach_px
from mapgen.lighting.light_tiles import level_strips
from mapgen.lighting.model import light_axis
from mapgen.lighting.stage import (
    BLOCK_TILES,
    LIGHT_DIR_NAME,
    BlockDone,
    BlockJob,
    Occluder,
    Surface,
    allocate_work_arrays,
    bake_block,
    cast_digests,
    light_key,
    light_workers,
    save_work_array,
)
from mapgen.pools import one_blas_thread
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_PX,
    RETIRED_SUFFIX,
    STAGING_SUFFIX,
    TILES_DIR_NAME,
    swap_into_place,
)
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.core.mapprogress import encode_stage

__all__ = ["LightBake", "bake_light", "block_rows"]


def block_rows(size: int) -> tuple[int, list[int]]:
    """A ``size`` bake's block edge, and per block row the surface rows its blocks read: to
    the row's end and the horizon's halo past it."""
    block = min(size, BLOCK_TILES * PYRAMID_TILE_PX)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    halo = 2 * horizon_reach_px(2 * spacing_m)
    return block, [min(size, top + block + halo) for top in range(0, size, block)]


def bake_light(
    surface: Surface,
    out_dir: Path,
    workers: int | None,
    occluder: Occluder | None = None,
    progress: bool = True,
    occluder_layers: Sequence[str] = (),
    key: JsonObject | None = None,
) -> JsonObject:
    """Write ``out_dir/light/`` from a captured surface; returns its sidecar's ``_meta``.

    ``LightBake`` with every block row at once: its arguments are this function's.
    """
    bake = LightBake(surface, out_dir, workers, occluder, occluder_layers)
    try:
        return bake.finish(key, progress)
    finally:
        bake.close()


class LightBake:
    """One bake of a surface into ``out_dir/light/``, a block row at a time: a row's blocks
    can bake once the surface holds every row they read (``reads``), so a draw still going
    need not wait for the bake, nor the bake for the draw's end.

    ``workers`` None is ``light_workers()``, counted when the first row is queued. ``occluder``
    is an optional crown-top raster on the sheet's grid, metres, NaN where empty, or ``(top,
    cover)`` with the covered share as a byte; it casts into the crown horizons that
    ``occluder_layers`` read, and only casts. The arches and overhangs come with the surface,
    in its ``SlabStore``. An occluder made by ``occluder_planes`` in the surface's directory is
    read where it is, not copied.
    """

    def __init__(
        self,
        surface: Surface,
        out_dir: Path,
        workers: int | None = None,
        occluder: Occluder | None = None,
        occluder_layers: Sequence[str] = (),
    ) -> None:
        self.surface, self.requested = surface, workers
        self.occluder, self.occluder_layers = occluder, occluder_layers
        size, work, spacing_m = surface.size, surface.directory, surface.spacing_m
        allocate_work_arrays(work, size)
        crown_top, crown_cover = occluder if isinstance(occluder, tuple) else (occluder, None)
        save_work_array(work, "occluder", crown_top)
        save_work_array(work, "occluder_cover", crown_cover, np.uint8)
        self.top = int(np.log2(size // PYRAMID_TILE_PX))
        self.root = out_dir / LIGHT_DIR_NAME
        self.staging = self.root / (TILES_DIR_NAME + STAGING_SUFFIX)
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(parents=True)
        self.block, self.reads = block_rows(size)
        self.common = BlockJob(
            work=str(work),
            dest=str(self.staging),
            z=self.top,
            spacing_m=spacing_m,
            halo_h=horizon_reach_px(2 * spacing_m),
            sky_halo=int(np.ceil(SKY_RADIUS_M / (2 * spacing_m))) + 2,
            skip_water=True,
        )
        self.rows: dict[int, Future[None]] = {}
        self.blocks = (size // self.block) ** 2
        self.done: list[BlockDone] = []
        self.pool: ProcessPoolExecutor | None = None
        self.workers = 0
        self.started = 0.0
        self.progress = False
        self._lock = threading.Lock()

    def queue(self, row: int) -> Future[None]:
        """Block row ``row`` on the light processes, once; the future settles when its last
        block is in. The surface must hold every row it reads by now."""
        if row in self.rows:
            return self.rows[row]
        if self.pool is None:
            self.workers = light_workers(self.requested)
            self.pool = ProcessPoolExecutor(max_workers=self.workers)
            self.started = time.time()
        self.surface.flush()
        settled: Future[None] = Future()
        size, block = self.surface.size, self.block
        jobs = [replace(self.common, block=(row * block, c, block)) for c in range(0, size, block)]
        left = [len(jobs)]
        with one_blas_thread():
            futures = [self.pool.submit(bake_block, job) for job in jobs]
        for future in futures:
            future.add_done_callback(partial(self._block_done, settled, left))
        self.rows[row] = settled
        return settled

    def _block_done(
        self, settled: Future[None], left: list[int], future: Future[BlockDone]
    ) -> None:
        failed = future.exception() if not future.cancelled() else CancelledError()
        with self._lock:
            if failed is None:
                self.done.append(future.result())
                if self.progress:
                    print(encode_stage("light", len(self.done) / self.blocks), flush=True)
            left[0] -= 1
            last = not left[0]
        if failed is not None and not settled.done():
            settled.set_exception(failed)
        elif last and not settled.done():
            settled.set_result(None)

    def finish(
        self,
        key: JsonObject | None = None,
        progress: bool = True,
        on_row: Callable[[int], None] | None = None,
    ) -> JsonObject:
        """Every block row not yet queued, the coarser levels, and the pyramid renamed into
        place; returns its sidecar's ``_meta``. ``key`` is the bake's ``light_key``, made
        here when None; ``on_row`` runs as each block row is in, in order."""
        if key is None:
            key = light_key(self.surface, cast_digests(self.occluder), self.occluder_layers)
        with self._lock:
            self.progress = progress
            if progress and self.done:
                print(encode_stage("light", len(self.done) / self.blocks), flush=True)
        for row in range(len(self.reads)):
            self.queue(row).result()
            if on_row is not None:
                on_row(row)
        native_s = time.time() - self.started
        tiles = sum(done.tiles for done in self.done)
        spacing_m, work = self.surface.spacing_m, self.surface.directory
        with one_blas_thread():
            for level in range(self.top - 1, -1, -1):
                spacing = spacing_m * 2 ** (self.top - level)
                tiles += level_strips(work, self.staging, level, spacing, self._pool(), level == 0)
        return self._install(key, tiles, native_s)

    def _pool(self) -> ProcessPoolExecutor:
        if self.pool is None:
            raise RuntimeError("the light's coarser levels need its block rows baked first")
        return self.pool

    def _install(self, key: JsonObject, tiles: int, native_s: float) -> JsonObject:
        root, staging, size = self.root, self.staging, self.surface.size
        written = sum(p.stat().st_size for p in staging.rglob("*.webp"))
        installed_by = swap_into_place(
            staging, root / TILES_DIR_NAME, root / (TILES_DIR_NAME + RETIRED_SUFFIX)
        )
        spacing_m, occluder = self.surface.spacing_m, self.occluder
        meta: JsonObject = {
            "generator": "tools/gen_map_renders.py",
            "kind": LIGHT_DIR_NAME,
            "light": {
                **light_axis(),
                "occluder_layers": list(self.occluder_layers) if occluder is not None else [],
            },
            "key": key,
            "tiles": {
                "tile_px": PYRAMID_TILE_PX,
                "hz_tile_px": PYRAMID_TILE_PX // 2,
                "max_z": self.top,
                "count": tiles,
                "bytes": written,
                "hz_bytes": sum(done.hz_bytes for done in self.done),
                "installed_by": installed_by,
            },
            "render": {
                "size_px": size,
                "metres_per_pixel": round(spacing_m, 4),
                "horizon_res_m": round(2 * spacing_m, 4),
                "blocks": self.blocks,
                "workers": self.workers,
                "seconds_native": round(native_s, 1),
                "seconds": round(time.time() - self.started, 1),
                "occluder": occluder is not None,
                "slab_tiles": len(list(self.surface.slabs.directory.glob("*.npy"))),
            },
        }
        (root / "meta.json").write_text(json.dumps({"_meta": meta}, indent=1), encoding="utf-8")
        return meta

    def close(self) -> None:
        """Stop the light processes, cancelling the blocks not yet started, and let go of
        what casts: the crowns may be memory maps of the scratch."""
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None
        self.occluder = None
