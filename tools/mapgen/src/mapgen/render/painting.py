"""A piece of a band coloured in one layer's style, over the ground every layer of the pass
shares.

``layer_job`` builds what a layer's bands read, once, over the window's columns;
``paint_band`` colours one piece of a band of it over a ``render/surface.py`` ground, then
the void and the falls, reading the piece's own columns of the job.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.hillshade import FLAT_SUN_DOT, flat_shade, hillshade, slope_degrees, sun_dot
from mapgen.palette.painted.band import painted_colours, painted_ndl
from mapgen.palette.painted.ground import ROCK_GRID_M, PaintedGround
from mapgen.palette.painted.shapes import PaintedScene, Sampler
from mapgen.palette.relief import BiomeSample, ReliefGround, relief_colours
from mapgen.palette.scene import BandGrid, BandScene, FloatGrid, ReliefScene, ShadedScene
from mapgen.palette.styles import (
    NOISE_SEED,
    PLAIN_LAYERS,
    biome_index,
    noise_fields,
    ramp_range,
    satellite_colours,
    terrain_colours,
    with_sea,
    with_void,
)
from mapgen.palette.water.falls import draw_falls
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.render.surface import (
    AxisTaps,
    BandSampling,
    BandSurface,
    GridTaps,
    GroundSources,
    cut_taps,
)
from mapgen.terrain.crown_stamp import LitCrowns, stamp_crowns
from mapgen.terrain.sample import (
    grid_position,
    reads_nothing,
    sample_noise,
    sample_plain,
    taps_footprint,
    taps_linear,
)
from satisfactory_mcp.core.arrays import BoolMask, F16Grid, F32Grid, F64Grid, I64Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["LayerJob", "domed_crowns", "layer_job", "paint_band"]

#: The flat ground's sun term, ``n.L`` of the default sun on level ground.
_FLAT_SUN = np.float32(FLAT_SUN_DOT)


@dataclass(frozen=True)
class SatelliteInputs:
    """What the satellite style adds: the biome colours and the noise octaves."""

    biome_rgb: U8Grid
    noise: list[tuple[F32Grid, float]]


@dataclass(frozen=True)
class PaintedInputs:
    """The painted style's ground and its column taps: its rock grid's and paint footprint's."""

    ground: PaintedGround
    rock_step: float
    rock_h: int
    rock_cols: AxisTaps
    footprint: float
    paint_cols: AxisTaps


@dataclass(frozen=True)
class LayerJob:
    """One layer's half of a draw: what its painter reads, built once, over the pass's ground.

    The column arrays and taps span the window; a piece reads its own columns of them. The
    pieces only read it, so any number of threads may share it.
    """

    layer: str
    ground: GroundSources
    size: int
    column_index: I64Grid
    biome_width: int
    biome_cols: I64Grid
    ramp: tuple[float, float]
    unlit: bool
    satellite: SatelliteInputs | None
    painted: PaintedInputs | None
    relief: ReliefGround | None
    falls: F64Grid | None

    @property
    def seabed(self) -> bool:
        """Whether the meshes in the water are the seabed's: in every style but painted."""
        return self.layer != "painted"


def layer_job(
    layer: str,
    ground: GroundSources,
    size: int,
    biome_rgb: U8Grid | None,
    biome_width: int,
    painted: PaintedGround | None,
    relief: ReliefGround | None,
    falls: F64Grid | None,
    unlit: bool,
) -> LayerJob:
    """The layer's own half of a draw: its style's inputs and the columns it reads them on."""
    field, window, x_cm = ground.field, ground.window, ground.x_cm
    if (layer == "painted") != (painted is not None):
        raise ValueError("the painted ground is the painted layer's, and only its")
    if layer not in PLAIN_LAYERS and painted is None and relief is None:
        raise ValueError(f"no painter draws {layer!r}")
    satellite = None
    if layer == "satellite":
        if biome_rgb is None:
            raise ValueError("the satellite layer is coloured from the biome raster")
        satellite = SatelliteInputs(biome_rgb, noise_fields(NOISE_SEED))
    return LayerJob(
        layer=layer,
        ground=ground,
        size=size,
        column_index=np.arange(window.c0, window.c1),
        biome_width=biome_width,
        biome_cols=biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], biome_width),
        ramp=ramp_range(field),
        unlit=unlit,
        satellite=satellite,
        painted=None if painted is None else _painted_inputs(painted, field, x_cm, ground),
        relief=relief,
        falls=falls,
    )


def _painted_inputs(
    painted: PaintedGround, field: hf.Field, x_cm: F64Grid, ground: GroundSources
) -> PaintedInputs:
    """The painted ground's column taps on its rock grid and over each pixel's footprint."""
    rock_step = field.spacing_cm * ROCK_GRID_M
    rock_h, rock_w = painted.rock[0].shape
    footprint = ground.spacing_m * 100.0 / field.spacing_cm
    field_x = grid_position(x_cm, field.x0_cm, field.spacing_cm, field.width)
    return PaintedInputs(
        ground=painted,
        rock_step=rock_step,
        rock_h=rock_h,
        rock_cols=taps_linear(grid_position(x_cm, field.x0_cm, rock_step, rock_w), rock_w),
        footprint=footprint,
        paint_cols=taps_footprint(field_x, footprint, field.width),
    )


def paint_band(job: LayerJob, grid: BandSampling, surface: BandSurface) -> FloatGrid:
    """One piece of a band in the layer's style over its ground, then the void and the falls."""
    rows, z_m = grid.rows, surface.z_m
    y_cm = job.ground.y_cm[rows.lo : rows.hi]
    scene: BandScene = {
        "z_m": z_m,
        "borrow": surface.borrow,
        "ramp_lo": job.ramp[0],
        "ramp_hi": job.ramp[1],
        "water": surface.water,
    }
    if job.painted is not None:
        rgb = _painted_colours(job, job.painted, grid, surface, scene)
    elif job.relief is not None:
        relief: ReliefScene = {**scene, "spacing_m": job.ground.spacing_m, "unlit": job.unlit}
        biome_rows = biome_index(y_cm, BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], job.biome_width)
        rgb = relief_colours(
            relief,
            job.relief,
            _sampler(grid.linear),
            _picker(biome_rows, job.biome_cols[grid.cols.cut]),
        )
    else:
        rgb = _style_colours(job, grid, scene)
    sea = job.ground.sea
    rgb = _void(rgb, surface.missing, sea, grid.linear, surface.weight, z_m)
    return draw_falls(rgb, job.falls, job.layer, grid.x_cm, y_cm, z_m, job.ground.spacing_m)


def _style_colours(job: LayerJob, grid: BandSampling, scene: BandScene) -> FloatGrid:
    """A style with a plain painter: the terrain and satellite ramps over the hillshade."""
    z_m, spacing_m = scene["z_m"], job.ground.spacing_m
    shade = flat_shade(z_m.shape) if job.unlit else hillshade(z_m, spacing_m)
    shaded: ShadedScene = {**scene, "shade": shade}
    if job.satellite is None:
        return terrain_colours(shaded)
    lo, hi, cut = grid.rows.lo, grid.rows.hi, grid.cols.cut
    y_cm = job.ground.y_cm[lo:hi]
    slope = slope_degrees(z_m, spacing_m)
    biome_rows = biome_index(y_cm, BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], job.biome_width)
    biome = job.satellite.biome_rgb[np.ix_(biome_rows, job.biome_cols[cut])]
    noise = job.satellite.noise
    return satellite_colours(
        {
            **shaded,
            "slope": slope,
            "biome_rgb": biome.astype(np.float32),
            "noise": sample_noise(noise, np.arange(lo, hi), job.column_index[cut], job.size),
        }
    )


def _painted_colours(
    job: LayerJob,
    painted: PaintedInputs,
    grid: BandSampling,
    surface: BandSurface,
    scene: BandScene,
) -> FloatGrid:
    """The game-painted style's band: its rock weight, crowns, sun term and water optics."""
    rows, cols, z_m, field = grid.rows, grid.cols, surface.z_m, job.ground.field
    spacing_m = job.ground.spacing_m
    y_cm = job.ground.y_cm[rows.lo : rows.hi]
    rock_rows = taps_linear(
        grid_position(y_cm, field.y0_cm, painted.rock_step, painted.rock_h), painted.rock_h
    )
    rock_weight = (
        np.zeros(z_m.shape, np.float32) if surface.rock_seen is None else surface.rock_seen
    )
    if surface.top_weight is not None:
        rock_weight = np.maximum(rock_weight, surface.top_weight)
    ground = painted.ground
    crowns = domed_crowns(ground, grid.x_cm, y_cm, spacing_m, job.unlit)
    meshes = (surface.mesh_weight, surface.mesh_class, surface.level_m)
    band: PaintedScene = {
        **scene,
        "crowns": crowns,
        "ndl": painted_ndl(z_m, spacing_m, job.unlit, meshes),
        "ndl_flat": _FLAT_SUN,
        "rock_weight": rock_weight,
        "top_weight": surface.top_weight,
        "mesh_weight": surface.mesh_weight,
        "mesh_class": surface.mesh_class,
        "mesh_family": _band_family(job.ground.meshes, (rows.cut, cols.cut)),
        "water_optics": ground.water_optics(grid.linear, surface.water.get("river")),
        "grid": BandGrid((rows.cut, cols.cut), rows.lo, rows.hi, cols.lo, cols.hi, spacing_m),
    }
    paint: GridTaps = (
        taps_footprint(grid.field_y, painted.footprint, field.height),
        cut_taps(painted.paint_cols, cols.cut),
    )
    rock: GridTaps = (rock_rows, cut_taps(painted.rock_cols, cols.cut))
    return painted_colours(
        band,
        ground,
        _sampler(paint),
        _sampler(rock),
    )


def domed_crowns(
    painted: PaintedGround,
    x_cm: F64Grid,
    y_cm: F64Grid,
    spacing_m: float,
    unlit: bool = False,
) -> LitCrowns | None:
    """The crowns over these pixel centres, with their domes lit by the shared sun."""
    if painted.crowns is None:
        return None
    stamped = stamp_crowns(painted.crowns, x_cm, y_cm, spacing_m * 100.0)
    dome = stamped["dome_m"] * np.float32(painted.palette["crowns"]["dome_gain"])
    ndl = np.full(dome.shape, _FLAT_SUN) if unlit else sun_dot(dome, spacing_m)
    return {**stamped, "ndl": ndl}


def _sampler(taps: GridTaps) -> Sampler:
    """A plane interpolated at ``taps``: the painters' way to read the field's grids."""

    def sample(plane: NDArray[np.generic]) -> F32Grid:
        return sample_plain(plane, taps)

    return sample


def _picker(rows: I64Grid, cols: I64Grid) -> BiomeSample:
    """A plane's texels at ``rows`` by ``cols``: the biome raster read nearest."""

    def pick(plane: F16Grid) -> F16Grid:
        return plane[np.ix_(rows, cols)]

    return pick


def _band_family(meshes: MeshPlanes | None, cut: tuple[slice, slice]) -> U8Grid | None:
    """The render-only meshes' rock family on this piece; None for a cache without the plane."""
    return (
        None
        if meshes is None or meshes.family is None
        else np.asarray(meshes.family[cut], np.uint8)
    )


def _void(
    rgb: FloatGrid,
    missing: BoolMask,
    sea: OpenSea | None,
    linear: GridTaps,
    rock: F32Grid | None,
    z_m: FloatGrid,
) -> FloatGrid:
    """A finished band under the void: no data at all, and the open sea's void planes with
    the cover and rim kept off the rocks a pixel's ``rock`` coverage holds where they stand
    out of the sea; without the open sea, no data only, in the page's sea."""
    if sea is None:
        return with_sea(rgb, missing)
    if not missing.any() and all(reads_nothing(p, linear) for p in (sea.void.cover, sea.void.rim)):
        return rgb.astype(np.result_type(rgb, np.float32), copy=False)
    cover, falloff, pit, rim = (sample_plain(p, linear) / np.float32(255.0) for p in sea.void)
    if rock is not None:
        # A rock deep in the void, under the sea's level, is the void's, as the artwork has it.
        standing = rock * np.clip(z_m - np.float32(OCEAN_LEVEL_M) + 0.5, 0.0, 1.0)
        cover, rim = cover * (1.0 - standing), rim * (1.0 - standing)
    cover = np.where(missing, np.float32(1.0), np.clip(cover, 0.0, 1.0))
    return with_void(rgb, cover, falloff, pit, rim)
