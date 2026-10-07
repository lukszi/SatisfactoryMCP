"""Floating geometry as the light captures it: arches and rock overhangs, a sparse store.

The draw hands the light, beside each band's heights, the surface without what floats and
that geometry's underside and top (``SlabPlanes``). Only the 256 px tiles that hold some are
kept, one file each, and a block of the bake reads its window at half resolution from them.
docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["SLAB_DIR_NAME", "SLAB_TILE_PX", "HalfSlabs", "SlabPlanes", "SlabStore"]

SLAB_DIR_NAME = "slabs"
#: The store's tile edge: a band's columns are cut in tiles of this many.
SLAB_TILE_PX = 256


class SlabPlanes(NamedTuple):
    """Rows of floating geometry: the surface without it, its underside and its top, metres;
    the underside and top NaN where nothing floats."""

    solid: F32Grid
    lo: F32Grid
    hi: F32Grid


class HalfSlabs(NamedTuple):
    """A window's slabs at half resolution: the solid surface (2 x 2 mean), and the underside
    and top of each cell's highest span, NaN where nothing floats."""

    solid: F32Grid
    lo: F32Grid
    hi: F32Grid


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
    """The slab tiles of one surface, in ``directory``: ``{row}_{col}.npy``, each a
    ``(3, rows, cols)`` float32 stack of ``SlabPlanes``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def put(self, row: int, c0: int, slabs: SlabPlanes) -> bytes:
        """Rows from ``row`` on, columns from ``c0``: each tile holding a slab written; the
        digest of what was written."""
        digest = hashlib.sha256()
        self.directory.mkdir(parents=True, exist_ok=True)
        stack = np.stack([np.ascontiguousarray(p, np.float32) for p in slabs])
        width = stack.shape[2]
        first = c0 - c0 % SLAB_TILE_PX
        for col in range(first, c0 + width, SLAB_TILE_PX):
            a, b = max(col, c0) - c0, min(col + SLAB_TILE_PX, c0 + width) - c0
            tile = stack[:, :, a:b]
            if not np.isfinite(tile[1]).any():
                continue
            tile = np.ascontiguousarray(tile)
            np.save(self.directory / f"{row}_{c0 + a}.npy", tile)
            digest.update(np.array([row, c0 + a], np.int64).tobytes() + tile.tobytes())
        return digest.digest()

    def _tiles(self, r0: int, r1: int, c0: int, c1: int) -> list[tuple[int, int, Path]]:
        found: list[tuple[int, int, Path]] = []
        if not self.directory.is_dir():
            return found
        for path in self.directory.glob("*.npy"):
            row, col = (int(v) for v in path.stem.split("_"))
            if row < r1 and col < c1 and row + SLAB_TILE_PX > r0 and col + SLAB_TILE_PX > c0:
                found.append((row, col, path))
        return found

    def half(self, window: tuple[int, int, int, int], z_half: F32Grid) -> HalfSlabs | None:
        """The window ``(r0, r1, c0, c1)`` (even edges, may reach off the sheet) at half
        resolution, the solid surface ``z_half`` where nothing floats; None without a slab."""
        r0, r1, c0, c1 = window
        tiles = self._tiles(r0, r1, c0, c1)
        if not tiles:
            return None
        solid = z_half.copy()
        lo = np.full(z_half.shape, np.nan, np.float32)
        hi = np.full(z_half.shape, np.nan, np.float32)
        for row, col, path in tiles:
            stack = np.load(path)
            a0, a1 = max(row, r0), min(row + stack.shape[1], r1)
            b0, b1 = max(col, c0), min(col + stack.shape[2], c1)
            part = stack[:, a0 - row : a1 - row, b0 - col : b1 - col]
            cells = (slice((a0 - r0) // 2, (a1 - r0) // 2), slice((b0 - c0) // 2, (b1 - c0) // 2))
            lo[cells], hi[cells] = _highest(part[1], part[2])
            floats = np.isfinite(lo[cells])
            solid[cells] = np.where(floats, _mean(part[0]), solid[cells])
        return HalfSlabs(solid, lo, hi)

    def full(self, window: tuple[int, int, int, int], z: F32Grid) -> tuple[F32Grid, F32Grid] | None:
        """The window's solid surface at full resolution (``z`` where nothing floats) and the
        floating geometry's underside, NaN where none; None without a slab."""
        r0, r1, c0, c1 = window
        tiles = self._tiles(r0, r1, c0, c1)
        if not tiles:
            return None
        solid, lo = z.copy(), np.full(z.shape, np.nan, np.float32)
        for row, col, path in tiles:
            stack = np.load(path)
            a0, a1 = max(row, r0), min(row + stack.shape[1], r1)
            b0, b1 = max(col, c0), min(col + stack.shape[2], c1)
            part = stack[:, a0 - row : a1 - row, b0 - col : b1 - col]
            solid[a0 - r0 : a1 - r0, b0 - c0 : b1 - c0] = part[0]
            lo[a0 - r0 : a1 - r0, b0 - c0 : b1 - c0] = part[1]
        return solid, lo
