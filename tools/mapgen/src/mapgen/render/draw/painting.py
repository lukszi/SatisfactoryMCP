"""A piece of a band coloured in one layer's style, over the ground every layer of the pass
shares.

``layer_job`` builds what a layer's bands read, once, over the window's columns;
``paint_band`` colours one piece of a band of it over a ``render/ground/surface.py`` ground, then
the void and the falls, reading the piece's own columns of the job; ``draw_band`` also draws
the painted layer apart at its trees.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.jit import gpu_on
from mapgen.lighting.hillshade import FLAT_SUN_DOT, flat_shade, hillshade
from mapgen.palette.painted.band import PaintedParts, painted_colours, painted_ndl, painted_parts
from mapgen.palette.painted.ground import ROCK_GRID_M, PaintedGround
from mapgen.palette.painted.shapes import PaintedScene, Sampler
from mapgen.palette.painted.trees import canopy_cover, crown_sun
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
from mapgen.render.ground.detail import drawn_share
from mapgen.render.ground.surface import (
    AxisTaps,
    BandSampling,
    BandSurface,
    GridTaps,
    GroundSources,
    cut_taps,
)
from mapgen.render.ground.void import DrawnVoid
from mapgen.terrain.crown_stamp import (
    CrownSet,
    LitCrowns,
    crown_placements,
    stamp_crowns,
    stamp_placed,
)
from mapgen.terrain.sample import grid_position, sample_plain, taps_footprint, taps_linear
from satisfactory_mcp.core.arrays import BoolMask, F16Grid, F32Grid, F64Grid, I64Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "DrawnBand",
    "LayerJob",
    "PieceBytes",
    "TreeSplit",
    "draw_band",
    "layer_job",
    "paint_band",
    "piece_bytes",
    "stamped_crowns",
]

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
    split: bool = False

    @property
    def seabed(self) -> bool:
        """Whether the meshes in the water are the seabed's: in every style but painted."""
        return self.layer != "painted"


class TreeSplit(NamedTuple):
    """A piece or band of a layer drawn apart at its trees, as bytes: the ground without them
    (RGB) and the trees (RGBA, straight alpha)."""

    ground: U8Grid
    trees: U8Grid


class PieceBytes(NamedTuple):
    """A piece's kept pixels as bytes, and its parts when the layer is drawn apart."""

    colour: U8Grid
    split: TreeSplit | None = None


def layer_job(
    layer: str,
    ground: GroundSources,
    biome_width: int,
    painted: PaintedGround | None,
    relief: ReliefGround | None,
    falls: F64Grid | None,
    unlit: bool,
    split: bool = False,
) -> LayerJob:
    """The layer's own half of a draw: its style's inputs and the columns it reads them on.
    ``split`` draws the painted layer, unlit, apart at its trees as well."""
    field, x_cm = ground.field, ground.x_cm
    if (layer == "painted") != (painted is not None):
        raise ValueError("the painted ground is the painted layer's, and only its")
    if layer not in PLAIN_LAYERS and painted is None and relief is None:
        raise ValueError(f"no painter draws {layer!r}")
    if split and (painted is None or not unlit):
        raise ValueError("only the painted layer drawn unlit is drawn apart at its trees")
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
        split=split,
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


def piece_bytes(job: LayerJob, grid: BandSampling, surface: BandSurface) -> PieceBytes:
    """The piece's kept pixels in the layer's style, as bytes, and its parts for a job drawn
    apart at its trees. With ``--gpu`` the terrain is drawn on the device
    (``render/gpu/terrain.py``), to the same bytes."""
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
            return PieceBytes(done)
    drawn = draw_band(job, grid, surface)
    kept = (grid.rows.kept, grid.cols.kept)
    colour = _bytes(drawn.colour[kept])
    if drawn.parts is None:
        return PieceBytes(colour)
    ground, trees, alpha = drawn.parts
    shown = np.rint(np.clip(alpha[kept], 0.0, 1.0) * np.float32(255.0)).astype(np.uint8)
    rgba = np.concatenate([_bytes(trees[kept]), shown[..., None]], axis=-1)
    return PieceBytes(colour, TreeSplit(_bytes(ground[kept]), rgba))


def _bytes(rgb: FloatGrid) -> U8Grid:
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _plain_terrain(job: LayerJob) -> bool:
    """Whether the job is the terrain style's, which draws no falls."""
    plain = job.painted is None and job.relief is None
    return plain and job.layer in PLAIN_LAYERS and job.layer not in FALL_STYLES


class DrawnBand(NamedTuple):
    """A piece of a band drawn, sRGB 0..255; for a job drawn apart, its ground, its trees'
    colour and their alpha 0..1."""

    colour: FloatGrid
    parts: tuple[FloatGrid, FloatGrid, FloatGrid] | None = None


def paint_band(job: LayerJob, grid: BandSampling, surface: BandSurface) -> FloatGrid:
    """One piece of a band in the layer's style over its ground, then the void and the falls."""
    return draw_band(job, grid, surface).colour


def draw_band(job: LayerJob, grid: BandSampling, surface: BandSurface) -> DrawnBand:
    """``paint_band``, with the parts of a job drawn apart: the void and the falls drawn on
    the ground, which no tree hides there, and the trees cleared under the void."""
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
    parts: PaintedParts | None = None
    if job.painted is not None:
        rgb, parts, hidden = _painted_colours(job, job.painted, grid, surface, scene)
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
    void = (surface.missing, job.ground.sea, surface.void)
    falls = (job.falls, job.layer, grid.x_cm, y_cm, z_m, job.ground.spacing_m)
    rgb = draw_falls(_void(rgb, *void), *falls, hidden)
    if parts is None:
        return DrawnBand(rgb)
    ground = draw_falls(_void(parts.ground, *void), *falls)
    return DrawnBand(rgb, (ground, parts.trees, _void_alpha(parts.alpha, *void)))


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
) -> tuple[FloatGrid, PaintedParts | None, Callable[[], FloatGrid | None]]:
    """The game-painted style's band: its rock weight, crowns, sun term and water optics; its
    parts for a job drawn apart; and what the crowns and Titan trees hide of it, worked out
    when asked."""
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
    centres = (grid.x_cm, y_cm)
    meshes = (surface.mesh_weight, surface.mesh_class, surface.level_m, surface.mesh_land)
    bumps = None if job.unlit else _ground_bumps(surface)
    band: PaintedScene = {
        **scene,
        "crowns": stamped_crowns(ground.crowns, centres, spacing_m, job.unlit),
        "titan_crowns": stamped_crowns(ground.titan_crowns, centres, spacing_m, job.unlit),
        "ndl": painted_ndl(z_m, spacing_m, job.unlit, meshes, bumps),
        "ndl_flat": _FLAT_SUN,
        "rock_weight": rock_weight,
        "top_weight": surface.top_weight,
        "mesh_weight": surface.mesh_weight,
        "mesh_class": surface.mesh_class,
        "mesh_family": _band_family(job.ground.meshes, (rows.cut, cols.cut)),
        "water_optics": ground.water_optics(grid.linear, surface.water.get("river")),
        "grid": BandGrid((rows.cut, cols.cut), rows.lo, rows.hi, cols.lo, cols.hi, spacing_m),
        "unlit": job.unlit,
        "detail": surface.detail,
    }
    paint: GridTaps = (
        taps_footprint(grid.field_y, painted.footprint, field.height),
        cut_taps(painted.paint_cols, cols.cut),
    )
    rock: GridTaps = (rock_rows, cut_taps(painted.rock_cols, cols.cut))
    hidden = partial(canopy_cover, band, ground)
    if job.split:
        parts = painted_parts(band, ground, _sampler(paint), _sampler(rock))
        return parts.colour, parts, hidden
    return painted_colours(band, ground, _sampler(paint), _sampler(rock)), None, hidden


def _ground_bumps(surface: BandSurface) -> F32Grid | None:
    """The ground's detail normal where the ground is drawn rather than rock or a mesh, for a
    sun term drawn into the colour; None without detail."""
    if surface.detail is None:
        return None
    shape = (surface.z_m.shape[0], surface.z_m.shape[1])
    drawn = drawn_share(shape, surface.rock_seen, surface.top_weight, surface.mesh_weight)
    return surface.detail.normal * drawn[..., None]


def stamped_crowns(
    crowns: CrownSet | None,
    centres: tuple[F64Grid, F64Grid],
    spacing_m: float,
    unlit: bool = False,
) -> LitCrowns | None:
    """The crowns over these pixel centres, each pixel's sun term from their normals
    (``trees.crown_sun``). With ``--gpu`` they are stamped on the device, to the same bits."""
    if crowns is None:
        return None
    step_cm = spacing_m * 100.0
    stamped = None
    if gpu_on():
        from mapgen.render.gpu.crowns import stamp_crowns as on_device

        placed = crown_placements(crowns, *centres, step_cm)
        stamped = on_device(crowns.atlas, placed, centres)
        if stamped is None:
            stamped = stamp_placed(crowns.atlas.atlas, placed, centres)
    else:
        stamped = stamp_crowns(crowns, *centres, step_cm)
    return {**stamped, "ndl": crown_sun(stamped, unlit)}


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


def _void_alpha(
    alpha: FloatGrid, missing: BoolMask, sea: OpenSea | None, void: DrawnVoid | None
) -> FloatGrid:
    """The trees' alpha under what ``_void`` draws over them: none where it draws it whole."""
    if sea is None:
        return np.where(missing, np.float32(0.0), alpha)
    if void is None:
        return alpha
    return alpha * (1.0 - void.cover) * (1.0 - void.rim)
