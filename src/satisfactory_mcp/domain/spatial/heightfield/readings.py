"""What a lookup in the field answers with: a point reading, its surfaces, an area, near water."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .. import caves as cave_masks
from .. import rocks as rock_pack
from .planes import PROV_FILL, PROV_NAMES, WATER_DRY, WATER_MEASURED

__all__ = ["AMBIGUOUS_M", "SURFACES", "Area", "NearWater", "Reading", "Surface", "Surfaces"]

#: Which surface a lookup reads. ``ground`` is the fused field and the default.
Surface = Literal["ground", "terrain", "top"]
SURFACES: tuple[str, ...] = ("ground", "terrain", "top")

#: Ground above the bare landscape by more than this is a rock or overhang, so the answer
#: may be a roof. Also the slack a hint gets: a thing rests on a surface at or below it.
AMBIGUOUS_M = 2.0


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
    #: Which of ``SURFACES`` answered.
    surface: str = "ground"
    #: The bare landscape under the point, where the field carries that plane.
    terrain_z_m: float | None = None
    #: The answer may be a rock top or roof rather than the floor beneath it. Without a
    #: terrain plane this is every cliff texel, since nothing else can tell them apart.
    ambiguous: bool = False
    #: ``caves.CAVE_VALUES``. Separate from ``ambiguous``: under ``inside`` ``z_m`` is the
    #: surface above the point and must not be presented as its height.
    cave: str = cave_masks.NONE
    #: Under ``inside``: ``z_m`` is a collision floor just under the hint, not the surface above.
    cave_floor: bool = False

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
    def height_known(self) -> bool:
        """Whether ``z_m`` is this point's height: false only in a cave with no floor found."""
        return self.cave != cave_masks.INSIDE or self.cave_floor

    @property
    def cave_note(self) -> str | None:
        """The one cave line for this reading, or ``None`` where no cave is known."""
        if self.cave_floor:
            return rock_pack.floor_note(self.z_m)
        return cave_masks.note(self.cave, self.z_m)

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
class Surfaces:
    """Every surface the field holds at one point, in metres; ``None`` where a plane is absent."""

    ground_m: float | None
    terrain_m: float | None
    top_m: float | None
    provenance: int
    #: Further collision surfaces under ``top``, highest first: shelves, overhangs, cave floors.
    floors: tuple[float, ...] = ()

    def candidates(self) -> list[tuple[str, float]]:
        return [
            (name, value)
            for name, value in (
                ("ground", self.ground_m),
                ("terrain", self.terrain_m),
                ("top", self.top_m),
                *(("floor", z) for z in self.floors),
            )
            if value is not None
        ]

    def pick(self, hint_m: float) -> tuple[str, float] | None:
        """The candidate nearest ``hint_m``, preferring one at or below hint + ``AMBIGUOUS_M``."""
        found = self.candidates()
        below = [c for c in found if c[1] <= hint_m + AMBIGUOUS_M]
        pool = below or found
        return min(pool, key=lambda c: abs(c[1] - hint_m)) if pool else None


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
    surface: str = "ground"
    #: Share of the rectangle where ground stands over the bare landscape by more than
    #: ``AMBIGUOUS_M``; without a terrain plane, the cliff share.
    ambiguous_pct: float = 0.0
    #: Share of the rectangle with a cave under it, from the cave mask.
    cave_pct: float = 0.0

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


def area_shape(z: np.ndarray, good: np.ndarray, spacing_m: float) -> dict[str, float | None]:
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
