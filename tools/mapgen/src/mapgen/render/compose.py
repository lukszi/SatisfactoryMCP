"""The band loop that draws a run's layers in one pass: each band's ground once, then every
layer's colour over it.

The bands run on threads (``render/drawpool.py``). ``render/surface.py`` composes a band's
ground and ``render/painting.py`` colours it in each layer's style.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, closing
from dataclasses import dataclass
from functools import partial
from typing import Protocol, TypeAlias, runtime_checkable

import numpy as np

from mapgen.cache import DirectPlanes, MeshPlanes, TopPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.palette.painted.ground import PaintedGround
from mapgen.palette.relief import ReliefGround
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.rivers import RiverWater, water_sources
from mapgen.palette.water.surface import WATER_EDGE_BLUR_M
from mapgen.render.drawpool import bands_held, in_order
from mapgen.render.painting import LayerJob, layer_job, paint_band
from mapgen.render.stencils import band_halo
from mapgen.render.surface import (
    GroundSources,
    Kernel,
    LightCapture,
    Owed,
    RegimeSources,
    WaterPlanes,
    Window,
    band_grid,
    band_surfaces,
)
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from mapgen.terrain.sample import frame_coordinates, grid_position, taps_linear, taps_pchip
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BAND_HALO",
    "BAND_ROWS",
    "DRAW_STAGE",
    "DrawPass",
    "SheetMaker",
    "render_layer",
    "render_layers",
]

#: Rows of the output drawn at a time: 256 rows of 32768 is 34 MB of float32 an array.
BAND_ROWS = 256

#: The rows each band is drawn beyond its edges and cropped after, so no stencil sees a band
#: edge: the widest reach at the largest size, from ``render/stencils.py``.
BAND_HALO = band_halo()

#: The progress stage of the pass, which draws every layer at once.
DRAW_STAGE = "draw"

#: Where a layer's sheet is drawn: ``(layer, shape)`` to a writable uint8 array.
SheetMaker: TypeAlias = Callable[[str, tuple[int, int, int]], U8Grid]


@runtime_checkable
class HeldPlane(Protocol):
    """A plane read a band at a time from a store that keeps decoded bands: ``BandArray``."""

    def holding(self, bands: int) -> AbstractContextManager[object]:
        """Keep at least ``bands`` decoded while the block runs."""
        ...


@dataclass(frozen=True)
class DrawPass:
    """One pass over the bands: the ground every layer shares, and each layer's job."""

    ground: GroundSources
    jobs: tuple[LayerJob, ...]

    @property
    def seabeds(self) -> frozenset[bool]:
        """The seabed rules the layers draw the meshes in the water by."""
        return frozenset(job.seabed for job in self.jobs)


def render_layers(
    layers: Sequence[str],
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
    relief: Mapping[str, ReliefGround] | None = None,
    unlit: bool = False,
    surface: LightCapture | None = None,
    water_level: np.ndarray | None = None,
    sea: OpenSea | None = None,
    threads: int = 1,
    sheets: SheetMaker | None = None,
) -> dict[str, U8Grid]:
    """Every layer of ``layers`` drawn in one pass over the bands, each band's ground composed
    once for all of them. Returns each layer's ``(rows, cols, 3)`` uint8 sheet, made by
    ``sheets`` (in memory when None).

    ``window`` is ``(r0, r1, c0, c1)``, with every raster passed in cut to it. ``painted`` is
    the painted layer's ground and ``relief`` each relief layer's. ``unlit`` draws the sun
    term flat; ``surface`` receives the heights and land weight the seabed rule draws, once,
    whatever the layers. ``sea`` is the run's ``OpenSea``, whose water planes replace
    ``water_level``'s. ``threads`` bands are drawn at once, to the same bytes; ``seam`` and
    ``regimes`` take the bands in order. The rest: docs/spatial-and-map.md sections 20, 25 and
    40.
    """
    if not layers or len(set(layers)) != len(layers):
        raise ValueError(f"a pass draws each of its layers once: {list(layers)}")
    if painted is not None and "painted" not in layers:
        raise ValueError("the painted ground is the painted layer's, and only its")
    box = Window(*(window or (0, size, 0, size)))
    ground = _ground_sources(
        field, box, size, borrow,
        height_dm=height_dm, direct=direct, seam=seam, regimes=regimes,
        measured_plane_u8=measured_plane_u8, overlay=overlay, kernel=kernel, meshes=meshes,
        reach=reach, rivers=rivers, surface=surface, water_level=water_level, sea=sea,
    )  # fmt: skip
    reliefs = relief or {}
    jobs = tuple(
        layer_job(layer, ground, size, biome_rgb, biome_width,
                  painted if layer == "painted" else None, reliefs.get(layer), falls, unlit)
        for layer in layers
    )  # fmt: skip
    shape = (box.r1 - box.r0, box.c1 - box.c0, 3)
    make = sheets or _in_memory
    out = {layer: make(layer, shape) for layer in layers}
    _draw(DrawPass(ground, jobs), out, size, progress, threads)
    return out


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
    """One layer alone, in memory: ``render_layers`` with that layer and its own ``relief``."""
    return render_layers(
        (layer,), field, biome_rgb, biome_width, borrow, size, progress,
        height_dm=height_dm, direct=direct, seam=seam, regimes=regimes,
        measured_plane_u8=measured_plane_u8, overlay=overlay, kernel=kernel, meshes=meshes,
        falls=falls, reach=reach, painted=painted, window=window, rivers=rivers,
        relief=None if relief is None else {layer: relief}, unlit=unlit, surface=surface,
        water_level=water_level, sea=sea, threads=threads,
    )[layer]  # fmt: skip


def _in_memory(_layer: str, shape: tuple[int, int, int]) -> U8Grid:
    return np.empty(shape, np.uint8)


def _draw(
    draw: DrawPass, sheets: dict[str, U8Grid], size: int, progress: bool, threads: int
) -> None:
    """The pass's bands into ``sheets`` on ``threads``, their measurements merged in order."""
    box = draw.ground.window
    tops = range(box.r0, box.r1, BAND_ROWS)
    threads = max(1, min(threads, len(tops)))
    started = time.time()
    with ExitStack() as stores:
        for plane in _band_planes(draw) if threads > 1 else ():
            stores.enter_context(plane.holding(bands_held(threads)))
        band = partial(_draw_band, draw, sheets)
        owed = stores.enter_context(closing(in_order(band, tops, threads)))
        for top, measured in zip(tops, owed, strict=True):
            for merge, value in measured:
                merge(value)
            if progress and (top // BAND_ROWS) % 16 == 0:
                done = (min(top + BAND_ROWS, box.r1) - box.r0) / (box.r1 - box.r0)
                print(
                    f"  {DRAW_STAGE}: {done:5.1%} of {size}x{size} in "
                    f"{time.time() - started:5.1f}s",
                    flush=True,
                )
                print(encode_stage(DRAW_STAGE, done), flush=True)


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
    """What the bands sample their ground from, with the column taps they share."""
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


def _band_planes(draw: DrawPass) -> list[HeldPlane]:
    """The band stores among the rasters the pass reads a band at a time."""
    ground = draw.ground
    planes: list[object] = [*(ground.direct or ())[:2], *(ground.overlay or ())[:2]]
    planes += [*(ground.meshes or ())]
    for job in draw.jobs:
        if job.painted is not None:
            painted = job.painted.ground
            planes += [
                getattr(painted, "rock_family", None),
                *(getattr(painted, "titan", None) or ())[:2],
            ]
    return [plane for plane in planes if isinstance(plane, HeldPlane)]


def _draw_band(draw: DrawPass, sheets: dict[str, U8Grid], top: int) -> list[Owed]:
    """Rows ``[top, top + BAND_ROWS)`` of every layer's sheet, all over one ground, and the
    ``(merge, measured)`` pairs the caller merges in band order. Nothing shared is written
    but those rows and the capture."""
    grid = band_grid(draw.ground, top, BAND_ROWS, BAND_HALO)
    surfaces, owed = band_surfaces(draw.ground, grid, draw.seabeds)
    rows, r0 = grid.rows, draw.ground.window.r0
    for job in draw.jobs:
        rgb = paint_band(job, grid, surfaces[job.seabed])
        out = sheets[job.layer]
        out[rows.top - r0 : rows.bottom - r0] = np.clip(rgb[rows.kept], 0, 255).astype(np.uint8)
    return owed
