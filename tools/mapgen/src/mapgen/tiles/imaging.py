"""Pillow as the cutters and pyramids use it: handed in rather than imported where it is used."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.imaging import ImageFactory, LanczosFilter

if TYPE_CHECKING:
    from PIL.Image import Image, Resampling

__all__ = ["TileImaging", "load_imaging"]


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
