"""The raise-only rule by which rocks and the top raster lift the ground over the lattice.

``blend_regimes`` is the field's own composition rule at the render's spacing; the top raster
and the meshes go through the same smoothed lift (docs/spatial-and-map.md sections 20 and 25).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.terrain.measure import SEAM_MID
from mapgen.terrain.rasters import pixel_coverage
from mapgen.terrain.sample import Taps, reads_nothing, sample_coverage, sample_plain
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "DIRECT_LIFT_KNEE_M",
    "LATTICE_EDGE_BLUR_M",
    "blend_regimes",
    "composite_top",
    "lattice_edge",
    "rock_kept",
    "smooth_lift",
]

#: The knee of the smoothed positive part that lets a rock raise the ground and never lower
#: it, in metres: the field's own hard ``max`` with its corner rounded, so the hillshade draws
#: no line round a formation's base. It sits at most half the knee above the hard answer.
DIRECT_LIFT_KNEE_M = 0.25

#: The ground lattice's edge, softened over this many metres inside it: where the lattice
#: stops, the rocks stand on the field's own fold instead, and the two meet over it.
LATTICE_EDGE_BLUR_M = 2.0


def lattice_edge(
    lattice: NDArray[np.floating], heights: NDArray[np.number], spacing_m: float
) -> U8Grid:
    """How much of each texel the fold stands in for the ground lattice, 0..255: 255 where the
    lattice knows nothing and the field's ``heights`` do, falling to 0 within about three
    ``LATTICE_EDGE_BLUR_M`` of that edge. Where both know nothing, the void draws."""
    have: BoolMask = (np.asarray(lattice) != hf.NODATA) | (np.asarray(heights) == hf.NODATA)
    share: F32Grid = np.asarray(
        ndimage.gaussian_filter(
            have.astype(np.float32), LATTICE_EDGE_BLUR_M / spacing_m, mode="nearest"
        ),
        np.float32,
    )
    share *= np.float32(2.0)
    share -= np.float32(1.0)
    np.clip(share, 0.0, 1.0, out=share)
    share[~have] = 0.0
    np.subtract(np.float32(1.0), share, out=share)
    share *= np.float32(255.0)
    edge: U8Grid = np.round(share).astype(np.uint8)
    return edge


def smooth_lift(delta_m: FloatGrid) -> FloatGrid:
    """The raise-only rule: ``max(delta, 0)`` with its corner rounded by the knee."""
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    return 0.5 * (delta_m + np.sqrt(delta_m * delta_m + knee * knee))


def composite_top(
    z_m: FloatGrid, top_z_cm: FloatGrid, top_coverage: NDArray[np.generic], subsamples: int = 1
) -> F32Grid:
    """``z_m`` raised by the top raster through the same coverage and smoothed lift as rocks."""
    w = np.clip(pixel_coverage(top_coverage, subsamples), 0.0, 1.0)
    delta = top_z_cm / np.float32(100.0) - z_m
    return (z_m + w * smooth_lift(delta)).astype(np.float32)


def blend_regimes(
    base_m: FloatGrid,
    missing: BoolMask,
    direct: tuple[F32Grid, NDArray[np.generic]],
    linear: Taps,
    subsamples: int,
    keep: F32Grid | None = None,
) -> tuple[F32Grid, BoolMask, F32Grid, F32Grid]:
    """The two-regime height and what it was made of: ``(z_m, missing, w, switched)``.

    The field's own composition rule at the render's spacing: ``z = base + w * lift(z_rock -
    base)``, with ``w`` the direct raster's coverage of the pixel (scaled by ``keep``, the
    share the void leaves a rock under the sea) and ``lift`` the smoothed positive part.
    Where the lattice knows nothing, the caller passes the field's own fold as ``base_m``.
    ``switched`` is the hard switch the seam trace measures the blend against.
    """
    z_cm, coverage = direct
    fraction = pixel_coverage(coverage, subsamples)
    if keep is not None:
        fraction = fraction * keep
    w = np.clip(fraction, 0.0, 1.0).astype(np.float32)
    z_direct_m = z_cm / np.float32(100.0)
    z_m = base_m + w * smooth_lift(z_direct_m - base_m)
    # A rock off the edge of the landscape is the whole answer: a switch, on an edge the
    # render already draws hard.
    only_rock = missing & (fraction > 0.0)
    z_m = np.where(only_rock, z_direct_m, z_m)
    w = np.where(only_rock, np.float32(1.0), w)
    switched = np.where(w >= SEAM_MID, np.maximum(z_direct_m, base_m), base_m)
    return (
        z_m.astype(np.float32),
        missing & (fraction <= 0.0),
        w,
        switched.astype(np.float32),
    )


def rock_kept(
    z_rock_cm: F32Grid,
    missing: BoolMask,
    linear: Taps,
    wet_plane: U8Grid | None,
    sea: OpenSea | None,
) -> F32Grid | None:
    """The share of its coverage a rock keeps under the void; None without the open sea.

    Out of the sea a rock keeps all of it; under the sea's level none on no data, else what
    the void's cover leaves of the wet share. The open sea always comes with its wet plane.
    """
    if sea is None:
        return None
    above = np.clip(z_rock_cm / np.float32(100.0) - np.float32(OCEAN_LEVEL_M) + 0.5, 0.0, 1.0)
    if reads_nothing(sea.void.cover, linear):
        under = np.where(missing, np.float32(1.0), np.float32(0.0))
    else:
        assert wet_plane is not None
        cover = np.clip(sample_plain(sea.void.cover, linear) / np.float32(255.0), 0.0, 1.0)
        under = np.where(missing, np.float32(1.0), cover * sample_coverage(wet_plane, linear))
    return (1.0 - (1.0 - above) * under).astype(np.float32)
