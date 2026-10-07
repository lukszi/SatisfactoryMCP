"""A band's wet pixels: the colour under the water is painted on those alone.

docs/map/renders.md section 26, "Painting only the wet pixels".
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TypeGuard, TypeVar, cast

import numpy as np
from numpy.typing import NDArray

from mapgen.palette.scene import FloatGrid

__all__ = ["WET_MOST", "EveryPixel", "WetPixels", "cover_mix", "float32_planes"]

#: Past this share of wet pixels a band's water is painted whole: gathering its planes then
#: costs more than the dry pixels' arithmetic it saves (about 1.3 times the whole band's on a
#: band that is all water).
WET_MOST = 0.75

_Scalar = TypeVar("_Scalar", bound=np.generic)
_Key = TypeVar("_Key")


def cover_mix(land: FloatGrid, under: FloatGrid, cover: FloatGrid) -> FloatGrid:
    """``land * (1 - cover) + under * cover``; ``cover`` has the trailing channel axis."""
    return land * (1.0 - cover) + under * cover


def float32_planes(*planes: object) -> bool:
    """Whether every plane is a float32 array or number, what a water kernel takes; with any
    other the numpy painter runs, whose float types follow its inputs'."""
    return all(
        isinstance(plane, np.ndarray | np.generic) and plane.dtype == np.float32 for plane in planes
    )


class WetPixels:
    """The pixels of a band whose water cover is not 0, and the band's planes read there.

    A plane is the band's when its first two axes are the band's: ``take`` lays its values at
    the wet pixels along one axis, so per-pixel arithmetic runs on them unchanged. ``whole``
    says the band is too wet for that to pay.
    """

    def __init__(self, cover: FloatGrid) -> None:
        self.shape = cover.shape
        self.index = np.flatnonzero(cover)
        self.whole = self.index.size > WET_MOST * cover.size

    def owns(self, plane: object) -> TypeGuard[NDArray[np.generic]]:
        """Whether ``plane`` is one of the band's, rather than a colour or a number."""
        return isinstance(plane, np.ndarray) and plane.shape[:2] == self.shape

    def take(self, plane: NDArray[_Scalar]) -> NDArray[_Scalar]:
        """The plane's values at the wet pixels, its trailing axes kept."""
        return np.take(plane.reshape(-1, *plane.shape[2:]), self.index, axis=0)

    def take_planes(
        self, planes: Mapping[_Key, object], keys: Collection[_Key] | None = None
    ) -> dict[_Key, object]:
        """``planes`` with the band's planes taken, in nested mappings too; with ``keys``,
        only those of its keys, so a read of any other fails rather than mixes shapes."""
        return {
            key: self._taken(value) for key, value in planes.items() if keys is None or key in keys
        }

    def _taken(self, value: object) -> object:
        if self.owns(value):
            return self.take(value)
        if isinstance(value, Mapping):
            return self.take_planes(cast("Mapping[object, object]", value))
        return value

    def mix(
        self, land: FloatGrid, wet_land: FloatGrid, under: FloatGrid, cover: FloatGrid
    ) -> FloatGrid:
        """``land`` with ``cover_mix`` written over its wet pixels, from ``wet_land`` (its
        values there) and the taken ``under`` and ``cover``. The dry pixels keep ``land``,
        which is what the mix gives them, ``land * 1 + under * 0``."""
        done = cover_mix(wet_land, under, cover)
        out = land.astype(done.dtype, order="C")
        out.reshape(-1, *out.shape[2:])[self.index] = done
        return out


class EveryPixel(WetPixels):
    """Every pixel of a band, in its order: a plane is taken as a flat view where it can be."""

    def __init__(self, shape: tuple[int, ...]) -> None:
        super().__init__(np.ones(shape, np.float32))

    def take(self, plane: NDArray[_Scalar]) -> NDArray[_Scalar]:
        """The plane laid flat, its trailing axes kept."""
        return plane.reshape(-1, *plane.shape[2:])
