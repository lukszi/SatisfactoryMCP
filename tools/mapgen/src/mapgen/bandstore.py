"""A 2-D plane stored as one zstd frame per band of rows, and read back a band at a time.

The layout, the measurements behind it and what a corrupt band does: docs/map/renders.md
section 39. ``zstandard`` is the ``gen`` extra's, so it is imported where it is used.
"""

from __future__ import annotations

import operator
import os
import struct
import threading
from collections import OrderedDict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Self, TypeAlias

import numpy as np
from numpy.typing import ArrayLike, DTypeLike, NDArray

from satisfactory_mcp.core.arrays import I64Grid

__all__ = [
    "BAND_ROWS",
    "CACHED_BANDS",
    "ZSTD_LEVEL",
    "BandArray",
    "BandKey",
    "BandStoreError",
    "BandWriter",
]

BAND_ROWS = 256
ZSTD_LEVEL = 1
CACHED_BANDS = 3
MAGIC = b"MGBANDS1"
#: magic, rows, cols, band rows, dtype string, offset table bytes.
TRAILER = struct.Struct("<8sqqq8sq")

#: A plane's rows or columns: one, a slice, or a gather by index list, index array or mask.
AxisKey: TypeAlias = int | np.integer | slice | list[int] | NDArray[np.integer] | NDArray[np.bool_]
#: What ``BandArray`` takes: rows, then optionally columns.
BandKey: TypeAlias = AxisKey | tuple[AxisKey] | tuple[AxisKey, AxisKey]


class BandStoreError(ValueError):
    """A band file that is truncated, of another shape or type, or fails a frame checksum."""


def _shuffle(band: NDArray[np.generic]) -> bytes:
    if band.itemsize == 1:
        return band.tobytes()
    return band.view(np.uint8).reshape(-1, band.itemsize).T.tobytes()


def _unshuffle(
    raw: bytes, dtype: np.dtype[np.generic], rows: int, cols: int
) -> NDArray[np.generic]:
    flat = np.frombuffer(raw, np.uint8)
    if dtype.itemsize == 1:
        return flat.view(dtype).reshape(rows, cols)
    out = np.empty((rows * cols, dtype.itemsize), np.uint8)
    out[...] = flat.reshape(dtype.itemsize, -1).T
    return out.view(dtype).reshape(rows, cols)


def _is_axis_key(part: object) -> bool:
    return part is not Ellipsis and part is not None


class BandWriter:
    """Writes a plane top to bottom, ``band_rows`` at a time; ``close`` commits it to disk."""

    def __init__(self, path: Path | str, shape: tuple[int, int], dtype: DTypeLike,
                 band_rows: int = BAND_ROWS, level: int = ZSTD_LEVEL) -> None:  # fmt: skip
        import zstandard

        self.path = Path(path)
        self.dtype: np.dtype[np.generic] = np.dtype(dtype)
        self.shape = (int(shape[0]), int(shape[1]))
        self.band_rows = int(band_rows)
        self._zc = zstandard.ZstdCompressor(
            level=level, write_checksum=True, write_content_size=True
        )
        self._file = open(self.path, "wb")  # noqa: SIM115 -- held until close() or discard()
        self._offsets = [0]
        self.next_row = 0

    def write(self, top: int, band: ArrayLike) -> None:
        band = np.ascontiguousarray(band, dtype=self.dtype)
        rows = band.shape[0]
        whole = rows == self.band_rows or (
            0 < rows < self.band_rows and top + rows == self.shape[0]
        )
        if top != self.next_row or band.shape[1:] != self.shape[1:] or not whole:
            raise ValueError(
                f"{self.path}: a band of {band.shape} at row {top}; the next is row "
                f"{self.next_row}, {self.band_rows} rows of {self.shape[1]}"
            )
        blob = self._zc.compress(_shuffle(band))
        self._file.write(blob)
        self._offsets.append(self._offsets[-1] + len(blob))
        self.next_row = top + rows

    def close(self) -> None:
        if self._file.closed:
            return
        if self.next_row != self.shape[0]:
            self.discard()
            raise ValueError(f"{self.path}: {self.next_row} of {self.shape[0]} rows written")
        table = np.asarray(self._offsets, "<i8").tobytes()
        self._file.write(table)
        dtype = self.dtype.str.encode("ascii")
        self._file.write(TRAILER.pack(MAGIC, *self.shape, self.band_rows, dtype, len(table)))
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()

    def discard(self) -> None:
        """Close without committing, and delete what was written."""
        self._file.close()
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind: type[BaseException] | None, *_rest: object) -> None:
        if kind is None:
            self.close()
        elif not self._file.closed:
            self.discard()


class BandArray:
    """A read-only plane decoded a band at a time, the last ``keep`` bands kept.

    Indexing takes a row, a row slice, an integer array of rows, and any column index after
    them; a row and a column give a 0-d array. ``on_corrupt`` is called before a corrupt band
    raises. Threads may share one: the cache and the decoder are behind a lock.
    """

    ndim = 2

    def __init__(self, path: Path | str, shape: tuple[int, int], dtype: DTypeLike,
                 keep: int = CACHED_BANDS,
                 on_corrupt: Callable[[], None] | None = None) -> None:  # fmt: skip
        import zstandard

        self.path = Path(path)
        self.dtype: np.dtype[np.generic] = np.dtype(dtype)
        self.shape = (int(shape[0]), int(shape[1]))
        self.keep, self.on_corrupt = keep, on_corrupt
        self._zstd: ModuleType = zstandard
        self._zd = zstandard.ZstdDecompressor()
        self._cache: OrderedDict[int, NDArray[np.generic]] = OrderedDict()
        self._lock = threading.Lock()
        self.band_rows, self._offsets = self._read_table()

    @contextmanager
    def holding(self, bands: int) -> Generator[Self]:
        """Keep at least ``bands`` decoded inside the block, and as many as before after it."""
        before = self._resize(max(self.keep, int(bands)))
        try:
            yield self
        finally:
            self._resize(before)

    def _resize(self, keep: int) -> int:
        with self._lock:
            before, self.keep = self.keep, keep
            while len(self._cache) > self.keep:
                self._cache.popitem(last=False)
            return before

    def _read_table(self) -> tuple[int, I64Grid]:
        with open(self.path, "rb") as f:
            end = f.seek(0, os.SEEK_END)
            if end < TRAILER.size:
                raise BandStoreError(f"{self.path} is too short to be a band file")
            f.seek(end - TRAILER.size)
            fields: tuple[bytes, int, int, int, bytes, int] = TRAILER.unpack(f.read(TRAILER.size))
            magic, rows, cols, band_rows, dtype_raw, table = fields
            if magic != MAGIC:
                raise BandStoreError(f"{self.path} is not a band file, or its end is missing")
            dtype = dtype_raw.rstrip(b"\0").decode("ascii", "replace")
            if (rows, cols) != self.shape or dtype != self.dtype.str or band_rows < 1:
                raise BandStoreError(
                    f"{self.path} holds a {rows}x{cols} {dtype} plane in bands of {band_rows}, "
                    f"not {self.shape[0]}x{self.shape[1]} {self.dtype.str}"
                )
            start = end - TRAILER.size - table
            if table % 8 or start < 0:
                raise BandStoreError(f"{self.path}: the band table is damaged")
            f.seek(start)
            offsets: I64Grid = np.frombuffer(f.read(table), "<i8")
        bands = -(-rows // band_rows)
        if (
            offsets.size != bands + 1
            or offsets[0] != 0
            or offsets[-1] != start
            or (np.diff(offsets) <= 0).any()
        ):
            raise BandStoreError(f"{self.path}: the band table is damaged")
        return band_rows, offsets

    def __len__(self) -> int:
        return self.shape[0]

    def __array__(
        self, dtype: DTypeLike | None = None, copy: bool | None = None
    ) -> NDArray[np.generic]:
        """The whole plane decoded: always a new array, so ``copy=False`` is refused."""
        if copy is False:
            raise ValueError("a band array is decoded into a new array; it has no view to share")
        whole = self[:]
        return whole if dtype is None else whole.astype(dtype)

    def _band(self, k: int) -> NDArray[np.generic]:
        with self._lock:
            return self._decoded(k)

    def _decoded(self, k: int) -> NDArray[np.generic]:
        hit = self._cache.get(k)
        if hit is not None:
            self._cache.move_to_end(k)
            return hit
        lo, hi = int(self._offsets[k]), int(self._offsets[k + 1])
        with open(self.path, "rb") as f:
            f.seek(lo)
            blob = f.read(hi - lo)
        top = k * self.band_rows
        rows = min(self.band_rows, self.shape[0] - top)
        size = rows * self.shape[1] * self.dtype.itemsize
        try:
            if self._zstd.frame_content_size(blob) != size:
                raise self._zstd.ZstdError(f"its frame does not hold the {size} bytes written")
            raw = self._zd.decompress(blob)
        except self._zstd.ZstdError as exc:
            if self.on_corrupt is not None:
                self.on_corrupt()
            raise BandStoreError(
                f"{self.path}: band {k} (rows {top} to {top + rows}) is corrupt: {exc}"
            ) from exc
        band = _unshuffle(raw, self.dtype, rows, self.shape[1])
        band.flags.writeable = False
        self._cache[k] = band
        while len(self._cache) > self.keep:
            self._cache.popitem(last=False)
        return band

    def _span(self, r0: int, r1: int, cols: slice) -> NDArray[np.generic]:
        """Rows ``[r0, r1)`` over ``cols``, each stored band cut to them before they join."""
        if r1 <= r0:
            return np.empty((0, self.shape[1]), self.dtype)[:, cols]
        k0, k1 = r0 // self.band_rows, (r1 - 1) // self.band_rows
        parts: list[NDArray[np.generic]] = []
        for k in range(k0, k1 + 1):
            top = k * self.band_rows
            rows = slice(max(r0, top) - top, min(r1, top + self.band_rows) - top)
            parts.append(self._band(k)[rows, cols])
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def _gather(self, rows: NDArray[np.integer] | NDArray[np.bool_]) -> NDArray[np.generic]:
        idx = (np.flatnonzero(rows) if rows.dtype == bool else rows).astype(np.int64)
        if idx.ndim != 1:
            raise IndexError("a band array's rows are picked by a 1-D index")
        idx = np.where(idx < 0, idx + self.shape[0], idx)
        if idx.size and (idx.min() < 0 or idx.max() >= self.shape[0]):
            raise IndexError(f"a row index is outside 0..{self.shape[0] - 1}")
        out = np.empty((idx.size, self.shape[1]), self.dtype)
        which = idx // self.band_rows
        for k in np.unique(which):
            sel = np.flatnonzero(which == k)
            out[sel] = self._band(int(k))[idx[sel] - int(k) * self.band_rows]
        return out

    def __getitem__(self, key: BandKey) -> NDArray[np.generic]:
        parts = key if isinstance(key, tuple) else (key,)
        if not 1 <= len(parts) <= 2 or not all(map(_is_axis_key, parts)):
            raise IndexError("a band array takes a row index and an optional column index")
        rows, cols = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], slice(None))
        # A column slice is cut from each stored band as it is read, so a piece of a band
        # copies its own columns only; any other column key picks from the rows read.
        cut = cols if isinstance(cols, slice) else slice(None)
        if isinstance(rows, slice):
            r0, r1, step = rows.indices(self.shape[0])
            if step == 1:
                out = self._span(r0, r1, cut)
            else:
                out = self._gather(np.arange(r0, r1, step))[:, cut]
        elif np.ndim(rows) == 0:
            row = operator.index(np.asarray(rows).item())
            row = row + self.shape[0] if row < 0 else row
            if not 0 <= row < self.shape[0]:
                raise IndexError(f"row {rows} is outside 0..{self.shape[0] - 1}")
            out = self._span(row, row + 1, cut)[0]
        else:
            if not isinstance(cols, slice) and np.ndim(cols) != 0:
                raise IndexError("index the rows, then the columns, of a band array")
            out = self._gather(np.asarray(rows))[:, cut]
        if not isinstance(cols, slice):
            out = out[..., cols]
        out.flags.writeable = False
        return out
