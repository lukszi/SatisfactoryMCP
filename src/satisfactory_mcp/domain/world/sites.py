"""Where the factory already is: infrastructure points, altitudes and clusters.

Plain functions over the build records rather than methods on a facet: none of the
three carries state, and the record list is the census's to hand over.
"""

from __future__ import annotations

from ..spatial import geo

__all__ = ["consumer_z", "infra_points", "selector", "sites"]


def infra_points(records: list[dict]) -> list[tuple[float, float]]:
    """XY of every built production building, for distance-to-infrastructure."""
    return [(r["pos"][0], r["pos"][1]) for r in records if r.get("pos")]


def consumer_z(
    records: list[dict], building_ids: tuple[str, ...] = ("Build_OilRefinery_C",)
) -> float | None:
    """Mean altitude of a consumer class, for pipe head-lift sign.

    Defaults to refineries because that is what a fluid field usually feeds.
    """
    zs = [r["pos"][2] for r in records if r.get("pos") and r["cls"] in building_ids]
    return sum(zs) / len(zs) if zs else None


def selector(centroid_cm: tuple[float, ...], diameter_m: float) -> str:
    """The ``near:`` circle that covers a site: 0.6x its spread, never under 50 m.

    Half the spread measurably clips members (432 of 461 on the reference world's main
    site, against 438 at 0.6), since a set of diameter d needs a radius up to d/sqrt(3).
    """
    x_m, y_m = int(centroid_cm[0] / 100), int(centroid_cm[1] / 100)
    return f"near:{x_m},{y_m}@{max(50, round(diameter_m * 0.6))}"


def sites(records: list[dict], link_m: float = 300.0) -> list[dict]:
    """Cluster built production buildings into named-by-content sites.

    ``instances`` holds each member's instance leaf, so a caller can say which sites a
    given machine set stands in.
    """
    placed = [r for r in records if r.get("pos")]
    points = [
        {"x": r["pos"][0], "y": r["pos"][1], "z": r["pos"][2], "kind": r["cls"], "rec": r}
        for r in placed
    ]
    out = []
    for c in geo.cluster(points, link_m=link_m):
        counts: dict[str, int] = {}
        for m in c.members:
            counts[m["kind"]] = counts.get(m["kind"], 0) + 1
        cx, cy, cz = c.centroid
        out.append(
            {
                "centroid": (round(cx), round(cy), round(cz)),
                "grid": geo.grid_cell(cx, cy),
                "direction": geo.direction_of(cx, cy),
                "buildings": counts,
                "count": c.size,
                "diameter_m": round(c.diameter_m),
                "selector": selector((round(cx), round(cy)), round(c.diameter_m)),
                "instances": [
                    str(m["rec"].get("instance", "")).rsplit(".", 1)[-1] for m in c.members
                ],
            }
        )
    out.sort(key=lambda s: -s["count"])
    return out
