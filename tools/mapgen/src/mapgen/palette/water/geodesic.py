"""Distance counted in steps through a mask, as a flood grows out from its seeds."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from satisfactory_mcp.core.arrays import BoolMask, I16Grid

__all__ = ["geodesic_steps"]

_CROSS = ndimage.generate_binary_structure(2, 1)
_SQUARE = ndimage.generate_binary_structure(2, 2)


def geodesic_steps(
    seed: BoolMask, inside: BoolMask, limit: int, *, octagon: bool = False
) -> I16Grid:
    """Steps from ``seed`` to each texel of ``inside``, through ``inside``; ``limit + 1`` past
    ``limit``, on the seeds and off ``inside``. A step reaches the 4-neighbours, or with
    ``octagon`` the 8- and the 4-neighbours in turn, an octagon close to the circle."""
    out = np.full(seed.shape, limit + 1, np.int16)
    front, reached = seed, seed.copy()
    for step in range(1, limit + 1):
        shape = _SQUARE if octagon and step % 2 else _CROSS
        front = ndimage.binary_dilation(front, shape) & inside & ~reached
        if not front.any():
            break
        out[front] = step
        reached |= front
    return out
