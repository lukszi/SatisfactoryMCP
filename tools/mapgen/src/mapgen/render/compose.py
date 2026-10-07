"""The band loop that draws a run's layers in one pass: each band in column pieces, each
piece's ground once, then every layer's colour over it.

The pieces run on threads (``render/drawpool.py``). ``render/surface.py`` composes a piece's
ground and settles each band once its pieces are in, and ``render/painting.py`` colours a
piece in each layer's style.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
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
from mapgen.render.drawpool import PIECE_COLS, bands_held, in_order
from mapgen.render.lift import lattice_edge
from mapgen.render.painting import LayerJob, layer_job, paint_band
from mapgen.render.stencils import band_halo, piece_halo
from mapgen.render.surface import (
    GroundSources,
    Kernel,
    LightCapture,
    PieceOwed,
    RegimeSources,
    Span,
    WaterPlanes,
    Window,
    band_grid,
    band_surfaces,
    settle_band,
    span,
)
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from mapgen.terrain.sample import frame_coordinates, grid_position, taps_linear, taps_pchip
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I8Grid, I16Grid, U8Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BAND_HALO",
    "BAND_ROWS",
    "DRAW_STAGE",
    "PIECE_HALO",
    "BandSink",
    "DrawPass",
    "render_layer",
    "render_layers",
]

#: Rows of the output drawn at a time: 256 rows of 32768 is 34 MB of float32 an array.
BAND_ROWS = 256

#: The rows each band is drawn beyond its edges and cropped after, so no stencil sees a band
#: edge: the widest reach at the largest size, from ``render/stencils.py``.
BAND_HALO = band_halo()

#: The columns each piece of a band is drawn beyond its edges and cropped after: the widest
#: reach along a row of the stencils a piece draws.
PIECE_HALO = piece_halo()

#: The progress stage of the pass, which draws every layer at once.
DRAW_STAGE = "draw"

#: Where a pass hands each band once it is settled, in order: its first row, and its rows of
#: every layer, which are the sink's from then on.
BandSink: TypeAlias = Callable[[int, dict[str, U8Grid]], None]


@runtime_checkable
class HeldPlane(Protocol):
    """A plane read a band at a time from a store that keeps decoded bands: ``BandArray``."""

    def holding(self, bands: int) -> AbstractContextManager[object]:
        """Keep at least ``bands`` decoded while the block runs."""
        ...


@dataclass(frozen=True)
class DrawPass:
    """One pass over the bands: the ground every layer shares, each layer's job, and the
    output columns of a band's pieces."""

    ground: GroundSources
    jobs: tuple[LayerJob, ...]
    columns: int

    @property
    def seabeds(self) -> frozenset[bool]:
        """The seabed rules the layers draw the meshes in the water by."""
        return frozenset(job.seabed for job in self.jobs)

    def rows(self, top: int) -> Span:
        """The band from row ``top``, with its halo."""
        window = self.ground.window
        return span(top, BAND_ROWS, (window.r0, window.r1), BAND_HALO)

    def cols(self, left: int) -> Span:
        """The piece from column ``left``, with its halo."""
        window = self.ground.window
        return span(left, self.columns, (window.c0, window.c1), PIECE_HALO)


def render_layers(
    layers: Sequence[str],
    field: hf.Field,
    biome_rgb: U8Grid | None,
    biome_width: int,
    borrow: tuple[I8Grid, U8Grid],
    size: int,
    progress: bool,
    height_dm: F32Grid | None = None,
    direct: DirectPlanes | None = None,
    seam: SeamTrace | None = None,
    regimes: RegimeCoverage | None = None,
    measured_plane_u8: U8Grid | None = None,
    overlay: TopPlanes | None = None,
    kernel: Kernel | None = None,
    meshes: MeshPlanes | None = None,
    falls: F64Grid | None = None,
    reach: U8Grid | None = None,
    painted: PaintedGround | None = None,
    window: tuple[int, int, int, int] | None = None,
    rivers: RiverWater | None = None,
    relief: Mapping[str, ReliefGround] | None = None,
    unlit: bool = False,
    surface: LightCapture | None = None,
    water_level: I16Grid | None = None,
    sea: OpenSea | None = None,
    threads: int = 1,
    bands: BandSink | None = None,
    columns: int = PIECE_COLS,
) -> dict[str, U8Grid]:
    """Every layer of ``layers`` drawn in one pass over the bands, each band in pieces of
    ``columns`` output columns whose ground is composed once for all of them. Returns each
    layer's ``(rows, cols, 3)`` uint8 sheet; with ``bands``, each band goes to it once
    settled instead, and nothing is returned.

    ``window`` is ``(r0, r1, c0, c1)``, with every raster passed in cut to it. ``painted`` is
    the painted layer's ground and ``relief`` each relief layer's. ``unlit`` draws the sun
    term flat; ``surface`` receives the heights and land weight the seabed rule draws, once,
    whatever the layers. ``sea`` is the run's ``OpenSea``, whose water planes replace
    ``water_level``'s. ``threads`` pieces are drawn at once, to the same bytes at any count
    and any width; ``surface``, ``seam``, ``regimes`` and ``bands`` take the bands in order.
    The rest: docs/spatial-and-map.md sections 20, 25, 40 and 42.
    """
    if not layers or len(set(layers)) != len(layers):
        raise ValueError(f"a pass draws each of its layers once: {list(layers)}")
    if painted is not None and "painted" not in layers:
        raise ValueError("the painted ground is the painted layer's, and only its")
    if columns < 1:
        raise ValueError(f"a piece draws at least one column, not {columns}")
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
    out = {} if bands is not None else {layer: np.empty(shape, np.uint8) for layer in layers}
    _draw(DrawPass(ground, jobs, columns), _Bands(box, layers, out, bands), size, progress,
          threads)  # fmt: skip
    return out


def render_layer(
    layer: str,
    field: hf.Field,
    biome_rgb: U8Grid | None,
    biome_width: int,
    borrow: tuple[I8Grid, U8Grid],
    size: int,
    progress: bool,
    height_dm: F32Grid | None = None,
    direct: DirectPlanes | None = None,
    seam: SeamTrace | None = None,
    regimes: RegimeCoverage | None = None,
    measured_plane_u8: U8Grid | None = None,
    overlay: TopPlanes | None = None,
    kernel: Kernel | None = None,
    meshes: MeshPlanes | None = None,
    falls: F64Grid | None = None,
    reach: U8Grid | None = None,
    painted: PaintedGround | None = None,
    window: tuple[int, int, int, int] | None = None,
    rivers: RiverWater | None = None,
    relief: ReliefGround | None = None,
    unlit: bool = False,
    surface: LightCapture | None = None,
    water_level: I16Grid | None = None,
    sea: OpenSea | None = None,
    threads: int = 1,
    columns: int = PIECE_COLS,
) -> U8Grid:
    """One layer alone, in memory: ``render_layers`` with that layer and its own ``relief``."""
    return render_layers(
        (layer,), field, biome_rgb, biome_width, borrow, size, progress,
        height_dm=height_dm, direct=direct, seam=seam, regimes=regimes,
        measured_plane_u8=measured_plane_u8, overlay=overlay, kernel=kernel, meshes=meshes,
        falls=falls, reach=reach, painted=painted, window=window, rivers=rivers,
        relief=None if relief is None else {layer: relief}, unlit=unlit, surface=surface,
        water_level=water_level, sea=sea, threads=threads, columns=columns,
    )[layer]  # fmt: skip


class _Bands:
    """Where a pass's pieces write: each band's rows of the sheets, or rows of its own until
    it is settled and handed to the sink."""

    def __init__(
        self, box: Window, layers: Sequence[str], sheets: dict[str, U8Grid], sink: BandSink | None
    ) -> None:
        self.box, self.layers, self.sheets, self.sink = box, layers, sheets, sink
        self.held: dict[int, dict[str, U8Grid]] = {}

    def open(self, top: int) -> None:
        """The band from row ``top``, made before its first piece is drawn."""
        box, stop = self.box, min(top + BAND_ROWS, self.box.r1)
        if self.sink is None:
            rows = slice(top - box.r0, stop - box.r0)
            self.held[top] = {layer: sheet[rows] for layer, sheet in self.sheets.items()}
            return
        shape = (stop - top, box.c1 - box.c0, 3)
        self.held[top] = {layer: np.empty(shape, np.uint8) for layer in self.layers}

    def settled(self, top: int) -> None:
        band = self.held.pop(top)
        if self.sink is not None:
            self.sink(top, band)


def _draw(draw: DrawPass, bands: _Bands, size: int, progress: bool, threads: int) -> None:
    """The pass's pieces into ``bands`` on ``threads``, band by band; each band, once its
    pieces are in, settled in order: its light captured, its measurements merged, and its
    rows handed on."""
    box = draw.ground.window
    lefts = range(box.c0, box.c1, draw.columns)
    order = [(top, left) for top in range(box.r0, box.r1, BAND_ROWS) for left in lefts]
    threads = max(1, min(threads, len(order)))
    started = time.time()

    def pieces() -> Iterator[tuple[int, int]]:
        for top, left in order:
            if left == box.c0:
                bands.open(top)
            yield top, left

    with ExitStack() as stores:
        for plane in _band_planes(draw) if threads > 1 else ():
            stores.enter_context(plane.holding(bands_held(threads, len(lefts))))
        piece = partial(_draw_piece, draw, bands.held)
        owed = stores.enter_context(closing(in_order(piece, pieces(), threads)))
        band: list[PieceOwed] = []
        for (top, _left), measured in zip(order, owed, strict=True):
            band.append(measured)
            if len(band) < len(lefts):
                continue
            for merge, value in settle_band(draw.ground, draw.rows(top), band):
                merge(value)
            band = []
            bands.settled(top)
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
    borrow: tuple[I8Grid, U8Grid],
    *,
    height_dm: F32Grid | None,
    direct: DirectPlanes | None,
    seam: SeamTrace | None,
    regimes: RegimeCoverage | None,
    measured_plane_u8: U8Grid | None,
    overlay: TopPlanes | None,
    kernel: Kernel | None,
    meshes: MeshPlanes | None,
    reach: U8Grid | None,
    rivers: RiverWater | None,
    surface: LightCapture | None,
    water_level: I16Grid | None,
    sea: OpenSea | None,
) -> GroundSources:
    """What the bands sample their ground from, with the column taps they share."""
    heights: I16Grid | F32Grid = field.height_dm if height_dm is None else height_dm
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
    spacing = field.spacing_cm / 100.0
    edge = None if direct is None else lattice_edge(direct.ground, heights, spacing)
    return GroundSources(
        field=field,
        heights=heights,
        kernel=kernel,
        window=window,
        x_cm=x_cm,
        y_cm=y_cm,
        spacing_m=spacing_m,
        direct=direct,
        lattice_edge=edge,
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
    field: hf.Field, regimes: RegimeCoverage | None, measured_plane_u8: U8Grid | None
) -> RegimeSources | None:
    """The regime table with the planes it reads; it needs the measurement plane."""
    if regimes is None:
        return None
    if measured_plane_u8 is None:
        raise ValueError("the regime table reads the measurement and provenance planes")
    return RegimeSources(regimes, measured_plane_u8, field.provenance_plane)


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


def _draw_piece(
    draw: DrawPass, held: dict[int, dict[str, U8Grid]], at: tuple[int, int]
) -> PieceOwed:
    """The piece at ``(top, left)`` of every layer's band, all over one ground, and what it
    owes its band. Nothing shared is written but its own pixels of the bands."""
    top, left = at
    grid = band_grid(draw.ground, draw.rows(top), draw.cols(left))
    surfaces, owed = band_surfaces(draw.ground, grid, draw.seabeds)
    rows, cols, box = grid.rows, grid.cols, draw.ground.window
    out = (slice(0, rows.stop - rows.start), slice(cols.start - box.c0, cols.stop - box.c0))
    band = held[top]
    for job in draw.jobs:
        rgb = paint_band(job, grid, surfaces[job.seabed])
        band[job.layer][out] = np.clip(rgb[rows.kept, cols.kept], 0, 255).astype(np.uint8)
    return owed
