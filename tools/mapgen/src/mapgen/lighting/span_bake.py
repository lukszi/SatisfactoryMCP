"""A light block's spans: arches and overhangs over the ground, crowns over both, as baked.

``block_spans`` reads a block's window: the slabs the draw captured (``lighting/slabs.py``)
and the crowns, each crown a span from ``CROWN_UNDERSIDE`` of its height to its top.
``horizon_cells`` marches them into the atlas, where a band floating over the horizon is
folded in at the elevation the sun's path has in that direction (``path_horizon``), and keeps
the bands the default sun reads, which ``default_shade`` shades per cell.
docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.lighting.horizon import (
    FADE_M,
    HORIZON_DIRS,
    OCCLUDER_FADE_M,
    crown_surface,
    march_horizon,
)
from mapgen.lighting.light_tiles import downsample, optional_array, padded_window
from mapgen.lighting.model import SHADOW_SOFT_DEG, sun_cells
from mapgen.lighting.slabs import SlabStore
from mapgen.lighting.spans import (
    CROWN_UNDERSIDE,
    Bands,
    SpanSurface,
    march_spans,
    path_elevation,
    span_surface,
)
from mapgen.lighting.sun import Sun
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = [
    "BlockSpans",
    "band_cover",
    "block_spans",
    "cell_shade",
    "default_shade",
    "full_resolution",
    "horizon_cells",
    "path_horizon",
    "plain_bands",
]

#: Rows of the window a block reads the crowns for at a time, at full resolution.
_CROWN_ROWS = 1024

_Window = tuple[int, int, int, int]


class BlockSpans(NamedTuple):
    """What casts on a block beyond its heights: the arches' and overhangs' spans over the
    ground (None where the window has none), and the crowns' (None without an occluder)."""

    ground: SpanSurface | None
    crowns: SpanSurface | None


def _crown_rows(z: F32Grid, top: F32Grid, share: F32Grid | None,
                out: tuple[F32Grid, F32Grid, F32Grid], row: int) -> None:  # fmt: skip
    """Rows of the window from ``row`` on: the crowns stood on ``z`` (the receivers), and their
    underside and top where they stand above it, at half resolution, into ``out``."""
    rec = crown_surface(z, top, share)
    lift = rec - z
    over = lift > 0
    nan = np.float32(np.nan)
    lo = np.where(over, z + CROWN_UNDERSIDE * lift, nan).astype(np.float32)
    hi = np.where(over, rec, nan).astype(np.float32)
    cells = slice(row // 2, (row + z.shape[0]) // 2)
    out[0][cells] = downsample(rec)
    out[1][cells] = downsample(lo, how=np.nanmin)
    out[2][cells] = downsample(hi, how=np.nanmax)


def _crowns(work: Path, window: _Window, z_window: F32Grid, solid: F32Grid) -> SpanSurface | None:
    """The crowns of the window as spans over ``solid`` (half resolution), or None."""
    occluder = optional_array(work, "occluder", np.float32)
    if occluder is None:
        return None
    cover = optional_array(work, "occluder_cover", np.uint8)
    r0, r1, c0, c1 = window
    half = (z_window.shape[0] // 2, z_window.shape[1] // 2)
    planes = (np.empty(half, np.float32), np.empty(half, np.float32), np.empty(half, np.float32))
    for row in range(0, r1 - r0, _CROWN_ROWS):
        a, b = r0 + row, min(r0 + row + _CROWN_ROWS, r1)
        top = padded_window(occluder, a, b, c0, c1, np.nan)
        share = (
            None if cover is None else padded_window(cover, a, b, c0, c1, 0.0) / np.float32(255.0)
        )
        _crown_rows(z_window[row : row + b - a], top, share, planes, row)
    receivers, lo, hi = planes
    return span_surface(receivers, solid, lo, hi)


def block_spans(work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid,
                slabs: SlabStore) -> BlockSpans:  # fmt: skip
    """The spans that cast on a block whose window is ``window``, at half resolution."""
    found = slabs.half(window, z_half)
    ground = None if found is None else span_surface(z_half, found.solid, found.lo, found.hi)
    solid = z_half if found is None else found.solid
    return BlockSpans(ground, _crowns(work, window, z_window, solid))


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


def horizon_cells(z_half: F32Grid, halo: int, spacing_m: float,
                  spans: BlockSpans) -> Iterator[tuple[int, F32Grid, Bands | None]]:  # fmt: skip
    """Each direction's ground cell, then its crown cell where the crowns stand above it, as
    the atlas stores them, with the bands they were made from (None for a plain march)."""
    for k in range(HORIZON_DIRS):
        az = k * 360.0 / HORIZON_DIRS
        el = path_elevation(az)
        ground: Bands | None = None
        if spans.ground is None:
            cell = march_horizon(z_half, halo, az, spacing_m)
        else:
            ground = march_spans(spans.ground, halo, az, spacing_m, FADE_M)
            cell = path_horizon(ground, el)
        yield k, cell, ground
        if spans.crowns is not None:
            crowns = march_spans(spans.crowns, halo, az, spacing_m, OCCLUDER_FADE_M)
            over = path_horizon(crowns, el)
            yield HORIZON_DIRS + k, np.where(over > cell, over, np.float32(0.0)), crowns


def _either_side(az: float) -> tuple[int, int, np.float32]:
    i0, i1, _crown0, _crown1 = sun_cells(az)
    f = (az % 360.0) / (360.0 / HORIZON_DIRS)
    return i0, i1, np.float32(f - np.floor(f))


def plain_bands(horizon: F32Grid) -> Bands:
    """A plain march's horizon as ``Bands``: no band, no span seen."""
    nan = np.full(horizon.shape, np.nan, np.float32)
    return Bands(horizon, nan, nan, np.zeros(horizon.shape, bool))


def default_shade(
    bands: dict[int, Bands], sun: Sun, crowns: bool
) -> tuple[BoolMask, F32Grid] | None:
    """The default sun's shade per cell where a span was in reach of its directions, and
    where: ``(use, shade)`` at half resolution, or None where none was. ``bands`` holds the
    ``sun_cells`` of ``sun``, the crowns' only with crowns."""
    az, el = sun
    i0, i1, w = _either_side(az)
    crowns = crowns and HORIZON_DIRS + i0 in bands
    keys = [i0, i1] + ([HORIZON_DIRS + i0, HORIZON_DIRS + i1] if crowns else [])
    seen = np.logical_or.reduce([bands[k].seen for k in keys])
    if not seen.any():
        return None
    shades: list[F32Grid] = []
    for k in (i0, i1):
        marched = [bands[k]] + ([bands[HORIZON_DIRS + k]] if crowns else [])
        hz = marched[0].horizon if len(marched) == 1 else np.maximum(*(b.horizon for b in marched))
        shades.append(cell_shade(hz, tuple((b.lo, b.hi) for b in marched), el))
    shade = (shades[0] * (np.float32(1.0) - w) + shades[1] * w).astype(np.float32)
    use: BoolMask = ndimage.binary_dilation(seen, iterations=1)
    return use, shade


def full_resolution(plane: NDArray[np.floating], shape: tuple[int, int]) -> F32Grid:
    """A half-resolution plane brought to ``shape`` as ``model.direct_term`` brings a horizon."""
    zoomed = ndimage.zoom(np.asarray(plane, np.float32), np.array(shape) / np.array(plane.shape),
                          order=1)  # fmt: skip
    return np.asarray(zoomed, np.float32)
