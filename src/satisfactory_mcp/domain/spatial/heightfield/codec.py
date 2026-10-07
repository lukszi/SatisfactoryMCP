"""The field's byte codec: one implementation for the generator and the loader alike."""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from ....core.arrays import I16Grid, U8Grid, U16Grid

__all__ = [
    "ZLIB_LEVEL",
    "RasterGrid",
    "decode_i16",
    "decode_u8",
    "decode_u16",
    "encode_i16",
    "encode_u8",
    "encode_u16",
]

ZLIB_LEVEL = 6

#: A decoded plane: int16 decimetres, a uint8 code, or the terrain plane's raw uint16.
RasterGrid: TypeAlias = NDArray[np.int16 | np.uint8 | np.uint16]


def encode_i16(grid: I16Grid) -> bytes:
    """One int16 raster to bytes: row-delta, then zlib.

    ``prepend=0`` makes each row decode from its own bytes. The delta is taken in int32 and
    truncated back, a two's-complement wrap that ``decode_i16`` undoes exactly, because two
    int16 values (a cliff top beside a no-data texel) can differ by more than an int16 holds.
    """
    if grid.dtype != np.int16:
        raise TypeError(f"expected an int16 raster, got {grid.dtype}")
    delta = np.diff(grid.astype(np.int32), axis=1, prepend=0).astype(np.int16)
    return zlib.compress(delta.tobytes(), ZLIB_LEVEL)


def decode_i16(blob: bytes, height: int, width: int) -> I16Grid:
    """The inverse of ``encode_i16``, given the shape the sidecar records.

    Summed in int16 on purpose: the wrap undoes ``encode_i16``'s truncation, and a wider
    accumulator peaked at 1.2 GB per decode, enough to exhaust memory under ``-n auto``.
    """
    delta = np.frombuffer(zlib.decompress(blob), dtype="<i2")
    if delta.size != height * width:
        raise ValueError(
            f"height raster is {delta.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return np.cumsum(delta.reshape(height, width), axis=1, dtype=np.int16)


def encode_u16(grid: U16Grid) -> bytes:
    """One uint16 raster to bytes: the int16 codec over the same bits, so the wrap is exact."""
    if grid.dtype != np.uint16:
        raise TypeError(f"expected a uint16 raster, got {grid.dtype}")
    return encode_i16(np.ascontiguousarray(grid).view(np.int16))


def decode_u16(blob: bytes, height: int, width: int) -> U16Grid:
    """The inverse of ``encode_u16``."""
    return decode_i16(blob, height, width).view(np.uint16)


def encode_u8(grid: U8Grid) -> bytes:
    """One uint8 raster to bytes: plain zlib, no delta."""
    if grid.dtype != np.uint8:
        raise TypeError(f"expected a uint8 raster, got {grid.dtype}")
    return zlib.compress(grid.tobytes(), ZLIB_LEVEL)


def decode_u8(blob: bytes, height: int, width: int) -> U8Grid:
    """The inverse of ``encode_u8``."""
    flat = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    if flat.size != height * width:
        raise ValueError(
            f"provenance raster is {flat.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return flat.reshape(height, width)


#: The decoder for each plane kind, keyed as ``store.PlaneStore`` names them.
DECODERS: dict[str, Callable[[bytes, int, int], RasterGrid]] = {
    "i16": decode_i16,
    "u8": decode_u8,
    "u16": decode_u16,
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
