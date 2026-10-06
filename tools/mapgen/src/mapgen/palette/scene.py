"""What the painters share: one band's scene, its water terms, crowns, optics and grid, and
the run's water planes and heights."""

from __future__ import annotations

from typing import NamedTuple, NotRequired, Protocol, TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import I16Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BandGrid",
    "BandScene",
    "BandTaps",
    "CrownBand",
    "CrownLayer",
    "FloatGrid",
    "PaintedScene",
    "ReconciledWater",
    "ReliefScene",
    "SatelliteScene",
    "ShadedScene",
    "WaterOptics",
    "WaterPlanes",
    "WaterTerms",
    "field_heights",
    "field_water",
]

#: A float plane of either width. numpy's stubs widen float32 arithmetic to float64, so a
#: painter's planes are typed by kind; the dtype they hold is float32 throughout.
FloatGrid: TypeAlias = NDArray[np.floating]

#: The water a run draws: the level plane in decimetres and its quality grades, which a field
#: written before the quality byte lacks.
WaterPlanes: TypeAlias = tuple[I16Grid, U8Grid | None]

#: How a 1 m plane is read onto a band (``terrain.sample``): per axis, its taps' indices and
#: weights, rows first.
BandTaps: TypeAlias = tuple[
    tuple[NDArray[np.integer], FloatGrid], tuple[NDArray[np.integer], FloatGrid]
]


class ReconciledWater(Protocol):
    """The field's water with the river boxes' share taken back (``water.rivers.RiverWater``)."""

    @property
    def water_dm(self) -> I16Grid: ...

    @property
    def grades(self) -> U8Grid: ...


def field_heights(field: hf.Field) -> I16Grid:
    """The field's height plane in decimetres, which a loaded field always holds."""
    heights = field.height_dm
    if heights is None:
        raise ValueError(f"the field in {field.directory} has no height plane")
    return heights


def field_water(field: hf.Field) -> tuple[I16Grid, U8Grid]:
    """The field's own water planes, for a step that cannot draw without both."""
    level, grades = field.water_raster(), field.water_quality_raster()
    if level is None or grades is None:
        raise ValueError(f"the field in {field.directory} has no water or no quality plane")
    return level, grades


class BandGrid(NamedTuple):
    """Where a band sits: its rows of the sheet with halo, its columns, and the pixel size."""

    band: slice
    lo: int
    hi: int
    c0: int
    c1: int
    spacing_m: float


class WaterTerms(TypedDict):
    """A band's water per pixel: cover, depth and the shore's terms, and the river's share."""

    banks: FloatGrid
    cover: FloatGrid
    depth: FloatGrid
    depth_m: FloatGrid
    ocean: FloatGrid
    edge: FloatGrid
    above_m: FloatGrid
    below_m: FloatGrid
    river: FloatGrid
    river_below_m: FloatGrid
    wet: NotRequired[FloatGrid]


class CrownBand(TypedDict, total=False):
    """The crowns stamped over a band: cover, linear colour, dome and top, and the dome's sun."""

    cover: FloatGrid
    rgb: FloatGrid
    dome_m: FloatGrid
    top_cm: FloatGrid
    ndl: FloatGrid


class CrownLayer(TypedDict):
    """The crowns lit and ready to lay over the pixel: alpha, colour, top and sunk share."""

    alpha: FloatGrid
    colour: FloatGrid
    top_m: FloatGrid
    sunk: FloatGrid


class WaterOptics(TypedDict, total=False):
    """Per-pixel water optics of the painted style's classes, and the class shares asked for."""

    k: FloatGrid
    body: FloatGrid
    deep: FloatGrid
    deep_tau_m: FloatGrid
    turbidity: FloatGrid
    tint: FloatGrid
    share: dict[int, FloatGrid]


class BandScene(TypedDict):
    """One band as every painter reads it: heights, the borrowed light, the ramp, the water."""

    z_m: FloatGrid
    borrow: FloatGrid
    ramp_lo: float
    ramp_hi: float
    water: WaterTerms


class ShadedScene(BandScene):
    """A band of the hypsometric terrain style: the hillshade over it."""

    shade: FloatGrid


class SatelliteScene(ShadedScene):
    """A band of the satellite style: its slope, biome colour and noise too."""

    slope: FloatGrid
    biome_rgb: FloatGrid
    noise: FloatGrid


class ReliefScene(BandScene):
    """A band of a relief style: its pixel size, and whether it is drawn unlit."""

    spacing_m: float
    unlit: NotRequired[bool]


class PaintedScene(BandScene):
    """A band of the painted style: its crowns, sun term, rock and mesh weights and optics."""

    crowns: CrownBand | None
    ndl: FloatGrid
    ndl_flat: np.float32
    rock_weight: FloatGrid
    mesh_weight: FloatGrid | None
    mesh_class: U8Grid | None
    mesh_family: U8Grid | None
    water_optics: WaterOptics | None
    grid: BandGrid
