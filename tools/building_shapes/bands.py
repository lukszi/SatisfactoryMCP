"""A mesh's height bands: the parts standing above per-class height breaks, traced like the
footprint. Why the breaks are found this way: docs/building-shapes.md, "Height bands"."""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np
from scipy import ndimage

from satisfactory_mcp.core.arrays import BoolMask, F64Grid, I64Grid
from tools.building_shapes.breaks import natural_breaks
from tools.building_shapes.silhouette import Ring, blobs, disk_mask, top_heights, traced

__all__ = [
    "MAX_BINS",
    "Band",
    "BandOptions",
    "band_floors",
    "band_surface",
    "bands_from_tops",
    "height_bands",
]

#: The most height bins the breaks are searched on; a taller class gets coarser bins.
MAX_BINS = 160


class Band(NamedTuple):
    """The parts higher than ``floor`` (same unit as the mesh); ``tier`` is the break's rank,
    1 for the lowest, kept when a band below it is dropped."""

    tier: int
    floor: float
    rings: list[Ring]


class BandOptions(NamedTuple):
    """How bands are cut; lengths in the mesh's unit, areas in its square."""

    #: One vertex budget per band, lowest first; their count is the most bands a class gets.
    budgets: tuple[int, ...]
    #: No band starts lower than this: walkways and base plates are the footprint's.
    min_floor: float
    #: The height-histogram bin the breaks are searched on.
    step: float
    #: The share of a band the next one up must leave showing, in strips at least
    #: ``min_width`` wide, for the break between them to count.
    min_visible: float = 0.0
    min_width: float = 0.0
    #: The least height between one band's floor and the next one's.
    min_rise: float = 0.0
    #: Parts thinner than twice this are not mass (railings, pipes, cables).
    opening: float = 0.0
    #: A band smaller than this, or covering more than ``max_overlap`` of the band kept
    #: below it, adds nothing and is dropped.
    min_area: float = 0.0
    max_overlap: float = 0.9
    min_fraction: float = 0.02
    #: The largest parts of a band kept; a lattice of small towers is texture, not shape.
    max_rings: int = 6


def _visible_steps(bins: I64Grid, count: int, radius: int) -> I64Grid:
    """``out[a + 1, b]``: covered cells whose whole ``radius`` disk lies in bins ``a`` to
    ``b - 1``, the part of band ``a`` the band at ``b`` leaves showing, at least that wide.
    ``out[0, b]`` is the same for the ground, which only needs the upper bound."""
    disk = disk_mask(radius)
    lowest = ndimage.minimum_filter(bins, footprint=disk, mode="constant", cval=-1)
    highest = ndimage.maximum_filter(bins, footprint=disk, mode="constant", cval=-1)
    covered = bins >= 0
    pairs = np.zeros((count + 1, count), dtype=np.int64)
    np.add.at(pairs, (lowest[covered] + 1, highest[covered]), 1)
    from_low = np.cumsum(pairs[::-1], axis=0)[::-1]
    below = np.cumsum(from_low, axis=1)
    return np.concatenate([np.zeros((count + 1, 1), np.int64), below], axis=1)


def _allowed_runs(
    bins: I64Grid, used: I64Grid, step: float, cell: float, options: BandOptions
) -> BoolMask:
    """``out[first, last]``: whether the occupied bins ``used[first:last]`` may be one group.

    Group 0 is the ground; a group starting higher is a band. Each group below the top one
    must leave ``min_visible`` of its area (and ``min_area`` at least) showing under the next
    band, in strips ``min_width`` wide, and rise ``min_rise``; no band starts under
    ``min_floor``; the top band covers ``min_area``.
    """
    n = len(used)
    count = int(used[-1]) + 1
    weights = np.bincount(bins[bins >= 0], minlength=count)[used].astype(np.float64)
    at_or_above = np.concatenate([np.cumsum(weights[::-1])[::-1], [0.0]])
    least_cells = options.min_area / (cell * cell)
    showing = _visible_steps(bins, count, max(1, round(options.min_width / 2 / cell)))
    starts = np.append(used, count)
    heights = starts * step
    first = np.arange(n + 1)[:, None]
    last = np.arange(n + 1)[None, :]
    needed = np.maximum(options.min_visible * at_or_above[first], least_cells)
    shown = showing[np.minimum(starts[first] + 1, count), starts[last]]
    risen = heights[last] - heights[first] >= options.min_rise
    allowed = (last > first) & risen & (shown >= needed)
    allowed[0, :] = (last[0] > 0) & (
        at_or_above[0] - at_or_above >= options.min_visible * at_or_above[0]
    )
    allowed[:, n] = (first[:, 0] < n) & (at_or_above >= least_cells)
    allowed[0, n] = True
    allowed[1:][heights[1:] < options.min_floor] = False
    return allowed


def band_floors(surface: F64Grid, options: BandOptions, cell: float) -> list[float]:
    """The class's own band floors: natural breaks in its area at each height.

    Heights are ``band_surface`` cells, capped at their 99th percentile so one antenna does
    not stretch the scale, and binned at ``options.step`` (coarser past ``MAX_BINS``). The
    breaks group the occupied bins as tightly as ``_allowed_runs`` lets them, into as many
    bands as there are budgets, or fewer when no such split shows; a floor is the lowest
    occupied height of its band.
    """
    covered = np.isfinite(surface)
    if not covered.any():
        return []
    capped = np.clip(np.where(covered, surface, 0.0), 0.0, np.percentile(surface[covered], 99))
    step = max(options.step, float(capped.max()) / MAX_BINS)
    bins = np.where(covered, np.floor(capped / step), -1).astype(np.int64)
    used, weights = np.unique(bins[covered], return_counts=True)
    values = used * step
    allowed = _allowed_runs(bins, used, step, cell, options)
    cuts = natural_breaks(values, weights.astype(np.float64), len(options.budgets) + 1, allowed)
    return [float(values[cut]) for cut in cuts]


def band_surface(top: F64Grid, radius: int, opening: int) -> F64Grid:
    """``top`` padded by ``radius``, grey-opened over ``opening`` and grey-closed over
    ``radius`` cells: each of its level sets is that height's band grid before hole filling,
    so the breaks are chosen on what is drawn, not on the mesh's speckle."""
    surface = np.pad(top, radius, constant_values=-np.inf)
    if opening:
        surface = ndimage.grey_opening(surface, footprint=disk_mask(opening), mode="nearest")
    return ndimage.grey_closing(surface, footprint=disk_mask(radius), mode="nearest")


def bands_from_tops(
    top: F64Grid, corner: tuple[float, float], cell: float, close: float, options: BandOptions
) -> list[Band]:
    """The bands of a top-height grid (``top_heights``); see ``height_bands``."""
    covered = np.isfinite(top)
    if not covered.any():
        return []
    radius = max(1, round(close / cell))
    min_cells = math.ceil(options.min_area / (cell * cell))
    outer = (corner[0] - radius * cell, corner[1] - radius * cell)
    below = int(blobs(covered, radius).sum())
    surface = band_surface(top, radius, round(options.opening / cell))
    bands: list[Band] = []
    for tier, floor in enumerate(band_floors(surface, options, cell), start=1):
        grid = ndimage.binary_fill_holes(surface >= floor)
        area = int(grid.sum())
        if area < min_cells or area > options.max_overlap * below:
            continue
        budget = options.budgets[tier - 1]
        rings = traced(grid, outer, cell, budget, options.min_fraction, min_cells)
        if rings:
            bands.append(Band(tier, floor, rings[: options.max_rings]))
            below = area
    return bands


def height_bands(triangles: F64Grid, cell: float, close: float, options: BandOptions) -> list[Band]:
    """The parts of ``triangles`` (``(n, 3, 3)``) standing above each of the class's breaks.

    Each band is opened by ``options.opening`` first, then closed and traced like the
    footprint; a band too small, or nearly the whole band kept below it, is dropped.
    """
    if len(triangles) == 0:
        return []
    top, corner = top_heights(triangles, cell)
    return bands_from_tops(top, corner, cell, close, options)
