"""What the readers and the cutter ask of Pillow and ``texture2ddecoder``, as protocols.

Both arrive as parameters, because the ``gen`` extra may not be imported at module scope
(DESIGN.md), so their shapes are stated here: exactly the calls made of them, which a test's
stand-in can satisfy as well as the real modules do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Self, TypedDict, TypeVar

__all__ = [
    "BlockDecoder",
    "ImageFactory",
    "ImageT",
    "LanczosFilter",
    "PngOptions",
    "SheetImage",
    "TileImage",
]


class BlockDecoder(Protocol):
    """``texture2ddecoder``: one level of BC blocks to BGRA bytes."""

    def decode_bc1(self, texture: bytes, width: int, height: int, /) -> bytes: ...

    def decode_bc3(self, texture: bytes, width: int, height: int, /) -> bytes: ...


class SheetImage(Protocol):
    """A Pillow image as the sheet reader stitches it: converted, then pasted into place."""

    def convert(self, mode: str, /) -> Self: ...

    def paste(self, im: Self, box: tuple[int, int], /) -> None: ...


#: The image type a factory makes, kept so a caller gets back the class it passed in.
ImageT = TypeVar("ImageT", bound=SheetImage)
_ImageT_co = TypeVar("_ImageT_co", covariant=True)


class ImageFactory(Protocol[_ImageT_co]):
    """``PIL.Image`` as the readers use it: a blank canvas, and an image over raw texels."""

    def new(self, mode: str, size: tuple[int, int], /) -> _ImageT_co: ...

    def frombytes(
        self, mode: str, size: tuple[int, int], data: bytes, decoder_name: str, /, *args: str
    ) -> _ImageT_co: ...


class PngOptions(TypedDict):
    """The keywords a tile is saved with."""

    format: str
    compress_level: int


class TileImage(Protocol):
    """A square Pillow image as the pyramid cutter uses it: resized, cropped, saved."""

    @property
    def width(self) -> int: ...

    def crop(self, box: tuple[int, int, int, int], /) -> Self: ...

    def resize(self, size: tuple[int, int], resample: int, /) -> Self: ...

    def save(self, fp: Path, /, *, format: str, compress_level: int) -> None: ...


class LanczosFilter(Protocol):
    """``PIL.Image`` as the cutter uses it: the one resampling filter every level is cut with."""

    @property
    def LANCZOS(self) -> int: ...
