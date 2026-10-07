"""Rectangle queries over a loaded field: the ``Area`` a pad reads as, and the nearest water."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from ....core.arrays import BoolMask, F32Grid
from .codec import RasterGrid
from .planes import (
    DM_PER_M,
    NODATA,
    PROV_CLIFF_DIRECT,
    PROV_CLIFF_VALUES,
    PROV_LANDSCAPE,
    TERRAIN_NAME,
    TOP_NAME,
    WATER_DRY,
)
from .readings import AMBIGUOUS_M, NO_SHAPE, SURFACES, Area, NearWater, Surface, area_shape
from .store import PlaneStore

__all__ = ["FieldAreas"]


@dataclass(frozen=True)
class _Window:
    """Half-open texel bounds of a rectangle on the grid, and the stride that fits the cap."""

    row_lo: int
    row_hi: int
    col_lo: int
    col_hi: int
    stride: int

    @property
    def cut(self) -> tuple[slice, slice]:
        """The strided view, as numpy indexes the field's planes."""
        return (
            slice(self.row_lo, self.row_hi, self.stride),
            slice(self.col_lo, self.col_hi, self.stride),
        )

    @property
    def rows(self) -> NDArray[np.int_]:
        return np.arange(self.row_lo, self.row_hi, self.stride)

    @property
    def cols(self) -> NDArray[np.int_]:
        return np.arange(self.col_lo, self.col_hi, self.stride)

    @property
    def texels(self) -> int:
        """Texels inside the bounds, before the stride."""
        return (self.row_hi - self.row_lo) * (self.col_hi - self.col_lo)


@dataclass(frozen=True)
class _Shares:
    """Texel counts of a decimated view as percentages of the rectangle that was asked for."""

    stride: int
    requested: float

    def pct(self, count: float) -> float:
        return 100.0 * count * self.stride * self.stride / self.requested


class FieldAreas(PlaneStore):
    """``area`` and ``nearest_water`` over the planes ``PlaneStore`` opens."""

    def _bounds(
        self, x0_cm: float, y0_cm: float, x1_cm: float, y1_cm: float
    ) -> tuple[int, int, int, int]:
        """Half-open ``(row_lo, row_hi, col_lo, col_hi)`` clipped to the grid; may be empty.

        Rounded like ``texel``, because the grid is vertex-aligned.
        """
        lo_x, hi_x = (x0_cm, x1_cm) if x0_cm <= x1_cm else (x1_cm, x0_cm)
        lo_y, hi_y = (y0_cm, y1_cm) if y0_cm <= y1_cm else (y1_cm, y0_cm)
        col_lo = round((lo_x - self.x0_cm) / self.spacing_cm)
        col_hi = round((hi_x - self.x0_cm) / self.spacing_cm) + 1
        row_lo = round((lo_y - self.y0_cm) / self.spacing_cm)
        row_hi = round((hi_y - self.y0_cm) / self.spacing_cm) + 1
        return (
            max(row_lo, 0),
            min(row_hi, self.height),
            max(col_lo, 0),
            min(col_hi, self.width),
        )

    def _window(
        self, x0_cm: float, y0_cm: float, x1_cm: float, y1_cm: float, max_texels: int
    ) -> _Window | None:
        """The rectangle's texels, decimated past ``max_texels``; ``None`` if none is on the grid."""
        row_lo, row_hi, col_lo, col_hi = self._bounds(x0_cm, y0_cm, x1_cm, y1_cm)
        if row_lo >= row_hi or col_lo >= col_hi:
            return None
        window = _Window(row_lo, row_hi, col_lo, col_hi, 1)
        if max_texels <= 0 or window.texels <= max_texels:
            return window
        return replace(window, stride=int(np.ceil(np.sqrt(window.texels / max_texels))))

    def area(
        self,
        x0_cm: float,
        y0_cm: float,
        x1_cm: float,
        y1_cm: float,
        max_texels: int = 1_000_000,
        *,
        surface: Surface = "ground",
        shape: bool = True,
    ) -> Area:
        """The terrain over a rectangle, as the facts a build decision reads.

        Always an ``Area``: off the grid is 100% no-data, which is an answer. Statistics are
        over the texels with data, and ``nodata_pct`` says how much of the pad they speak for.
        Past ``max_texels`` the view is decimated by ``stride``, which smooths slope and
        roughness, so two areas compare only at equal strides. ``surface`` names the plane;
        one this field lacks is a ``ValueError``, never another surface. ``shape=False``
        skips slope and roughness, most of the cost.
        """
        self._require(surface)
        span_cols = round(abs(x1_cm - x0_cm) / self.spacing_cm) + 1
        span_rows = round(abs(y1_cm - y0_cm) / self.spacing_cm) + 1
        requested = span_rows * span_cols
        empty = Area(
            x0_cm=x0_cm,
            y0_cm=y0_cm,
            x1_cm=x1_cm,
            y1_cm=y1_cm,
            stride=1,
            requested_texels=requested,
            texels=0,
            nodata_pct=100.0,
            surface=surface,
        )
        window = self._window(x0_cm, y0_cm, x1_cm, y1_cm, max_texels)
        if window is None:
            return empty
        z, good = self._surface_cut(surface, window)
        n_good = int(good.sum())
        stride = window.stride
        shares = _Shares(stride, float(requested) if requested else 1.0)
        # Over the REQUESTED rectangle, so a pad hanging off the grid reports what nobody
        # measured; stride^2 because a decimated view stands for the whole area.
        outside = max(requested - float(z.size) * stride * stride, 0.0)
        nodata_pct = 100.0 * ((z.size - n_good) * stride * stride + outside) / shares.requested
        if n_good == 0:
            return replace(empty, stride=stride)
        z_valid = z[good]
        submerged, water_level, water_drop = self._area_water(window, z, good, shares)
        return Area(
            x0_cm=x0_cm,
            y0_cm=y0_cm,
            x1_cm=x1_cm,
            y1_cm=y1_cm,
            stride=stride,
            requested_texels=requested,
            texels=n_good,
            nodata_pct=round(nodata_pct, 1),
            z_min_m=round(float(z_valid.min()), 1),
            z_max_m=round(float(z_valid.max()), 1),
            z_mean_m=round(float(z_valid.mean()), 1),
            z_median_m=round(float(np.median(z_valid)), 1),
            **(area_shape(z, good, self.spacing_cm * stride / 100.0) if shape else NO_SHAPE),
            submerged_pct=round(submerged, 1),
            water_level_m=water_level,
            water_below_ground_m=water_drop,
            provenance_pct=self._provenance_pct(surface, window, n_good, shares),
            surface=surface,
            ambiguous_pct=round(shares.pct(self._ambiguous_count(window)), 1),
            cave_pct=round(shares.pct(self._cave_count(window, good)), 1),
        )

    def _require(self, surface: Surface) -> None:
        if surface not in SURFACES:
            raise ValueError(f"surface {surface!r} is not one of {', '.join(SURFACES)}")
        if surface == "terrain" and not self.has_terrain:
            raise ValueError("this field has no terrain plane (regenerate with generator v4)")
        if surface == "top" and not self.has_top:
            raise ValueError("this field has no top plane (regenerate with generator v4)")

    def _surface_cut(self, surface: Surface, window: _Window) -> tuple[F32Grid, BoolMask]:
        """``(z in metres as float32, has-data mask)`` for one surface over a window."""
        if surface == "terrain":
            return self._terrain_cut(window)
        plane = self.height_dm if surface == "ground" else self.plane(TOP_NAME)
        if plane is None:
            raise ValueError("this field has no top plane (regenerate with generator v4)")
        raw = plane[window.cut]
        good = raw != NODATA
        return np.where(good, raw, 0).astype(np.float32) / np.float32(DM_PER_M), good

    def _terrain_cut(self, window: _Window) -> tuple[F32Grid, BoolMask]:
        """The terrain plane read onto a window of the grid, unresampled; off its frame, no data."""
        plane = self.plane(TERRAIN_NAME)
        tg = self.terrain_grid
        rows, cols = window.rows, window.cols
        z = np.zeros((rows.size, cols.size), np.float32)
        good = np.zeros((rows.size, cols.size), bool)
        if plane is None or tg is None or tg["row_off"] is None or tg["col_off"] is None:
            return z, good
        tr, tc = rows - tg["row_off"], cols - tg["col_off"]
        ok_r = np.nonzero((tr >= 0) & (tr < tg["height"]))[0]
        ok_c = np.nonzero((tc >= 0) & (tc < tg["width"]))[0]
        if ok_r.size == 0 or ok_c.size == 0:
            return z, good
        step = window.stride
        raw = plane[tr[ok_r[0]] : tr[ok_r[-1]] + 1 : step, tc[ok_c[0]] : tc[ok_c[-1]] + 1 : step]
        place = np.ix_(ok_r, ok_c)
        good[place] = raw != 0
        z[place] = np.where(
            raw != 0,
            (raw.astype(np.float32) - np.float32(tg["zero"])) / np.float32(tg["units_per_m"])
            + np.float32(tg["offset_m"]),
            0,
        )
        return z, good

    def _provenance_pct(
        self, surface: Surface, window: _Window, n_good: int, shares: _Shares
    ) -> dict[int, float]:
        """Share of the rectangle each layer answered; all of the terrain plane is landscape."""
        if surface == "terrain":
            counts = np.zeros(PROV_CLIFF_DIRECT + 1, np.int64)
            counts[PROV_LANDSCAPE] = n_good
        else:
            counts = np.bincount(
                self.provenance_plane[window.cut].ravel(), minlength=PROV_CLIFF_DIRECT + 1
            )
        return {
            int(code): round(shares.pct(float(counts[code])), 1)
            for code in range(len(counts))
            if counts[code]
        }

    def _ambiguous_count(self, window: _Window) -> int:
        """Texels of a window where the ground may be a roof: over terrain by ``AMBIGUOUS_M``."""
        ground, has_ground = self._surface_cut("ground", window)
        cliff = np.isin(self.provenance_plane[window.cut], PROV_CLIFF_VALUES)
        if not self.has_terrain:
            return int((has_ground & cliff).sum())
        terrain, has_terrain = self._terrain_cut(window)
        over = has_terrain & (ground - terrain > AMBIGUOUS_M)
        hole = ~has_terrain & cliff
        return int((has_ground & (over | hole)).sum())

    def _cave_count(self, window: _Window, good: BoolMask) -> int:
        found = self.caves()
        if found is None:
            return 0
        xs_cm = self.x0_cm + window.cols.astype(np.float64) * self.spacing_cm
        ys_cm = self.y0_cm + window.rows.astype(np.float64) * self.spacing_cm
        flagged = found.flagged(xs_cm, ys_cm)
        return int((flagged & good).sum())

    def _area_water(
        self, window: _Window, z: F32Grid, good: BoolMask, shares: _Shares
    ) -> tuple[float, float | None, float | None]:
        """Submerged share, water surface level, and its drop below the dry ground."""
        water = self.water_raster()
        if water is None:
            return 0.0, None, None
        wet_dm = water[window.cut]
        wet = self._wet_mask(window, wet_dm)
        n_wet = int(wet.sum())
        if n_wet == 0:
            return 0.0, None, None
        level = float(np.median(wet_dm[wet].astype(np.float32))) / DM_PER_M
        # Against the DRY ground, not the pad median: on a pad that is mostly lake the median
        # is the lake bed, and a pond 40 m below a plateau rim would read a drop near zero.
        dry_z = z[good & ~wet]
        drop = None if dry_z.size == 0 else round(float(np.median(dry_z)) - level, 1)
        return shares.pct(n_wet), round(level, 1), drop

    def _wet_mask(self, window: _Window, wet_dm: RasterGrid) -> BoolMask:
        """Which texels of a window have water standing on them: ``Reading.submerged``, vectorised."""
        wet = wet_dm != NODATA
        grades = self.water_quality_raster()
        if grades is not None:
            return wet & (grades[window.cut] != WATER_DRY)
        raw = self.height_dm[window.cut]
        return wet & (raw != NODATA) & (wet_dm > raw)

    def nearest_water(
        self,
        x_cm: float,
        y_cm: float,
        radius_cm: float,
        max_texels: int = 1_000_000,
    ) -> NearWater | None:
        """The closest standing water to a point within a square of that half-width.

        ``None`` only when this field has no water plane, which is not "no water nearby".
        Decimated past ``max_texels`` as ``area`` is, so ``distance_m`` is quantised to
        ``stride`` metres and the stride is reported.
        """
        water = self.water_raster()
        if water is None:
            return None
        radius_m = radius_cm / 100.0
        window = self._window(
            x_cm - radius_cm, y_cm - radius_cm, x_cm + radius_cm, y_cm + radius_cm, max_texels
        )
        if window is None:
            return NearWater(radius_m=radius_m, stride=1, covered_pct=0.0)
        span = round(2 * radius_cm / self.spacing_cm) + 1
        covered = round(100.0 * window.texels / float(span * span), 1) if span else 0.0
        cut, stride = window.cut, window.stride
        wet_dm = water[cut]
        rows, cols = np.nonzero(self._wet_mask(window, wet_dm))
        if rows.size == 0:
            return NearWater(radius_m=radius_m, stride=stride, covered_pct=covered)
        # In the decimated view's index space, then scaled back: once stride > 1 the centre
        # is rarely on a sampled texel, so its offset is carried through.
        centre_col = ((x_cm - self.x0_cm) / self.spacing_cm - window.col_lo) / stride
        centre_row = ((y_cm - self.y0_cm) / self.spacing_cm - window.row_lo) / stride
        d2 = (cols - centre_col) ** 2 + (rows - centre_row) ** 2
        best = int(np.argmin(d2))
        grades = self.water_quality_raster()
        return NearWater(
            radius_m=radius_m,
            stride=stride,
            covered_pct=covered,
            distance_m=round(float(np.sqrt(d2[best])) * self.spacing_cm * stride / 100.0, 1),
            level_m=round(float(wet_dm[rows[best], cols[best]]) / DM_PER_M, 1),
            quality=int(grades[cut][rows[best], cols[best]]) if grades is not None else WATER_DRY,
        )
