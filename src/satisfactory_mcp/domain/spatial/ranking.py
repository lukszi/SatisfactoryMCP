"""Score candidate build sites.

The score is a weighted sum of min-max normalised terms, and **every raw component is
returned alongside it** so a caller can re-weight for its own priorities rather than
trusting one opaque number.

Throughput counts **untapped, reachable** capacity only. Total capacity is the wrong
measure for "where should I build": a field whose nodes all have miners on them offers
nothing, and a field of well satellites offers nothing until the Pressurizer is
unlocked.

Terrain, where the reader has a heightfield, is read the same way: as raw numbers with a
small weight, never as a filter. Buildable is NOT flat -- foundations on stilts are
ordinary play and a cliff is sometimes the reason to build there -- so nothing here may
reject a site for being steep, wet or lumpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import TypedDict

from ...core.gamedata.constants import PURITY_MULT
from . import geo
from .heightfield import Area, Field
from .nodes.extraction import AnnotatedNode, untapped_rate

__all__ = ["SITE_PAD_M", "WEIGHTS", "PadTerms", "SiteRaw", "SiteScore", "rank_sites"]

#: Default weights. Throughput dominates; spread and distance are real but secondary
#: costs; purity is a tiebreak because a pure node needs fewer machines for the same
#: output.
#:
#: ``roughness`` is the smallest term and must stay the smallest: it may separate otherwise
#: comparable fields and must never outrank a throughput difference. Terrain is levelled with
#: foundations, so no terrain term here is ever a filter.
WEIGHTS = {
    "throughput": 1.00,
    "spread": -0.35,
    "distance": -0.25,
    "purity": 0.20,
    "roughness": -0.10,
}

#: The square of ground the terrain terms describe, centred on the cluster centroid. A
#: pad, not the node field: spread already reports how far apart the nodes are, and what
#: this asks is what the ground is like where the smelters would stand.
SITE_PAD_M = 200.0


class PadTerms(TypedDict):
    """The ground under a site's pad, as raw measurements; all null with no terrain field."""

    pad_roughness_m: float | None
    pad_slope_deg: float | None
    pad_z_range_m: float | None
    pad_submerged_pct: float | None
    pad_nodata_pct: float | None


class SiteRaw(PadTerms):
    """Every raw component of a site's score, before normalising."""

    untapped_rate: float
    total_rate: float
    nodes: int
    spread_m: float
    distance_to_infra_m: float | None
    purity_quality: float
    altitude_vs_consumer_m: float | None


@dataclass
class SiteScore:
    cluster: geo.Cluster[AnnotatedNode]
    score: float
    raw: SiteRaw
    normalised: dict[str, float] = field(default_factory=dict[str, float])

    @property
    def centroid(self) -> tuple[float, float, float]:
        return self.cluster.centroid


def _normalise(values: list[float]) -> list[float]:
    """Min-max to 0..1. A degenerate spread maps everything to 0.5, which keeps the
    term neutral instead of arbitrarily favouring one candidate."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _purity_quality(cluster: geo.Cluster[AnnotatedNode]) -> float:
    """Mean purity multiplier scaled to 0..1 (impure 0.25, normal 0.5, pure 1.0)."""
    members = cluster.members
    if not members:
        return 0.0
    total = sum(PURITY_MULT.get(m.get("purity", "normal"), 1.0) for m in members)
    return (total / len(members)) / 2.0


def _distance_to_infra_m(
    cluster: geo.Cluster[AnnotatedNode], infra: list[tuple[float, float]]
) -> float | None:
    if not infra:
        return None
    cx, cy, _ = cluster.centroid
    return min(geo.distance_m((cx, cy), p) for p in infra)


def _altitude_delta_m(
    cluster: geo.Cluster[AnnotatedNode], consumer_z: float | None
) -> float | None:
    """Height of the field above the consumer, in metres.

    The sign matters and is easy to get backwards: a field 268 m ABOVE the refineries
    feeds them downhill and needs no pipeline pumps, so a positive delta is an
    advantage for fluids, not a cost.
    """
    if consumer_z is None:
        return None
    _, _, cz = cluster.centroid
    return (cz - consumer_z) / 100.0


def _pad(cluster: geo.Cluster[AnnotatedNode], terrain: Field | None) -> Area | None:
    """The terrain over ``SITE_PAD_M`` of ground at the centroid, or ``None`` with no field.

    ``None`` is the normal case: the heightfield is cut from the reader's own game install
    and most machines have none.
    """
    if terrain is None:
        return None
    cx, cy, _ = cluster.centroid
    half = SITE_PAD_M * 100 / 2
    return terrain.area(cx - half, cy - half, cx + half, cy + half)


def _pad_raw(area: Area | None) -> PadTerms:
    """The terrain facts as raw columns. Every one is a measurement, never a verdict."""
    if area is None:
        return {
            "pad_roughness_m": None,
            "pad_slope_deg": None,
            "pad_z_range_m": None,
            "pad_submerged_pct": None,
            "pad_nodata_pct": None,
        }
    return {
        "pad_roughness_m": area.roughness_m,
        "pad_slope_deg": area.slope_mean_deg,
        "pad_z_range_m": area.z_range_m,
        "pad_submerged_pct": area.submerged_pct,
        "pad_nodata_pct": area.nodata_pct,
    }


def _fill_unknown_distances(distances: list[float | None]) -> list[float]:
    """A missing distance (no infrastructure known) scores as the farthest known, never 0 km."""
    known = [d for d in distances if d is not None]
    fallback = max(known) if known else 0.0
    return [fallback if d is None else d for d in distances]


def _fill_unknown_roughness(roughness: list[float | None]) -> list[float]:
    """An unseen pad scores as the MEDIAN of the seen ones: no-data is ignorance, and 0 or
    the worst would turn it into a claim. With no field at all the term is a flat 0.5."""
    seen = sorted(r for r in roughness if r is not None)
    median_m = seen[len(seen) // 2] if seen else 0.0
    return [median_m if r is None else r for r in roughness]


def rank_sites(
    clusters: list[geo.Cluster[AnnotatedNode]],
    infra: list[tuple[float, float]] | None = None,
    consumer_z: float | None = None,
    weights: dict[str, float] | None = None,
    require_untapped: bool = True,
    terrain: Field | None = None,
) -> list[SiteScore]:
    """Rank candidate fields, best first."""
    weights = {**WEIGHTS, **(weights or {})}
    infra = infra or []

    pool = list(clusters)
    if require_untapped:
        pool = [c for c in pool if untapped_rate(c.members) > 0]
    if not pool:
        return []

    throughput = [untapped_rate(c.members) for c in pool]
    spread = [c.diameter_m for c in pool]
    distance = [_distance_to_infra_m(c, infra) for c in pool]
    purity = [_purity_quality(c) for c in pool]
    pads = [_pad(c, terrain) for c in pool]
    roughness = [None if p is None else p.roughness_m for p in pads]

    normalised_throughput = _normalise(throughput)
    normalised_spread = _normalise(spread)
    normalised_distance = _normalise(_fill_unknown_distances(distance))
    normalised_purity = _normalise(purity)
    normalised_roughness = _normalise(_fill_unknown_roughness(roughness))

    out: list[SiteScore] = []
    for i, cluster in enumerate(pool):
        normalised = {
            "throughput": normalised_throughput[i],
            "spread": normalised_spread[i],
            "distance": normalised_distance[i],
            "purity": normalised_purity[i],
            "roughness": normalised_roughness[i],
        }
        score = sum(weights[k] * normalised[k] for k in normalised)
        to_infra_m = distance[i]
        out.append(
            SiteScore(
                cluster=cluster,
                score=round(score, 4),
                raw={
                    "untapped_rate": round(throughput[i], 2),
                    "total_rate": round(sum(m.get("rate", 0.0) for m in cluster.members), 2),
                    "nodes": cluster.size,
                    "spread_m": round(spread[i], 1),
                    "distance_to_infra_m": None if to_infra_m is None else round(to_infra_m, 1),
                    "purity_quality": round(purity[i], 3),
                    "altitude_vs_consumer_m": _altitude_delta_m(cluster, consumer_z),
                    **_pad_raw(pads[i]),
                },
                normalised={k: round(v, 3) for k, v in normalised.items()},
            )
        )
    out.sort(key=lambda s: -s.score)
    return out
