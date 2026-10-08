"""A light block's spans: arches and overhangs over the ground, crowns over both, as baked.

``block_spans`` reads a block's window: the slabs the draw captured (``lighting/spans/slabs.py``)
and the crowns, each crown a span from ``CROWN_UNDERSIDE`` of its height to its top.
``horizon_cells`` marches them into the atlas, where a band floating over the horizon is
folded in at the elevation the sun's path has in that direction (``path_horizon``), and keeps
the bands the default sun reads, which ``default_shade`` shades per cell.
docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

from collections.abc import Container, Iterator
from pathlib import Path
from typing import NamedTuple, Protocol, TypeVar

import numpy as np
from scipy import ndimage

from mapgen.jit import gpu_on
from mapgen.lighting.horizon import (
    FADE_M,
    HORIZON_DIRS,
    OCCLUDER_FADE_M,
    Fade,
    crown_surface,
    march_horizon,
)
from mapgen.lighting.light_tiles import downsample, optional_array, padded_window
from mapgen.lighting.model import SHADOW_SOFT_DEG, sun_cells
from mapgen.lighting.spans.holes import Holes, fill_holes, half_heights
from mapgen.lighting.spans.march import (
    CROWN_UNDERSIDE,
    Bands,
    SpanSurface,
    march_spans,
    path_elevation,
    span_surface,
)
from mapgen.lighting.spans.slabs import SlabStore
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
    "horizon_cells",
    "path_horizon",
    "plain_bands",
    "shade_cells",
]

#: Rows of the window a block reads the crowns for at a time, at full resolution.
_CROWN_ROWS = 1024

_Window = tuple[int, int, int, int]

PlaneT = TypeVar("PlaneT")
BandsT = TypeVar("BandsT")


class BlockSpans(NamedTuple):
    """What casts on a block beyond its heights: the arches' and overhangs' spans over the
    ground (None where the window has none), and the crowns' (None without an occluder)."""

    ground: SpanSurface | None
    crowns: SpanSurface | None


def _crown_rows(
    z: F32Grid, top: F32Grid, share: F32Grid | None, out: tuple[F32Grid, F32Grid, F32Grid], row: int
) -> None:
    """Rows of the window from ``row`` on: the crowns stood on ``z`` (the receivers), and their
    underside and top where they stand above it, at half resolution, into ``out``."""
    rec = crown_surface(z, top, share)
    lift = rec - z
    over = lift > 0
    nan = np.float32(np.nan)
    lo = np.where(over, z + CROWN_UNDERSIDE * lift, nan).astype(np.float32)
    hi = np.where(over, rec, nan).astype(np.float32)
    cells = slice(row // 2, (row + z.shape[0]) // 2)
    out[0][cells] = half_heights(rec)
    out[1][cells] = downsample(lo, how=np.nanmin)
    out[2][cells] = downsample(hi, how=np.nanmax)


def _crowns(
    work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid, solid: F32Grid
) -> SpanSurface | None:
    """The crowns of the window as spans over ``solid`` (half resolution), or None. Rows off
    the sheet hold none."""
    occluder = optional_array(work, "occluder", np.float32)
    if occluder is None:
        return None
    cover = optional_array(work, "occluder_cover", np.uint8)
    r0, r1, c0, c1 = window
    nan = np.full(z_half.shape, np.nan, np.float32)
    planes = (z_half.copy(), nan, nan.copy())
    first, last = max(r0, 0) - r0, min(r1, occluder.shape[0]) - r0
    for row in range(first, last, _CROWN_ROWS):
        a, b = r0 + row, min(r0 + row + _CROWN_ROWS, r0 + last)
        top = padded_window(occluder, a, b, c0, c1, np.nan)
        share = (
            None if cover is None else padded_window(cover, a, b, c0, c1, 0.0) / np.float32(255.0)
        )
        _crown_rows(z_window[row : row + b - a], top, share, planes, row)
    receivers, lo, hi = planes
    return span_surface(receivers, solid, lo, hi)


def block_spans(
    work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid, slabs: SlabStore
) -> BlockSpans:
    """The spans that cast on a block whose window is ``window``, at half resolution."""
    found = slabs.half(window, z_half)
    ground = None if found is None else span_surface(z_half, found.solid, found.lo, found.hi)
    solid = z_half if found is None else found.solid
    return BlockSpans(ground, _crowns(work, window, z_window, z_half, solid))


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


def path_horizon(bands: Bands, el: float) -> F32Grid:
    """The horizon a page that reads horizons alone shades with: the band folded in where it
    hides the sun disc at ``el``, the sun path's elevation in this direction, else the horizon.
    Exact for a sun on the path; the bake's own shade reads the band (``cell_shade``)."""
    hz = bands.horizon
    cover = band_cover(hz, ((bands.lo, bands.hi),), el)
    folded = np.float32(el) + np.float32(SHADOW_SOFT_DEG) * (cover - np.float32(0.5))
    return np.where(cover > 0, np.maximum(hz, folded), hz).astype(np.float32)


class Cell(NamedTuple):
    """An atlas cell as marched: its index, its degrees as the atlas stores them, the bands it
    was made from (None for a plain march), and the horizon it was cut from: for a crown cell
    the crowns' whole, received on the canopy top."""

    k: int
    deg: F32Grid
    bands: Bands | None
    whole: F32Grid


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
    def above(self, over: PlaneT, cell: PlaneT) -> PlaneT: ...
    def host(self, plane: PlaneT) -> F32Grid: ...
    def host_bands(self, bands: BandsT) -> Bands: ...


class _OnHost:
    """``CellOps`` in numpy, and numba's kernels where the switch says so."""

    def __init__(self, z_half: F32Grid, halo: int, spacing_m: float, holes: Holes | None) -> None:
        self.z_half, self.halo, self.spacing_m, self.holes = z_half, halo, spacing_m, holes

    def plain(self, az_deg: float) -> F32Grid:
        found = march_horizon(self.z_half, self.halo, az_deg, self.spacing_m)
        return fill_holes(found, self.holes, 0.0)

    def bands(self, surface: SpanSurface, az_deg: float, fade: Fade) -> Bands:
        return _filled(march_spans(surface, self.halo, az_deg, self.spacing_m, fade), self.holes)

    def path(self, bands: Bands, el: float) -> F32Grid:
        return path_horizon(bands, el)

    def above(self, over: F32Grid, cell: F32Grid) -> F32Grid:
        return np.where(over > cell, over, np.float32(0.0))

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
    """Each direction's ground cell, then its crown cell where the crowns stand above it, as
    the atlas stores them (``Cell``). A hole (``lighting/spans/holes.py``) takes the cells of the
    pixel nearest it. A cell not in ``wanted`` may come without its bands. With the switch at
    CUDA the block's planes stay on the device (``_on_device``)."""
    host = _OnHost(z_half, halo, spacing_m, holes)
    if gpu_on():
        yield from _on_device(host, spans, wanted)
        return
    for k in range(HORIZON_DIRS):
        yield from _direction(host, spans, k, wanted)


def _direction(
    ops: CellOps[PlaneT, BandsT], spans: BlockSpans, k: int, wanted: Container[int] | None
) -> list[Cell]:
    """Direction ``k``'s ground cell and crown cell."""
    az = k * 360.0 / HORIZON_DIRS
    el = path_elevation(az)
    ground: BandsT | None = None
    if spans.ground is None:
        cell = ops.plain(az)
    else:
        ground = ops.bands(spans.ground, az, FADE_M)
        cell = ops.path(ground, el)
    deg = ops.host(cell)
    found = [Cell(k, deg, _kept(ops, ground, k, wanted), deg)]
    if spans.crowns is not None:
        crowns = ops.bands(spans.crowns, az, OCCLUDER_FADE_M)
        over = ops.path(crowns, el)
        kept = _kept(ops, crowns, HORIZON_DIRS + k, wanted)
        found.append(Cell(HORIZON_DIRS + k, ops.host(ops.above(over, cell)), kept, ops.host(over)))
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

    surfaces = [s for s in (spans.ground, spans.crowns) if s is not None]
    try:
        device: DeviceCells | None = DeviceCells(
            host.z_half, host.halo, host.spacing_m, surfaces, host.holes
        )
    except MemoryError:  # CuPy's OutOfMemoryError
        device = None
    try:
        for k in range(HORIZON_DIRS):
            found = None
            if device is not None:
                try:
                    found = _direction(device, spans, k, wanted)
                    gpu.count(True, 1 + (spans.crowns is not None))
                except MemoryError:
                    device = None
            yield from found if found is not None else _direction(host, spans, k, wanted)
    finally:
        device = None  # its planes go before the pool hands the device's memory back
        gpu.release()


def _weighted(az: float) -> list[tuple[int, np.float32]]:
    """The ground's directions a sun at ``az`` reads, with their weights: the two either side
    of it, or the one it stands on."""
    i0, i1, _crown0, _crown1 = sun_cells(az)
    f = (az % 360.0) / (360.0 / HORIZON_DIRS)
    w = np.float32(f - np.floor(f))
    return [(i0, np.float32(1.0) - w)] + ([(i1, w)] if w > 0 else [])


def shade_cells(az: float) -> set[int]:
    """The atlas cells whose bands ``default_shade`` reads for a sun at ``az``."""
    return {k + offset for k, _w in _weighted(az) for offset in (0, HORIZON_DIRS)}


def plain_bands(horizon: F32Grid) -> Bands:
    """A plain march's horizon as ``Bands``: no band, no span seen."""
    nan = np.full(horizon.shape, np.nan, np.float32)
    return Bands(horizon, nan, nan, np.zeros(horizon.shape, bool))


def default_shade(
    bands: dict[int, Bands], sun: Sun, crowns: bool
) -> tuple[BoolMask, F32Grid] | None:
    """The default sun's shade per cell where a span was in reach of its directions, and
    where: ``(use, shade)`` at half resolution, or None where none was. ``bands`` holds the
    ``shade_cells`` of ``sun``, the crowns' only with crowns."""
    az, el = sun
    weighted = _weighted(az)
    crowns = crowns and HORIZON_DIRS + weighted[0][0] in bands
    keys = [k + offset for k, _w in weighted for offset in ((0, HORIZON_DIRS) if crowns else (0,))]
    seen = np.logical_or.reduce([bands[k].seen for k in keys])
    if not seen.any():
        return None
    shade: F32Grid | None = None
    for k, w in weighted:
        marched = [bands[k]] + ([bands[HORIZON_DIRS + k]] if crowns else [])
        hz = marched[0].horizon if len(marched) == 1 else np.maximum(*(b.horizon for b in marched))
        part = cell_shade(hz, tuple((b.lo, b.hi) for b in marched), el) * w
        shade = part if shade is None else (shade + part).astype(np.float32)
    assert shade is not None
    use: BoolMask = ndimage.binary_dilation(seen, iterations=1)
    return use, shade.astype(np.float32)
