"""One piece of a band's ground as every layer draws it: heights, rocks, overlay, meshes,
water, borrow.

``band_grid`` places a piece of a band's rows on the field's lattice and ``band_surfaces``
composes the ground there. Only the meshes' seabed depends on the layer, so one surface
serves every layer that draws the seabed and a second one the painted layer. What the light
captures and the seam trace measures waits for the band's last piece: ``settle_band``
(docs/spatial-and-map.md sections 20, 25, 29 and 40).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import partial
from typing import NamedTuple, Protocol, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import DirectPlanes, MeshPlanes, TopPlanes
from mapgen.lighting.borrow import BORROW_CLAMP, BORROW_GAIN
from mapgen.lighting.slabs import SlabPlanes
from mapgen.palette.scene import WaterTerms
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.rivers import RiverWater
from mapgen.palette.water.shore import (
    MESH_FULL_LIFT_M,
    blend_water,
    composite_meshes,
    shore_terms,
)
from mapgen.palette.water.surface import WATER_DEPTH_FULL_M, water_alpha, water_depth_fraction
from mapgen.render.floating import FieldPiece, FloatSources, band_slabs, piece_slabs
from mapgen.render.lift import blend_regimes, composite_top, rock_kept
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from mapgen.terrain.sample import (
    AxisTaps,
    PchipTaps,
    grid_position,
    reads_nothing,
    sample_coverage,
    sample_plain,
    sample_surface,
    taps_linear,
)
from satisfactory_mcp.core.arrays import (
    BoolMask,
    F32Grid,
    F64Grid,
    FloatGrid,
    I8Grid,
    I16Grid,
    I64Grid,
    U8Grid,
)
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "AxisTaps",
    "BandSampling",
    "BandSurface",
    "GridTaps",
    "GroundSources",
    "Kernel",
    "LightCapture",
    "LightPlanes",
    "Owed",
    "PieceOwed",
    "RegimeSources",
    "SeamPlanes",
    "Span",
    "WaterPlanes",
    "Window",
    "band_grid",
    "band_surfaces",
    "band_water_terms",
    "cut_taps",
    "settle_band",
    "span",
]

#: A band's taps: its rows' and its columns'.
GridTaps: TypeAlias = tuple[AxisTaps, AxisTaps]
#: How a height raster's taps are built along one axis: ``taps_pchip`` or ``taps_cubic``.
Kernel: TypeAlias = Callable[[F64Grid, int], AxisTaps]
#: A band's measurement owed to an accumulator, merged in band order: ``(merge, value)``.
Owed: TypeAlias = tuple[Callable[..., None], object]


class Window(NamedTuple):
    """The rows ``[r0, r1)`` and columns ``[c0, c1)`` of the sheet a draw covers."""

    r0: int
    r1: int
    c0: int
    c1: int


class WaterPlanes(NamedTuple):
    """The water a band samples on the field's grid: its level and the wet and measured planes."""

    level: I16Grid
    wet: U8Grid
    measured: U8Grid


class RegimeSources(NamedTuple):
    """The regime table's accumulator and the field planes it reads on the field's grid."""

    coverage: RegimeCoverage
    measured: U8Grid
    provenance: NDArray[np.integer]


class LightCapture(Protocol):
    """Where the drawn heights and land weight go for the light bake (``lighting.stage``),
    with what floats over them."""

    def put(self, row: int, z_m: FloatGrid, land: FloatGrid, columns: slice = ...,
            slabs: SlabPlanes | None = ..., /) -> None:  # fmt: skip
        """Rows from ``row`` on, over ``columns`` of the sheet."""
        ...


@dataclass(frozen=True)
class GroundSources:
    """What every band's ground is sampled from, and the column taps they share, built once.

    ``x_cm`` holds the window's pixel centres, ``y_cm`` the whole sheet's; a piece reads its
    columns of them and of the taps. The bands only read it, so any number of threads may
    share it.
    """

    field: hf.Field
    heights: I16Grid | F32Grid
    kernel: Kernel
    window: Window
    x_cm: F64Grid
    y_cm: F64Grid
    spacing_m: float
    direct: DirectPlanes | None
    overlay: TopPlanes | None
    meshes: MeshPlanes | None
    water: WaterPlanes | None
    sea: OpenSea | None
    seam: SeamTrace | None
    regimes: RegimeSources | None
    borrow: tuple[I8Grid, U8Grid]
    blur_px: float
    reach: U8Grid | None
    rivers: RiverWater | None
    capture: LightCapture | None
    cols_smooth: AxisTaps
    cols_linear: AxisTaps
    art_cols: AxisTaps
    art_y0_cm: float
    art_step_cm: float
    prov_cols: I64Grid


class Span(NamedTuple):
    """A band's output rows, or a piece's output columns, ``[start, stop)`` of the sheet,
    drawn over ``[lo, hi)`` with the halo.

    ``cut`` is ``[lo, hi)`` along that axis of a raster cut to the window.
    """

    start: int
    stop: int
    lo: int
    hi: int
    cut: slice

    @property
    def kept(self) -> slice:
        """Where the output lies in arrays drawn over the span: the halo cropped."""
        return slice(self.start - self.lo, self.stop - self.lo)


def span(first: int, count: int, edges: tuple[int, int], halo: int) -> Span:
    """``count`` outputs from ``first`` on, within the window's ``edges``, and ``halo`` more
    either side as far as the window goes."""
    w0, w1 = edges
    stop = min(first + count, w1)
    lo, hi = max(first - halo, w0), min(stop + halo, w1)
    return Span(first, stop, lo, hi, slice(lo - w0, hi - w0))


def cut_taps(taps: AxisTaps, cut: slice) -> AxisTaps:
    """Column taps cut to ``cut``: a piece's own columns of the window's."""
    if isinstance(taps, PchipTaps):
        return PchipTaps(taps.indices[:, cut], taps.fraction[cut])
    index, weight = taps
    return index[:, cut], weight[:, cut]


@dataclass(frozen=True)
class BandSampling:
    """A piece of a band placed on the field's lattice: its rows' and columns' spans, both
    kinds of taps, and its pixel centres along the row and taps on the artwork."""

    rows: Span
    cols: Span
    field_y: F64Grid
    smooth: GridTaps
    linear: GridTaps
    x_cm: F64Grid
    art_cols: AxisTaps


@dataclass(frozen=True)
class BandSurface:
    """One band's ground, halo included, ready for any layer's painter.

    ``weight`` is the rock's coverage and ``rock_seen`` how much of it stands proud, both None
    without the direct regime; ``top_weight`` the overlay's lift; ``level_m`` the water level,
    NaN where there is none.
    """

    z_m: FloatGrid
    missing: BoolMask
    weight: F32Grid | None
    rock_seen: FloatGrid | None
    top_weight: FloatGrid | None
    water_m: FloatGrid
    level_m: FloatGrid
    wet: FloatGrid
    measured: FloatGrid
    mesh_weight: FloatGrid | None
    mesh_class: U8Grid | None
    water: WaterTerms
    borrow: FloatGrid


class SeamPlanes(NamedTuple):
    """What the seam trace and the regime table read of the output: the blended and the
    switched heights, the rock's weight, and its height over the lattice."""

    z_m: F32Grid
    switched: F32Grid
    weight: F32Grid
    delta: F32Grid


class LightPlanes(NamedTuple):
    """What the light captures of the output: the heights under the seabed rule, the land
    weight, and what floats over them, None where nothing does."""

    z_m: F32Grid
    land: F32Grid
    slabs: SlabPlanes | None = None


class PieceOwed(NamedTuple):
    """What a piece hands its band, over its output pixels: the planes the band measures,
    without the direct regime None, and the light's, without a capture None."""

    seam: SeamPlanes | None
    light: LightPlanes | None


def band_water_terms(
    z_m: FloatGrid,
    water_m: FloatGrid,
    wet: FloatGrid,
    measured: FloatGrid,
    blur_px: float,
    reach: U8Grid | None,
    linear: GridTaps,
    spacing_m: float,
) -> WaterTerms:
    """Recipe 5's water, and within ``reach`` of the sea the ocean's crossing rule. ``wet``
    rides along for the rivers: past the last wet texel, the edge's blur is no water."""
    old_cover = water_alpha(z_m, water_m, wet, measured, blur_px)
    old_depth = water_depth_fraction(z_m, water_m, measured)
    if reach is None:
        terms = blend_water(None, old_cover, old_depth, None, WATER_DEPTH_FULL_M)
    else:
        terms = blend_water(
            sample_coverage(reach, linear),
            old_cover,
            old_depth,
            shore_terms(z_m, spacing_m),
            WATER_DEPTH_FULL_M,
        )
    terms["wet"] = wet
    return cast(WaterTerms, terms)


def band_grid(sources: GroundSources, rows: Span, cols: Span) -> BandSampling:
    """The piece ``cols`` of the band ``rows``, halos included, on the field's lattice."""
    field = sources.field
    field_y = grid_position(
        sources.y_cm[rows.lo : rows.hi], field.y0_cm, field.spacing_cm, field.height
    )
    return BandSampling(
        rows=rows,
        cols=cols,
        field_y=field_y,
        smooth=(sources.kernel(field_y, field.height), cut_taps(sources.cols_smooth, cols.cut)),
        linear=(taps_linear(field_y, field.height), cut_taps(sources.cols_linear, cols.cut)),
        x_cm=sources.x_cm[cols.cut],
        art_cols=cut_taps(sources.art_cols, cols.cut),
    )


def band_surfaces(
    sources: GroundSources, grid: BandSampling, seabeds: Collection[bool]
) -> tuple[dict[bool, BandSurface], PieceOwed]:
    """One piece's ground under each seabed rule in ``seabeds``, and what it owes its band.

    Only the meshes depend on the rule: a seabed rule leaves the meshes in the water to the
    seabed (``shore.composite_meshes``). The rest is composed once, and the meshes and the
    water over them once per rule; without meshes both rules are one surface. With a capture,
    the heights and land weight drawn under the seabed rule are owed to the light stage,
    whatever rules the layers draw with. The arrays are read-only: every layer's painter
    reads them.
    """
    rows, cols, smooth, linear = grid.rows, grid.cols, grid.smooth, grid.linear
    z_dm, missing = sample_surface(sources.heights, smooth, linear, hf.NODATA)
    z_m = z_dm / np.float32(hf.DM_PER_M)
    field = FieldPiece(z_m, missing, None)
    weight = rock_seen = seam = None
    if sources.direct is not None:
        z_m, missing, weight, rock_seen, seam, base_m = _direct_regime(
            sources, sources.direct, grid, z_m, missing
        )
        field = field._replace(base_m=base_m)
    top_weight = None
    if sources.overlay is not None:
        top = sources.overlay
        below = z_m
        z_m = composite_top(
            z_m,
            np.asarray(top.z[rows.cut, cols.cut], np.float32),
            np.asarray(top.coverage[rows.cut, cols.cut]),
            top.subsamples,
        )
        top_weight = np.clip((z_m - below) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    water_m, level_m, wet, measured = _sample_water_surface(
        z_m, sources.water, sources.sea, smooth, linear
    )
    planes, borrow = (water_m, wet, measured), _borrow(sources, grid)
    rules = set(seabeds)
    if sources.capture is not None:
        rules.add(True)
    surfaces: dict[bool, BandSurface] = {}
    for seabed in rules:
        if sources.meshes is None and surfaces:
            surfaces[seabed] = next(iter(surfaces.values()))
            continue
        lifted, mesh_weight, mesh_class = z_m, None, None
        if sources.meshes is not None:
            lifted, mesh_weight, mesh_class = _meshes(sources.meshes, grid, z_m, level_m, seabed)
        surfaces[seabed] = _read_only(BandSurface(
            z_m=lifted, missing=missing, weight=weight, rock_seen=rock_seen,
            top_weight=top_weight, water_m=water_m, level_m=level_m, wet=wet, measured=measured,
            mesh_weight=mesh_weight, mesh_class=mesh_class,
            water=_water_terms(sources, linear, lifted, planes), borrow=borrow,
        ))  # fmt: skip
    light = None
    if sources.capture is not None:
        lit = surfaces[True]
        floating = piece_slabs(_float_sources(sources, grid), field, lit.z_m, level_m)
        light = _light_planes(grid, lit.z_m, missing, lit.water, floating)
    return {seabed: surfaces[seabed] for seabed in seabeds}, PieceOwed(seam, light)


def _float_sources(sources: GroundSources, grid: BandSampling) -> FloatSources:
    """What ``piece_slabs`` composes a piece's solid surface from."""
    wet = None if sources.water is None else sources.water.wet
    return FloatSources(
        direct=sources.direct, overlay=sources.overlay, meshes=sources.meshes,
        cut=(grid.rows.cut, grid.cols.cut), kept=(grid.rows.kept, grid.cols.kept),
        linear=grid.linear, keep_rock=partial(rock_kept, wet_plane=wet, sea=sources.sea),
    )  # fmt: skip


def settle_band(sources: GroundSources, rows: Span, pieces: Sequence[PieceOwed]) -> list[Owed]:
    """A band's pieces, in column order, put together: its rows go to the light stage, and
    the seam and regime measurements are returned for the caller to merge in band order.

    The band is measured whole, as it was drawn before it had pieces, so the seam trace's
    second differences and thinning and the regime table's float sums are the same.
    """
    window = sources.window
    lights = [piece.light for piece in pieces if piece.light is not None]
    if sources.capture is not None and lights:
        z_m = np.concatenate([light.z_m for light in lights], axis=1)
        land = np.concatenate([light.land for light in lights], axis=1)
        columns, slabs = slice(window.c0, window.c1), band_slabs(lights)
        if slabs is None:
            sources.capture.put(rows.start, z_m, land, columns)
        else:
            sources.capture.put(rows.start, z_m, land, columns, slabs)
    seams = [piece.seam for piece in pieces if piece.seam is not None]
    if not seams:
        return []
    seam = SeamPlanes(*(np.concatenate(planes, axis=1) for planes in zip(*seams, strict=True)))
    owed: list[Owed] = []
    if sources.seam is not None:
        measured = sources.seam.measure(
            seam.z_m, seam.switched, seam.weight, sources.spacing_m, seam.delta
        )
        owed.append((sources.seam.merge, measured))
    if sources.regimes is not None:
        owed.append(_regimes_owed(sources, sources.regimes, rows, seam.weight))
    return owed


def _read_only(surface: BandSurface) -> BandSurface:
    """``surface`` with its arrays and its water's marked read-only, so no layer's painter
    changes what the next one reads."""
    planes = [*vars(surface).values(), *surface.water.values()]
    for plane in planes:
        if isinstance(plane, np.ndarray):
            plane.flags.writeable = False
    return surface


def _meshes(
    meshes: MeshPlanes, grid: BandSampling, z_m: FloatGrid, level_m: FloatGrid, seabed: bool
) -> tuple[FloatGrid, FloatGrid, U8Grid]:
    """The piece's ground raised by its render-only meshes: ``(z_m, weight, kept class)``."""
    cut = (grid.rows.cut, grid.cols.cut)
    return composite_meshes(
        z_m,
        np.asarray(meshes.z_cm[cut], np.float32),
        np.asarray(meshes.cls[cut], np.uint8),
        level_m,
        composite_top,
        seabed=seabed,
    )


def _water_terms(
    sources: GroundSources,
    linear: GridTaps,
    z_m: FloatGrid,
    planes: tuple[FloatGrid, FloatGrid, FloatGrid],
) -> WaterTerms:
    """The band's water over ``z_m``, the rivers' laid over it; ``planes`` is ``(water_m, wet,
    measured)``."""
    water_m, wet, measured = planes
    water = band_water_terms(
        z_m, water_m, wet, measured, sources.blur_px, sources.reach, linear, sources.spacing_m
    )
    if sources.rivers is not None:
        water = sources.rivers.over(water, z_m, linear, sources.spacing_m)
    return water


def _light_planes(
    grid: BandSampling,
    z_m: FloatGrid,
    missing: BoolMask,
    water: WaterTerms,
    slabs: SlabPlanes | None,
) -> LightPlanes:
    """The piece's output pixels for the light stage: the heights, NaN where no data is drawn
    (``lighting/holes.py``), the land weight, and what floats over them
    (``render/floating.py``), cut to them already."""
    kept = (grid.rows.kept, grid.cols.kept)
    dry = np.where(missing, 0.0, 1.0 - water["cover"])
    return LightPlanes(np.where(missing, np.float32(np.nan), z_m)[kept], dry[kept], slabs)


def _direct_regime(
    sources: GroundSources,
    direct: DirectPlanes,
    grid: BandSampling,
    z_m: FloatGrid,
    missing: BoolMask,
) -> tuple[FloatGrid, BoolMask, F32Grid, FloatGrid, SeamPlanes | None, FloatGrid]:
    """The rocks composited onto the lattice under them: ``(z_m, missing, weight, rock_seen,
    seam, base_m)``, ``seam`` the output pixels the band measures when it measures and
    ``base_m`` the ground the rocks stand on. Where that lattice knows nothing the field's
    fold stands in."""
    smooth, linear = grid.smooth, grid.linear
    ground_dm, ground_missing = sample_surface(direct.ground, smooth, linear, hf.NODATA)
    base_m = np.where(ground_missing, z_m, ground_dm / np.float32(hf.DM_PER_M))
    cut = (grid.rows.cut, grid.cols.cut)
    rock = (np.asarray(direct.z[cut], np.float32), np.asarray(direct.coverage[cut]))
    wet_plane = None if sources.water is None else sources.water.wet
    kept = rock_kept(rock[0], missing, linear, wet_plane, sources.sea)
    z_m, missing, weight, switched = blend_regimes(
        base_m, missing, rock, linear, direct.subsamples, kept
    )
    rock_lift = np.clip((z_m - base_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    rock_seen = np.where(ground_missing, weight, np.minimum(weight, rock_lift))
    seam = None
    if sources.seam is not None or sources.regimes is not None:
        out = (grid.rows.kept, grid.cols.kept)
        delta = (rock[0] / 100.0 - base_m)[out]
        seam = SeamPlanes(z_m[out], switched[out], weight[out], delta)
    return z_m, missing, weight, rock_seen, seam, base_m


def _regimes_owed(
    sources: GroundSources, regimes: RegimeSources, rows: Span, weight: F32Grid
) -> Owed:
    """The regime table's count of this band's output rows, read on the field's nearest texel."""
    field = sources.field
    prov_rows = np.clip(
        np.round((sources.y_cm[rows.start : rows.stop] - field.y0_cm) / field.spacing_cm).astype(
            np.int64
        ),
        0,
        field.height - 1,
    )
    picked = np.ix_(prov_rows, sources.prov_cols)
    counted = regimes.coverage.measure(
        regimes.provenance[picked], weight, regimes.measured[picked] > 0
    )
    return regimes.coverage.merge, counted


def _borrow(sources: GroundSources, grid: BandSampling) -> FloatGrid:
    """The artwork's shading borrowed where the field's province is coarse, clamped."""
    detail, province = sources.borrow
    y_cm = sources.y_cm[grid.rows.lo : grid.rows.hi]
    art_y = grid_position(y_cm, sources.art_y0_cm, sources.art_step_cm, SHEET_PX)
    art_taps = (taps_linear(art_y, SHEET_PX), grid.art_cols)
    strength = sample_plain(province, grid.linear) / 255.0
    lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, art_taps) / 127.0)
    return np.clip(lift, *BORROW_CLAMP)


def _sample_water_surface(
    z_m: FloatGrid,
    water: WaterPlanes | None,
    sea: OpenSea | None,
    smooth: GridTaps,
    linear: GridTaps,
) -> tuple[FloatGrid, FloatGrid, FloatGrid, FloatGrid]:
    """One band's water surface, level (NaN where none), wet cover and measured share.

    With the open sea, the wet cover counts only the share of a pixel that is not void, so
    the void's edge is never drawn as land.
    """
    if water is None:
        wet = measured = np.zeros(z_m.shape, np.float32)
        return z_m, np.full(z_m.shape, np.nan, np.float32), wet, measured
    water_dm, water_missing = sample_surface(water.level, smooth, linear, hf.NODATA)
    water_m = water_dm / np.float32(hf.DM_PER_M)
    level_m = np.where(water_missing, np.nan, water_m)
    wet = sample_coverage(water.wet, linear)
    measured = sample_coverage(water.measured, linear) / np.where(wet <= 0.0, 1.0, wet)
    if sea is not None and not reads_nothing(sea.void.cover, linear):
        land = 1.0 - sample_plain(sea.void.cover, linear) / np.float32(255.0)
        wet = np.clip(wet / np.maximum(land, np.float32(1e-3)), 0.0, 1.0)
    return water_m, level_m, wet, np.clip(measured, 0.0, 1.0)
