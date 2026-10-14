"""A light block's atlas cells: the ground with its arches and overhangs, the trees, the canopy.

``block_spans`` reads a block's window: the slabs the draw captured (``lighting/spans/slabs.py``)
and the trees (``lighting/spans/trees.py``). ``horizon_cells`` marches each direction's cells:
the ground's horizon; the trees', a porous medium whose shade is folded at the elevation the
sun's path has in that direction (``lighting/spans/porous.py``); the ground's horizon received
on the canopy top; and the band floating over the horizon, its shade folded there too
(``fold_band``).
``default_shade`` shades the ground's bands per cell for the default sun.
docs/map/light-and-crowns.md section 29, "Arches as spans", "A porous canopy" and
"Bands off the sun's path".
"""

from __future__ import annotations

from collections.abc import Container, Iterator
from pathlib import Path
from typing import NamedTuple, Protocol, TypeVar

import numpy as np
from scipy import ndimage

from mapgen.jit import gpu_on
from mapgen.lighting.horizon import FADE_M, HORIZON_DIRS, Fade, march_horizon
from mapgen.lighting.model import BAND_CELL, CANOPY_CELL, CROWN_CELL, SHADOW_SOFT_DEG, sun_cells
from mapgen.lighting.spans.holes import Holes, fill_holes
from mapgen.lighting.spans.march import (
    Bands,
    SpanSurface,
    march_spans,
    path_elevation,
    span_surface,
)
from mapgen.lighting.spans.porous import PorousSurface, fold, march_porous
from mapgen.lighting.spans.slabs import SlabStore
from mapgen.lighting.spans.trees import (
    CanopyPlanes,
    TreePlanes,
    canopy_cell,
    canopy_planes,
    tree_planes,
    trees_porous,
)
from mapgen.lighting.sun import Sun
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = [
    "BlockSpans",
    "Cell",
    "CellOps",
    "band_cover",
    "block_spans",
    "cell_shade",
    "default_shade",
    "fold_band",
    "horizon_cells",
    "open_fold",
    "plain_bands",
    "shade_cells",
]

_Window = tuple[int, int, int, int]

PlaneT = TypeVar("PlaneT")
BandsT = TypeVar("BandsT")


class BlockSpans(NamedTuple):
    """What casts on a block beyond its heights, at half resolution: the arches' and
    overhangs' spans over the ground (None where the window has none); the trees (None where
    none stands); and the solid ground, what blocks with the spans left out, which the
    canopy cells are marched over."""

    ground: SpanSurface | None
    trees: TreePlanes | None = None
    solid: F32Grid | None = None


def block_spans(
    work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid, slabs: SlabStore
) -> BlockSpans:
    """The spans that cast on a block whose window is ``window``, at half resolution."""
    found = slabs.half(window, z_half)
    ground = None if found is None else span_surface(z_half, found.solid, found.lo, found.hi)
    solid = z_half if found is None else found.solid
    return BlockSpans(ground, tree_planes(work, window, z_window, z_half), solid)


def band_cover(hz: F32Grid, bands: tuple[tuple[F32Grid, F32Grid], ...], el: float) -> F32Grid:
    """The share of the sun disc (``SHADOW_SOFT_DEG`` across, at ``el``) the bands hide above
    the horizon ``hz``: their union, two at most; 0 where none. Degrees throughout."""
    half = np.float32(SHADOW_SOFT_DEG / 2)
    floor = np.maximum(hz, np.float32(el) - half)
    ceil = np.float32(el) + half

    def part(lo: F32Grid, hi: F32Grid) -> F32Grid:
        span = np.minimum(hi, ceil) - np.maximum(lo, floor)
        return np.nan_to_num(np.clip(span, 0.0, np.float32(SHADOW_SOFT_DEG)), nan=0.0)

    cover = np.zeros(hz.shape, np.float32)
    for lo, hi in bands:
        cover += part(lo, hi)
    if len(bands) == 2:
        (a_lo, a_hi), (b_lo, b_hi) = bands
        cover -= part(np.maximum(a_lo, b_lo), np.minimum(a_hi, b_hi))
    return (cover / np.float32(SHADOW_SOFT_DEG)).astype(np.float32)


def cell_shade(hz: F32Grid, bands: tuple[tuple[F32Grid, F32Grid], ...], el: float) -> F32Grid:
    """The sun disc's share in shadow per cell: the horizon's soft edge, and the bands above."""
    soft = np.clip((hz - np.float32(el)) / np.float32(SHADOW_SOFT_DEG) + np.float32(0.5), 0, 1)
    return np.clip(soft + band_cover(hz, bands, el), 0, 1).astype(np.float32)


def fold_band(bands: Bands, el: float) -> F32Grid:
    """The band's shade at ``el``, the sun path's elevation in this direction, folded as the
    trees' is: ``el + soft * (cover - 1/2)``, ``el - soft / 2`` where it hides none. Exact for
    a sun on the path; the bake's own shade reads the band (``cell_shade``)."""
    cover = band_cover(bands.horizon, ((bands.lo, bands.hi),), el)
    found: F32Grid = np.float32(el) + np.float32(SHADOW_SOFT_DEG) * (cover - np.float32(0.5))
    return found.astype(np.float32)


def open_fold(el: float) -> np.float32:
    """``fold_band`` where the band hides none of the sun disc."""
    return np.float32(el) - np.float32(SHADOW_SOFT_DEG / 2)


class Cell(NamedTuple):
    """An atlas cell as marched: its index, its degrees as the atlas stores them, the bands it
    was made from (kept for a wanted ground cell, else None), and where it stores a band's or
    the trees' shade folded in (None for a horizon)."""

    k: int
    deg: F32Grid
    bands: Bands | None
    band_in: BoolMask | None


def _filled(bands: Bands, holes: Holes | None) -> Bands:
    """``bands`` with each hole given its nearest pixel's (``holes.fill_holes``)."""
    if holes is None:
        return bands
    seen = fill_holes(bands.seen.astype(np.float32), holes, 0.0) > 0
    nan = float("nan")
    return Bands(
        fill_holes(bands.horizon, holes, 0.0),
        fill_holes(bands.lo, holes, nan),
        fill_holes(bands.hi, holes, nan),
        seen,
    )


class CellOps(Protocol[PlaneT, BandsT]):
    """What ``horizon_cells`` does to a direction, on the host (``_OnHost``) or on the device
    (``lighting/spans/device.py``), and the planes it hands back to the host."""

    def plain(self, az_deg: float) -> PlaneT: ...
    def bands(self, surface: SpanSurface, az_deg: float, fade: Fade) -> BandsT: ...
    def path(self, bands: BandsT, el: float) -> PlaneT: ...
    def horizon(self, bands: BandsT) -> PlaneT: ...
    def band_in(self, cell: PlaneT, el: float) -> BoolMask: ...
    def porous(self, az_deg: float) -> F32Grid: ...
    def canopy(self, az_deg: float) -> F32Grid: ...
    def host(self, plane: PlaneT) -> F32Grid: ...
    def host_bands(self, bands: BandsT) -> Bands: ...


class _OnHost:
    """``CellOps`` in numpy, and numba's kernels where the switch says so."""

    def __init__(
        self,
        z_half: F32Grid,
        halo: int,
        spacing_m: float,
        holes: Holes | None,
        trees: tuple[PorousSurface, CanopyPlanes] | None = None,
    ) -> None:
        self.z_half, self.halo, self.spacing_m, self.holes = z_half, halo, spacing_m, holes
        self.trees = trees

    def plain(self, az_deg: float) -> F32Grid:
        found = march_horizon(self.z_half, self.halo, az_deg, self.spacing_m)
        return fill_holes(found, self.holes, 0.0)

    def bands(self, surface: SpanSurface, az_deg: float, fade: Fade) -> Bands:
        return _filled(march_spans(surface, self.halo, az_deg, self.spacing_m, fade), self.holes)

    def path(self, bands: Bands, el: float) -> F32Grid:
        return fold_band(bands, el)

    def horizon(self, bands: Bands) -> F32Grid:
        return bands.horizon

    def band_in(self, cell: F32Grid, el: float) -> BoolMask:
        """Where the band cell holds some of the band's shade."""
        found: BoolMask = cell > open_fold(el)
        return found

    def porous(self, az_deg: float) -> F32Grid:
        """The trees' optical depth toward ``az_deg`` (``porous.march_porous``), filled."""
        assert self.trees is not None
        depth = march_porous(self.trees[0], self.halo, az_deg, self.spacing_m)
        return fill_holes(depth, self.holes, 0.0)

    def canopy(self, az_deg: float) -> F32Grid:
        """The ground's horizon received on the canopy (``trees.canopy_cell``), filled."""
        assert self.trees is not None
        found = canopy_cell(self.trees[1], self.halo, az_deg, self.spacing_m)
        return fill_holes(found, self.holes, 0.0)

    def host(self, plane: F32Grid) -> F32Grid:
        return plane

    def host_bands(self, bands: Bands) -> Bands:
        return bands


def horizon_cells(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    spans: BlockSpans,
    holes: Holes | None = None,
    wanted: Container[int] | None = None,
) -> Iterator[Cell]:
    """Each direction's ground cell, then its trees' and its canopy's cells where the block
    has trees, and its band cell where it has spans, as the atlas stores them (``Cell``). A hole (``lighting/spans/holes.py``) takes
    the cells of the pixel nearest it. Only a ground cell in ``wanted`` (every one when None)
    comes with its bands. With the switch at CUDA the block's planes stay on the device
    (``_on_device``)."""
    trees = None
    if spans.trees is not None:
        solid = z_half if spans.solid is None else spans.solid
        trees = (trees_porous(spans.trees), canopy_planes(solid, spans.trees, halo, spacing_m))
    host = _OnHost(z_half, halo, spacing_m, holes, trees)
    if gpu_on():
        yield from _on_device(host, spans, wanted)
        return
    for k in range(HORIZON_DIRS):
        yield from _direction(host, spans, k, wanted)


def _direction(
    ops: CellOps[PlaneT, BandsT], spans: BlockSpans, k: int, wanted: Container[int] | None
) -> list[Cell]:
    """Direction ``k``'s ground cell, then its trees', canopy's and band's cells."""
    az = k * 360.0 / HORIZON_DIRS
    el = path_elevation(az)
    ground: BandsT | None = None
    if spans.ground is None:
        found = [Cell(k, ops.host(ops.plain(az)), None, None)]
    else:
        ground = ops.bands(spans.ground, az, FADE_M)
        found = [Cell(k, ops.host(ops.horizon(ground)), _kept(ops, ground, k, wanted), None)]
    if spans.trees is not None:
        deg, folded = fold(ops.porous(az), el)
        found.append(Cell(CROWN_CELL + k, deg, None, folded))
        found.append(Cell(CANOPY_CELL + k, ops.canopy(az), None, None))
    if ground is not None:
        band = ops.path(ground, el)
        found.append(Cell(BAND_CELL + k, ops.host(band), None, ops.band_in(band, el)))
    return found


def _kept(
    ops: CellOps[PlaneT, BandsT], bands: BandsT | None, k: int, wanted: Container[int] | None
) -> Bands | None:
    if bands is None or (wanted is not None and k not in wanted):
        return None
    return ops.host_bands(bands)


def _on_device(host: _OnHost, spans: BlockSpans, wanted: Container[int] | None) -> Iterator[Cell]:
    """``horizon_cells`` with the block's planes on the device. Out of device memory, the
    directions left take the host's operations, whose marches go to the device a call at a
    time or, without room for one, to numba; each march is counted where it ran."""
    from mapgen.lighting import gpu
    from mapgen.lighting.spans.device import DeviceCells

    surfaces = [] if spans.ground is None else [spans.ground]
    try:
        device: DeviceCells | None = DeviceCells(
            host.z_half, host.halo, host.spacing_m, surfaces, host.holes, host.trees
        )
    except MemoryError:  # CuPy's OutOfMemoryError
        device = None
    try:
        for k in range(HORIZON_DIRS):
            found = None
            if device is not None:
                try:
                    found = _direction(device, spans, k, wanted)
                    gpu.count(True, 1 + 2 * (spans.trees is not None))
                except MemoryError:
                    device = None
            yield from found if found is not None else _direction(host, spans, k, wanted)
    finally:
        device = None  # its planes go before the pool hands the device's memory back
        gpu.release()


def _weighted(az: float) -> list[tuple[int, np.float32]]:
    """The ground's directions a sun at ``az`` reads, with their weights: the two either side
    of it, or the one it stands on."""
    i0, i1, *_trees = sun_cells(az)
    f = (az % 360.0) / (360.0 / HORIZON_DIRS)
    w = np.float32(f - np.floor(f))
    return [(i0, np.float32(1.0) - w)] + ([(i1, w)] if w > 0 else [])


def shade_cells(az: float) -> set[int]:
    """The ground cells whose bands ``default_shade`` reads for a sun at ``az``."""
    return {k for k, _w in _weighted(az)}


def plain_bands(horizon: F32Grid) -> Bands:
    """A plain march's horizon as ``Bands``: no band, no span seen."""
    nan = np.full(horizon.shape, np.nan, np.float32)
    return Bands(horizon, nan, nan, np.zeros(horizon.shape, bool))


def default_shade(bands: dict[int, Bands], sun: Sun) -> tuple[BoolMask, F32Grid] | None:
    """The default sun's shade per cell where a span was in reach of its directions, and
    where: ``(use, shade)`` at half resolution, or None where none was. ``bands`` holds the
    ``shade_cells`` of ``sun``."""
    az, el = sun
    weighted = _weighted(az)
    seen = np.logical_or.reduce([bands[k].seen for k, _w in weighted])
    if not seen.any():
        return None
    shade: F32Grid | None = None
    for k, w in weighted:
        part = cell_shade(bands[k].horizon, ((bands[k].lo, bands[k].hi),), el) * w
        shade = part if shade is None else (shade + part).astype(np.float32)
    assert shade is not None
    use: BoolMask = ndimage.binary_dilation(seen, iterations=1)
    return use, shade.astype(np.float32)
