"""Synthetic height rasters for the light's kernels: no install, no field."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from satisfactory_mcp.core.arrays import F32Grid


def octave_terrain(side: int, seed: int) -> F32Grid:
    """Three octaves of smooth noise, metres, ``side`` by ``side``; held off zero, so no
    signed zero reaches a comparison."""
    rng = np.random.default_rng(seed)
    z = np.full((side, side), 0.37, np.float32)
    for octave, amp in ((4, 40.0), (16, 8.0), (64, 2.0)):
        small = rng.standard_normal((octave + 3, octave + 3)).astype(np.float32)
        z += amp * ndimage.zoom(small, side / octave, order=3)[:side, :side]
    return z
