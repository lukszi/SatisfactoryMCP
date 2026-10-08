"""A piece of a band coloured in one layer's style, over the ground every layer of the pass
shares.

``layer_job`` builds what a layer's bands read, once, over the window's columns;
``paint_band`` colours one piece of a band of it over a ``render/ground/surface.py`` ground, then
the void and the falls, reading the piece's own columns of the job.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.jit import gpu_on
from mapgen.lighting.hillshade import FLAT_SUN_DOT, flat_shade, hillshade, sun_dot
from mapgen.palette.painted.band import painted_colours, painted_ndl
from mapgen.palette.painted.ground import ROCK_GRID_M, PaintedGround
from mapgen.palette.painted.shapes import PaintedScene, Sampler
from mapgen.palette.painted.trees import canopy_cover
from mapgen.palette.relief import BiomeSample, ReliefGround, relief_colours
from mapgen.palette.scene import BandGrid, BandScene, FloatGrid, ReliefScene, ShadedScene
from mapgen.palette.styles import (
    PLAIN_LAYERS,
    biome_index,
    ramp_range,
    terrain_colours,
    with_sea,
    with_void,
)
from mapgen.palette.water.falls import FALL_STYLES, draw_falls
from mapgen.palette.water.open_sea import OpenSea
from mapgen.render.ground.surface import (
    AxisTaps,
    BandSampling,
    BandSurface,
    GridTaps,
    GroundSources,
    cut_taps,
)
from mapgen.render.ground.void import DrawnVoid
from mapgen.terrain.crown_stamp import LitCrowns, stamp_crowns
from mapgen.terrain.sample import grid_position, sample_plain, taps_footprint, taps_linear
from satisfactory_mcp.core.arrays import BoolMask, F16Grid, F32Grid, F64Grid, I64Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["LayerJob", "domed_crowns", "layer_job", "paint_band", "piece_bytes"]

#: The flat ground's sun term, ``n.L`` of the default sun on level ground.
_FLAT_SUN = np.float32(FLAT_SUN_DOT)


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
    biome_width: int
    biome_cols: I64Grid
    ramp: tuple[float, float]
    unlit: bool
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
    biome_width: int,
    painted: PaintedGround | None,
    relief: ReliefGround | None,
    falls: F64Grid | None,
    unlit: bool,
) -> LayerJob:
    """The layer's own half of a draw: its style's inputs and the columns it reads them on."""
    field, x_cm = ground.field, ground.x_cm
    if (layer == "painted") != (painted is not None):
        raise ValueError("the painted ground is the painted layer's, and only its")
    if layer not in PLAIN_LAYERS and painted is None and relief is None:
        raise ValueError(f"no painter draws {layer!r}")
    return LayerJob(
        layer=layer,
        ground=ground,
        biome_width=biome_width,
        biome_cols=biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], biome_width),
        ramp=ramp_range(field),
        unlit=unlit,
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


def piece_bytes(job: LayerJob, grid: BandSampling, surface: BandSurface) -> U8Grid:
    """The piece's kept pixels in the layer's style, as bytes. With ``--gpu`` the terrain is
    drawn on the device (``render/gpu/terrain.py``), to the same bytes."""
    if gpu_on() and _plain_terrain(job):
        from mapgen.render.gpu.terrain import TerrainPiece, terrain_bytes

        piece = TerrainPiece(
            z_m=surface.z_m,
            spacing_m=None if job.unlit else job.ground.spacing_m,
            borrow=surface.borrow,
            water=surface.water,
            missing=surface.missing,
            void=surface.void,
            open_sea=job.ground.sea is not None,
            ramp=job.ramp,
            kept=(grid.rows.kept, grid.cols.kept),
        )
        done = terrain_bytes(piece)
        if done is not None:
            return done
    rgb = paint_band(job, grid, surface)
    return np.clip(rgb[grid.rows.kept, grid.cols.kept], 0, 255).astype(np.uint8)


def _plain_terrain(job: LayerJob) -> bool:
    """Whether the job is the terrain style's, which draws no falls."""
    plain = job.painted is None and job.relief is None
    return plain and job.layer in PLAIN_LAYERS and job.layer not in FALL_STYLES


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
    hidden: Callable[[], FloatGrid | None] | None = None
    if job.painted is not None:
        rgb, hidden = _painted_colours(job, job.painted, grid, surface, scene)
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
        rgb = _terrain_colours(job, scene)
    rgb = _void(rgb, surface.missing, job.ground.sea, surface.void)
    spacing_m = job.ground.spacing_m
    return draw_falls(rgb, job.falls, job.layer, grid.x_cm, y_cm, z_m, spacing_m, hidden)


def _terrain_colours(job: LayerJob, scene: BandScene) -> FloatGrid:
    """The plain painter: the terrain ramp over the hillshade."""
    z_m = scene["z_m"]
    shade = flat_shade(z_m.shape) if job.unlit else hillshade(z_m, job.ground.spacing_m)
    shaded: ShadedScene = {**scene, "shade": shade}
    return terrain_colours(shaded)


def _painted_colours(
    job: LayerJob,
    painted: PaintedInputs,
    grid: BandSampling,
    surface: BandSurface,
    scene: BandScene,
) -> tuple[FloatGrid, Callable[[], FloatGrid | None]]:
    """The game-painted style's band: its rock weight, crowns, sun term and water optics; and
    what the crowns and Titan trees hide of it, worked out when asked."""
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
    meshes = (surface.mesh_weight, surface.mesh_class, surface.level_m, surface.mesh_land)
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
        "unlit": job.unlit,
    }
    paint: GridTaps = (
        taps_footprint(grid.field_y, painted.footprint, field.height),
        cut_taps(painted.paint_cols, cols.cut),
    )
    rock: GridTaps = (rock_rows, cut_taps(painted.rock_cols, cols.cut))
    rgb = painted_colours(band, ground, _sampler(paint), _sampler(rock))
    return rgb, partial(canopy_cover, band, ground)


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
    rgb: FloatGrid, missing: BoolMask, sea: OpenSea | None, void: DrawnVoid | None
) -> FloatGrid:
    """A finished band under the void: the open sea's void as the ground drew it
    (``render/ground/void.py``); without the open sea, no data only, in the page's sea."""
    if sea is None:
        return with_sea(rgb, missing)
    if void is None:
        return rgb.astype(np.result_type(rgb, np.float32), copy=False)
    return with_void(rgb, *void)
