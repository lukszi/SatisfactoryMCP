"""The max-Z scatter rasteriser that cliffs, crown sprites and render meshes share."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid, I64Grid, U16Grid, U32Grid

__all__ = [
    "INSTANCE_BATCH",
    "RASTER_FLUSH",
    "MaxZRaster",
]


#: The rasteriser's scatter buffer, in candidate texels. Bounded so a 21,000-placement run
#: holds a few hundred MB rather than the whole 120 M-triangle scatter at once.
RASTER_FLUSH = 6_000_000

#: Instances of one mesh transformed per ``add``, so a foliage set is stamped in batches.
INSTANCE_BATCH = 512


class MaxZRaster:
    """Scatter-max rasteriser over the landscape frame: the highest triangle wins a texel.

    Triangles arrive faster than they can be reduced -- 120 M of them across the placements
    -- so candidates are buffered and folded in batches by a lexsort on (texel, z) and a
    take-last.

    ``sample`` is where in a texel, in texels, its value is taken: 0 at the vertex
    ``origin + col * scale``, which is where ``heightfield`` reads every plane; 0.5 at the
    texel centre, which is a render's pixel.
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
    ) -> None:
        self.width, self.height = width, height
        self.origin_x_cm, self.origin_y_cm, self.scale = x0_cm, y0_cm, scale
        self.sample = sample
        self.z: F32Grid = np.full(height * width, -np.inf, dtype=np.float32)
        self.source_id: U16Grid = np.zeros(height * width, dtype=np.uint16)
        self.density: U32Grid = np.zeros(height * width, dtype=np.uint32)
        self._pending_texels: list[I64Grid] = []
        self._pending_heights: list[F32Grid] = []
        self._pending_sources: list[U16Grid] = []
        self._pending_count = 0
        self._pending_samples: list[I64Grid] = []
        self._pending_sample_count = 0

    def count_samples(self, points: NDArray[np.floating]) -> None:
        """Record which texel each SOURCE VERTEX landed in. The density plane, accumulated.

        Not the fold's question: the fold answers every texel a triangle covers, however
        large the triangle, while this counts only the texels the geometry sampled. A texel
        with no samples still has a height, and that height is a plane interpolation.

        A vertex counts for the texel whose sample point is nearest it, the convention
        ``add`` writes heights under; two would put density half a texel off its heights.
        """
        shift = 0.5 - self.sample
        col = np.floor((points[:, 0] - self.origin_x_cm) / self.scale + shift).astype(np.int64)
        row = np.floor((points[:, 1] - self.origin_y_cm) / self.scale + shift).astype(np.int64)
        ok = (col >= 0) & (col < self.width) & (row >= 0) & (row < self.height)
        if not ok.any():
            return
        self._pending_samples.append(row[ok] * self.width + col[ok])
        self._pending_sample_count += int(ok.sum())
        if self._pending_sample_count > RASTER_FLUSH:
            self.fold_samples()

    def fold_samples(self) -> None:
        """Reduce the buffered sample texels into the density plane.

        Sorted and run-length counted rather than ``bincount``-ed: a bincount over the frame
        allocates a 43-million-element temporary on every one of dozens of folds.
        """
        if not self._pending_samples:
            return
        texels = np.concatenate(self._pending_samples)
        self._pending_samples, self._pending_sample_count = [], 0
        unique, counts = np.unique(texels, return_counts=True)
        self.density[unique] += counts.astype(np.uint32)

    def _on_fold(self, texels: I64Grid, sources: U16Grid) -> None:
        """Every buffered candidate, before the fold keeps each texel's highest one."""

    def fold_heights(self) -> None:
        """Reduce the buffered candidates into the height and source planes."""
        if not self._pending_texels:
            return
        texels = np.concatenate(self._pending_texels)
        heights = np.concatenate(self._pending_heights)
        sources = np.concatenate(self._pending_sources)
        self._pending_texels, self._pending_heights, self._pending_sources = [], [], []
        self._pending_count = 0
        self._on_fold(texels, sources)
        order = np.lexsort((heights, texels))
        texels, heights, sources = texels[order], heights[order], sources[order]
        last = np.empty(texels.size, bool)
        last[-1] = True
        last[:-1] = texels[1:] != texels[:-1]
        texels, heights, sources = texels[last], heights[last], sources[last]
        better = heights > self.z[texels]
        self.z[texels[better]] = heights[better]
        self.source_id[texels[better]] = sources[better]

    def add(self, tri: NDArray[np.floating], source_id: int) -> None:
        """Buffer every texel covered by ``tri`` (M, 3, 3) in world cm, with its plane Z.

        Bucketed by bounding-box span so one vectorised barycentric test runs over a whole
        bucket at a fixed candidate-grid size, instead of every triangle paying for the
        largest one's box.
        """
        fx = (tri[:, :, 0] - self.origin_x_cm) / self.scale
        fy = (tri[:, :, 1] - self.origin_y_cm) / self.scale
        z = tri[:, :, 2]
        box_col0 = np.floor(fx.min(1) - 0.5)
        box_col1 = np.ceil(fx.max(1) + 0.5)
        box_row0 = np.floor(fy.min(1) - 0.5)
        box_row1 = np.ceil(fy.max(1) + 0.5)
        span = np.maximum(box_col1 - box_col0, box_row1 - box_row0).astype(np.int32)
        for size in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            pick = (span <= size) & (span > (size // 2 if size > 1 else 0))
            if not pick.any():
                continue
            steps = np.arange(size + 1, dtype=np.float32)
            ox, oy = np.meshgrid(steps, steps)
            gx = box_col0[pick][:, None] + ox.ravel()[None, :] + self.sample
            gy = box_row0[pick][:, None] + oy.ravel()[None, :] + self.sample
            ax, ay = fx[pick, 0][:, None], fy[pick, 0][:, None]
            bx, by = fx[pick, 1][:, None], fy[pick, 1][:, None]
            cx, cy = fx[pick, 2][:, None], fy[pick, 2][:, None]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            l3 = 1.0 - l1 - l2
            col = np.floor(gx).astype(np.int32)
            row = np.floor(gy).astype(np.int32)
            ok = (
                (l1 >= -1e-6)
                & (l2 >= -1e-6)
                & (l3 >= -1e-6)
                & (col >= 0)
                & (col < self.width)
                & (row >= 0)
                & (row < self.height)
            )
            if not ok.any():
                continue
            plane = l1 * z[pick, 0][:, None] + l2 * z[pick, 1][:, None] + l3 * z[pick, 2][:, None]
            self._pending_texels.append(row[ok].astype(np.int64) * self.width + col[ok])
            self._pending_heights.append(plane[ok].astype(np.float32))
            self._pending_sources.append(np.full(int(ok.sum()), source_id, np.uint16))
            self._pending_count += int(ok.sum())
        if self._pending_count > RASTER_FLUSH:
            self.fold_heights()

    def result(self) -> tuple[F32Grid, U16Grid, U32Grid]:
        """The height plane in cm (``nan`` where nothing landed), the sources, the density."""
        self.fold_heights()
        self.fold_samples()
        z = self.z.reshape(self.height, self.width)
        return (
            np.where(np.isfinite(z), z, np.nan).astype(np.float32),
            self.source_id.reshape(self.height, self.width),
            self.density.reshape(self.height, self.width),
        )
