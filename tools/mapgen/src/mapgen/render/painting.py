"""A band coloured in one layer's style, over the ground every layer of the pass shares.

``layer_job`` builds what a layer's bands read, once; ``paint_band`` colours one band of it
over a ``render/surface.py`` ground, then the void and the falls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias, cast

import numpy as np

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.hillshade import FLAT_SUN_DOT, flat_shade, hillshade, slope_degrees, sun_dot
from mapgen.palette.painted.band import painted_colours, painted_ndl
from mapgen.palette.painted.ground import ROCK_GRID_M, PaintedGround
from mapgen.palette.relief import ReliefGround, relief_colours
from mapgen.palette.scene import BandGrid, BandScene, FloatGrid, SatelliteScene, WaterOptics
from mapgen.palette.styles import (
    LAYER_PAINTERS,
    NOISE_SEED,
    biome_index,
    noise_fields,
    ramp_range,
    with_sea,
    with_void,
)
from mapgen.palette.water.falls import draw_falls
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.render.surface import AxisTaps, BandSampling, BandSurface, GridTaps, GroundSources
from mapgen.terrain.crown_stamp import CrownBand, stamp_crowns
from mapgen.terrain.sample import (
    grid_position,
    reads_nothing,
    sample_noise,
    sample_plain,
    taps_footprint,
    taps_linear,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["LayerJob", "domed_crowns", "layer_job", "paint_band"]

#: The flat ground's sun term, ``n.L`` of the default sun on level ground.
_FLAT_SUN = np.float32(FLAT_SUN_DOT)

#: A style's colours for one band's scene, sRGB 0..255.
Painter: TypeAlias = Callable[[SatelliteScene], np.ndarray]
#: How a painter reads a plane of its own grid over a band.
PlaneReader: TypeAlias = Callable[[np.ndarray], np.ndarray]


class _Scene(BandScene, total=False):
    """A band's scene as it is built: the base, then the keys its layer's painter reads."""

    spacing_m: float
    unlit: bool
    shade: FloatGrid
    slope: FloatGrid
    biome_rgb: FloatGrid
    noise: FloatGrid
    crowns: CrownBand | None
    ndl: FloatGrid
    ndl_flat: np.float32
    rock_weight: FloatGrid
    mesh_weight: np.ndarray | None
    mesh_class: np.ndarray | None
    mesh_family: np.ndarray | None
    water_optics: WaterOptics | None
    grid: BandGrid


@dataclass(frozen=True)
class SatelliteInputs:
    """What the satellite style adds: the biome colours and the noise octaves."""

    biome_rgb: np.ndarray
    noise: list[tuple[np.ndarray, float]]


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

    The bands only read it, so any number of threads may share it.
    """

    layer: str
    ground: GroundSources
    size: int
    column_index: I64Grid
    biome_width: int
    biome_cols: I64Grid
    ramp: tuple[float, float]
    unlit: bool
    painter: Painter | None
    satellite: SatelliteInputs | None
    painted: PaintedInputs | None
    relief: ReliefGround | None
    falls: np.ndarray | None

    @property
    def seabed(self) -> bool:
        """Whether the meshes in the water are the seabed's: in every style but painted."""
        return self.layer != "painted"


def layer_job(
    layer: str,
    ground: GroundSources,
    size: int,
    biome_rgb: np.ndarray | None,
    biome_width: int,
    painted: PaintedGround | None,
    relief: ReliefGround | None,
    falls: np.ndarray | None,
    unlit: bool,
) -> LayerJob:
    """The layer's own half of a draw: its style's inputs and the columns it reads them on."""
    field, window, x_cm = ground.field, ground.window, ground.x_cm
    painter = LAYER_PAINTERS.get(layer)
    if (layer == "painted") != (painted is not None):
        raise ValueError("the painted ground is the painted layer's, and only its")
    if painter is None and painted is None and relief is None:
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
        painter=painter,
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


def paint_band(job: LayerJob, grid: BandSampling, surface: BandSurface) -> np.ndarray:
    """One band in the layer's style over its ground, then the void and the falls."""
    rows, z_m = grid.rows, surface.z_m
    y_cm = job.ground.y_cm[rows.lo : rows.hi]
    scene: _Scene = {
        "z_m": z_m,
        "borrow": surface.borrow,
        "ramp_lo": job.ramp[0],
        "ramp_hi": job.ramp[1],
        "water": surface.water,
    }
    if job.painted is not None:
        rgb = _painted_colours(job, job.painted, grid, surface, scene)
    elif job.relief is not None:
        scene["spacing_m"] = job.ground.spacing_m
        scene["unlit"] = job.unlit
        biome_rows = biome_index(y_cm, BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], job.biome_width)
        rgb = relief_colours(
            _as_painter_dict(scene),
            job.relief,
            _sampler(grid.linear),
            _picker(biome_rows, job.biome_cols),
        )
    else:
        rgb = _style_colours(job, grid, scene)
    sea = job.ground.sea
    rgb = _void(rgb, surface.missing, sea, grid.linear, surface.weight, z_m)
    return draw_falls(rgb, job.falls, job.layer, job.ground.x_cm, y_cm, z_m, job.ground.spacing_m)


def _style_colours(job: LayerJob, grid: BandSampling, scene: _Scene) -> np.ndarray:
    """A style with a plain painter: the terrain and satellite ramps over the hillshade."""
    assert job.painter is not None
    z_m, spacing_m = scene["z_m"], job.ground.spacing_m
    scene["shade"] = flat_shade(z_m.shape) if job.unlit else hillshade(z_m, spacing_m)
    if job.satellite is not None:
        lo, hi = grid.rows.lo, grid.rows.hi
        y_cm = job.ground.y_cm[lo:hi]
        scene["slope"] = slope_degrees(z_m, spacing_m)
        biome_rows = biome_index(y_cm, BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], job.biome_width)
        biome = job.satellite.biome_rgb[np.ix_(biome_rows, job.biome_cols)]
        scene["biome_rgb"] = biome.astype(np.float32)
        noise = job.satellite.noise
        scene["noise"] = sample_noise(noise, np.arange(lo, hi), job.column_index, job.size)
    return job.painter(cast(SatelliteScene, scene))


def _painted_colours(
    job: LayerJob,
    painted: PaintedInputs,
    grid: BandSampling,
    surface: BandSurface,
    scene: _Scene,
) -> np.ndarray:
    """The game-painted style's band: its rock weight, crowns, sun term and water optics."""
    rows, z_m, field = grid.rows, surface.z_m, job.ground.field
    window, spacing_m = job.ground.window, job.ground.spacing_m
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
    scene["crowns"] = domed_crowns(ground, job.ground.x_cm, y_cm, spacing_m, job.unlit)
    meshes = (surface.mesh_weight, surface.mesh_class, surface.level_m)
    scene["ndl"] = painted_ndl(z_m, spacing_m, job.unlit, meshes)
    scene["ndl_flat"] = _FLAT_SUN
    scene["rock_weight"] = rock_weight
    scene["mesh_weight"] = surface.mesh_weight
    scene["mesh_class"] = surface.mesh_class
    scene["mesh_family"] = _band_family(job.ground.meshes, rows.band)
    optics = ground.water_optics(grid.linear, surface.water.get("river"))
    scene["water_optics"] = cast("WaterOptics | None", optics)
    scene["grid"] = BandGrid(rows.band, rows.lo, rows.hi, window.c0, window.c1, spacing_m)
    paint: GridTaps = (
        taps_footprint(grid.field_y, painted.footprint, field.height),
        painted.paint_cols,
    )
    rock: GridTaps = (rock_rows, painted.rock_cols)
    return painted_colours(
        _as_painter_dict(scene),
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
) -> CrownBand | None:
    """The crowns over these pixel centres, with their domes lit by the shared sun."""
    if painted.crowns is None:
        return None
    stamped = stamp_crowns(painted.crowns, x_cm, y_cm, spacing_m * 100.0)
    dome = stamped["dome_m"] * np.float32(painted.palette["crowns"]["dome_gain"])
    stamped["ndl"] = np.full(dome.shape, _FLAT_SUN) if unlit else sun_dot(dome, spacing_m)
    return stamped


def _sampler(taps: GridTaps) -> PlaneReader:
    """A plane interpolated at ``taps``: the painters' way to read the field's grids."""

    def sample(plane: np.ndarray) -> np.ndarray:
        return sample_plain(plane, taps)

    return sample


def _picker(rows: np.ndarray, cols: np.ndarray) -> PlaneReader:
    """A plane's texels at ``rows`` by ``cols``: the biome raster read nearest."""

    def pick(plane: np.ndarray) -> np.ndarray:
        return plane[np.ix_(rows, cols)]

    return pick


def _as_painter_dict(scene: _Scene) -> dict[str, object]:
    """The scene as the painters take it, until they take a ``BandScene``: a cast, no copy."""
    return cast("dict[str, object]", scene)


def _band_family(meshes: MeshPlanes | None, band: slice) -> np.ndarray | None:
    """The render-only meshes' rock family on this band; None for a cache without the plane."""
    return None if meshes is None or meshes.family is None else np.asarray(meshes.family[band])


def _void(
    rgb: np.ndarray,
    missing: BoolMask,
    sea: OpenSea | None,
    linear: GridTaps,
    rock: F32Grid | None,
    z_m: np.ndarray,
) -> np.ndarray:
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
