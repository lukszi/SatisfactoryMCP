"""The seam between the two regimes, measured along a line, and what each regime drew."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "SEAM_MID",
    "SEAM_NEAR_TEXELS",
    "SEAM_PURE",
    "SEAM_RATIO_MAX",
    "SEAM_SAME_SURFACE_M",
    "SEAM_SAMPLE_MAX_PER_BAND",
    "SEAM_SWITCH_CEILING",
    "RegimeCoverage",
    "SeamTrace",
]

#: The p99 second difference at the seam over the pure regimes, as a ratio; "at the seam"
#: is the whole blend (tools/mapgen/README.md, "Design notes").
SEAM_MID = 0.5
SEAM_PURE = 0.02
SEAM_RATIO_MAX = 1.5

#: What a hard switch reads: an identity, not a bound.
SEAM_SWITCH_CEILING = 1.0

SEAM_SAME_SURFACE_M = 0.5

#: How far from a crossing the pool is gathered, in output texels (7.3 m at z7).
SEAM_NEAR_TEXELS = 32

#: How many texels of each pool one band contributes. A systematic sample rather than the
#: whole pool, whose percentile moves in the fourth decimal over tens of millions of texels.
SEAM_SAMPLE_MAX_PER_BAND = 200_000


# --------------------------------------------------------------------------------------
# The seam, measured along a line rather than at a probe.
# --------------------------------------------------------------------------------------


class SeamTrace:
    """Second differences of the drawn height at the join, and what they can be read against.

    A trace rather than probes: probes are sparse relative to a seam, and a ridge one texel
    wide along a density contour is invisible to all of them. What is measured is
    ``|d2z/dx2|`` along every row of every band over the whole square, with each 3-texel
    stencil sorted by the weights under all three of its texels.

    **Read the ratios against the switch, not against the pure regimes.** This file
    composites a rock onto a lattice by the rock's own coverage, so the join IS the rock's
    silhouette -- a real cliff edge, where enormous curvature is the correct answer, and the
    pure-regime ratio reads 138 on the shipped render with every bit of that terrain.
    Restricting the comparison to where the two surfaces agree only moves the join to the
    rock's base, which is also real. The hard ``max`` over the same texels is the one
    reference that is not the terrain, and it is not a gate either: a convex blend cannot be
    rougher than the switch between its own extreme points, so the number says how much of
    that ceiling the fade spends (0.5 on the shipped render).

    The smoothness itself is guaranteed by the arithmetic rather than by this statistic:
    ``blend_regimes`` is a convex combination in the pixel's coverage, plus a positive part
    smoothed by ``DIRECT_LIFT_KNEE_M``.
    """

    def __init__(self) -> None:
        self.pools: dict[str, list[np.ndarray]] = {
            "seam": [],
            "switch": [],
            "pure_direct": [],
            "pure_kernel": [],
            "seam_same_surface": [],
            "pure_same_surface": [],
        }
        self.rows = 0

    @staticmethod
    def _thin(values: np.ndarray) -> np.ndarray:
        """A systematic sample of a pool, so the whole sheet costs a bounded number of MB.

        Every k-th value of a selection already in raster order, which for a percentile is a
        sample rather than a filter.
        """
        stride = max(1, values.size // SEAM_SAMPLE_MAX_PER_BAND)
        return values[::stride].astype(np.float32)

    def _keep(self, name: str, curvature: np.ndarray, mask: np.ndarray) -> None:
        if mask.any():
            self.pools[name].append(self._thin(curvature[mask]))

    def add(self, z_m, z_switched, w, spacing_m: float, delta=None) -> None:
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
        self.rows += z_m.shape[0]
        if not at_seam.any():
            return
        near = ndimage.maximum_filter1d(at_seam, 2 * SEAM_NEAR_TEXELS + 1, axis=1, mode="nearest")
        self._keep("seam", blended, at_seam)
        self._keep("switch", switched, at_seam)
        self._keep("pure_direct", blended, near & (bottom >= 1.0 - SEAM_PURE))
        self._keep("pure_kernel", blended, near & (top <= SEAM_PURE))
        if delta is None:
            return
        gap = np.abs(delta)
        same = np.minimum(np.minimum(gap[:, :-2], gap[:, 1:-1]), gap[:, 2:]) <= SEAM_SAME_SURFACE_M
        self._keep("seam_same_surface", blended, at_seam & same)
        self._keep("pure_same_surface", blended, near & same & ~at_seam)

    def result(self) -> dict:
        pooled = {
            name: (np.concatenate(values) if values else np.zeros(0, np.float32))
            for name, values in self.pools.items()
        }
        p99 = {
            name: float(np.percentile(values, 99)) if values.size else None
            for name, values in pooled.items()
        }
        if p99["seam"] is None or not p99["switch"]:
            return {
                "measured": False,
                "why": (
                    "no 3-texel stencil straddled the join, which is what a render with no "
                    "rocks in it looks like"
                ),
            }

        def ratio(over: str) -> float | None:
            return None if not p99[over] else round(p99["seam"] / p99[over], 4)

        reference = [p99["pure_direct"], p99["pure_kernel"]]
        beside = max([v for v in reference if v is not None], default=None)
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
            "share_of_a_hard_switch": ratio("switch"),
            "share_of_a_hard_switch_ceiling": SEAM_SWITCH_CEILING,
            "against_the_pure_regimes": (None if not beside else round(p99["seam"] / beside, 4)),
            "against_the_terrain_where_the_surfaces_agree": ratio("pure_same_surface"),
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

    Counted rather than argued, because "the geometry answers this pixel" is a claim about
    how much of a picture. The provinces are the field's own, sampled nearest at output
    resolution: a province is a name and the average of two names is not one.

    The direct bucket is **split by the density plane**, which is all that plane does here.
    It does not decide whether the triangles are drawn -- the rock's own coverage of the
    pixel decides that -- it decides what the drawn answer IS: a texel a source vertex landed
    in is a measurement, and one the rasteriser reached across a triangle wider than itself
    is a facet. The sidecar says which is which rather than letting a reader assume.
    """

    def __init__(self) -> None:
        self.counts: dict[int, list[int]] = {}
        self.weight: dict[int, float] = {}

    def add(self, prov: np.ndarray, w: np.ndarray, measured: np.ndarray) -> None:
        regime = np.where(
            w >= 1.0 - SEAM_PURE,
            np.where(measured, 0, 1),
            np.where(w > SEAM_PURE, 2, 3),
        )
        for value in np.unique(prov):
            key = int(value)
            row = self.counts.setdefault(key, [0, 0, 0, 0])
            here = prov == value
            picked = regime[here]
            for index in range(4):
                row[index] += int(np.count_nonzero(picked == index))
            self.weight[key] = self.weight.get(key, 0.0) + float(w[here].sum())

    NAMES = ("direct_measured", "direct_facet", "faded", "kernel")

    def result(self) -> dict:
        total = sum(sum(row) for row in self.counts.values()) or 1
        out = {
            "definition": (
                f"direct: coverage >= {1 - SEAM_PURE}, split by density.u8.z into the texels "
                "a source vertex landed in (a measurement) and the texels the rasteriser "
                "reached across a triangle wider than the output texel (a facet); faded: "
                f"{SEAM_PURE} < coverage < {1 - SEAM_PURE}, a silhouette; kernel: coverage "
                f"<= {SEAM_PURE}, the landscape and fill lattices alone. mean_w beside them "
                "is the unbucketed answer: how much of the height over that province the "
                "rasterised rocks contributed, averaged."
            ),
            "per_province_pct_of_sheet": {},
        }
        for value, row in sorted(self.counts.items()):
            name = hf.PROV_NAMES.get(value, f"layer {value}")
            here = sum(row) or 1
            out["per_province_pct_of_sheet"][name] = {
                **{key: round(100 * row[i] / total, 4) for i, key in enumerate(self.NAMES)},
                "province_pct_of_sheet": round(100 * here / total, 4),
                "mean_w": round(self.weight.get(value, 0.0) / here, 5),
            }
        pooled = [sum(row[i] for row in self.counts.values()) for i in range(4)]
        out["sheet_pct"] = {
            **{key: round(100 * pooled[i] / total, 4) for i, key in enumerate(self.NAMES)},
            "mean_w": round(sum(self.weight.values()) / total, 5),
        }
        return out
