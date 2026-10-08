"""Which of a placed rock's triangles each raster pass over a band draws.

A rock is transformed once per band and its triangles sorted once: those reaching the band
that face up (every one where the winding is unknown), which the top and the floor draw, and
those facing down high enough over the rock's foot to be an underside. The numpy here is the
reference ``kernels.band_faces`` and ``kernels.extent`` reproduce.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.jit import kernels_on
from satisfactory_mcp.core.arrays import FloatGrid, I32Grid, I64Grid

__all__ = ["Faces", "band_faces", "extent"]


class Faces(NamedTuple):
    """Row numbers of a mesh's triangles: facing up, and the undersides."""

    up: I32Grid
    down: I32Grid


def band_faces(
    world: FloatGrid, tris: I64Grid, facing: int, bounds: tuple[float, float], rise: float | None
) -> Faces:
    """``tris`` over the vertices ``world`` (cm) whose Y interval reaches ``bounds``, sorted by
    their facing (``facing`` is the winding's sign, 0 where it is unknown).

    ``down`` is empty unless ``rise`` is given: the downward faces whose highest corner is
    more than ``rise`` over the lowest vertex.
    """
    if kernels_on():
        from mapgen.terrain.maxz import kernels

        lid = np.array(bounds, np.float32)
        return Faces(
            *kernels.band_faces(world, tris, lid, facing, np.nan if rise is None else rise)
        )
    ty = world[:, 1][tris]
    rows = np.flatnonzero((ty.max(1) >= bounds[0]) & (ty.min(1) <= bounds[1])).astype(np.int32)
    if not facing:
        return Faces(rows, rows[:0])
    tri = world[tris[rows]]
    up = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2] * facing
    if rise is None:
        return Faces(rows[up > 0], rows[:0])
    lowest = float(world[:, 2].min())
    return Faces(rows[up > 0], rows[(up < 0) & (tri[:, :, 2].max(1) > lowest + rise)])


def extent(
    world: FloatGrid, tris: I64Grid, rows: I32Grid, frame: tuple[float, float, float]
) -> FloatGrid:
    """``(x low, x high, y low, y high)`` of the corners of ``tris``' ``rows`` in texels of the
    grid ``frame`` (origin x, origin y, scale in cm); NaN where a corner is."""
    if kernels_on():
        from mapgen.terrain.maxz import kernels

        return kernels.extent(world, tris, rows, np.array(frame, world.dtype))
    tri = world[tris[rows]]
    fx = (tri[:, :, 0] - frame[0]) / frame[2]
    fy = (tri[:, :, 1] - frame[1]) / frame[2]
    return np.array([fx.min(), fx.max(), fy.min(), fy.max()], world.dtype)
