"""A banded raster: each band's sub-samples folded onto the output grid, and the bands stored.

The direct pass, the top pass and the render-only meshes rasterise a band at a time into the
render's own grid (``terrain.rasters``, ``terrain.top_raster``, ``terrain.render_meshes``);
``write_banded_raster`` folds each band and writes it into the cache the run reads back.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Protocol, TypeAlias, TypeVar

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import (
    CACHE_SIDECAR_NAME,
    DIRECT_BAND_ROWS,
    DIRECT_COVERAGE_NAME,
    DIRECT_FAMILY_NAME,
    DIRECT_Z_NAME,
    PLANE_DTYPES,
    STORAGE_BANDS,
    DirectStamp,
    band_spans,
    rewrite_planes,
    write_sidecar,
)
from mapgen.gamedata.frame import BOUNDS_M
from satisfactory_mcp.core.arrays import F32Grid, U8Grid, U16Grid

__all__ = [
    "DIRECT_RASTER_ROLE",
    "RASTER_THREADS",
    "TOP_RASTER_ROLE",
    "BandPlanes",
    "BandRaster",
    "RasterStats",
    "fold_band",
    "in_band_order",
    "pixel_coverage",
    "raster_threads",
    "reduce_direct",
    "reduce_source",
    "write_banded_raster",
]

#: What a direct or top cache's sidecar says it holds.
DIRECT_RASTER_ROLE = (
    "max-Z of the cliff geometry on this render's own grid, in world centimetres, "
    "with the count of sub-samples that hit something beside it. Written once and "
    "read by every layer; deleted at the end of the run unless --keep-direct."
)


#: Bands a raster pass rasterises at once, fewer on fewer cores (docs/map/renders.md
#: section 20): the bands are independent and their kernels release the GIL.
RASTER_THREADS = 16

_Span = TypeVar("_Span")
_Band = TypeVar("_Band")


TOP_RASTER_ROLE = (
    "max-Z of the arches and foliage boulders on this render's own grid, in world "
    "centimetres, with the count of sub-samples that hit something beside it. Written once "
    "and read by every layer; deleted at the end of the run unless --keep-direct."
)


class RasterStats(DirectStamp):
    """A direct or top cache's sidecar: its stamp, and what the pass wrote."""

    storage: str
    sub_texel_m: float
    texels_with_geometry: int
    share_of_the_sheet: float
    seconds: float
    band_rows: int
    role: str


#: A band its rasteriser has folded onto the output grid itself: plane name -> rows.
BandPlanes: TypeAlias = dict[str, NDArray[np.generic]]


class BandRaster(Protocol):
    """One band of a banded raster: max-Z in cm, and with it the winning source per sample;
    or the band's planes, already on the output grid."""

    def __call__(
        self, x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int, subsamples: int, /
    ) -> F32Grid | tuple[F32Grid, U16Grid] | BandPlanes: ...


def raster_threads() -> int:
    """``RASTER_THREADS``, but no more than the cores."""
    return max(1, min(RASTER_THREADS, os.cpu_count() or 1))


def in_band_order(
    band: Callable[[_Span], _Band], spans: Iterable[_Span], threads: int
) -> Iterator[_Band]:
    """``band`` over ``spans`` on ``threads`` threads, yielded in the spans' order, at most
    ``threads`` bands past the one waited on. One thread is a plain loop."""
    if threads <= 1:
        yield from map(band, spans)
        return
    pending: deque[Future[_Band]] = deque()
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="raster") as pool:
        try:
            for span in spans:
                pending.append(pool.submit(band, span))
                if len(pending) > threads:
                    yield pending.popleft().result()
            while pending:
                yield pending.popleft().result()
        finally:
            for future in pending:
                future.cancel()


def reduce_direct(sub_z: F32Grid, rows: int, cols: int, subsamples: int) -> tuple[F32Grid, U8Grid]:
    """A sub-sampled band folded onto the output grid: mean height and coverage count.

    The mean is over the sub-samples that HIT something and the count comes back beside it,
    which is the difference between "half a rock and half the ground behind it" and "a rock
    at half its height".
    """
    if subsamples == 1:
        hit = np.isfinite(sub_z)
        return np.where(hit, sub_z, 0.0).astype(np.float32), hit.astype(np.uint8)
    block = sub_z.reshape(rows, subsamples, cols, subsamples)
    hit = np.isfinite(block)
    count = hit.sum((1, 3)).astype(np.uint8)
    total = np.where(hit, block, 0.0).sum((1, 3), dtype=np.float32)
    return (total / np.maximum(count, 1)).astype(np.float32), count


def reduce_source(
    sub_z: F32Grid, sub_source: NDArray[np.integer], rows: int, cols: int, subsamples: int
) -> U8Grid:
    """The source id of each output texel's highest sub-sample; 0 where nothing fell."""
    hit = np.isfinite(sub_z)
    if subsamples == 1:
        return np.where(hit, sub_source, 0).astype(np.uint8)
    z = np.where(hit, sub_z, -np.inf).reshape(rows, subsamples, cols, subsamples)
    src = np.where(hit, sub_source, 0).reshape(rows, subsamples, cols, subsamples)
    z = z.transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    src = src.transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    best = np.take_along_axis(src, z.argmax(-1)[..., None], -1)[..., 0]
    return best.astype(np.uint8)


def fold_band(
    sub: F32Grid | tuple[F32Grid, U16Grid] | BandPlanes, rows: int, cols: int, subsamples: int
) -> BandPlanes:
    """A band raster's answer as the planes of its cache, on the output grid."""
    if isinstance(sub, dict):
        return sub
    sub_z, sub_source = sub if isinstance(sub, tuple) else (sub, None)
    band_z, band_coverage = reduce_direct(sub_z, rows, cols, subsamples)
    planes: BandPlanes = {DIRECT_Z_NAME: band_z, DIRECT_COVERAGE_NAME: band_coverage}
    if sub_source is not None:
        planes[DIRECT_FAMILY_NAME] = reduce_source(sub_z, sub_source, rows, cols, subsamples)
    return planes


def pixel_coverage(coverage: NDArray[np.generic], subsamples: int) -> F32Grid:
    """The share of a pixel's sub-samples a triangle hit, in [0, 1]. No neighbour is read."""
    return coverage.astype(np.float32) / np.float32(subsamples * subsamples)


def write_banded_raster(
    band_raster: BandRaster,
    directory: Path,
    size: int,
    subsamples: int,
    stamp: DirectStamp,
    progress: bool,
    *,
    role: str = DIRECT_RASTER_ROLE,
    threads: int | None = None,
) -> RasterStats:
    """Rasterise every placed rock into the render's own grid, ``DIRECT_BAND_ROWS`` at a time,
    into a cache ``cached_raster`` reads back only under the same ``stamp``.

    A ``band_raster`` that returns ``(z, source)`` also writes the family plane, and one that
    returns ``BandPlanes`` writes those; the first band says which, before any plane is
    opened, and every band after it must agree. The bands are rasterised on ``threads``
    (``raster_threads()`` when None) and written in their order.
    """
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    x0_cm = BOUNDS_M["x_min_m"] * 100
    started = time.time()

    def band_at(span: tuple[int, int]) -> BandPlanes:
        top, bottom = span
        y0_cm = BOUNDS_M["y_min_m"] * 100 + top * step_cm
        sub = band_raster(x0_cm, y0_cm, step_cm, bottom - top, size, subsamples)
        return fold_band(sub, bottom - top, size, subsamples)

    spans = list(band_spans(size, DIRECT_BAND_ROWS))
    bands = in_band_order(band_at, spans, raster_threads() if threads is None else threads)
    first = next(bands)
    names = tuple(first)
    covered = 0
    with rewrite_planes(directory, names, size, DIRECT_BAND_ROWS, clear=PLANE_DTYPES) as planes:
        for band, (top, bottom) in enumerate(spans):
            got = first if band == 0 else next(bands)
            if tuple(got) != names:
                raise ValueError(f"band {band} of {directory.name} changed what it returns")
            for plane, name in zip(planes, names, strict=True):
                plane.write(top, got[name])
            covered += int(np.count_nonzero(got[DIRECT_COVERAGE_NAME]))
            if progress and band % 8 == 0:
                print(
                    f"  {directory.name}: {bottom / size:5.1%} of {size}x{size} at "
                    f"{step_cm / 100 / subsamples:.4f} m, {covered / 1e6:.1f} M texels, "
                    f"{time.time() - started:5.1f}s",
                    flush=True,
                )
    stats: RasterStats = {
        **stamp,
        "storage": STORAGE_BANDS,
        "sub_texel_m": round(step_cm / 100 / subsamples, 5),
        "texels_with_geometry": covered,
        "share_of_the_sheet": round(100 * covered / (size * size), 3),
        "seconds": round(time.time() - started, 1),
        "band_rows": DIRECT_BAND_ROWS,
        "role": role,
    }
    write_sidecar(directory / CACHE_SIDECAR_NAME, stats)
    return stats
