"""The raster passes' ``MaxZRaster``: numba's scan and fold wherever the kernels are on.

``KernelRaster`` folds the same candidates at the same points as ``MaxZRaster``, so its planes
are numpy's bit for bit; it buffers none of them, so an open fold holds 11 bytes a texel where
numpy's scan holds its sample grids. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.maxz_raster import RASTER_FLUSH, MaxZRaster
from mapgen.jit import kernels_on
from satisfactory_mcp.core.arrays import F32Grid, I64Grid

if TYPE_CHECKING:
    from mapgen.terrain.maxz.kernels import Fold, Planes

__all__ = ["KernelRaster", "max_z_raster"]

#: numpy's weak Python floats in ``add`` and ``_scan``, after the origin, scale and sample.
_CONSTANTS = (0.5, 1.0, -1e-6, 1e-12)
#: The rows handed to the kernel when every triangle is drawn.
_EVERY = np.empty(0, np.int32)


class KernelRaster(MaxZRaster):
    """``MaxZRaster`` with its scan and fold on numba: the same planes, bit for bit.

    The fold is open per texel (its height, source and mark) beside the list of texels it
    marked; ``fold_heights`` closes it into the planes.
    """

    def __init__(
        self,
        width: int,
        height: int,
        x0_cm: float,
        y0_cm: float,
        scale: float,
        *,
        sample: float = 0.0,
        ceiling: F32Grid | None = None,
        row0: int = 0,
    ) -> None:
        super().__init__(
            width, height, x0_cm, y0_cm, scale, sample=sample, ceiling=ceiling, row0=row0
        )
        texels = width * height
        if texels > np.iinfo(np.int32).max:
            raise ValueError(f"{texels} texels: the fold indexes them in int32")
        lid = np.empty(0, np.float32) if self.ceiling is None else self.ceiling
        self._planes: Planes = (self.z, self.source_id, np.ascontiguousarray(lid))
        self._fold: Fold | None = None
        self._frames: dict[np.dtype, NDArray[np.floating]] = {}

    def _open_fold(self) -> Fold:
        """The open fold, made on the first ``add`` after the last ``fold_heights``."""
        if self._fold is None:
            texels = self.width * self.height
            self._fold = (
                np.empty(texels, np.float32),
                np.empty(texels, np.uint16),
                np.zeros(texels, np.uint8),
                np.empty(texels, np.int32),
                np.zeros(2, np.int64),
            )
        return self._fold

    def _frame(self, dtype: np.dtype) -> NDArray[np.floating]:
        if dtype not in self._frames:
            values = (self.origin_x_cm, self.origin_y_cm, self.scale, self.sample, *_CONSTANTS)
            self._frames[dtype] = np.array(values, dtype)
        return self._frames[dtype]

    def add(self, tri: NDArray[np.floating], source_id: int) -> None:
        triangles = len(tri)
        self.add_indexed(
            np.reshape(tri, (-1, 3)), np.arange(3 * triangles).reshape(-1, 3), source_id
        )

    def add_indexed(
        self,
        world: NDArray[np.floating],
        tris: I64Grid,
        source_id: int,
        rows: NDArray[np.integer] | None = None,
    ) -> None:
        from mapgen.terrain.maxz import kernels

        if world.dtype.type not in (np.float32, np.float64):
            raise TypeError(f"vertices of {world.dtype}: the kernels scan float32 or float64")
        verts = np.ascontiguousarray(world).reshape(-1, 3)
        picked = _EVERY if rows is None else np.ascontiguousarray(rows, np.int32)
        kernels.scan(
            verts,
            (np.ascontiguousarray(tris, np.int64), picked, rows is None, world.shape[-2]),
            self._frame(verts.dtype),
            (self.width, self.height, self.row0),
            np.uint16(source_id),
            self._planes,
            self._open_fold(),
            RASTER_FLUSH,
        )

    def fold_heights(self) -> None:
        """The open fold closed into the planes, and its memory let go until the next add."""
        if self._fold is not None:
            from mapgen.terrain.maxz import kernels

            kernels.commit(self._planes, self._fold)
            self._fold = None


def max_z_raster(
    width: int,
    height: int,
    x0_cm: float,
    y0_cm: float,
    scale: float,
    *,
    sample: float = 0.0,
    ceiling: F32Grid | None = None,
    row0: int = 0,
) -> MaxZRaster:
    """A ``KernelRaster`` where the kernels are on and the ceiling is float32, else numpy's."""
    fits = ceiling is None or ceiling.dtype == np.float32
    kind = KernelRaster if kernels_on() and fits else MaxZRaster
    return kind(width, height, x0_cm, y0_cm, scale, sample=sample, ceiling=ceiling, row0=row0)
