"""The parallel tile cutter: every tree of a layer through one encode pool that never waits.

Levels are resampled in strips on threads, encoded by processes straight out of shared
memory, and a level two trees share is resampled once. The bytes are the serial
``install_pyramid``'s. docs/spatial-and-map.md section 17, "Cutting in parallel".
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import threading
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self, TypeVar, cast

import numpy as np

from mapgen.pools import free_ram_bytes
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.imaging import ImageFactory, LanczosFilter
from satisfactory_mcp.core.gameassets.pyramid import (
    LevelRecord,
    PyramidError,
    commit_tree,
    encode_tile_row,
    level_record,
    pyramid_record,
    pyramid_top_z,
    stage_tree,
)
from satisfactory_mcp.core.jsontypes import JsonObject

if TYPE_CHECKING:
    from PIL.Image import Image, Resampling

__all__ = [
    "CUT_WORKERS",
    "Block",
    "Cutter",
    "Source",
    "TileImaging",
    "Tree",
    "load_imaging",
    "resample_strip",
    "strip_spans",
]

T = TypeVar("T")

#: Encoders when ``--cut-workers`` is not given.
CUT_WORKER_CAP = 24
CUT_WORKERS = min(os.cpu_count() or 1, CUT_WORKER_CAP)
#: Threads resampling in the parent; Pillow releases the GIL inside a resize.
LANCZOS_THREADS = min(os.cpu_count() or 1, 8)
#: Source pixels per resampling strip, at Pillow's four bytes a pixel.
STRIP_BYTES = 1 << 27
#: What one encoder holds, and what stays free while a new block waits for memory.
WORKER_BYTES = 200 << 20
RAM_RESERVE = 4 << 30


class TileImaging(ImageFactory["Image"], LanczosFilter, Protocol):
    """``PIL.Image`` as the cutters and pyramids use it, handed in rather than imported."""

    @property
    def Resampling(self) -> type[Resampling]: ...

    def fromarray(self, obj: U8Grid, /) -> Image: ...


def load_imaging() -> TileImaging:
    """Pillow, once ``require_gen`` has shown it is there, with its size limit off.

    The limit is a decompression-bomb rule for images off the internet; an 8192 px sheet is
    the point here. The cast: Pillow sets ``LANCZOS`` at import, out of its stubs' sight.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    return cast(TileImaging, Image)


def strip_spans(source_px: int, side: int, width: int) -> list[tuple[int, int]]:
    """The output rows ``[r0, r1)`` each resampling strip of a ``side`` level fills."""
    if source_px % side:
        raise PyramidError(f"a {side} px level does not divide a {source_px} px sheet")
    rows = max(1, STRIP_BYTES // (4 * width * (source_px // side)))
    return [(r0, min(r0 + rows, side)) for r0 in range(0, side, rows)]


def resample_strip(image_mod: TileImaging, src: U8Grid, out: U8Grid, r0: int, r1: int) -> None:
    """Rows ``[r0, r1)`` of ``src`` Lanczos'd to ``out``'s size, written into ``out``.

    The pixels a resize of the whole sheet gives: Pillow's taps for a row depend only on its
    position, and the halo covers the 3 * scale source rows Lanczos reaches either side.
    """
    height, width = src.shape[:2]
    scale = height // out.shape[0]
    halo = 3 * scale + 4
    y0, y1 = r0 * scale, r1 * scale
    top, bottom = max(y0 - halo, 0), min(y1 + halo, height)
    part = image_mod.fromarray(src[top:bottom]).resize(
        (out.shape[1], r1 - r0), image_mod.LANCZOS, box=(0, y0 - top, width, y1 - top)
    )
    out[r0:r1] = np.asarray(part)


def _failure(future: Future[T]) -> BaseException | None:
    return CancelledError() if future.cancelled() else future.exception()


class Block:
    """One level's pixels in shared memory, freed when the last hold on it is released."""

    def __init__(self, shape: tuple[int, int, int]) -> None:
        self.shape = shape
        self.shm = SharedMemory(create=True, size=int(np.prod(shape)))
        self.array: U8Grid | None = np.ndarray(shape, np.uint8, buffer=self.shm.buf)
        self.ready: Future[Block] = Future()
        self.freed = False
        self._holds = 1
        self._lock = threading.Lock()

    @property
    def pixels(self) -> U8Grid:
        """The level's pixels, while the block is held."""
        if self.array is None:
            raise RuntimeError("a freed block was read")
        return self.array

    def hold(self) -> None:
        with self._lock:
            if self._holds <= 0:
                raise RuntimeError("a freed block was held again")
            self._holds += 1

    def release(self, _done: object = None) -> None:
        with self._lock:
            self._holds -= 1
            if self._holds:
                return
        self.free()

    def free(self) -> None:
        """Close and unlink now: the last release, or a cutter closing whatever is left."""
        with self._lock:
            if self.freed:
                return
            self.array = None
            try:
                self.shm.close()
            except BufferError:
                return
            self.freed = True
        self.shm.unlink()


class Source:
    """A published sheet and the levels resampled from it, each computed once.

    It holds every block it made until ``close``; work in flight holds its own.
    """

    def __init__(self, cutter: Cutter, top: Block) -> None:
        self.cutter, self.top = cutter, top
        self.levels: dict[int, Block] = {top.shape[0]: top}
        self.children: list[Source] = []

    @property
    def px(self) -> int:
        return self.top.shape[0]

    def level(self, side: int) -> Block:
        if side not in self.levels:
            self.levels[side] = self.cutter.resample(self.top, side)
        return self.levels[side]

    def derive(self, side: int) -> Source:
        """What a downscale to ``side`` is cut from: this source at its own size, else that level."""
        if side == self.px:
            return self
        top = self.level(side)
        top.hold()
        child = Source(self.cutter, top)
        self.children.append(child)
        return child

    def close(self) -> None:
        for child in self.children:
            child.close()
        for block in self.levels.values():
            block.release()
        self.children, self.levels = [], {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class Tree:
    """One tile tree being cut into its staging directory: per level, its queued row jobs."""

    def __init__(self, out_dir: Path, dir_name: str, tile_px: int, text: str) -> None:
        self.out_dir, self.dir_name, self.tile_px, self.text = out_dir, dir_name, tile_px, text
        self.staging = stage_tree(out_dir, dir_name)
        self.levels: dict[int, Future[list[Future[int]]]] = {}


class Cutter:
    """One encode pool and one resampling pool for every tree of a layer."""

    def __init__(
        self, image_mod: TileImaging, workers: int, threads: int = LANCZOS_THREADS
    ) -> None:
        free = free_ram_bytes()
        if free is not None:
            workers = max(1, min(workers, (free - RAM_RESERVE) // WORKER_BYTES))
        self.image_mod, self.workers = image_mod, workers
        # Spawned, not forked: the encoders start while the resampling threads run.
        spawn = multiprocessing.get_context("spawn")
        self.encoders = ProcessPoolExecutor(max_workers=workers, mp_context=spawn)
        self.threads = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="lanczos")
        self.blocks: list[Block] = []
        self.inflight: set[Future[int]] = set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.threads.shutdown(wait=True, cancel_futures=True)
        self.encoders.shutdown(wait=True, cancel_futures=True)
        gc.collect()
        for block in self.blocks:
            block.free()

    def _track(self, future: Future[int]) -> Future[int]:
        self.inflight.add(future)
        future.add_done_callback(self.inflight.discard)
        return future

    def _block(self, shape: tuple[int, int, int]) -> Block:
        """A new block, once there is room: while RAM is short, in-flight work is waited on."""
        need = int(np.prod(shape)) + RAM_RESERVE
        while (free := free_ram_bytes()) is not None and free < need:
            busy = [future for future in list(self.inflight) if not future.done()]
            if not busy:
                break
            wait(busy, timeout=1.0, return_when=FIRST_COMPLETED)
        block = Block(shape)
        self.blocks.append(block)
        return block

    def publish(self, sheet: U8Grid) -> Source:
        """A copy of ``sheet`` the encoders read; ``sheet`` is the caller's again on return."""
        block = self._block(sheet.shape)
        np.copyto(block.pixels, sheet)
        block.ready.set_result(block)
        return Source(self, block)

    def resample(self, source: Block, side: int) -> Block:
        """``source`` Lanczos'd to ``side``, in strips on the threads once ``source`` is ready."""
        out = self._block((side, side, 3))
        source.hold()
        source.ready.add_done_callback(lambda ready: self._strips(ready, source, out))
        return out

    def _strips(self, ready: Future[Block], source: Block, out: Block) -> None:
        if (failed := _failure(ready)) is not None:
            out.ready.set_exception(failed)
            source.release()
            return
        spans = strip_spans(source.shape[0], out.shape[0], source.shape[1])
        left, failures = [len(spans)], list[BaseException]()
        lock = threading.Lock()

        def strip(r0: int, r1: int) -> int:
            resample_strip(self.image_mod, source.pixels, out.pixels, r0, r1)
            return r1 - r0

        def done(future: Future[int]) -> None:
            # Settled only once every strip has stopped writing: a failure settled early
            # would let the level be freed under the strips still running.
            with lock:
                left[0] -= 1
                last = not left[0]
                if (failed := _failure(future)) is not None:
                    failures.append(failed)
            if last:
                source.release()
                if failures:
                    out.ready.set_exception(failures[0])
                else:
                    out.ready.set_result(out)

        for r0, r1 in spans:
            self._track(self.threads.submit(strip, r0, r1)).add_done_callback(done)

    def tree(self, source: Source, out_dir: Path, dir_name: str, tile_px: int, text: str) -> Tree:
        """Stage ``dir_name`` and queue every level of it, the top first."""
        tree = Tree(out_dir, dir_name, tile_px, text)
        for z in range(pyramid_top_z(source.px, tile_px), -1, -1):
            (tree.staging / str(z)).mkdir()
            tree.levels[z] = self._encode(tree, z, source.level(tile_px << z))
        return tree

    def _encode(self, tree: Tree, z: int, block: Block) -> Future[list[Future[int]]]:
        """The level's row jobs, queued on the encoders once its pixels are ready."""
        jobs: Future[list[Future[int]]] = Future()
        block.hold()

        def submit(ready: Future[Block]) -> None:
            try:
                if (failed := _failure(ready)) is not None:
                    raise failed
                queued: list[Future[int]] = []
                for row in range(block.shape[0] // tree.tile_px):
                    block.hold()
                    job = (block.shm.name, block.shape[1], z, row, str(tree.staging), tree.tile_px)
                    future = self._track(self.encoders.submit(encode_tile_row, job))
                    future.add_done_callback(block.release)
                    queued.append(future)
                jobs.set_result(queued)
            except BaseException as exc:
                jobs.set_exception(exc)
            finally:
                block.release()

        block.ready.add_done_callback(submit)
        return jobs

    def install(self, tree: Tree) -> JsonObject:
        """Wait for every tile of ``tree``, then check it and rename it into place."""
        levels: list[LevelRecord] = []
        for z in sorted(tree.levels):
            written = sum(future.result() for future in tree.levels[z].result())
            levels.append(level_record(z, written, tree.text, tree.tile_px))
        stats = pyramid_record(levels, tree.tile_px, self.workers, tree.dir_name)
        return commit_tree(stats, tree.out_dir, tree.dir_name)
