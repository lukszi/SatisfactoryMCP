"""The raise-only rule by which rocks and the top raster lift the ground over the lattice.

``blend_regimes`` is the field's own composition rule at the render's spacing; the top raster
and the meshes go through the same smoothed lift (docs/spatial-and-map.md sections 20 and 25).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from mapgen.terrain.measure import SEAM_MID
from mapgen.terrain.rasters import pixel_coverage
from mapgen.terrain.sample import Taps
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid

__all__ = ["DIRECT_LIFT_KNEE_M", "blend_regimes", "composite_top", "smooth_lift"]

#: The knee of the smoothed positive part that lets a rock raise the ground and never lower
#: it, in metres: the field's own hard ``max`` with its corner rounded, so the hillshade draws
#: no line round a formation's base. It sits at most half the knee above the hard answer.
DIRECT_LIFT_KNEE_M = 0.25


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
