"""A light block's spans: arches and overhangs over the ground, the trees over both, as baked.

``block_spans`` reads a block's window: the slabs the draw captured (``lighting/spans/slabs.py``)
and the trees, each crown a span from its species' underside to its top and each Titan tree a
slab of ``TITAN_SLAB_M`` (``lighting/undersides.py``). ``horizon_cells`` marches them into the
atlas: the ground's cells, then the crowns' and the Titan trees' each alone, a band floating
over the horizon folded in at the elevation the sun's path has in that direction
(``path_horizon``). ``canopy_cells`` marches the trees together over the ground for the default
sun, whose bands ``default_shade`` shades per cell. docs/map/light-and-crowns.md section 29,
"Arches as spans".
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Iterator
from functools import partial
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
from mapgen.lighting.model import CROWN_CELL, SHADOW_SOFT_DEG, TITAN_CELL, TITAN_FADE_M, sun_cells
from mapgen.lighting.occluders import UNDER_SCALE, UNDER_TITAN
from mapgen.lighting.spans.holes import OPEN_M, Holes, fill_holes, half_heights
from mapgen.lighting.spans.march import (
    Bands,
    SpanSurface,
    march_spans,
    path_elevation,
    span_surface,
)
from mapgen.lighting.spans.slabs import SlabStore
from mapgen.lighting.sun import Sun
from mapgen.lighting.undersides import CROWN_UNDERSIDE, TITAN_SLAB_M
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = [
    "BlockSpans",
    "Cell",
    "CellOps",
    "TreePlanes",
    "band_cover",
    "block_spans",
    "canopy_cells",
    "cell_shade",
    "default_shade",
    "horizon_cells",
    "path_horizon",
    "plain_bands",
    "shade_cells",
    "tree_spans",
    "tree_surfaces",
]

#: Rows of the window a block reads the crowns for at a time, at full resolution.
_CROWN_ROWS = 1024

_Window = tuple[int, int, int, int]

PlaneT = TypeVar("PlaneT")
BandsT = TypeVar("BandsT")


class BlockSpans(NamedTuple):
    """What casts on a block beyond its heights, at half resolution: the arches' and
    overhangs' spans over the ground (None where the window has none); the crowns' and the
    Titan trees' each alone over open ground, received on the canopy top (None where none
    stands); and both together over the ground, which the default sun's crowned light reads."""

    ground: SpanSurface | None
    crowns: SpanSurface | None
    titans: SpanSurface | None = None
    canopy: SpanSurface | None = None


class TreePlanes(NamedTuple):
    """A window's trees at half resolution: the canopy top the receivers stand on, and the
    crowns' and the Titan trees' underside and top, NaN where none stands."""

    receivers: F32Grid
    crown_lo: F32Grid
    crown_hi: F32Grid
    titan_lo: F32Grid
    titan_hi: F32Grid


def tree_spans(
    z: F32Grid, top: F32Grid, share: F32Grid | None, under: F32Grid | None
) -> tuple[F32Grid, tuple[F32Grid, F32Grid], tuple[F32Grid, F32Grid]]:
    """The trees stood on ``z`` (the receivers), and the crowns' and the Titan trees' underside
    and top where they stand above it, NaN elsewhere. A crown's underside is its species'
    share of its lift (``under`` bytes, None for ``CROWN_UNDERSIDE``); a Titan tree's is
    ``TITAN_SLAB_M`` under its top."""
    rec = crown_surface(z, top, share)
    lift = rec - z
    over = lift > 0
    titan = over & (under == np.float32(UNDER_TITAN)) if under is not None else over & False
    crown = over & ~titan
    if under is None:
        share_lo = np.float32(round(CROWN_UNDERSIDE * UNDER_SCALE) / UNDER_SCALE)
    else:
        share_lo = under / np.float32(UNDER_SCALE)
    nan = np.float32(np.nan)
    slab = np.maximum(z, rec - np.float32(TITAN_SLAB_M))
    crowns = np.where(crown, z + share_lo * lift, nan), np.where(crown, rec, nan)
    titans = np.where(titan, slab, nan), np.where(titan, rec, nan)
    as32 = partial(np.asarray, dtype=np.float32)
    return rec, (as32(crowns[0]), as32(crowns[1])), (as32(titans[0]), as32(titans[1]))


def _tree_rows(
    z: F32Grid, rows: tuple[F32Grid, F32Grid | None, F32Grid | None], out: TreePlanes, row: int
) -> None:
    """Rows of the window from ``row`` on: ``tree_spans`` of the crown top, share and
    underside ``rows``, at half resolution, into ``out``."""
    rec, crowns, titans = tree_spans(z, *rows)
    cells = slice(row // 2, (row + z.shape[0]) // 2)
    out.receivers[cells] = half_heights(rec)
    for (lo, hi), (lo_out, hi_out) in (
        (crowns, (out.crown_lo, out.crown_hi)),
        (titans, (out.titan_lo, out.titan_hi)),
    ):
        lo_out[cells] = downsample(lo, how=np.nanmin)
        hi_out[cells] = downsample(hi, how=np.nanmax)


def _tree_planes(
    work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid
) -> TreePlanes | None:
    """The trees of the window at half resolution, or None without an occluder. Rows off the
    sheet hold none."""
    occluder = optional_array(work, "occluder", np.float32)
    if occluder is None:
        return None
    cover = optional_array(work, "occluder_cover", np.uint8)
    unders = optional_array(work, "occluder_under", np.uint8)
    r0, r1, c0, c1 = window
    nan = np.full(z_half.shape, np.nan, np.float32)
    planes = TreePlanes(z_half.copy(), nan, nan.copy(), nan.copy(), nan.copy())
    first, last = max(r0, 0) - r0, min(r1, occluder.shape[0]) - r0
    for row in range(first, last, _CROWN_ROWS):
        a, b = r0 + row, min(r0 + row + _CROWN_ROWS, r0 + last)
        top = padded_window(occluder, a, b, c0, c1, np.nan)
        share = (
            None if cover is None else padded_window(cover, a, b, c0, c1, 0.0) / np.float32(255.0)
        )
        under = None if unders is None else padded_window(unders, a, b, c0, c1, 0.0)
        _tree_rows(z_window[row : row + b - a], (top, share, under), planes, row)
    return planes


def _alone(receivers: F32Grid, lo: F32Grid, hi: F32Grid, ground: F32Grid) -> SpanSurface | None:
    """Spans marched alone: over open ground, their tops standing on ``ground``; None where
    the window holds none."""
    if not np.isfinite(lo).any():
        return None
    return span_surface(receivers, np.full(lo.shape, OPEN_M, np.float32), lo, hi, ground)


def tree_surfaces(
    planes: TreePlanes, solid: F32Grid
) -> tuple[SpanSurface | None, SpanSurface | None, SpanSurface | None]:
    """The crowns alone, the Titan trees alone, and both over ``solid``: of two at a cell,
    the one whose top is higher."""
    rec = planes.receivers
    crowns = _alone(rec, planes.crown_lo, planes.crown_hi, solid)
    titans = _alone(rec, planes.titan_lo, planes.titan_hi, solid)
    if crowns is None and titans is None:
        return None, None, None
    titan = np.isfinite(planes.titan_hi) & ~(planes.crown_hi > planes.titan_hi)
    lo = np.where(titan, planes.titan_lo, planes.crown_lo)
    hi = np.where(titan, planes.titan_hi, planes.crown_hi)
    return crowns, titans, span_surface(rec, solid, lo, hi)


def block_spans(
    work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid, slabs: SlabStore
) -> BlockSpans:
    """The spans that cast on a block whose window is ``window``, at half resolution."""
    found = slabs.half(window, z_half)
    ground = None if found is None else span_surface(z_half, found.solid, found.lo, found.hi)
    solid = z_half if found is None else found.solid
    planes = _tree_planes(work, window, z_window, z_half)
    if planes is None:
        return BlockSpans(ground, None)
    return BlockSpans(ground, *tree_surfaces(planes, solid))


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
    was made from (kept for a wanted ground cell, else None), and where it stores a band
    folded in (None for a plain march)."""

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
    def band_in(self, cell: PlaneT, bands: BandsT) -> BoolMask: ...
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

    def band_in(self, cell: F32Grid, bands: Bands) -> BoolMask:
        """Where the cell is the band folded in: above the horizon it was cut from."""
        return cell > bands.horizon

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
    """Each direction's ground cell, then its crowns' and its Titan trees' cells where the
    block has them, as the atlas stores them (``Cell``). A hole (``lighting/spans/holes.py``)
    takes the cells of the pixel nearest it. Only a ground cell in ``wanted`` (every one when
    None) comes with its bands. With the switch at CUDA the block's planes stay on the device
    (``_on_device``)."""
    host = _OnHost(z_half, halo, spacing_m, holes)
    if gpu_on():
        yield from _on_device(host, spans, wanted)
        return
    for k in range(HORIZON_DIRS):
        yield from _direction(host, spans, k, wanted)


def _direction(
    ops: CellOps[PlaneT, BandsT], spans: BlockSpans, k: int, wanted: Container[int] | None
) -> list[Cell]:
    """Direction ``k``'s ground cell, then its crowns' and Titan trees' cells."""
    az = k * 360.0 / HORIZON_DIRS
    el = path_elevation(az)
    ground: BandsT | None = None
    if spans.ground is None:
        cell = ops.plain(az)
    else:
        ground = ops.bands(spans.ground, az, FADE_M)
        cell = ops.path(ground, el)
    band_in = None if ground is None else ops.band_in(cell, ground)
    found = [Cell(k, ops.host(cell), _kept(ops, ground, k, wanted), band_in)]
    for first, surface, fade in (
        (CROWN_CELL, spans.crowns, OCCLUDER_FADE_M),
        (TITAN_CELL, spans.titans, TITAN_FADE_M),
    ):
        if surface is not None:
            trees = ops.bands(surface, az, fade)
            whole = ops.path(trees, el)
            found.append(Cell(first + k, ops.host(whole), None, ops.band_in(whole, trees)))
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

    surfaces = [s for s in (spans.ground, spans.crowns, spans.titans) if s is not None]
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
                    gpu.count(True, 1 + (spans.crowns is not None) + (spans.titans is not None))
                except MemoryError:
                    device = None
            yield from found if found is not None else _direction(host, spans, k, wanted)
    finally:
        device = None  # its planes go before the pool hands the device's memory back
        gpu.release()


def canopy_cells(
    z_half: F32Grid,
    halo: int,
    spacing_m: float,
    spans: BlockSpans,
    holes: Holes | None,
    dirs: Iterable[int],
) -> Iterator[tuple[int, F32Grid, Bands]]:
    """For each direction in ``dirs``, the crowns and Titan trees together over the ground,
    received on the canopy top: the horizon with its band folded in, and the bands. What the
    default sun's crowned light reads, made on the host; a march goes to the device a call at
    a time with the switch at CUDA."""
    if spans.canopy is None:
        return
    host = _OnHost(z_half, halo, spacing_m, holes)
    for k in sorted(dirs):
        az = k * 360.0 / HORIZON_DIRS
        bands = host.bands(spans.canopy, az, OCCLUDER_FADE_M)
        yield k, host.path(bands, path_elevation(az)), bands


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
