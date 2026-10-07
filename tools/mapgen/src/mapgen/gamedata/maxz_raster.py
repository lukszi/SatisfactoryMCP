"""The max-Z scatter rasteriser that cliffs, crown sprites and render meshes share."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid, I64Grid, U16Grid, U32Grid

__all__ = [
    "INSTANCE_BATCH",
    "MAX_SPAN",
    "RASTER_FLUSH",
    "MaxZRaster",
]


#: The rasteriser's scatter buffer, in candidate texels. Bounded so a 21,000-placement run
#: holds a few hundred MB rather than the whole 120 M-triangle scatter at once.
RASTER_FLUSH = 6_000_000

#: Instances of one mesh transformed per ``add``, so a foliage set is stamped in batches.
INSTANCE_BATCH = 512

#: The candidate-grid sizes ``add`` buckets triangles into, by bounding-box span in texels.
_BUCKETS = (1, 2, 4, 8, 16, 32, 64, 128, 256)
#: The widest bucket. A wider box is scanned in tiles of this many texels.
MAX_SPAN = _BUCKETS[-1]
#: Tiles of wide triangles scanned per vectorised pass: about a million sample points.
_WIDE_TILES = 16


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
        largest one's box. A box wider than ``MAX_SPAN`` is scanned in tiles of that size.
        """
        fx = (tri[:, :, 0] - self.origin_x_cm) / self.scale
        fy = (tri[:, :, 1] - self.origin_y_cm) / self.scale
        z = tri[:, :, 2]
        box_col0 = np.floor(fx.min(1) - 0.5)
        box_col1 = np.ceil(fx.max(1) + 0.5)
        box_row0 = np.floor(fy.min(1) - 0.5)
        box_row1 = np.ceil(fy.max(1) + 0.5)
        span = np.maximum(box_col1 - box_col0, box_row1 - box_row0).astype(np.int32)
        for size in _BUCKETS:
            pick = (span <= size) & (span > (size // 2 if size > 1 else 0))
            if pick.any():
                self._scan(fx[pick], fy[pick], z[pick], box_col0[pick], box_row0[pick], size,
                           size, source_id)  # fmt: skip
        wide = np.flatnonzero(span > MAX_SPAN)
        if wide.size:
            box = np.stack([box_col0, box_row0, box_col1, box_row1], axis=1)[wide]
            self._add_wide(fx[wide], fy[wide], z[wide], box, source_id)
        if self._pending_count > RASTER_FLUSH:
            self.fold_heights()

    def _add_wide(self, fx: NDArray[np.floating], fy: NDArray[np.floating],
                  z: NDArray[np.floating], box: NDArray[np.floating],
                  source_id: int) -> None:  # fmt: skip
        """Scan each wide triangle's box, clipped to the raster, in tiles of ``MAX_SPAN``.

        A tile tests the same sample points against the same triangle as one unbounded
        bucket would, so the texels and heights do not depend on the tiling.
        """
        tile_w, tile_h = min(MAX_SPAN, self.width - 1), min(MAX_SPAN, self.height - 1)
        first = np.maximum(box[:, :2], 0)
        last = np.minimum(box[:, 2:], [self.width - 1, self.height - 1])
        owners: list[NDArray[np.intp]] = []
        corners: list[NDArray[np.floating]] = []
        for k in np.flatnonzero((first <= last).all(axis=1)):
            cols = np.arange(first[k, 0], last[k, 0] + 1, tile_w + 1, dtype=box.dtype)
            rows = np.arange(first[k, 1], last[k, 1] + 1, tile_h + 1, dtype=box.dtype)
            grid = np.stack([axis.ravel() for axis in np.meshgrid(cols, rows)], axis=1)
            owners.append(np.full(len(grid), k, np.intp))
            corners.append(grid)
        if not owners:
            return
        owner, corner = np.concatenate(owners), np.concatenate(corners)
        for start in range(0, len(owner), _WIDE_TILES):
            pick, at = owner[start : start + _WIDE_TILES], corner[start : start + _WIDE_TILES]
            self._scan(fx[pick], fy[pick], z[pick], at[:, 0], at[:, 1], tile_w, tile_h,
                       source_id)  # fmt: skip
            if self._pending_count > RASTER_FLUSH:
                self.fold_heights()

    def _scan(self, fx: NDArray[np.floating], fy: NDArray[np.floating],
              z: NDArray[np.floating], col0: NDArray[np.floating], row0: NDArray[np.floating],
              cols: int, rows: int, source_id: int) -> None:  # fmt: skip
        """Buffer the sample points of a ``cols + 1`` by ``rows + 1`` grid from each
        ``(col0, row0)`` that fall inside that row's triangle."""
        ox, oy = np.meshgrid(np.arange(cols + 1, dtype=np.float32),
                             np.arange(rows + 1, dtype=np.float32))  # fmt: skip
        gx = col0[:, None] + ox.ravel()[None, :] + self.sample
        gy = row0[:, None] + oy.ravel()[None, :] + self.sample
        ax, ay = fx[:, 0][:, None], fy[:, 0][:, None]
        bx, by = fx[:, 1][:, None], fy[:, 1][:, None]
        cx, cy = fx[:, 2][:, None], fy[:, 2][:, None]
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
            return
        plane = l1 * z[:, 0][:, None] + l2 * z[:, 1][:, None] + l3 * z[:, 2][:, None]
        self._pending_texels.append(row[ok].astype(np.int64) * self.width + col[ok])
        self._pending_heights.append(plane[ok].astype(np.float32))
        self._pending_sources.append(np.full(int(ok.sum()), source_id, np.uint16))
        self._pending_count += int(ok.sum())

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
