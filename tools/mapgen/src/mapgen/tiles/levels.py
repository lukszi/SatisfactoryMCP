"""A sheet's pyramid levels, resampled as its rows come in rather than from the whole sheet.

Each level is still one Lanczos downscale of the whole sheet (``core/gameassets/pyramid``).
A strip of a level reads ``3 * scale + 4`` source rows past its edges; with those in, it is
the bytes of those rows of a whole-sheet resize. So a level computes each strip once the rows
it reads have arrived, and the sheet keeps only the rows some level still reads.
docs/map/renders.md sections 17 and 42.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import numpy as np

from mapgen.tiles.imaging import TileImaging
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PyramidError

__all__ = ["STRIP_BYTES", "Level", "SheetRows", "resample_rows", "strip_halo", "strip_rows"]

#: Source pixels a strip reads, at Pillow's four bytes a pixel.
STRIP_BYTES = 1 << 27


def strip_halo(scale: int) -> int:
    """Source rows a strip of a level ``scale`` times smaller reads past either edge.

    Pillow's Lanczos reaches ``2.5 * scale + 0.5`` rows; this is that with room to spare.
    """
    return 3 * scale + 4


def strip_rows(source_px: int, side: int) -> int:
    """Output rows of a ``side`` level a strip computes: ``STRIP_BYTES`` of source, at least one."""
    if side < 1 or source_px % side:
        raise PyramidError(f"a {side} px level does not divide a {source_px} px sheet")
    return max(1, STRIP_BYTES // (4 * source_px * (source_px // side)))


def resample_rows(
    image_mod: TileImaging, window: U8Grid, first: int, px: int, side: int, r0: int, r1: int
) -> U8Grid:
    """Rows ``[r0, r1)`` of a ``px`` sheet Lanczos'd to ``side``, from ``window``, the sheet's
    rows from ``first`` on: the bytes of those rows of a resize of the whole sheet.

    ``window`` must hold every row the strip reads. Pillow's taps for an output row depend
    only on its position, so the ``box`` places the strip where it lies in the sheet.
    """
    scale = px // side
    halo = strip_halo(scale)
    y0, y1 = r0 * scale, r1 * scale
    top, bottom = max(y0 - halo, 0), min(y1 + halo, px)
    if top < first or bottom > first + window.shape[0]:
        raise ValueError(f"rows {top}:{bottom} are not all in the window {first}:+{len(window)}")
    part = image_mod.fromarray(np.ascontiguousarray(window[top - first : bottom - first]))
    resized = part.resize((side, r1 - r0), image_mod.LANCZOS, box=(0, y0 - top, px, y1 - top))
    return np.asarray(resized)


class Level:
    """One level being resampled: the rows done, and the rows a strip computes."""

    def __init__(self, px: int, side: int) -> None:
        self.side, self.scale = side, px // side
        self.strip = strip_rows(px, side)
        self.done = 0

    @property
    def finished(self) -> bool:
        return self.done >= self.side

    def reads_from(self) -> int:
        """The first source row the next strip reads."""
        return max(self.done * self.scale - strip_halo(self.scale), 0)

    def next_strip(self, have: int, px: int) -> int | None:
        """Where the next strip ends once the sheet's first ``have`` rows are in, or None
        while too few are. A level waits for a whole strip, but for its last rows."""
        if self.finished:
            return None
        if have >= px:
            return min(self.done + self.strip, self.side)
        ready = (have - strip_halo(self.scale)) // self.scale
        if ready >= self.done + self.strip:
            return self.done + self.strip
        return None


class SheetRows:
    """A square sheet arriving a run of rows at a time, in order, and the levels of ``sides``
    resampled from it as their strips' rows come in."""

    def __init__(self, image_mod: TileImaging, px: int, sides: Iterable[int]) -> None:
        self.image_mod, self.px = image_mod, px
        self.levels = [Level(px, side) for side in sorted(set(sides), reverse=True)]
        if any(level.side >= px for level in self.levels):
            raise PyramidError(f"a level of a {px} px sheet is smaller than the sheet")
        self.have = 0
        self.kept: list[tuple[int, U8Grid]] = []

    @property
    def complete(self) -> bool:
        """Every row in, and every level resampled."""
        return self.have == self.px and all(level.finished for level in self.levels)

    def add(self, rows: U8Grid) -> Iterator[tuple[int, U8Grid]]:
        """Take the next ``rows``; yield ``(side, rows)``: these rows as the sheet's own
        (``side`` is ``px``), then each level's strips that can now be computed, in order."""
        if rows.ndim != 3 or rows.shape[1:] != (self.px, 3):
            raise ValueError(f"rows of a {self.px} px sheet are ({self.px}, 3), not {rows.shape}")
        if self.have + rows.shape[0] > self.px:
            raise ValueError(f"rows past the end of a {self.px} px sheet")
        self.kept.append((self.have, rows))
        self.have += rows.shape[0]
        yield self.px, rows
        for level in self.levels:
            while (end := level.next_strip(self.have, self.px)) is not None:
                yield level.side, self._resample(level, end)
        self._forget()

    def _resample(self, level: Level, end: int) -> U8Grid:
        top = level.reads_from()
        out = resample_rows(self.image_mod, self._window(top), top, self.px, level.side,
                            level.done, end)  # fmt: skip
        level.done = end
        return out

    def _window(self, top: int) -> U8Grid:
        """The kept rows from ``top`` on: a view when one run of rows holds them."""
        parts = [(first, rows) for first, rows in self.kept if first + rows.shape[0] > top]
        if parts[0][0] > top:
            raise ValueError(f"row {top} is no longer kept")
        first, rows = parts[0]
        if len(parts) == 1:
            return rows[top - first :]
        return np.concatenate([rows[top - first :], *(rows for _, rows in parts[1:])])

    def _forget(self) -> None:
        """Drop the runs of rows no level reads again."""
        needed = [level.reads_from() for level in self.levels if not level.finished]
        keep_from = min(needed, default=self.have)
        self.kept = [(first, rows) for first, rows in self.kept if first + len(rows) > keep_from]
