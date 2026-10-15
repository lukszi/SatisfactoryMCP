"""What a piece hands the light stage: the surface the seabed rule draws, with the meshes the
painted rule stands in the sea, and the detail normal with the rock look's tilt.

The seabed rule leaves coral, shells and terraces standing in the water to the seabed. The
painted layer draws them, so the light takes their heights and land weight from the painted
rule's surface there: the live sun lights them, and they cast and take shadows as the ground
does. A coral speck the painted layer draws as water stays water. docs/map/light-and-crowns.md
section 36, "Coral trees are no crowns", and docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast

import numpy as np

from mapgen.lighting.spans.slabs import SlabPlanes
from mapgen.palette.painted.rock_look.tilt import TiltPlanes, band_tilt
from mapgen.palette.painted.surfaces import sunk_specks
from mapgen.palette.water.shore import seabed_keeps
from mapgen.render.ground.detail import detail_bytes, drawn_share, with_tilt
from mapgen.render.ground.void import land_weight
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, I8Grid, U8Grid

if TYPE_CHECKING:
    from mapgen.palette.painted.shapes import PaintedScene
    from mapgen.render.ground.surface import BandSampling, BandSurface, GroundSources

__all__ = ["LightPlanes", "SeaMeshes", "light_planes", "sea_meshes"]


class LightPlanes(NamedTuple):
    """What the light captures of the output: the heights under the seabed rule with the
    painted rule's meshes in the sea, the land weight, what floats over them, None where
    nothing does, and the detail normal as bytes, None without detail textures."""

    z_m: F32Grid
    land: F32Grid
    slabs: SlabPlanes | None = None
    detail: I8Grid | None = None


class SeaMeshes(NamedTuple):
    """Where the painted rule stands a mesh in the sea, and that rule's heights, land weight,
    mesh weight and class there."""

    where: BoolMask
    z_m: FloatGrid
    land: FloatGrid
    mesh_weight: FloatGrid
    mesh_class: U8Grid


def sea_meshes(own: BandSurface) -> SeaMeshes | None:
    """The meshes of ``own``, the painted rule's surface, that the seabed rule leaves to the
    seabed (``shore.seabed_keeps``); None where there are none."""
    kept, weight = own.mesh_class, own.mesh_weight
    if kept is None or weight is None:
        return None
    where = (kept > 0) & ~seabed_keeps(kept, own.z_m, own.level_m, own.mesh_land)
    if not where.any():
        return None
    scene = {"water": own.water, "mesh_weight": weight, "mesh_class": kept}
    water = sunk_specks(cast("PaintedScene", scene))
    land = land_weight(own.missing, water["cover"], own.void)
    return SeaMeshes(where, own.z_m, land, weight, kept)


def light_planes(
    grid: BandSampling,
    rules: tuple[BandSurface, BandSurface],
    slabs: SlabPlanes | None,
    sources: GroundSources,
) -> LightPlanes:
    """The piece's output pixels for the light stage, cut to them: the heights, NaN where no
    data is drawn (``lighting/spans/holes.py``), and the land weight
    (``render/ground/void.py``) of ``rules``' seabed surface with its painted one's meshes in
    the sea over both, what floats over them (``render/ground/floating.py``) and, with
    detail textures, the detail normal where the ground is drawn rather than a rock or a mesh,
    with the rock look's tilt where the run has it (``rock_look/tilt.py``)."""
    lit, own = rules
    kept = (grid.rows.kept, grid.cols.kept)
    land = land_weight(lit.missing, lit.water["cover"], lit.void)
    z_m, mesh_weight, mesh_class = lit.z_m, lit.mesh_weight, lit.mesh_class
    sea = sea_meshes(own)
    if sea is not None:
        z_m = np.where(sea.where, sea.z_m, z_m)
        land = np.where(sea.where, sea.land, land)
        mesh_weight = np.where(sea.where, sea.mesh_weight, _or_zero(mesh_weight))
        mesh_class = np.where(sea.where, sea.mesh_class, _or_zero(mesh_class)).astype(np.uint8)
    detail = None
    if sources.textures is not None:
        drawn = drawn_share(z_m.shape, lit.rock_seen, lit.top_weight, mesh_weight)
        detail = detail_bytes(lit.detail, drawn)
        if sources.rock_tilt is not None:
            planes = TiltPlanes(
                z_m, lit.rock_seen, lit.top_weight, mesh_weight, mesh_class,
                _cut(sources.meshes.family if sources.meshes is not None else None, grid),
                (grid.rows.lo, grid.cols.lo, sources.spacing_m),
            )  # fmt: skip
            family = _cut(sources.rock_tilt.family, grid)
            detail = with_tilt(detail, band_tilt(sources.rock_tilt, planes, family))
        detail = detail[kept]
    z_m = np.where(lit.missing, np.float32(np.nan), z_m)
    return LightPlanes(z_m[kept], land[kept], slabs, detail)


def _or_zero(plane: FloatGrid | U8Grid | None) -> FloatGrid | U8Grid | int:
    return 0 if plane is None else plane


def _cut(plane: object, grid: BandSampling) -> U8Grid | None:
    """A raster plane on the piece, as bytes; None without it."""
    if plane is None:
        return None
    return np.asarray(cast("U8Grid", plane)[grid.rows.cut, grid.cols.cut], np.uint8)
