"""Rectangle queries over a loaded field: the ``Area`` a pad reads as, and the nearest water.

``FieldAreas`` is mixed into ``field.Field`` and reads its planes, grid and cave masks.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

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
from .readings import AMBIGUOUS_M, SURFACES, Area, NearWater, area_shape

__all__ = ["FieldAreas"]


class FieldAreas:
    """``window`` and ``nearest_water`` for ``Field``, which supplies the planes they cut."""

    def _rows_cols(
        self, x0_cm: float, y0_cm: float, x1_cm: float, y1_cm: float
    ) -> tuple[int, int, int, int]:
        """Half-open ``(row_lo, row_hi, col_lo, col_hi)`` clipped to the grid.

        Rounded like ``texel``, because the grid is vertex-aligned; the bounds may come out
        empty (``lo >= hi``) for a rectangle entirely off the grid, and every caller must
        cope with that rather than assume an overlap.
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

    def window(
        self,
        x0_cm: float,
        y0_cm: float,
        x1_cm: float,
        y1_cm: float,
        max_texels: int = 1_000_000,
        *,
        surface: str = "ground",
        shape: bool = True,
    ) -> Area:
        """The terrain over a rectangle, as the facts a build decision reads.

        Always an ``Area``, never ``None``: a rectangle off the grid is 100% no-data, which
        is an answer, and returning ``None`` there would make "nothing is known" and "you
        asked wrong" the same result. Every statistic is over the texels that HAVE data, and
        ``nodata_pct`` is what says how much of the pad they speak for.

        Reads a strided numpy view, never a per-texel loop: at 1 m over a 750 km2 world a
        200 m pad is 40k texels and a kilometre pad is a million, and ``at()`` costs ~1.2 us
        a texel. Beyond ``max_texels`` the view is decimated by an integer ``stride``, which
        is reported -- decimation lowers roughness and slope, because it cannot see detail
        finer than the new spacing, so a caller comparing two areas must compare their
        strides too.

        ``surface`` picks the plane the statistics are over; a plane this field lacks is a
        ``ValueError``, never a silent fall back to another surface. ``shape=False`` skips
        slope and roughness, which are most of the cost.
        """
        if surface not in SURFACES:
            raise ValueError(f"surface {surface!r} is not one of {', '.join(SURFACES)}")
        if surface == "terrain" and not self.has_terrain:
            raise ValueError("this field has no terrain plane (regenerate with generator v4)")
        if surface == "top" and not self.has_top:
            raise ValueError("this field has no top plane (regenerate with generator v4)")
        row_lo, row_hi, col_lo, col_hi = self._rows_cols(x0_cm, y0_cm, x1_cm, y1_cm)
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
        if row_lo >= row_hi or col_lo >= col_hi:
            return empty

        stride = 1
        inside = (row_hi - row_lo) * (col_hi - col_lo)
        if max_texels > 0 and inside > max_texels:
            stride = int(np.ceil(np.sqrt(inside / max_texels)))
        cut = (slice(row_lo, row_hi, stride), slice(col_lo, col_hi, stride))

        z, good = self._surface_cut(surface, cut)
        n_good = int(good.sum())
        # The percentages are over the REQUESTED rectangle, so a pad hanging off the grid
        # edge reports the part nobody measured instead of a confident answer about the
        # rest. Scaled by stride^2 because a decimated view stands for the whole area.
        seen = float(z.size) * stride * stride
        outside = max(requested - seen, 0.0)
        denom = float(requested) if requested else 1.0
        nodata_pct = 100.0 * ((z.size - n_good) * stride * stride + outside) / denom
        if n_good == 0:
            return replace(empty, stride=stride)

        z_valid = z[good]

        if surface == "terrain":
            counts = np.zeros(PROV_CLIFF_DIRECT + 1, np.int64)
            counts[PROV_LANDSCAPE] = n_good
        else:
            counts = np.bincount(self._prov[cut].ravel(), minlength=PROV_CLIFF_DIRECT + 1)
        provenance_pct = {
            int(code): round(100.0 * float(counts[code]) * stride * stride / denom, 1)
            for code in range(len(counts))
            if counts[code]
        }
        ambiguous = 100.0 * self._ambiguous_count(cut) * stride * stride / denom
        cave = 100.0 * self._cave_count(cut, good) * stride * stride / denom

        submerged, water_level, water_drop = self._area_water(cut, z, good, z_valid, stride, denom)

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
            **(area_shape(z, good, self.spacing_cm * stride / 100.0) if shape else {}),
            submerged_pct=round(submerged, 1),
            water_level_m=water_level,
            water_below_ground_m=water_drop,
            provenance_pct=provenance_pct,
            surface=surface,
            ambiguous_pct=round(ambiguous, 1),
            cave_pct=round(cave, 1),
        )

    def _surface_cut(self, surface: str, cut: tuple[slice, slice]) -> tuple[np.ndarray, np.ndarray]:
        """``(z in metres as float32, has-data mask)`` for one surface over a cut of the grid."""
        if surface == "terrain":
            return self._terrain_cut(cut)
        plane = self._height_dm if surface == "ground" else self._plane(TOP_NAME)
        raw = plane[cut]  # type: ignore[index]
        good = raw != NODATA
        return np.where(good, raw, 0).astype(np.float32) / np.float32(DM_PER_M), good

    def _terrain_cut(self, cut: tuple[slice, slice]) -> tuple[np.ndarray, np.ndarray]:
        """The terrain plane resampled-free onto a cut of the main grid; off its frame is no data."""
        plane = self._plane(TERRAIN_NAME)
        tg = self._terrain_grid
        rows = np.arange(cut[0].start, cut[0].stop, cut[0].step)
        cols = np.arange(cut[1].start, cut[1].stop, cut[1].step)
        z = np.zeros((rows.size, cols.size), np.float32)
        good = np.zeros((rows.size, cols.size), bool)
        if plane is None or tg is None or tg["row_off"] is None:
            return z, good
        tr, tc = rows - tg["row_off"], cols - tg["col_off"]
        ok_r = np.nonzero((tr >= 0) & (tr < tg["height"]))[0]
        ok_c = np.nonzero((tc >= 0) & (tc < tg["width"]))[0]
        if ok_r.size == 0 or ok_c.size == 0:
            return z, good
        step = cut[0].step or 1
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

    def _ambiguous_count(self, cut: tuple[slice, slice]) -> int:
        """Texels of a cut where the ground may be a roof: over terrain by ``AMBIGUOUS_M``."""
        ground, has_ground = self._surface_cut("ground", cut)
        if not self.has_terrain:
            return int((has_ground & np.isin(self._prov[cut], PROV_CLIFF_VALUES)).sum())
        terrain, has_terrain = self._terrain_cut(cut)
        over = has_terrain & (ground - terrain > AMBIGUOUS_M)
        hole = ~has_terrain & np.isin(self._prov[cut], PROV_CLIFF_VALUES)
        return int((has_ground & (over | hole)).sum())

    def _cave_count(self, cut: tuple[slice, slice], good: np.ndarray) -> int:
        found = self.caves()
        if found is None:
            return 0
        rows = np.arange(cut[0].start, cut[0].stop, cut[0].step)
        cols = np.arange(cut[1].start, cut[1].stop, cut[1].step)
        flagged = found.flagged(
            self.x0_cm + cols * self.spacing_cm, self.y0_cm + rows * self.spacing_cm
        )
        return int((flagged & good).sum())

    def _area_water(
        self,
        cut: tuple[slice, slice],
        z: np.ndarray,
        good: np.ndarray,
        z_valid: np.ndarray,
        stride: int,
        denom: float,
    ) -> tuple[float, float | None, float | None]:
        """Submerged share, water surface level, and its drop below the dry ground."""
        water = self._water_raster()
        if water is None:
            return 0.0, None, None
        wet_dm = water[cut]
        wet = self._wet_mask(cut, wet_dm)
        n_wet = int(wet.sum())
        if n_wet == 0:
            return 0.0, None, None
        level = float(np.median(wet_dm[wet].astype(np.float32))) / DM_PER_M
        dry_z = z[good & ~wet]
        # `water_below_ground_m` is measured against the DRY ground, not the pad median: on
        # a pad that is mostly lake the pad median IS the lake bed, and the drop would come
        # out near zero for a pond 40 m below a plateau rim.
        drop = None if dry_z.size == 0 else round(float(np.median(dry_z)) - level, 1)
        return 100.0 * n_wet * stride * stride / denom, round(level, 1), drop

    def _wet_mask(self, cut: tuple[slice, slice], wet_dm: np.ndarray) -> np.ndarray:
        """Which texels of a cut have water standing on them. Vectorises ``Reading.submerged``.

        With a quality plane, wet is ``quality != WATER_DRY``; without one -- a field
        written before that plane -- all there is to go on is ``water > ground``, which over
        the fill layer reads the open ocean as dry. The fallback stays because a
        pre-quality field is still readable, not because the comparison is sound.
        """
        wet = wet_dm != NODATA
        grades = self._water_quality_raster()
        if grades is not None:
            return wet & (grades[cut] != WATER_DRY)
        raw = self._height_dm[cut]
        return wet & (raw != NODATA) & (wet_dm > raw)

    def nearest_water(
        self,
        x_cm: float,
        y_cm: float,
        radius_cm: float,
        max_texels: int = 1_000_000,
    ) -> NearWater | None:
        """The closest standing water to a point within a square of that half-width.

        ``None`` only when this field carries no water plane at all, which is a different
        answer from "no water nearby" and must not be collapsed into it. Decimated past
        ``max_texels`` exactly as ``window`` is, so ``distance_m`` is quantised to
        ``stride`` metres and ``stride`` is reported rather than folded away.
        """
        water = self._water_raster()
        if water is None:
            return None
        radius_m = radius_cm / 100.0
        row_lo, row_hi, col_lo, col_hi = self._rows_cols(
            x_cm - radius_cm, y_cm - radius_cm, x_cm + radius_cm, y_cm + radius_cm
        )
        span = round(2 * radius_cm / self.spacing_cm) + 1
        inside = max(row_hi - row_lo, 0) * max(col_hi - col_lo, 0)
        covered = round(100.0 * inside / float(span * span), 1) if span else 0.0
        if inside == 0:
            return NearWater(radius_m=radius_m, stride=1, covered_pct=0.0)

        stride = 1
        if max_texels > 0 and inside > max_texels:
            stride = int(np.ceil(np.sqrt(inside / max_texels)))
        cut = (slice(row_lo, row_hi, stride), slice(col_lo, col_hi, stride))
        wet_dm = water[cut]
        rows, cols = np.nonzero(self._wet_mask(cut, wet_dm))
        if rows.size == 0:
            return NearWater(radius_m=radius_m, stride=stride, covered_pct=covered)

        # Distance in the DECIMATED view's index space, then scaled back: the centre is
        # rarely on a sampled texel once stride > 1, so the offset has to be carried
        # through rather than assumed zero.
        centre_col = ((x_cm - self.x0_cm) / self.spacing_cm - col_lo) / stride
        centre_row = ((y_cm - self.y0_cm) / self.spacing_cm - row_lo) / stride
        d2 = (cols - centre_col) ** 2 + (rows - centre_row) ** 2
        best = int(np.argmin(d2))
        grades = self._water_quality_raster()
        return NearWater(
            radius_m=radius_m,
            stride=stride,
            covered_pct=covered,
            distance_m=round(float(np.sqrt(d2[best])) * self.spacing_cm * stride / 100.0, 1),
            level_m=round(float(wet_dm[rows[best], cols[best]]) / DM_PER_M, 1),
            quality=int(grades[cut][rows[best], cols[best]]) if grades is not None else WATER_DRY,
        )
