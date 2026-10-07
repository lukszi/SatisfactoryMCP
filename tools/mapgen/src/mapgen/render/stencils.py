"""How far the steps of a band's draw read past the pixel they write, and the halos that hold them.

``render/compose.py`` draws the layers 256 rows at a time, each band ``BAND_HALO`` rows past its
edges, and each band in column pieces ``PIECE_HALO`` columns past theirs, cropped after. A
step that reads its neighbours, a gradient, a blur or a mean, draws what the whole sheet would
only while all it reads lies within the halo. docs/map/renders.md section 40.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mapgen.gamedata.frame import BOUNDS_M, RENDER_PX
from mapgen.palette.water.surface import WATER_EDGE_BLUR_M
from mapgen.terrain.measure import SEAM_NEAR_TEXELS

__all__ = [
    "GAUSSIAN_TRUNCATE",
    "HALO_STEP",
    "STENCILS",
    "Stencil",
    "band_halo",
    "band_reach",
    "piece_halo",
    "piece_reach",
    "pixel_m",
    "water_blur_reach",
]

#: Where ``scipy.ndimage.gaussian_filter`` ends its kernel, in sigmas: its default.
GAUSSIAN_TRUNCATE = 4.0

#: The halo is the widest reach rounded up to whole steps of this many rows.
HALO_STEP = 8


@dataclass(frozen=True)
class Stencil:
    """A step of the draw that reads its neighbours, and the functions that take it.

    ``reach(size)`` is how many pixels away it reads, through every step before it, along a
    row and, unless ``rows`` is False, across rows. ``pieces`` is False for a step that reads
    a band's rows once its pieces are put together, which no piece edge cuts.
    """

    name: str
    sites: tuple[str, ...]
    reach: Callable[[int], int]
    rows: bool = True
    pieces: bool = True


def pixel_m(size: int) -> float:
    """A pixel's edge at ``size``, in metres."""
    return (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size


def water_blur_reach(size: int) -> int:
    """The water edge's Gaussian radius in pixels at ``size``, where scipy ends its kernel."""
    return int(GAUSSIAN_TRUNCATE * WATER_EDGE_BLUR_M / pixel_m(size) + 0.5)


def _gradient(_size: int) -> int:
    """A central difference, one pixel either side."""
    return 1


def _rock_top(_size: int) -> int:
    """The gradient's normal, then a 3 x 3 mean of its ramp."""
    return 2


def _sunk_specks(size: int) -> int:
    """A 3 x 3 mean over the water's cover: the edge's blur, or the shore crossing's gradient."""
    return max(water_blur_reach(size), 1) + 1


def _seam_trace(_size: int) -> int:
    """A second difference along the row, then the neighbourhood of a seam either side."""
    return SEAM_NEAR_TEXELS + 1


#: Every step of a band's draw that reads a neighbour, by ``mapgen``-relative site.
STENCILS: tuple[Stencil, ...] = (
    Stencil(
        "gradient",
        (
            "lighting.hillshade.sun_dot",
            "lighting.hillshade.slope_degrees",
            "lighting.model.surface_direct",
            "palette.relief._shade",
            "palette.water.shore.shore_terms",
        ),
        _gradient,
    ),
    Stencil("rock top", ("palette.painted.surfaces.top_cover",), _rock_top),
    Stencil("water edge blur", ("palette.water.surface.water_alpha",), water_blur_reach),
    Stencil("sunk specks", ("palette.painted.surfaces.sunk_specks",), _sunk_specks),
    Stencil(
        "seam trace", ("terrain.measure.SeamTrace.measure",), _seam_trace, rows=False, pieces=False
    ),
)


def band_reach(size: int) -> int:
    """The farthest any stencil reads across rows at ``size``."""
    return max(stencil.reach(size) for stencil in STENCILS if stencil.rows)


def piece_reach(size: int) -> int:
    """The farthest any stencil a piece draws reads along its rows at ``size``."""
    return max(stencil.reach(size) for stencil in STENCILS if stencil.pieces)


def _in_steps(reach: int) -> int:
    return -(-reach // HALO_STEP) * HALO_STEP


def band_halo(size: int = RENDER_PX) -> int:
    """The rows that hold every stencil at ``size``, in whole ``HALO_STEP``s.

    A reach is fixed in pixels or in metres, so the largest size holds it at every size.
    """
    return _in_steps(band_reach(size))


def piece_halo(size: int = RENDER_PX) -> int:
    """The columns that hold every stencil a piece draws at ``size``, in whole ``HALO_STEP``s."""
    return _in_steps(piece_reach(size))
