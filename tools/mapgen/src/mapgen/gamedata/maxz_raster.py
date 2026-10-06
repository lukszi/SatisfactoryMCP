"""The max-Z scatter rasteriser that cliffs, crown sprites and render meshes share."""

from __future__ import annotations

import numpy as np

__all__ = [
    "RASTER_FLUSH",
    "MaxZRaster",
]


#: The rasteriser's scatter buffer, in candidate texels. Bounded so a 21,000-placement run
#: holds a few hundred MB rather than the whole 120 M-triangle scatter at once.
RASTER_FLUSH = 6_000_000


class MaxZRaster:
    """Scatter-max rasteriser over the landscape frame: the highest triangle wins a texel.

    Triangles arrive faster than they can be reduced -- 120 M of them across the placements
    -- so candidates are buffered and folded in batches by a lexsort on (texel, z) and a
    take-last.

    ``sample`` is where in a texel, in texels, its value is taken: 0 at the vertex
    ``x0 + col * scale``, which is where ``heightfield`` reads every plane; 0.5 at the
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
        self.x0, self.y0, self.scale = x0_cm, y0_cm, scale
        self.sample = sample
        self.z = np.full(height * width, -np.inf, dtype=np.float32)
        self.src = np.zeros(height * width, dtype=np.uint16)
        self.density = np.zeros(height * width, dtype=np.uint32)
        self._idx: list[np.ndarray] = []
        self._z: list[np.ndarray] = []
        self._s: list[np.ndarray] = []
        self._n = 0
        self._samples: list[np.ndarray] = []
        self._sample_n = 0

    def count_samples(self, points: np.ndarray) -> None:
        """Record which texel each SOURCE VERTEX landed in. The density plane, accumulated.

        Not the fold's question: the fold answers every texel a triangle covers, however
        large the triangle, while this counts only the texels the geometry sampled. A texel
        with no samples still has a height, and that height is a plane interpolation.

        A vertex counts for the texel whose sample point is nearest it, the convention
        ``add`` writes heights under; two would put density half a texel off its heights.
        """
        shift = 0.5 - self.sample
        col = np.floor((points[:, 0] - self.x0) / self.scale + shift).astype(np.int64)
        row = np.floor((points[:, 1] - self.y0) / self.scale + shift).astype(np.int64)
        ok = (col >= 0) & (col < self.width) & (row >= 0) & (row < self.height)
        if not ok.any():
            return
        self._samples.append(row[ok] * self.width + col[ok])
        self._sample_n += int(ok.sum())
        if self._sample_n > RASTER_FLUSH:
            self.flush_samples()

    def flush_samples(self) -> None:
        """Reduce the buffered sample texels into the density plane.

        Sorted and run-length counted rather than ``bincount``-ed: a bincount over the frame
        allocates a 43-million-element temporary on every one of dozens of flushes.
        """
        if not self._samples:
            return
        idx = np.concatenate(self._samples)
        self._samples, self._sample_n = [], 0
        unique, counts = np.unique(idx, return_counts=True)
        self.density[unique] += counts.astype(np.uint32)

    def flush(self) -> None:
        if not self._idx:
            return
        idx = np.concatenate(self._idx)
        z = np.concatenate(self._z)
        src = np.concatenate(self._s)
        self._idx, self._z, self._s, self._n = [], [], [], 0
        order = np.lexsort((z, idx))
        idx, z, src = idx[order], z[order], src[order]
        last = np.empty(idx.size, bool)
        last[-1] = True
        last[:-1] = idx[1:] != idx[:-1]
        idx, z, src = idx[last], z[last], src[last]
        better = z > self.z[idx]
        self.z[idx[better]] = z[better]
        self.src[idx[better]] = src[better]

    def add(self, tri: np.ndarray, source_id: int) -> None:
        """Buffer every texel covered by ``tri`` (M, 3, 3) in world cm, with its plane Z.

        Bucketed by bounding-box span so one vectorised barycentric test runs over a whole
        bucket at a fixed candidate-grid size, instead of every triangle paying for the
        largest one's box.
        """
        fx = (tri[:, :, 0] - self.x0) / self.scale
        fy = (tri[:, :, 1] - self.y0) / self.scale
        z = tri[:, :, 2]
        x0 = np.floor(fx.min(1) - 0.5)
        x1 = np.ceil(fx.max(1) + 0.5)
        y0 = np.floor(fy.min(1) - 0.5)
        y1 = np.ceil(fy.max(1) + 0.5)
        span = np.maximum(x1 - x0, y1 - y0).astype(np.int32)
        for size in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            pick = (span <= size) & (span > (size // 2 if size > 1 else 0))
            if not pick.any():
                continue
            steps = np.arange(size + 1, dtype=np.float32)
            ox, oy = np.meshgrid(steps, steps)
            gx = x0[pick][:, None] + ox.ravel()[None, :] + self.sample
            gy = y0[pick][:, None] + oy.ravel()[None, :] + self.sample
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
            self._idx.append(row[ok].astype(np.int64) * self.width + col[ok])
            self._z.append(plane[ok].astype(np.float32))
            self._s.append(np.full(int(ok.sum()), source_id, np.uint16))
            self._n += int(ok.sum())
        if self._n > RASTER_FLUSH:
            self.flush()

    def result(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        self.flush()
        self.flush_samples()
        z = self.z.reshape(self.height, self.width)
        return (
            np.where(np.isfinite(z), z, np.nan).astype(np.float32),
            self.src.reshape(self.height, self.width),
            self.density.reshape(self.height, self.width),
        )
