"""The field's byte codec: one implementation for the generator and the loader alike."""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path

import numpy as np

__all__ = [
    "ZLIB_LEVEL",
    "decode_i16",
    "decode_u8",
    "decode_u16",
    "encode_i16",
    "encode_u8",
    "encode_u16",
]

ZLIB_LEVEL = 6


def encode_i16(grid: np.ndarray) -> bytes:
    """One int16 raster to bytes: row-delta, then zlib.

    ``prepend=0`` makes the first column its own absolute value, so a row decodes from its
    own bytes and a corrupt stream cannot shift the whole field by a constant. The
    subtraction is done in int32 and truncated back: two int16 values can differ by more
    than an int16 holds -- a cliff top beside a no-data texel does -- and the truncation is
    the two's-complement wrap ``decode_i16`` undoes, so the pair is exact for every input.
    """
    if grid.dtype != np.int16:
        raise TypeError(f"expected an int16 raster, got {grid.dtype}")
    delta = np.diff(grid.astype(np.int32), axis=1, prepend=0).astype(np.int16)
    return zlib.compress(delta.tobytes(), ZLIB_LEVEL)


def decode_i16(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_i16``, given the shape the sidecar records.

    The shape lives in ``meta.json`` beside the georeference and not in the stream, so a
    raster whose length disagrees with it is a mismatched pair rather than a raster to
    reshape into whatever fits.

    Summed in int16 on purpose: the wrap is the inverse of ``encode_i16``'s truncation, and a
    wider accumulator peaked at 1.2 GB per decode, enough to exhaust memory under ``-n auto``.
    """
    delta = np.frombuffer(zlib.decompress(blob), dtype="<i2")
    if delta.size != height * width:
        raise ValueError(
            f"height raster is {delta.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return np.cumsum(delta.reshape(height, width), axis=1, dtype=np.int16)


def encode_u16(grid: np.ndarray) -> bytes:
    """One uint16 raster to bytes: the int16 codec over the same bits, so the wrap is exact."""
    if grid.dtype != np.uint16:
        raise TypeError(f"expected a uint16 raster, got {grid.dtype}")
    return encode_i16(np.ascontiguousarray(grid).view(np.int16))


def decode_u16(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_u16``."""
    return decode_i16(blob, height, width).view(np.uint16)


def encode_u8(grid: np.ndarray) -> bytes:
    """One uint8 raster to bytes: plain zlib, no delta."""
    if grid.dtype != np.uint8:
        raise TypeError(f"expected a uint8 raster, got {grid.dtype}")
    return zlib.compress(grid.tobytes(), ZLIB_LEVEL)


def decode_u8(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_u8``."""
    flat = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    if flat.size != height * width:
        raise ValueError(
            f"provenance raster is {flat.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return flat.reshape(height, width)


DECODERS = {"i16": decode_i16, "u8": decode_u8, "u16": decode_u16}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
