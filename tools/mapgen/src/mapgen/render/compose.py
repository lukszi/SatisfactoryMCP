"""The band loop that draws a layer's sheet: each band's ground, then the layer's colour.

The bands run on threads (``render/drawpool.py``). ``render/surface.py`` composes a band's
ground and ``paint_band`` colours it in the layer's style.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack, closing
from dataclasses import dataclass
from functools import partial
from typing import Protocol, TypeAlias, cast, runtime_checkable

import numpy as np

from mapgen.cache import DirectPlanes, MeshPlanes, TopPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.hillshade import (
    SUN_ALTITUDE_DEG,
    flat_shade,
    hillshade,
    slope_degrees,
    sun_dot,
)
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
from mapgen.palette.water.rivers import RiverWater, water_sources
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.palette.water.surface import WATER_EDGE_BLUR_M
from mapgen.render.drawpool import bands_held, in_order
from mapgen.render.stencils import band_halo
from mapgen.render.surface import (
    AxisTaps,
    BandSampling,
    BandSurface,
    GridTaps,
    GroundSources,
    Kernel,
    LightCapture,
    Owed,
    RegimeSources,
    WaterPlanes,
    Window,
    band_grid,
    band_surface,
)
from mapgen.terrain.crown_stamp import CrownBand, stamp_crowns
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from mapgen.terrain.sample import (
    frame_coordinates,
    grid_position,
    reads_nothing,
    sample_noise,
    sample_plain,
    taps_footprint,
    taps_linear,
    taps_pchip,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid, U8Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BAND_HALO",
    "BAND_ROWS",
    "LayerJob",
    "domed_crowns",
    "paint_band",
    "render_layer",
]

#: Rows of the output drawn at a time: 256 rows of 32768 is 34 MB of float32 an array.
BAND_ROWS = 256

#: The rows each band is drawn beyond its edges and cropped after, so no stencil sees a band
#: edge: the widest reach at the largest size, from ``render/stencils.py``.
BAND_HALO = band_halo()

#: The flat ground's sun term, ``n.L`` of the default sun on level ground.
_FLAT_SUN = np.float32(np.sin(np.deg2rad(SUN_ALTITUDE_DEG)))

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


@runtime_checkable
class HeldPlane(Protocol):
    """A plane read a band at a time from a store that keeps decoded bands: ``BandArray``."""

    def holding(self, bands: int) -> AbstractContextManager[object]:
        """Keep at least ``bands`` decoded while the block runs."""
        ...


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
    """One layer's draw: the sources of its ground and what its painter reads, built once.

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


def render_layer(
    layer: str,
    field: hf.Field,
    biome_rgb: np.ndarray | None,
    biome_width: int,
    borrow: tuple[np.ndarray, np.ndarray],
    size: int,
    progress: bool,
    height_dm: np.ndarray | None = None,
    direct: DirectPlanes | None = None,
    seam: SeamTrace | None = None,
    regimes: RegimeCoverage | None = None,
    measured_plane_u8: np.ndarray | None = None,
    overlay: TopPlanes | None = None,
    kernel: Kernel | None = None,
    meshes: MeshPlanes | None = None,
    falls: np.ndarray | None = None,
    reach: np.ndarray | None = None,
    painted: PaintedGround | None = None,
    window: tuple[int, int, int, int] | None = None,
    rivers: RiverWater | None = None,
    relief: ReliefGround | None = None,
    unlit: bool = False,
    surface: LightCapture | None = None,
    water_level: np.ndarray | None = None,
    sea: OpenSea | None = None,
    threads: int = 1,
) -> U8Grid:
    """One whole layer, drawn a band of rows at a time. Returns ``(rows, cols, 3)`` uint8.

    ``window`` is ``(r0, r1, c0, c1)``, with every raster passed in cut to it. ``unlit``
    draws the sun term flat; ``surface`` receives the drawn heights and land weight. ``sea``
    is the run's ``OpenSea``, whose water planes replace ``water_level``'s. ``threads`` bands
    are drawn at once, to the same bytes; ``seam`` and ``regimes`` take the bands in order.
    The rest: docs/spatial-and-map.md sections 20, 25 and 40.
    """
    sheet = Window(*(window or (0, size, 0, size)))
    ground = _ground_sources(
        field, sheet, size, borrow,
        height_dm=height_dm, direct=direct, seam=seam, regimes=regimes,
        measured_plane_u8=measured_plane_u8, overlay=overlay, kernel=kernel, meshes=meshes,
        reach=reach, rivers=rivers, surface=surface, water_level=water_level, sea=sea,
    )  # fmt: skip
    job = _layer_job(layer, ground, size, biome_rgb, biome_width, painted, relief, falls, unlit)
    out = np.empty((sheet.r1 - sheet.r0, sheet.c1 - sheet.c0, 3), np.uint8)
    tops = range(sheet.r0, sheet.r1, BAND_ROWS)
    threads = max(1, min(threads, len(tops)))
    started = time.time()
    with ExitStack() as stores:
        for plane in _band_planes(job) if threads > 1 else ():
            stores.enter_context(plane.holding(bands_held(threads)))
        owed = stores.enter_context(closing(in_order(partial(_draw_band, job, out), tops, threads)))
        for top, measured in zip(tops, owed, strict=True):
            for merge, value in measured:
                merge(value)
            if progress and (top // BAND_ROWS) % 16 == 0:
                done = (min(top + BAND_ROWS, sheet.r1) - sheet.r0) / (sheet.r1 - sheet.r0)
                print(
                    f"  {layer}: {done:5.1%} of {size}x{size} in {time.time() - started:5.1f}s",
                    flush=True,
                )
                print(encode_stage(f"draw:{layer}", done), flush=True)
    return out


def _ground_sources(
    field: hf.Field,
    window: Window,
    size: int,
    borrow: tuple[np.ndarray, np.ndarray],
    *,
    height_dm: np.ndarray | None,
    direct: DirectPlanes | None,
    seam: SeamTrace | None,
    regimes: RegimeCoverage | None,
    measured_plane_u8: np.ndarray | None,
    overlay: TopPlanes | None,
    kernel: Kernel | None,
    meshes: MeshPlanes | None,
    reach: np.ndarray | None,
    rivers: RiverWater | None,
    surface: LightCapture | None,
    water_level: np.ndarray | None,
    sea: OpenSea | None,
) -> GroundSources:
    """What a layer's bands sample their ground from, with the column taps they share."""
    heights = field.height_dm if height_dm is None else height_dm
    if heights is None:
        raise ValueError("a field without its height plane has nothing to draw")
    kernel = taps_pchip if kernel is None else kernel
    x_cm, y_cm = frame_coordinates(size)
    x_cm = x_cm[window.c0 : window.c1]
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    drawn, grades = (water_level, None) if sea is None else sea.planes
    level, wet, measured = water_sources(field, rivers, drawn, grades)
    water = None
    if level is not None and wet is not None and measured is not None:
        water = WaterPlanes(level, wet, measured)
    field_x = grid_position(x_cm, field.x0_cm, field.spacing_cm, field.width)
    art_step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SHEET_PX
    art_x0_cm = BOUNDS_M["x_min_m"] * 100 + art_step_cm / 2
    art_x = grid_position(x_cm, art_x0_cm, art_step_cm, SHEET_PX)
    return GroundSources(
        field=field,
        heights=heights,
        kernel=kernel,
        window=window,
        x_cm=x_cm,
        y_cm=y_cm,
        spacing_m=spacing_m,
        direct=direct,
        overlay=overlay,
        meshes=meshes,
        water=water,
        sea=sea,
        seam=seam,
        regimes=_regime_sources(field, regimes, measured_plane_u8),
        borrow=borrow,
        blur_px=WATER_EDGE_BLUR_M / spacing_m,
        reach=reach,
        rivers=rivers,
        capture=surface,
        cols_smooth=kernel(field_x, field.width),
        cols_linear=taps_linear(field_x, field.width),
        art_cols=taps_linear(art_x, SHEET_PX),
        art_y0_cm=BOUNDS_M["y_min_m"] * 100 + art_step_cm / 2,
        art_step_cm=art_step_cm,
        # Nearest, never in between: a province is a name.
        prov_cols=np.clip(
            np.round((x_cm - field.x0_cm) / field.spacing_cm).astype(np.int64), 0, field.width - 1
        ),
    )


def _regime_sources(
    field: hf.Field, regimes: RegimeCoverage | None, measured_plane_u8: np.ndarray | None
) -> RegimeSources | None:
    """The regime table with the planes it reads; it needs the measurement plane."""
    if regimes is None:
        return None
    provenance = field.provenance_plane
    if measured_plane_u8 is None or provenance is None:
        raise ValueError("the regime table reads the measurement and provenance planes")
    return RegimeSources(regimes, measured_plane_u8, provenance)


def _layer_job(
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


def _band_planes(job: LayerJob) -> list[HeldPlane]:
    """The band stores among the rasters the layer reads a band at a time."""
    ground = job.ground
    planes: list[object] = [*(ground.direct or ())[:2], *(ground.overlay or ())[:2]]
    planes += [*(ground.meshes or ())]
    if job.painted is not None:
        painted = job.painted.ground
        planes += [
            getattr(painted, "rock_family", None),
            *(getattr(painted, "titan", None) or ())[:2],
        ]
    return [plane for plane in planes if isinstance(plane, HeldPlane)]


def _draw_band(job: LayerJob, out: U8Grid, top: int) -> list[Owed]:
    """Rows ``[top, top + BAND_ROWS)`` into ``out``, and the ``(merge, measured)`` pairs the
    caller merges in band order. Nothing shared is written but those rows and the capture."""
    grid = band_grid(job.ground, top, BAND_ROWS, BAND_HALO)
    surface, owed = band_surface(job.ground, grid, seabed=job.layer != "painted")
    rgb = paint_band(job, grid, surface)
    rows, r0 = grid.rows, job.ground.window.r0
    out[rows.top - r0 : rows.bottom - r0] = np.clip(rgb[rows.kept], 0, 255).astype(np.uint8)
    return owed


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
    scene["ndl"] = painted_ndl(z_m, spacing_m, job.unlit, job.ground.capture, meshes)
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
