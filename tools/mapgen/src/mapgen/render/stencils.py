"""How far the steps of a band's draw read past the pixel they write, and the halo that holds them.

``render/compose.py`` draws a layer 256 rows at a time, each band ``BAND_HALO`` rows past its
edges and cropped after. A step that reads its neighbours, a gradient, a blur or a mean, draws
what the whole sheet would only while all it reads lies within the halo. docs/map/renders.md
section 40.
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

    ``reach(size)`` is how many pixels away it reads, through every step before it; ``rows``
    is False for one that reads along its row only.
    """

    name: str
    sites: tuple[str, ...]
    reach: Callable[[int], int]
    rows: bool = True


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
            "palette.relief._lambert",
            "palette.water.shore.shore_terms",
        ),
        _gradient,
    ),
    Stencil("rock top", ("palette.painted.surfaces.top_cover",), _rock_top),
    Stencil("water edge blur", ("palette.water.surface.water_alpha",), water_blur_reach),
    Stencil("sunk specks", ("palette.painted.surfaces.sunk_specks",), _sunk_specks),
    Stencil("seam trace", ("terrain.measure.SeamTrace.measure",), _seam_trace, rows=False),
)


def band_reach(size: int) -> int:
    """The farthest any stencil reads across rows at ``size``."""
    return max(stencil.reach(size) for stencil in STENCILS if stencil.rows)


def band_halo(size: int = RENDER_PX) -> int:
    """The rows that hold every stencil at ``size``, in whole ``HALO_STEP``s.

    A reach is fixed in pixels or in metres, so the largest size holds it at every size.
    """
    return -(-band_reach(size) // HALO_STEP) * HALO_STEP
