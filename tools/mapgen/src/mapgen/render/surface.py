"""One band's ground as every layer draws it: heights, rocks, overlay, meshes, water, borrow.

``band_grid`` places a band's rows on the field's lattice and ``band_surface`` composes the
ground there. Only the meshes' seabed depends on the layer, so one surface can serve every
layer drawn from it (docs/spatial-and-map.md sections 20, 25 and 40).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple, Protocol, TypeAlias, cast

import numpy as np

from mapgen.cache import DirectPlanes, MeshPlanes, TopPlanes
from mapgen.lighting.borrow import BORROW_CLAMP, BORROW_GAIN
from mapgen.palette.scene import WaterTerms
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.rivers import RiverWater
from mapgen.palette.water.shore import (
    MESH_FULL_LIFT_M,
    OCEAN_LEVEL_M,
    blend_water,
    composite_meshes,
    shore_terms,
)
from mapgen.palette.water.surface import WATER_DEPTH_FULL_M, water_alpha, water_depth_fraction
from mapgen.terrain.measure import SEAM_MID, RegimeCoverage, SeamTrace
from mapgen.terrain.rasters import pixel_coverage
from mapgen.terrain.sample import (
    grid_position,
    reads_nothing,
    sample_coverage,
    sample_plain,
    sample_surface,
    taps_linear,
)
from satisfactory_mcp.core.arrays import BoolMask, F64Grid, I64Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "DIRECT_LIFT_KNEE_M",
    "AxisTaps",
    "BandRows",
    "BandSampling",
    "BandSurface",
    "GridTaps",
    "GroundSources",
    "Kernel",
    "LightCapture",
    "Owed",
    "RegimeSources",
    "WaterPlanes",
    "Window",
    "band_grid",
    "band_surface",
    "band_water_terms",
    "blend_regimes",
    "composite_top",
    "smooth_lift",
]

#: The knee of the smoothed positive part that lets a rock raise the ground and never lower
#: it, in metres: the field's own hard ``max`` with its corner rounded, so the hillshade draws
#: no line round a formation's base. It sits at most half the knee above the hard answer.
DIRECT_LIFT_KNEE_M = 0.25

#: One axis of sampling taps: the indices and their weights (or PCHIP's cell fractions).
AxisTaps: TypeAlias = tuple[np.ndarray, np.ndarray]
#: A band's taps: its rows' and its columns'.
GridTaps: TypeAlias = tuple[AxisTaps, AxisTaps]
#: How a height raster's taps are built along one axis: ``taps_pchip`` or ``taps_cubic``.
Kernel: TypeAlias = Callable[[np.ndarray, int], AxisTaps]
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

    level: np.ndarray
    wet: np.ndarray
    measured: np.ndarray


class RegimeSources(NamedTuple):
    """The regime table's accumulator and the field planes it reads on the field's grid."""

    coverage: RegimeCoverage
    measured: np.ndarray
    provenance: np.ndarray


class LightCapture(Protocol):
    """Where the drawn heights and land weight go for the light bake (``lighting.stage``)."""

    def put(self, row: int, z_m: np.ndarray, land: np.ndarray, columns: slice = ..., /) -> None:
        """Rows from ``row`` on, over ``columns`` of the sheet."""
        ...


@dataclass(frozen=True)
class GroundSources:
    """What every band's ground is sampled from, and the column taps they share, built once.

    ``x_cm`` holds the window's pixel centres, ``y_cm`` the whole sheet's. The bands only
    read it, so any number of threads may share it.
    """

    field: hf.Field
    heights: np.ndarray
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
    borrow: tuple[np.ndarray, np.ndarray]
    blur_px: float
    reach: np.ndarray | None
    rivers: RiverWater | None
    capture: LightCapture | None
    cols_smooth: AxisTaps
    cols_linear: AxisTaps
    art_cols: AxisTaps
    art_y0_cm: float
    art_step_cm: float
    prov_cols: I64Grid


class BandRows(NamedTuple):
    """A band's output rows ``[top, bottom)``, drawn over ``[lo, hi)`` with its halo.

    ``band`` is ``[lo, hi)`` in the rows of a raster cut to the window.
    """

    top: int
    bottom: int
    lo: int
    hi: int
    band: slice

    @property
    def kept(self) -> slice:
        """The rows of a band's arrays that are output, its halo cropped."""
        return slice(self.top - self.lo, self.bottom - self.lo)


@dataclass(frozen=True)
class BandSampling:
    """A band placed on the field's lattice: its rows' positions and both kinds of taps."""

    rows: BandRows
    field_y: F64Grid
    smooth: GridTaps
    linear: GridTaps


@dataclass(frozen=True)
class BandSurface:
    """One band's ground, halo included, ready for any layer's painter.

    ``weight`` is the rock's coverage and ``rock_seen`` how much of it stands proud, both None
    without the direct regime; ``top_weight`` the overlay's lift; ``level_m`` the water level,
    NaN where there is none.
    """

    z_m: np.ndarray
    missing: BoolMask
    weight: np.ndarray | None
    rock_seen: np.ndarray | None
    top_weight: np.ndarray | None
    water_m: np.ndarray
    level_m: np.ndarray
    wet: np.ndarray
    measured: np.ndarray
    mesh_weight: np.ndarray | None
    mesh_class: np.ndarray | None
    water: WaterTerms
    borrow: np.ndarray


def smooth_lift(delta_m: np.ndarray) -> np.ndarray:
    """The raise-only rule: ``max(delta, 0)`` with its corner rounded by the knee."""
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    return 0.5 * (delta_m + np.sqrt(delta_m * delta_m + knee * knee))


def composite_top(
    z_m: np.ndarray, top_z_cm: np.ndarray, top_coverage: np.ndarray, subsamples: int = 1
) -> np.ndarray:
    """``z_m`` raised by the top raster through the same coverage and smoothed lift as rocks."""
    w = np.clip(pixel_coverage(top_coverage, subsamples), 0.0, 1.0)
    delta = top_z_cm / np.float32(100.0) - z_m
    return (z_m + w * smooth_lift(delta)).astype(np.float32)


def blend_regimes(
    base_m: np.ndarray,
    missing: BoolMask,
    direct: tuple[np.ndarray, np.ndarray],
    linear: GridTaps,
    subsamples: int,
    keep: np.ndarray | None = None,
) -> tuple[np.ndarray, BoolMask, np.ndarray, np.ndarray]:
    """The two-regime height and what it was made of: ``(z_m, missing, w, switched)``.

    The field's own composition rule at the render's spacing: ``z = base + w * lift(z_rock -
    base)``, with ``w`` the direct raster's coverage of the pixel (scaled by ``keep``, the
    share the void leaves a rock under the sea) and ``lift`` the smoothed positive part.
    Where the lattice knows nothing, the caller passes the field's own fold as ``base_m``.
    ``switched`` is the hard switch the seam trace measures the blend against.
    """
    z_cm, coverage = direct
    fraction = pixel_coverage(coverage, subsamples)
    if keep is not None:
        fraction = fraction * keep
    w = np.clip(fraction, 0.0, 1.0).astype(np.float32)
    z_direct_m = z_cm / np.float32(100.0)
    z_m = base_m + w * smooth_lift(z_direct_m - base_m)
    # A rock off the edge of the landscape is the whole answer: a switch, on an edge the
    # render already draws hard.
    only_rock = missing & (fraction > 0.0)
    z_m = np.where(only_rock, z_direct_m, z_m)
    w = np.where(only_rock, np.float32(1.0), w)
    switched = np.where(w >= SEAM_MID, np.maximum(z_direct_m, base_m), base_m)
    return (
        z_m.astype(np.float32),
        missing & (fraction <= 0.0),
        w,
        switched.astype(np.float32),
    )


def band_water_terms(
    z_m: np.ndarray,
    water_m: np.ndarray,
    wet: np.ndarray,
    measured: np.ndarray,
    blur_px: float,
    reach: np.ndarray | None,
    linear: GridTaps,
    spacing_m: float,
) -> dict[str, np.ndarray]:
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
    return terms


def band_grid(sources: GroundSources, top: int, band_rows: int, halo: int) -> BandSampling:
    """Rows ``[top, top + band_rows)`` with ``halo`` rows either side, on the field's lattice."""
    r0, r1 = sources.window.r0, sources.window.r1
    bottom = min(top + band_rows, r1)
    lo, hi = max(top - halo, r0), min(bottom + halo, r1)
    field = sources.field
    field_y = grid_position(sources.y_cm[lo:hi], field.y0_cm, field.spacing_cm, field.height)
    return BandSampling(
        rows=BandRows(top, bottom, lo, hi, slice(lo - r0, hi - r0)),
        field_y=field_y,
        smooth=(sources.kernel(field_y, field.height), sources.cols_smooth),
        linear=(taps_linear(field_y, field.height), sources.cols_linear),
    )


def band_surface(
    sources: GroundSources, grid: BandSampling, seabed: bool
) -> tuple[BandSurface, list[Owed]]:
    """One band's ground, and the seam and regime measurements it owes, merged in band order.

    ``seabed`` draws the meshes under the sea's level too. With a capture, the drawn heights
    and land weight go to the light stage.
    """
    rows, smooth, linear = grid.rows, grid.smooth, grid.linear
    z_dm, missing = sample_surface(sources.heights, smooth, linear, hf.NODATA)
    z_m = z_dm / np.float32(hf.DM_PER_M)
    weight = rock_seen = None
    owed: list[Owed] = []
    if sources.direct is not None:
        z_m, missing, weight, rock_seen, owed = _direct_regime(
            sources, sources.direct, grid, z_m, missing
        )
    top_weight = None
    if sources.overlay is not None:
        top_z, top_coverage, top_subsamples = sources.overlay
        below = z_m
        z_m = composite_top(
            z_m,
            np.asarray(top_z[rows.band], np.float32),
            np.asarray(top_coverage[rows.band]),
            top_subsamples,
        )
        top_weight = np.clip((z_m - below) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    water_m, level_m, wet, measured = _sample_water_surface(
        z_m, sources.water, sources.sea, smooth, linear
    )
    mesh_weight = mesh_class = None
    if sources.meshes is not None:
        z_m, mesh_weight, mesh_class = composite_meshes(
            z_m,
            np.asarray(sources.meshes.z_cm[rows.band], np.float32),
            np.asarray(sources.meshes.cls[rows.band]),
            level_m,
            composite_top,
            seabed=seabed,
        )
    water = band_water_terms(
        z_m, water_m, wet, measured, sources.blur_px, sources.reach, linear, sources.spacing_m
    )
    if sources.rivers is not None:
        water = sources.rivers.over(water, z_m, linear, sources.spacing_m)
    if sources.capture is not None:
        dry = np.where(missing, 0.0, 1.0 - water["cover"])
        columns = slice(sources.window.c0, sources.window.c1)
        sources.capture.put(rows.top, z_m[rows.kept], dry[rows.kept], columns)
    surface = BandSurface(
        z_m=z_m, missing=missing, weight=weight, rock_seen=rock_seen, top_weight=top_weight,
        water_m=water_m, level_m=level_m, wet=wet, measured=measured, mesh_weight=mesh_weight,
        mesh_class=mesh_class, water=cast(WaterTerms, water), borrow=_borrow(sources, grid),
    )  # fmt: skip
    return surface, owed


def _direct_regime(
    sources: GroundSources,
    direct: DirectPlanes,
    grid: BandSampling,
    z_m: np.ndarray,
    missing: BoolMask,
) -> tuple[np.ndarray, BoolMask, np.ndarray, np.ndarray, list[Owed]]:
    """The rocks composited onto the lattice under them: ``(z_m, missing, weight, rock_seen,
    owed)``. Where that lattice knows nothing the field's fold stands in."""
    rows, smooth, linear = grid.rows, grid.smooth, grid.linear
    direct_z, direct_coverage, lattice, subsamples = direct
    ground_dm, ground_missing = sample_surface(lattice, smooth, linear, hf.NODATA)
    base_m = np.where(ground_missing, z_m, ground_dm / np.float32(hf.DM_PER_M))
    rock = (np.asarray(direct_z[rows.band], np.float32), np.asarray(direct_coverage[rows.band]))
    wet_plane = None if sources.water is None else sources.water.wet
    kept = _rock_kept(rock[0], missing, wet_plane, sources.sea, linear)
    z_m, missing, weight, switched = blend_regimes(base_m, missing, rock, linear, subsamples, kept)
    rock_lift = np.clip((z_m - base_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    rock_seen = np.where(ground_missing, weight, np.minimum(weight, rock_lift))
    owed: list[Owed] = []
    keep = rows.kept
    if sources.seam is not None:
        delta = (rock[0] / 100.0 - base_m)[keep]
        seam = sources.seam.measure(
            z_m[keep], switched[keep], weight[keep], sources.spacing_m, delta
        )
        owed.append((sources.seam.merge, seam))
    if sources.regimes is not None:
        owed.append(_regimes_owed(sources, sources.regimes, rows, weight[keep]))
    return z_m, missing, weight, rock_seen, owed


def _regimes_owed(
    sources: GroundSources, regimes: RegimeSources, rows: BandRows, weight: np.ndarray
) -> Owed:
    """The regime table's count of this band's output rows, read on the field's nearest texel."""
    field = sources.field
    prov_rows = np.clip(
        np.round((sources.y_cm[rows.top : rows.bottom] - field.y0_cm) / field.spacing_cm).astype(
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


def _borrow(sources: GroundSources, grid: BandSampling) -> np.ndarray:
    """The artwork's shading borrowed where the field's province is coarse, clamped."""
    detail, province = sources.borrow
    y_cm = sources.y_cm[grid.rows.lo : grid.rows.hi]
    art_y = grid_position(y_cm, sources.art_y0_cm, sources.art_step_cm, SHEET_PX)
    art_taps = (taps_linear(art_y, SHEET_PX), sources.art_cols)
    strength = sample_plain(province, grid.linear) / 255.0
    lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, art_taps) / 127.0)
    return np.clip(lift, *BORROW_CLAMP)


def _sample_water_surface(
    z_m: np.ndarray,
    water: WaterPlanes | None,
    sea: OpenSea | None,
    smooth: GridTaps,
    linear: GridTaps,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
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


def _rock_kept(
    z_rock_cm: np.ndarray,
    missing: BoolMask,
    wet_plane: np.ndarray | None,
    sea: OpenSea | None,
    linear: GridTaps,
) -> np.ndarray | None:
    """The share of its coverage a rock keeps under the void; None without the open sea.

    Out of the sea a rock keeps all of it; under the sea's level none on no data, else what
    the void's cover leaves of the wet share. The open sea always comes with its wet plane.
    """
    if sea is None:
        return None
    above = np.clip(z_rock_cm / np.float32(100.0) - np.float32(OCEAN_LEVEL_M) + 0.5, 0.0, 1.0)
    if reads_nothing(sea.void.cover, linear):
        under = np.where(missing, np.float32(1.0), np.float32(0.0))
    else:
        assert wet_plane is not None
        cover = np.clip(sample_plain(sea.void.cover, linear) / np.float32(255.0), 0.0, 1.0)
        under = np.where(missing, np.float32(1.0), cover * sample_coverage(wet_plane, linear))
    return (1.0 - (1.0 - above) * under).astype(np.float32)
