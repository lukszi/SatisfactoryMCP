"""The parallel tile cutter: every tree of a run cut while its sheets' rows are still coming in.

Each sheet takes its rows in order on a lane of a thread pool, resamples its levels in strips
as their rows arrive (``tiles/levels.py``), and hands each row of tiles to a pool of encoder
processes through shared memory as soon as it is whole. No sheet is held whole, and the bytes
are the serial ``install_pyramid``'s. docs/map/renders.md sections 17 and 42.
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from typing import NamedTuple, Self

import numpy as np

from mapgen.pools import free_ram_bytes
from mapgen.tiles.imaging import TileImaging
from mapgen.tiles.levels import SheetRows
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.pyramid import (
    PyramidError,
    commit_tree,
    encode_tile_row,
    level_record,
    pyramid_record,
    pyramid_top_z,
    stage_tree,
)
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["CUT_WORKERS", "Sheet", "TileStream", "TreeSpec"]

#: Encoders when ``--cut-workers`` is not given.
CUT_WORKER_CAP = 24
CUT_WORKERS = min(os.cpu_count() or 1, CUT_WORKER_CAP)
#: Threads the sheets' lanes run on; Pillow releases the GIL inside a resize.
LANCZOS_THREADS = min(os.cpu_count() or 1, 8)
#: What one encoder holds, and what stays free while new rows wait for memory.
WORKER_BYTES = 200 << 20
RAM_RESERVE = 4 << 30
#: Rows queued on the lanes or encoding before ``put`` waits for them.
QUEUED_BYTES = 2 << 30

#: How a sheet's rows are changed on its lane before it takes them.
Transform = Callable[[U8Grid], U8Grid]


class TreeSpec(NamedTuple):
    """A tile tree: its directory, its tile size, and what its levels say they were cut from."""

    dir_name: str
    tile_px: int
    text: str


class _Tree:
    """One tree staged and being cut: per level, the encoders' futures of its tile rows."""

    def __init__(self, out_dir: Path, spec: TreeSpec, top_px: int) -> None:
        self.out_dir, self.spec = out_dir, spec
        self.top_z = pyramid_top_z(top_px, spec.tile_px)
        self.staging = stage_tree(out_dir, spec.dir_name)
        self.rows: dict[int, list[Future[int]]] = {}
        for z in range(self.top_z + 1):
            (self.staging / str(z)).mkdir()
            self.rows[z] = []

    def futures(self) -> list[Future[int]]:
        return [future for level in self.rows.values() for future in level]


class _Lane:
    """Tasks run one at a time, in the order they were posted, on the stream's threads."""

    def __init__(self, stream: TileStream) -> None:
        self.stream = stream
        self.tasks: deque[tuple[Callable[[], None], int]] = deque()
        self.running = False

    @property
    def idle(self) -> bool:
        return not self.running and not self.tasks

    def post(self, task: Callable[[], None], nbytes: int) -> None:
        stream = self.stream
        if stream.threads is None:
            stream.run(task)
            return
        with stream.changed:
            stream.queued += nbytes
            self.tasks.append((task, nbytes))
            if self.running:
                return
            self.running = True
        stream.threads.submit(self._drain)

    def _drain(self) -> None:
        stream = self.stream
        while True:
            with stream.changed:
                if not self.tasks:
                    self.running = False
                    stream.changed.notify_all()
                    return
                task, nbytes = self.tasks.popleft()
            stream.run(task)
            with stream.changed:
                stream.queued -= nbytes
                stream.changed.notify_all()


class _TileRows:
    """One level of one tree: its rows gathered into rows of tiles, each encoded once whole."""

    def __init__(self, stream: TileStream, tree: _Tree, z: int) -> None:
        self.stream, self.tree, self.z = stream, tree, z
        self.parts: list[U8Grid] = []
        self.pending = 0

    def add(self, rows: U8Grid) -> None:
        tile = self.tree.spec.tile_px
        self.parts.append(rows)
        self.pending += rows.shape[0]
        while self.pending >= tile:
            joined = self.parts[0] if len(self.parts) == 1 else np.concatenate(self.parts)
            self.parts = [joined[tile:]] if joined.shape[0] > tile else []
            self.pending -= tile
            futures = self.tree.rows[self.z]
            futures.append(self.stream.encode(joined[:tile], self.tree, self.z, len(futures)))


class Sheet:
    """One sheet cut as its rows come in: its levels, the trees cut from them, and the sheet
    a downscale's tree is cut from (``tiles@2x/`` from 16384 when the sheet is larger)."""

    def __init__(
        self,
        stream: TileStream,
        out_dir: Path,
        px: int,
        trees: Sequence[TreeSpec],
        dense: tuple[TreeSpec, int] | None = None,
    ) -> None:
        self.px, self.posted = px, 0
        self.trees = [_Tree(out_dir, spec, px) for spec in trees]
        self.child: Sheet | None = None
        self.consumers: dict[int, list[Callable[[U8Grid], None]]] = defaultdict(list)
        if dense is not None and (dense_px := min(px, dense[1])) < px:
            self.child = Sheet(stream, out_dir, dense_px, [dense[0]])
            self.consumers[dense_px].append(self.child.post)
        elif dense is not None:
            self.trees.append(_Tree(out_dir, dense[0], px))
        for tree in self.trees:
            for z in range(tree.top_z + 1):
                side = tree.spec.tile_px << z
                self.consumers[side].append(_TileRows(stream, tree, z).add)
        self.rows = SheetRows(stream.image_mod, px, [side for side in self.consumers if side < px])
        self.lane = _Lane(stream)

    def sheets(self) -> list[Sheet]:
        """This sheet and the one its downscale's tree is cut from, parent first."""
        return [self] if self.child is None else [self, *self.child.sheets()]

    def post(self, rows: U8Grid, transform: Transform | None = None) -> None:
        """Queue ``rows``, the next of the sheet, on its lane; ``transform`` runs there first."""
        self.posted += rows.shape[0]
        if self.posted > self.px:
            raise PyramidError(f"rows past the end of a {self.px} px sheet")
        self.lane.post(partial(self._take, rows, transform), rows.nbytes)

    def _take(self, rows: U8Grid, transform: Transform | None) -> None:
        if transform is not None:
            rows = transform(rows)
        for side, out in self.rows.add(rows):
            for consume in self.consumers.get(side, ()):
                consume(out)


class TileStream:
    """One encode pool and one pool of lanes for every sheet of a run.

    ``workers`` encode, capped by free memory; one cuts serially on the caller's thread, each
    row as it is put. A failure anywhere stops the work and is raised by the next ``put`` or
    ``install``.
    """

    def __init__(
        self, image_mod: TileImaging, workers: int, threads: int = LANCZOS_THREADS
    ) -> None:
        free = free_ram_bytes()
        if free is not None:
            workers = max(1, min(workers, (free - RAM_RESERVE) // WORKER_BYTES))
        self.image_mod, self.workers = image_mod, max(1, workers)
        self.encoders: ProcessPoolExecutor | None = None
        self.threads: ThreadPoolExecutor | None = None
        if self.workers > 1:
            # Spawned, not forked: the encoders start while the lanes' threads run.
            spawn = multiprocessing.get_context("spawn")
            self.encoders = ProcessPoolExecutor(max_workers=self.workers, mp_context=spawn)
            self.threads = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="lanczos")
        self.changed = threading.Condition()
        self.queued = 0
        self.failure: BaseException | None = None
        self.blocks: set[SharedMemory] = set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, _tb: object) -> None:
        if exc is not None:
            self.fail(exc)
        if self.threads is not None:
            self.threads.shutdown(wait=True, cancel_futures=True)
        if self.encoders is not None:
            self.encoders.shutdown(wait=True, cancel_futures=True)
        gc.collect()
        for block in list(self.blocks):
            self._free(block)

    def warm(self) -> None:
        """Start every encoder now: spawning interpreters costs more than a small cut."""
        if self.encoders is not None:
            list(self.encoders.map(int, range(self.workers)))

    def sheet(
        self,
        out_dir: Path,
        px: int,
        trees: Sequence[TreeSpec],
        dense: tuple[TreeSpec, int] | None = None,
    ) -> Sheet:
        """A ``px`` sheet cut into ``trees`` under ``out_dir``, each staged now; ``dense`` is a
        tree cut from the sheet downscaled to at most its size."""
        return Sheet(self, out_dir, px, trees, dense)

    def put(self, sheet: Sheet, rows: U8Grid, transform: Transform | None = None) -> None:
        """``sheet.post``, once the rows queued and encoding leave room for these."""
        if self.threads is not None:
            with self.changed:
                while self.failure is None and self.queued > 0 and not self._room(rows.nbytes):
                    self.changed.wait(1.0)
        self._raise()
        sheet.post(rows, transform)

    def _room(self, nbytes: int) -> bool:
        free = free_ram_bytes()
        enough = free is None or free >= nbytes + RAM_RESERVE
        return enough and self.queued + nbytes <= QUEUED_BYTES

    def run(self, task: Callable[[], None]) -> None:
        """A lane's task, unless the stream has failed; its failure fails the stream."""
        if self.failure is not None:
            return
        try:
            task()
        except BaseException as exc:
            self.fail(exc)

    def fail(self, exc: BaseException) -> None:
        with self.changed:
            if self.failure is None:
                self.failure = exc
            self.changed.notify_all()

    def _raise(self) -> None:
        if self.failure is not None:
            raise self.failure

    def encode(self, rows: U8Grid, tree: _Tree, z: int, row: int) -> Future[int]:
        """Row ``row`` of level ``z``'s tiles, copied to shared memory and queued."""
        block = SharedMemory(create=True, size=rows.nbytes)
        view = np.ndarray(rows.shape, np.uint8, buffer=block.buf)
        view[...] = rows
        del view
        job = (block.name, rows.shape[1], z, row, str(tree.staging), tree.spec.tile_px)
        with self.changed:
            self.blocks.add(block)
            self.queued += rows.nbytes
        if self.encoders is None:
            future: Future[int] = Future()
            try:
                future.set_result(encode_tile_row(job))
            except Exception as exc:
                future.set_exception(exc)
        else:
            future = self.encoders.submit(encode_tile_row, job)
        future.add_done_callback(partial(self._encoded, block, rows.nbytes))
        return future

    def _encoded(self, block: SharedMemory, nbytes: int, future: Future[int]) -> None:
        self._free(block)
        failed = future.exception() if not future.cancelled() else None
        with self.changed:
            self.queued -= nbytes
            if failed is not None and self.failure is None:
                self.failure = failed
            self.changed.notify_all()

    def _free(self, block: SharedMemory) -> None:
        with self.changed:
            if block not in self.blocks:
                return
            self.blocks.discard(block)
        block.close()
        block.unlink()

    def install(self, sheet: Sheet) -> list[JsonObject]:
        """Wait for every tile of ``sheet``'s trees, then check each and rename it into place:
        the sheet's own trees first, then its downscale's."""
        sheets = sheet.sheets()
        trees = [tree for part in sheets for tree in part.trees]
        with self.changed:
            while self.failure is None and not (
                all(part.lane.idle for part in sheets)
                and all(future.done() for tree in trees for future in tree.futures())
            ):
                self.changed.wait(1.0)
        self._raise()
        for part in sheets:
            if not part.rows.complete:
                raise PyramidError(f"a {part.px} px sheet was cut from {part.rows.have} rows")
        return [self._commit(tree) for tree in trees]

    def _commit(self, tree: _Tree) -> JsonObject:
        spec = tree.spec
        levels = []
        for z in range(tree.top_z + 1):
            written = sum(future.result() for future in tree.rows[z])
            levels.append(level_record(z, written, spec.text, spec.tile_px))
        stats = pyramid_record(levels, spec.tile_px, self.workers, spec.dir_name)
        installed: JsonObject = commit_tree(stats, tree.out_dir, spec.dir_name)
        return installed
