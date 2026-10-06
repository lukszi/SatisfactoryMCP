"""What one band hands a painter: the scene, its water terms, crowns, optics and grid."""

from __future__ import annotations

from typing import NamedTuple, Required, TypedDict

import numpy as np

from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["BandGrid", "BandScene", "CrownBand", "CrownLayer", "WaterOptics", "WaterTerms"]


class BandGrid(NamedTuple):
    """Where a band sits: its rows of the sheet with halo, its columns, and the pixel size."""

    band: slice
    lo: int
    hi: int
    c0: int
    c1: int
    spacing_m: float


class WaterTerms(TypedDict, total=False):
    """A band's water per pixel: cover, depth and the shore's terms, and the river's share."""

    banks: F32Grid
    cover: F32Grid
    depth: F32Grid
    depth_m: F32Grid
    ocean: F32Grid
    edge: F32Grid
    above_m: F32Grid
    below_m: F32Grid
    river: F32Grid
    river_below_m: F32Grid
    wet: F32Grid


class CrownBand(TypedDict, total=False):
    """The crowns stamped over a band: cover, linear colour, dome and top, and the dome's sun."""

    cover: F32Grid
    rgb: F32Grid
    dome_m: F32Grid
    top_cm: F32Grid
    ndl: F32Grid


class CrownLayer(TypedDict):
    """The crowns lit and ready to lay over the pixel: alpha, colour, top and sunk share."""

    alpha: F32Grid
    colour: F32Grid
    top_m: F32Grid
    sunk: F32Grid


class WaterOptics(TypedDict, total=False):
    """Per-pixel water optics of the painted style's classes, and the class shares asked for."""

    k: F32Grid
    body: F32Grid
    deep: F32Grid
    deep_tau_m: F32Grid
    turbidity: F32Grid
    tint: F32Grid
    share: dict[int, F32Grid]


class BandScene(TypedDict, total=False):
    """One band as every painter reads it; the painted, relief and satellite keys are their own."""

    z_m: Required[F32Grid]
    borrow: Required[F32Grid]
    ramp_lo: Required[float]
    ramp_hi: Required[float]
    water: Required[WaterTerms]
    spacing_m: float
    unlit: bool
    shade: F32Grid
    slope: F32Grid
    biome_rgb: F32Grid
    noise: np.ndarray
    crowns: CrownBand | None
    ndl: F32Grid
    ndl_flat: np.float32
    rock_weight: F32Grid
    mesh_weight: F32Grid | None
    mesh_class: np.ndarray | None
    mesh_family: np.ndarray | None
    water_optics: WaterOptics | None
    grid: BandGrid
