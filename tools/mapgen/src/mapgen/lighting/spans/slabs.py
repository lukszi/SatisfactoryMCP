"""Floating geometry as the light captures it: arches and rock overhangs, a sparse store.

The draw hands the light, beside each band's heights, the surface without what floats and
that geometry's underside and top (``SlabPlanes``). Only the tiles of ``SLAB_TILE_PX``
columns that hold some are kept, one file each, and a block of the bake reads its window at
half resolution from them. docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["SLAB_DIR_NAME", "SLAB_TILE_PX", "SlabPlanes", "SlabStore"]

SLAB_DIR_NAME = "slabs"
#: The store's tile width: a put's columns are cut in tiles of this many. A tile holds every
#: row of its put, and its file name says how many.
SLAB_TILE_PX = 256


class SlabPlanes(NamedTuple):
    """Floating geometry: the surface without it, its underside and its top, metres; the
    underside and top NaN where nothing floats. Rows as a band hands them over, or a window's
    cells at half resolution (``SlabStore.half``)."""

    solid: F32Grid
    lo: F32Grid
    hi: F32Grid


class _Tile(NamedTuple):
    """A stored tile: its first row and column, its rows and columns, and its file."""

    row: int
    col: int
    rows: int
    cols: int
    path: Path


def _cells(a: NDArray[np.floating]) -> NDArray[np.floating]:
    """``a`` as half-resolution cells of four: ``(h, w, 4)``, row by row within a cell."""
    h, w = a.shape[0] // 2, a.shape[1] // 2
    return a[: 2 * h, : 2 * w].reshape(h, 2, w, 2).transpose(0, 2, 1, 3).reshape(h, w, 4)


def _highest(lo: F32Grid, hi: F32Grid) -> tuple[F32Grid, F32Grid]:
    """Per cell of four, the span whose top is highest (the first of equals): two spans side
    by side are never read as one. NaN where none floats."""
    tops = _cells(hi)
    pick = np.argmax(np.where(np.isnan(tops), -np.inf, tops), axis=-1)[..., None]
    low = np.take_along_axis(_cells(lo), pick, axis=-1)[..., 0]
    high = np.take_along_axis(tops, pick, axis=-1)[..., 0]
    return low.astype(np.float32), high.astype(np.float32)


def _mean(a: NDArray[np.floating]) -> F32Grid:
    h, w = a.shape[0] // 2, a.shape[1] // 2
    return np.mean(a[: 2 * h, : 2 * w].reshape(h, 2, w, 2), axis=(1, 3)).astype(np.float32)


class SlabStore:
    """The slab tiles of one surface, in ``directory``: ``{row}_{col}_{rows}x{cols}.npy``,
    each a ``(3, rows, cols)`` float32 stack of ``SlabPlanes``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def put(self, row: int, c0: int, slabs: SlabPlanes) -> bytes:
        """Rows from ``row`` on, columns from ``c0``: each tile holding a slab written; the
        digest of what was written."""
        digest = hashlib.sha256()
        self.directory.mkdir(parents=True, exist_ok=True)
        stack = np.stack([np.ascontiguousarray(p, np.float32) for p in slabs])
        rows, width = stack.shape[1], stack.shape[2]
        first = c0 - c0 % SLAB_TILE_PX
        for col in range(first, c0 + width, SLAB_TILE_PX):
            a, b = max(col, c0) - c0, min(col + SLAB_TILE_PX, c0 + width) - c0
            tile = stack[:, :, a:b]
            if not np.isfinite(tile[1]).any():
                continue
            tile = np.ascontiguousarray(tile)
            np.save(self.directory / f"{row}_{c0 + a}_{rows}x{b - a}.npy", tile)
            digest.update(np.array([row, c0 + a], np.int64).tobytes() + tile.tobytes())
        return digest.digest()

    def _tiles(self, r0: int, r1: int, c0: int, c1: int) -> list[_Tile]:
        found: list[_Tile] = []
        if not self.directory.is_dir():
            return found
        for path in self.directory.glob("*.npy"):
            row, col, extent = path.stem.split("_")
            rows, cols = extent.split("x")
            tile = _Tile(int(row), int(col), int(rows), int(cols), path)
            rows_meet = tile.row < r1 and tile.row + tile.rows > r0
            if rows_meet and tile.col < c1 and tile.col + tile.cols > c0:
                found.append(tile)
        return found

    def _parts(
        self, tiles: list[_Tile], window: tuple[int, int, int, int]
    ) -> Iterator[tuple[slice, slice, F32Grid]]:
        """Each tile's part inside the window: its rows and columns of the window, and its
        ``(3, rows, cols)`` stack there."""
        r0, r1, c0, c1 = window
        for tile in tiles:
            a0, a1 = max(tile.row, r0), min(tile.row + tile.rows, r1)
            b0, b1 = max(tile.col, c0), min(tile.col + tile.cols, c1)
            stack = np.load(tile.path)
            part = stack[:, a0 - tile.row : a1 - tile.row, b0 - tile.col : b1 - tile.col]
            yield slice(a0 - r0, a1 - r0), slice(b0 - c0, b1 - c0), part

    def half(self, window: tuple[int, int, int, int], z_half: F32Grid) -> SlabPlanes | None:
        """The window ``(r0, r1, c0, c1)`` (even edges, may reach off the sheet) at half
        resolution, the solid surface ``z_half`` where nothing floats; None without a slab."""
        tiles = self._tiles(*window)
        if not tiles:
            return None
        solid = z_half.copy()
        lo = np.full(z_half.shape, np.nan, np.float32)
        hi = np.full(z_half.shape, np.nan, np.float32)
        for rows, cols, part in self._parts(tiles, window):
            cells = (slice(rows.start // 2, rows.stop // 2), slice(cols.start // 2, cols.stop // 2))
            lo[cells], hi[cells] = _highest(part[1], part[2])
            floats = np.isfinite(lo[cells])
            solid[cells] = np.where(floats, _mean(part[0]), solid[cells])
        return SlabPlanes(solid, lo, hi)

    def full(self, window: tuple[int, int, int, int], z: F32Grid) -> tuple[F32Grid, F32Grid] | None:
        """The window's solid surface at full resolution (``z`` where nothing floats) and the
        floating geometry's underside, NaN where none; None without a slab."""
        tiles = self._tiles(*window)
        if not tiles:
            return None
        solid, lo = z.copy(), np.full(z.shape, np.nan, np.float32)
        for rows, cols, part in self._parts(tiles, window):
            solid[rows, cols] = part[0]
            lo[rows, cols] = part[1]
        return solid, lo
