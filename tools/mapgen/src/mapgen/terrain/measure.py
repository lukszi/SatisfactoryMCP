"""The seam between the two regimes, measured along a line, and what each regime drew."""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from satisfactory_mcp.core.arrays import BoolMask, F32Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "SEAM_MID",
    "SEAM_NEAR_TEXELS",
    "SEAM_PURE",
    "SEAM_SAME_SURFACE_M",
    "SEAM_SAMPLE_MAX_PER_BAND",
    "SEAM_SWITCH_CEILING",
    "RegimeCounts",
    "RegimeCoverage",
    "SeamMeasure",
    "SeamTrace",
    "measured_lines",
]

#: The hard switch ``SeamTrace`` reads against takes the direct answer at this weight and up.
SEAM_MID = 0.5
#: A direct weight within this of 0 or 1 is one regime alone; between them, the blend.
SEAM_PURE = 0.02
#: What a hard switch reads: an identity, not a bound.
SEAM_SWITCH_CEILING = 1.0
#: Two surfaces this close are the same ground, for the "surfaces agree" pools.
SEAM_SAME_SURFACE_M = 0.5
#: How far from a crossing the pool is gathered, in output texels (7.3 m at z7).
SEAM_NEAR_TEXELS = 32
#: How many texels of each pool one band contributes. A systematic sample rather than the
#: whole pool, whose percentile moves in the fourth decimal over tens of millions of texels.
SEAM_SAMPLE_MAX_PER_BAND = 200_000

#: One band's ``SeamTrace.measure``: its rows, and each pool's thinned values.
SeamMeasure: TypeAlias = tuple[int, list[tuple[str, F32Grid]]]
#: One band's ``RegimeCoverage.measure``: per province, its four regime counts and weight.
RegimeCounts: TypeAlias = list[tuple[int, list[int], float]]


class SeamTrace:
    """Second differences of the drawn height along every row, pooled by the regime weights.

    A trace rather than probes, which miss a ridge one texel wide along a density contour.
    Each 3-texel stencil is pooled by the weights under all three of its texels. Read it
    against the hard switch, not the pure regimes: the join is the rock's own silhouette, and
    ``blend_regimes``' arithmetic, not this statistic, is what keeps it smooth (the sidecar's
    ``reading``; docs/map/renders.md section 20).
    """

    def __init__(self) -> None:
        self.pools: dict[str, list[F32Grid]] = {
            "seam": [],
            "switch": [],
            "pure_direct": [],
            "pure_kernel": [],
            "seam_same_surface": [],
            "pure_same_surface": [],
        }
        self.rows = 0

    @staticmethod
    def _thin(values: NDArray[np.floating]) -> F32Grid:
        """A systematic sample of a pool, so the whole sheet costs a bounded number of MB.

        Every k-th value of a selection already in raster order, which for a percentile is a
        sample rather than a filter.
        """
        stride = max(1, values.size // SEAM_SAMPLE_MAX_PER_BAND)
        return values[::stride].astype(np.float32)

    def add(
        self,
        z_m: F32Grid,
        z_switched: F32Grid,
        w: F32Grid,
        spacing_m: float,
        delta: F32Grid | None = None,
    ) -> None:
        self.merge(self.measure(z_m, z_switched, w, spacing_m, delta))

    def merge(self, measured: SeamMeasure) -> None:
        """Pool one band's ``measure``; bands merged in sheet order pool what ``add`` would."""
        rows, kept = measured
        self.rows += rows
        for name, values in kept:
            self.pools[name].append(values)

    def measure(
        self,
        z_m: F32Grid,
        z_switched: F32Grid,
        w: F32Grid,
        spacing_m: float,
        delta: F32Grid | None = None,
    ) -> SeamMeasure:
        """One band's rows and thinned pools, touching nothing shared: safe on any thread."""
        kept: list[tuple[str, F32Grid]] = []

        def keep(name: str, curvature: NDArray[np.floating], mask: BoolMask) -> None:
            if mask.any():
                kept.append((name, self._thin(curvature[mask])))

        blended = np.abs(np.diff(z_m, n=2, axis=1)) / (spacing_m * spacing_m)
        switched = np.abs(np.diff(z_switched, n=2, axis=1)) / (spacing_m * spacing_m)
        # A second difference reads three texels, so it belongs to the regime all three of
        # them are in, and to the join if they are not all in one. Classify by the middle
        # weight alone and a sharp join hides: its curvature lands one texel to the side, in
        # a stencil whose middle texel is still pure.
        low, middle, high = w[:, :-2], w[:, 1:-1], w[:, 2:]
        top = np.maximum(np.maximum(low, middle), high)
        bottom = np.minimum(np.minimum(low, middle), high)
        at_seam = (top > SEAM_PURE) & (bottom < 1.0 - SEAM_PURE)
        if not at_seam.any():
            return z_m.shape[0], kept
        near = ndimage.maximum_filter1d(at_seam, 2 * SEAM_NEAR_TEXELS + 1, axis=1, mode="nearest")
        keep("seam", blended, at_seam)
        keep("switch", switched, at_seam)
        keep("pure_direct", blended, near & (bottom >= 1.0 - SEAM_PURE))
        keep("pure_kernel", blended, near & (top <= SEAM_PURE))
        if delta is None:
            return z_m.shape[0], kept
        gap = np.abs(delta)
        same = np.minimum(np.minimum(gap[:, :-2], gap[:, 1:-1]), gap[:, 2:]) <= SEAM_SAME_SURFACE_M
        keep("seam_same_surface", blended, at_seam & same)
        keep("pure_same_surface", blended, near & same & ~at_seam)
        return z_m.shape[0], kept

    def result(self) -> JsonObject:
        pooled = {
            name: (np.concatenate(values) if values else np.zeros(0, np.float32))
            for name, values in self.pools.items()
        }
        p99 = {
            name: float(np.percentile(values, 99)) if values.size else None
            for name, values in pooled.items()
        }
        seam, switch = p99["seam"], p99["switch"]
        if seam is None or not switch:
            return {
                "measured": False,
                "why": (
                    "no 3-texel stencil straddled the join, which is what a render with no "
                    "rocks in it looks like"
                ),
            }

        def ratio(over: float | None) -> float | None:
            return None if not over else round(seam / over, 4)

        beside = max(
            [v for v in (p99["pure_direct"], p99["pure_kernel"]) if v is not None], default=None
        )
        return {
            "measured": True,
            "method": (
                "|d2z/dx2| along every row of the drawn height, in 1/m. Every 3-texel "
                "stencil is sorted by the weights under ALL THREE of its texels: straddling "
                f"the join, wholly direct (every weight within {SEAM_PURE} of 1) or wholly "
                f"kernel (every weight within {SEAM_PURE} of 0). The two pure pools are "
                f"further restricted to within {SEAM_NEAR_TEXELS} texels of a straddling "
                "stencil. A fourth pool is the height a HARD MAX would have drawn over the "
                "straddling stencils themselves."
            ),
            "texels": {name: int(values.size) for name, values in pooled.items()},
            "p99_curvature": {
                name: (None if value is None else round(value, 5)) for name, value in p99.items()
            },
            "share_of_a_hard_switch": ratio(switch),
            "share_of_a_hard_switch_ceiling": SEAM_SWITCH_CEILING,
            "against_the_pure_regimes": ratio(beside),
            "against_the_terrain_where_the_surfaces_agree": ratio(p99["pure_same_surface"]),
            "surfaces_agree_within_m": SEAM_SAME_SURFACE_M,
            "reading": (
                "share_of_a_hard_switch is the number to read and it is a DESCRIPTION, not a "
                "gate: a convex blend of two surfaces cannot be rougher than the switch "
                "between them, because rounding the weight to 0 or 1 is the extreme point of "
                "that blend, so this can never exceed 1 and never fail. What it says is how "
                "much of that ceiling the fade spends. The two numbers beside it are the "
                "design's own reference and a repair of it, and both measure the TERRAIN "
                "rather than the sampler once the composition is by coverage, because then "
                "every join lies on a geometric feature -- the rock's silhouette, or its "
                "base. They are recorded because they were tried. What guarantees the "
                "smoothness is the arithmetic: a convex combination in a continuously "
                "reconstructed coverage, plus a positive part smoothed to C-infinity."
            ),
        }


class RegimeCoverage:
    """How much of the sheet each regime drew, per province of the field underneath it.

    Provinces are sampled nearest: a province is a name, and two names do not average. The
    direct bucket is split by the density plane into measurements and facets, which is all
    that plane decides (docs/map/renders.md section 20).
    """

    NAMES = ("direct_measured", "direct_facet", "faded", "kernel")

    def __init__(self) -> None:
        self.counts: dict[int, list[int]] = {}
        self.weight: dict[int, float] = {}

    def add(self, prov: NDArray[np.integer], w: F32Grid, measured: BoolMask) -> None:
        self.merge(self.measure(prov, w, measured))

    def merge(self, measured: RegimeCounts) -> None:
        """Add one band's ``measure``; bands merged in sheet order sum what ``add`` would."""
        for key, counts, weight in measured:
            row = self.counts.setdefault(key, [0, 0, 0, 0])
            for index in range(4):
                row[index] += counts[index]
            self.weight[key] = self.weight.get(key, 0.0) + weight

    @staticmethod
    def measure(prov: NDArray[np.integer], w: F32Grid, measured: BoolMask) -> RegimeCounts:
        """One band's counts and weight per province, touching nothing shared."""
        regime = np.where(
            w >= 1.0 - SEAM_PURE,
            np.where(measured, 0, 1),
            np.where(w > SEAM_PURE, 2, 3),
        )
        out: RegimeCounts = []
        for value in np.unique(prov):
            here = prov == value
            picked = regime[here]
            counts = [int(np.count_nonzero(picked == index)) for index in range(4)]
            out.append((int(value), counts, float(w[here].sum())))
        return out

    def result(self) -> JsonObject:
        total = sum(sum(row) for row in self.counts.values()) or 1
        provinces: JsonObject = {}
        for value, row in sorted(self.counts.items()):
            name = hf.PROV_NAMES.get(value, f"layer {value}")
            here = sum(row) or 1
            provinces[name] = {
                **{key: round(100 * row[i] / total, 4) for i, key in enumerate(self.NAMES)},
                "province_pct_of_sheet": round(100 * here / total, 4),
                "mean_w": round(self.weight.get(value, 0.0) / here, 5),
            }
        pooled = [sum(row[i] for row in self.counts.values()) for i in range(4)]
        return {
            "definition": (
                f"direct: coverage >= {1 - SEAM_PURE}, split by density.u8.z into the texels "
                "a source vertex landed in (a measurement) and the texels the rasteriser "
                "reached across a triangle wider than the output texel (a facet); faded: "
                f"{SEAM_PURE} < coverage < {1 - SEAM_PURE}, a silhouette; kernel: coverage "
                f"<= {SEAM_PURE}, the landscape and fill lattices alone. mean_w beside them "
                "is the unbucketed answer: how much of the height over that province the "
                "rasterised rocks contributed, averaged."
            ),
            "per_province_pct_of_sheet": provinces,
            "sheet_pct": {
                **{key: round(100 * pooled[i] / total, 4) for i, key in enumerate(self.NAMES)},
                "mean_w": round(sum(self.weight.values()) / total, 5),
            },
        }


def measured_lines(trace: JsonObject, regimes: JsonObject) -> list[str]:
    """The run's report of a ``SeamTrace`` and a ``RegimeCoverage`` result."""
    lines: list[str] = []
    curvature = trace.get("p99_curvature")
    if trace.get("measured") and isinstance(curvature, dict):
        lines.append(
            f"  seam trace: p99 |d2z/dx2| {curvature['seam']} over the "
            f"blend against {curvature['switch']} for the hard max on "
            f"the same texels -- the fade spends "
            f"{trace['share_of_a_hard_switch']} of that ceiling; against the terrain "
            f"beside the join it reads {trace['against_the_pure_regimes']}, which is "
            "the design's own reference and is measuring the silhouette"
        )
    return [*lines, f"  regimes: {regimes['sheet_pct']}"]
