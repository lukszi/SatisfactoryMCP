"""The 1 m terrain field: the on-disk format, and the loader that is only a loader.

``tools/gen_world_heightmap.py`` cuts the field out of the reader's own installed game and
writes it to the gitignored ``data/local/heightmap/``; this module is the byte format both
sides agree on and a reader over it. **Absent is the normal case** -- the repository ships
no terrain, so ``load_field()`` returns ``None`` on any machine where nobody ran the
generator, and every caller carries on without one. A raster is ``zlib`` over raw bytes,
the two int16 ones row-delta first, and a texel read costs a full decode of its plane, so
each plane is decoded lazily and cached. What each plane means is stated at the constant
that names it; the georeference is in ``meta.json`` and is never assumed.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from ... import config

__all__ = [
    "DENSITY_NAME",
    "DIR_NAME",
    "HEIGHT_NAME",
    "META_NAME",
    "NODATA",
    "PROV_CLIFF",
    "PROV_CLIFF_DIRECT",
    "PROV_CLIFF_VALUES",
    "PROV_FILL",
    "PROV_LANDSCAPE",
    "PROV_NAME",
    "PROV_NAMES",
    "PROV_NODATA",
    "PROV_WATER_NAME",
    "WATER_DRY",
    "WATER_LEVEL_ONLY",
    "WATER_MEASURED",
    "WATER_NAME",
    "WATER_QUALITY_NAME",
    "WATER_QUALITY_NAMES",
    "Area",
    "Field",
    "NearWater",
    "Reading",
    "decode_i16",
    "decode_u8",
    "encode_i16",
    "encode_u8",
    "field_dir",
    "load_field",
]

#: The directory the generator writes and this module reads, under ``data/local/``.
DIR_NAME = "heightmap"

#: The planes over one grid, plus the sidecar carrying the georeference. Height and water
#: are int16 DECIMETRES of world Z; provenance, water quality and density are uint8 codes.
HEIGHT_NAME = "height.i16.z"
PROV_NAME = "prov.u8.z"
WATER_NAME = "water.i16.z"
WATER_QUALITY_NAME = "waterq.u8.z"
DENSITY_NAME = "density.u8.z"
META_NAME = "meta.json"

#: The int16 value that means "nothing is known here". Not zero: zero is sea level and a
#: real answer, so a no-data texel read as zero is a flat sea at the map's edge.
NODATA = -32768

#: Which layer answered a texel. The numbers are the file format and must not be
#: renumbered; 2 is unused. 5 splits the cliff province by HOW the texel was answered -- a
#: source vertex landed in it, against 4 where the rasteriser interpolated across a
#: triangle wider than the texel -- and is additive: a reader testing ``== PROV_CLIFF``
#: reads 5 as the whole of what 4 meant before. The split is announced by the presence of
#: ``density.u8.z`` and never inferred from a texel being 4.
PROV_NODATA = 0
PROV_LANDSCAPE = 1
PROV_FILL = 3
PROV_CLIFF = 4
PROV_CLIFF_DIRECT = 5

#: Both values that are the cliff layer. Anything asking "is this texel cliff" means this;
#: testing ``== PROV_CLIFF`` alone misses 5.
PROV_CLIFF_VALUES = (PROV_CLIFF, PROV_CLIFF_DIRECT)

PROV_NAMES = {
    PROV_NODATA: "no data",
    PROV_LANDSCAPE: "landscape",
    PROV_FILL: "fill",
    PROV_CLIFF: "cliff",
    PROV_CLIFF_DIRECT: "cliff, direct",
}

#: What the water channel is called when a reading has one. Not a provenance value: water
#: is a second surface over the same texel, not a different source for the ground.
PROV_WATER_NAME = "water"

#: ``waterq.u8.z``'s values, which are the file format: do not renumber. A water LEVEL
#: comes from a cooked water volume's own box and is good to centimetres wherever there is
#: water; a DEPTH is that level minus the ground, so it exists only where the ground was
#: measured at 1 m -- the landscape and cliff layers. Over the fill layer and over no-data
#: the bed is unknown, and ``WATER_LEVEL_ONLY`` says so rather than subtracting a number
#: it does not have.
WATER_DRY = 0
WATER_MEASURED = 1
WATER_LEVEL_ONLY = 2

WATER_QUALITY_NAMES = {
    WATER_DRY: "dry",
    WATER_MEASURED: "water, depth measured",
    WATER_LEVEL_ONLY: "water, depth unknown",
}

ZLIB_LEVEL = 6

DM_PER_M = 10.0

#: What a caller is told when the sidecar records no measured accuracy for a layer. Only
#: reached by a hand-written or truncated ``meta.json``; the generator always measures.
UNKNOWN_ACCURACY_M = None


# --------------------------------------------------------------------------------------
# The codec. Pure functions over arrays and bytes: one implementation, imported by both
# the generator and the loader, so the two cannot hold different opinions about the format.
# --------------------------------------------------------------------------------------


def encode_i16(grid: np.ndarray) -> bytes:
    """One int16 raster to bytes: row-delta, then zlib.

    ``prepend=0`` makes the first column its own absolute value, so a row decodes from its
    own bytes and a corrupt stream cannot shift the whole field by a constant. The
    subtraction is done in int32 and truncated back: two int16 values can differ by more
    than an int16 holds -- a cliff top beside a no-data texel does -- and the truncation is
    the two's-complement wrap ``decode_i16`` undoes, so the pair is exact for every input.
    """
    if grid.dtype != np.int16:
        raise TypeError(f"expected an int16 raster, got {grid.dtype}")
    delta = np.diff(grid.astype(np.int32), axis=1, prepend=0).astype(np.int16)
    return zlib.compress(delta.tobytes(), ZLIB_LEVEL)


def decode_i16(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_i16``, given the shape the sidecar records.

    The shape lives in ``meta.json`` beside the georeference and not in the stream, so a
    raster whose length disagrees with it is a mismatched pair rather than a raster to
    reshape into whatever fits.

    Summed in int16 on purpose: the wrap is the inverse of ``encode_i16``'s truncation, and a
    wider accumulator peaked at 1.2 GB per decode, enough to exhaust memory under ``-n auto``.
    """
    delta = np.frombuffer(zlib.decompress(blob), dtype="<i2")
    if delta.size != height * width:
        raise ValueError(
            f"height raster is {delta.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return np.cumsum(delta.reshape(height, width), axis=1, dtype=np.int16)


def encode_u8(grid: np.ndarray) -> bytes:
    """One uint8 raster to bytes: plain zlib, no delta."""
    if grid.dtype != np.uint8:
        raise TypeError(f"expected a uint8 raster, got {grid.dtype}")
    return zlib.compress(grid.tobytes(), ZLIB_LEVEL)


def decode_u8(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_u8``."""
    flat = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    if flat.size != height * width:
        raise ValueError(
            f"provenance raster is {flat.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return flat.reshape(height, width)


# --------------------------------------------------------------------------------------
# Reading the field.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One texel of the field, with the uncertainty that belongs to that texel.

    A reading is **not** an ``elevation.Sample``: a sample is a thing somebody's save says
    is at a height, a reading is the game's own cooked terrain looked up. They are kept
    apart all the way out to the page, because one median over both describes neither.
    """

    z_m: float
    provenance: int
    accuracy_m: float | None
    water_m: float | None = None
    water_quality: int = WATER_DRY

    @property
    def source(self) -> str:
        return PROV_NAMES.get(self.provenance, f"layer {self.provenance}")

    @property
    def submerged(self) -> bool:
        """Whether water stands over this ground. Never a terrain correction.

        The quality channel decides it, not a comparison here: over the fill layer the
        ground is a 3.9 m-quantised raster that routinely rounds *above* a sea surface 17 m
        down, so ``water_m > z_m`` reads the open ocean as dry. That comparison is the
        fallback only for a field written before the quality plane existed, where it is all
        there is.
        """
        if self.water_m is None:
            return False
        if self.water_quality != WATER_DRY:
            return True
        return self.water_m > self.z_m

    @property
    def depth_known(self) -> bool:
        """Whether the ground under the water was measured well enough to subtract."""
        return self.water_quality == WATER_MEASURED or (
            self.water_quality == WATER_DRY and self.submerged
        )

    @property
    def water_depth_m(self) -> float | None:
        """How deep the water is, or ``None`` where the bed is not known well enough.

        ``None`` rather than zero: a texel of open ocean has a real depth this field cannot
        state, and 0.0 would read as "the water is exactly at the ground".
        """
        if not self.submerged or not self.depth_known or self.water_m is None:
            return None
        return max(self.water_m - self.z_m, 0.0)


@dataclass(frozen=True)
class Area:
    """What a rectangle of the field is like, as separate measurements.

    Not a buildability score, and no term here may be combined into one. Flat is not the
    same as buildable -- stilts are ordinary play and a cliff is sometimes the point -- so
    every term stays a raw number in its own units and the caller decides what it is worth.

    Nothing here is a placement claim. An area states that water covers 40% of a pad and how
    far below the rim it stands; it cannot state how many extractors fit, because a water
    volume's shape is level geometry this field does not carry.

    ``texels`` counts what was actually read after ``stride``; ``requested_texels`` counts
    the full 1 m rectangle asked for. The percentages are over ``requested_texels``, so a
    pad half off the grid reads as half no-data rather than as a confident answer about
    its other half.
    """

    x0_cm: float
    y0_cm: float
    x1_cm: float
    y1_cm: float
    stride: int
    requested_texels: int
    texels: int
    nodata_pct: float
    z_min_m: float | None = None
    z_max_m: float | None = None
    z_mean_m: float | None = None
    z_median_m: float | None = None
    #: Mean and 90th-percentile gradient magnitude, in degrees from horizontal.
    slope_mean_deg: float | None = None
    slope_p90_deg: float | None = None
    #: RMS deviation from the least-squares plane through the pad, in metres. This is the
    #: term that separates "steep" from "lumpy": a clean 20 deg ramp has a large slope and
    #: near-zero roughness, and a boulder field has the reverse.
    roughness_m: float | None = None
    submerged_pct: float = 0.0
    #: Median surface level of whatever water stands in the rectangle, and how far that is
    #: below the dry ground's median. A negative drop means the water is ABOVE the ground
    #: median, which is what a pad that is mostly lake looks like. One rectangle, one water
    #: body: over a rim with the sea on one side and a hill lake on the other the median
    #: lands between two real surfaces and is neither of them.
    water_level_m: float | None = None
    water_below_ground_m: float | None = None
    #: Share of the rectangle each source layer answered, by ``PROV_NAMES`` key. The fill
    #: layer is a 3.9 m-quantised stand-in, so a pad that is mostly fill has numbers whose
    #: error bars swamp the roughness they report -- that is what this is for.
    provenance_pct: dict[int, float] = field(default_factory=dict)

    @property
    def z_range_m(self) -> float | None:
        if self.z_min_m is None or self.z_max_m is None:
            return None
        # Re-rounded: the difference of two 1-dp floats is not one, and a raw subtraction
        # prints 21.099999999999994 into an answer a player reads.
        return round(self.z_max_m - self.z_min_m, 1)

    @property
    def coarse_pct(self) -> float:
        """Share answered by a layer whose accuracy is worse than the roughness scale."""
        return self.provenance_pct.get(PROV_FILL, 0.0)


@dataclass(frozen=True)
class NearWater:
    """How far the closest standing water is from a point, and what level it stands at.

    A distance and a surface height, never a capacity: this says water is reachable, not
    that anything may be built on it. ``distance_m is None`` means none was found inside
    the searched box, which is only as strong a statement as ``covered_pct`` -- a box that
    ran off the grid searched less than it was asked to.
    """

    radius_m: float
    stride: int
    #: Share of the requested box that was on the grid at all.
    covered_pct: float
    distance_m: float | None = None
    level_m: float | None = None
    #: ``WATER_QUALITY_NAMES`` at the texel found; ``WATER_LEVEL_ONLY`` means the bed under
    #: it was never measured, so the level is sound and any depth read off it is not.
    quality: int = WATER_DRY


def _area_shape(z: np.ndarray, good: np.ndarray, spacing_m: float) -> dict[str, float | None]:
    """Slope and roughness over a window, kept apart because they answer different questions.

    Slope is the gradient magnitude per texel; roughness is the RMS residual from the
    least-squares plane through the whole window, so a uniform ramp -- steep, and perfectly
    buildable on stilts -- comes out rough 0. Reporting one number for both would hide
    exactly the distinction a player makes by eye.

    ``None`` for a window too small or too empty to have the shape: a single row has no
    gradient across it, and three points always fit a plane exactly.
    """
    out: dict[str, float | None] = {
        "slope_mean_deg": None,
        "slope_p90_deg": None,
        "roughness_m": None,
    }
    if z.shape[0] >= 2 and z.shape[1] >= 2:
        masked = np.where(good, z, np.float32(np.nan))
        dy, dx = np.gradient(masked, spacing_m)
        mag = np.hypot(dx, dy)
        finite = np.isfinite(mag)
        if finite.any():
            deg = np.degrees(np.arctan(mag[finite]))
            out["slope_mean_deg"] = round(float(deg.mean()), 1)
            out["slope_p90_deg"] = round(float(np.percentile(deg, 90)), 1)

    rows, cols = np.nonzero(good)
    if rows.size >= 4:
        # Coordinates centred before the normal equations: uncentred, the 3x3 is dominated
        # by the constant term at map-scale offsets and solves badly.
        xs = cols.astype(np.float64) * spacing_m
        ys = rows.astype(np.float64) * spacing_m
        xs -= xs.mean()
        ys -= ys.mean()
        zs = z[good].astype(np.float64)
        design = np.column_stack([np.ones_like(xs), xs, ys])
        try:
            coefficients = np.linalg.solve(design.T @ design, design.T @ zs)
            residual = zs - design @ coefficients
        except np.linalg.LinAlgError:
            residual = zs - zs.mean()
        out["roughness_m"] = round(float(np.sqrt(float((residual**2).mean()))), 2)
    return out


class Field:
    """A loaded heightmap: its rasters, a georeference, and the accuracy it measured.

    Constructed by ``load_field``. Height and provenance are decoded when the object is
    built, since there is no answer without both; the water and density planes only when
    something asks, because a decoded plane is tens of MB resident.
    """

    def __init__(self, meta: dict[str, Any], directory: Path) -> None:
        self.meta = meta
        self.directory = directory
        grid = meta["grid"]
        self.width = int(grid["width"])
        self.height = int(grid["height"])
        self.x0_cm = float(grid["x0_cm"])
        self.y0_cm = float(grid["y0_cm"])
        self.spacing_cm = float(grid["spacing_cm"])
        self._height_dm = decode_i16(
            (directory / HEIGHT_NAME).read_bytes(), self.height, self.width
        )
        self._prov = decode_u8((directory / PROV_NAME).read_bytes(), self.height, self.width)
        self._water_dm: np.ndarray | None = None
        self._water_tried = False
        self._water_quality: np.ndarray | None = None
        self._water_quality_tried = False
        self._density: np.ndarray | None = None
        self._density_tried = False
        self._accuracy = {
            int(key): value.get("accuracy_m")
            for key, value in (meta.get("provenance") or {}).items()
            if str(key).lstrip("-").isdigit()
        }

    # -- geometry ----------------------------------------------------------------------

    @property
    def build(self) -> str | None:
        """The game build this field was cut from, for the staleness the project announces."""
        source = (self.meta.get("sources") or {}).get("game") or {}
        pinned = source.get("game_version_pinned")
        return pinned if isinstance(pinned, str) else None

    def texel(self, x_cm: float, y_cm: float) -> tuple[int, int] | None:
        """``(row, col)`` for a world coordinate, or ``None`` if it is off the grid.

        Rounded, never floored: the grid is **vertex-aligned**, so a texel's height belongs
        to the point ``x0 + col*spacing`` exactly. Flooring would answer with the vertex up
        to a metre south-west of the question, which on a cliff edge is a different cliff.
        """
        col = round((x_cm - self.x0_cm) / self.spacing_cm)
        row = round((y_cm - self.y0_cm) / self.spacing_cm)
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        return row, col

    def _water_raster(self) -> np.ndarray | None:
        if not self._water_tried:
            self._water_tried = True
            path = self.directory / WATER_NAME
            if path.is_file():
                self._water_dm = decode_i16(path.read_bytes(), self.height, self.width)
        return self._water_dm

    def _water_quality_raster(self) -> np.ndarray | None:
        """``waterq.u8.z``, or ``None`` for a field written before it existed."""
        if not self._water_quality_tried:
            self._water_quality_tried = True
            path = self.directory / WATER_QUALITY_NAME
            if path.is_file():
                self._water_quality = decode_u8(path.read_bytes(), self.height, self.width)
        return self._water_quality

    def density_raster(self) -> np.ndarray | None:
        """``density.u8.z``, or ``None`` for a field written before it existed.

        How many source vertices landed in each texel, clamped at 255 and zero wherever the
        cliff layer did not answer -- the landscape and the fill are lattices, so "samples
        per texel" is not a question either has. It is the only plane that can tell a
        renderer asking for a sub-metre pixel whether it is reading a measurement or an
        interpolant. ``None`` is not zero: a field predating the plane knows nothing about
        its own density, and that must not be read as "no samples anywhere".
        """
        if not self._density_tried:
            self._density_tried = True
            path = self.directory / DENSITY_NAME
            if path.is_file():
                self._density = decode_u8(path.read_bytes(), self.height, self.width)
        return self._density

    def at(self, x_cm: float, y_cm: float) -> Reading | None:
        """The terrain at one world coordinate, or ``None`` where the field knows nothing.

        ``None`` covers both silences -- off the grid, and a no-data texel inside it --
        because they are one answer to the caller: nothing is known about that spot.
        """
        where = self.texel(x_cm, y_cm)
        if where is None:
            return None
        row, col = where
        raw = int(self._height_dm[row, col])
        if raw == NODATA:
            return None
        provenance = int(self._prov[row, col])
        water = self._water_raster()
        water_m = None
        quality = WATER_DRY
        if water is not None:
            wet = int(water[row, col])
            if wet != NODATA:
                water_m = wet / DM_PER_M
                grades = self._water_quality_raster()
                if grades is not None:
                    quality = int(grades[row, col])
        return Reading(
            z_m=raw / DM_PER_M,
            provenance=provenance,
            accuracy_m=self._accuracy.get(provenance, UNKNOWN_ACCURACY_M),
            water_m=water_m,
            water_quality=quality,
        )

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
        """
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
        )
        if row_lo >= row_hi or col_lo >= col_hi:
            return empty

        stride = 1
        inside = (row_hi - row_lo) * (col_hi - col_lo)
        if max_texels > 0 and inside > max_texels:
            stride = int(np.ceil(np.sqrt(inside / max_texels)))
        cut = (slice(row_lo, row_hi, stride), slice(col_lo, col_hi, stride))

        raw = self._height_dm[cut]
        good = raw != NODATA
        n_good = int(good.sum())
        # The percentages are over the REQUESTED rectangle, so a pad hanging off the grid
        # edge reports the part nobody measured instead of a confident answer about the
        # rest. Scaled by stride^2 because a decimated view stands for the whole area.
        seen = float(raw.size) * stride * stride
        outside = max(requested - seen, 0.0)
        denom = float(requested) if requested else 1.0
        nodata_pct = 100.0 * ((raw.size - n_good) * stride * stride + outside) / denom
        if n_good == 0:
            return replace(empty, stride=stride)

        z = np.where(good, raw, 0).astype(np.float32) / np.float32(DM_PER_M)
        z_valid = z[good]

        prov = self._prov[cut]
        counts = np.bincount(prov.ravel(), minlength=PROV_CLIFF_DIRECT + 1)
        provenance_pct = {
            int(code): round(100.0 * float(counts[code]) * stride * stride / denom, 1)
            for code in range(len(counts))
            if counts[code]
        }

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
            **_area_shape(z, good, self.spacing_cm * stride / 100.0),
            submerged_pct=round(submerged, 1),
            water_level_m=water_level,
            water_below_ground_m=water_drop,
            provenance_pct=provenance_pct,
        )

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


#: Loaded fields, keyed by the directory and its sidecar's mtime, so a regenerated field is
#: picked up without a restart while a repeated question costs a dictionary lookup.
_CACHE: dict[tuple[str, int], Field] = {}


def field_dir(local_dir: Path | None = None) -> Path:
    """Where the field lives. Resolved at call time so a test can point it elsewhere."""
    if local_dir is not None:
        return Path(local_dir)
    return config.data_dir() / "local" / DIR_NAME


def load_field(local_dir: Path | None = None) -> Field | None:
    """The terrain field, or ``None`` if this machine has none.

    Every failure mode -- no directory, no sidecar, a sidecar that will not parse, a raster
    whose length disagrees with it -- returns ``None`` rather than raising: the caller asked
    "is there terrain here", and "no" is a complete answer. A broken field is diagnosed by
    the generator, not here.
    """
    directory = field_dir(local_dir)
    meta_path = directory / META_NAME
    try:
        stamp = meta_path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(directory), stamp)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return None
        field = Field(meta, directory)
    except (OSError, ValueError, TypeError, KeyError, zlib.error):
        return None
    _CACHE.clear()
    _CACHE[key] = field
    return field
