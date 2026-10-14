"""The light's coarser levels, each strip built as soon as the block rows under it are in.

``light_tiles.CoarseLevel`` builds one level a strip at a time; this chains them from the
native level down to z0, so a strip runs once the strips above it wrote the rows it reads.
The bytes are those of a level built whole. docs/map/renders.md section 42.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, Future
from pathlib import Path

from mapgen.lighting.light_tiles import CoarseLevel, TileSet, level_layout, open_sources

__all__ = ["CoarseLevels"]


class CoarseLevels:
    """The levels below a bake's native level ``top``, fed the rows of its half-resolution
    sources as the block rows come in (``advance``).

    ``encoder`` takes each strip's tiles, with at most ``ahead`` pieces waiting (None is
    ``LEVEL_AHEAD``); both may be swapped between strips. The levels below the first are
    written to ``{name}.z{level}.npy`` in ``work``, removed by ``finish``.
    """

    def __init__(
        self,
        work: Path,
        dest: Path,
        top: int,
        spacing_m: float,
        encoder: Executor,
        ahead: int | None = None,
    ) -> None:
        self.work, self.dest, self.top, self.spacing_m = work, dest, top, spacing_m
        self.encoder, self.ahead = encoder, ahead
        self.levels: list[CoarseLevel] = []
        self.pending: deque[list[Future[int]]] = deque()

    def advance(self, rows: int, folded: TileSet, stop: Callable[[], bool] = lambda: False) -> None:
        """Every strip whose sources are in, the first level's sources holding ``rows`` rows;
        ``folded`` names the native tiles with a folded band among the rows baked. ``stop``
        is asked before each strip."""
        for i in range(self.top):
            level = self._level(i)
            if level is None:
                return
            shift = self.top - level.level
            above = frozenset((x >> shift, y >> shift) for x, y in folded)
            while level.ready(rows):
                if stop():
                    return
                level.strip(self.encoder, self.pending, above, self.ahead)
            rows = level.written

    def _level(self, i: int) -> CoarseLevel | None:
        """The ``i``-th level below the native one, opened once the one above wrote to it."""
        if i < len(self.levels):
            return self.levels[i]
        z = self.top - 1 - i
        if i == 0:
            sources = open_sources(self.work)
        else:
            above = self.levels[i - 1]
            if above.below is None:
                return None
            sources = above.below
        below = f".z{z - 1}" if z else None
        spacing_m = self.spacing_m * 2 ** (i + 1)
        self.levels.append(CoarseLevel(sources, self.dest, z, spacing_m, self.work, below))
        return self.levels[-1]

    def finish(self) -> int:
        """Wait for every tile, let go of the levels' files and remove them; returns the
        tiles written."""
        while self.pending:
            for future in self.pending.popleft():
                future.result()
        return self.close()

    def close(self) -> int:
        """The levels' files closed and removed, whatever is still encoding; the tiles."""
        tiles = sum(level.tiles for level in self.levels)
        while self.levels:
            self.levels.pop().close()  # Windows removes a file only once its last map is closed
        self.pending.clear()
        for z in range(self.top - 1):
            for name, _, _ in level_layout(2):
                (self.work / f"{name}.z{z}.npy").unlink(missing_ok=True)
        return tiles
