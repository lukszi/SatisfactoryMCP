"""Exact map geometry. No hand-authored region shapes here.

The coordinate frame, established four independent ways::

    -Y = north      +X = east      +Z = up      1 m = 100 cm exactly

Everything in this module is derived and exact. Fuzzy biome *names* live in
``spatial.regions`` and are advisory only -- they never feed a calculation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

__all__ = [
    "CM_PER_M",
    "DIRECTIONS",
    "FIELD_LINK_M",
    "GRID_CELL",
    "MAP_SQUARE_M",
    "Cluster",
    "PlacedT",
    "bbox",
    "bearing_deg",
    "centroid",
    "cluster",
    "diameter_m",
    "direction_of",
    "distance_3d_m",
    "distance_m",
    "grid_cell",
    "in_direction",
    "inside",
    "xy_of",
]

CM_PER_M = 100.0

#: Single-linkage distance that recovers the real resource fields: the widest oil field
#: spreads 288 m, far less than any gap between two fields.
FIELD_LINK_M = 200.0

#: Biome grid: 1.024 km cells, numbered from the SOUTH-WEST corner. [WIKI]
GRID_CELL = 102_400.0
GRID_X0 = -319_600.0  # west edge of column X0
GRID_Y0_SOUTH = 302_800.0  # south edge of row Y0

#: Content extents measured over the 2,687 static world objects of
#: ``data/world_resource_nodes.json`` and ``data/world_collectibles.json``, and
#: reproducible from those two files alone.
CONTENT_BBOX = (-298_838.0, -314_104.0, 406_564.0, 304_196.0)  # minx, miny, maxx, maxy

#: The in-game map square in metres (minx, miny, maxx, maxy); ``tiles.DEFAULT_MAP_BOUNDS_M``.
MAP_SQUARE_M = (-3247.0, -3750.0, 4253.0, 3750.0)

#: Compass bearings in degrees, clockwise from north.
DIRECTIONS: dict[str, float] = {
    "north": 0.0,
    "northeast": 45.0,
    "east": 90.0,
    "southeast": 135.0,
    "south": 180.0,
    "southwest": 225.0,
    "west": 270.0,
    "northwest": 315.0,
}
_ALIASES = {
    "n": "north",
    "ne": "northeast",
    "e": "east",
    "se": "southeast",
    "s": "south",
    "sw": "southwest",
    "w": "west",
    "nw": "northwest",
}


def grid_cell(x: float, y: float) -> str:
    """Biome grid cell label, e.g. ``"X3Y4"``. Exact, no interpolation."""
    i = math.floor((x - GRID_X0) / GRID_CELL)
    j = math.floor((GRID_Y0_SOUTH - y) / GRID_CELL)
    return f"X{i}Y{j}"


def bearing_deg(x: float, y: float, ox: float = 0.0, oy: float = 0.0) -> float:
    """Compass bearing from (ox, oy) to (x, y), degrees clockwise from north.

    North is -Y, so the bearing is ``atan2(dx, -dy)`` and not ``atan2(dy, dx)``.
    """
    return math.degrees(math.atan2(x - ox, -(y - oy))) % 360.0


def direction_of(x: float, y: float, ox: float = 0.0, oy: float = 0.0) -> str:
    """Nearest of the eight compass names."""
    b = bearing_deg(x, y, ox, oy)
    return min(DIRECTIONS, key=lambda name: _angle_gap(b, DIRECTIONS[name]))


def _angle_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def normalise_direction(name: str) -> str:
    key = name.strip().casefold().replace("-", "").replace(" ", "")
    key = _ALIASES.get(key, key)
    if key not in DIRECTIONS:
        raise ValueError(f"unknown direction {name!r}; use one of {sorted(DIRECTIONS)}")
    return key


def in_direction(
    x: float,
    y: float,
    direction: str,
    origin: tuple[float, float] | None = None,
    half_angle: float = 60.0,
) -> bool:
    """Cone test, falling back to a hemisphere when no origin is given.

    A hemisphere about the map centre is what "what oil is in the north" means; a cone from
    an origin is what "north of me" means.
    """
    d = normalise_direction(direction)
    if origin is None:
        return _hemisphere(x, y, d)
    b = bearing_deg(x, y, origin[0], origin[1])
    return _angle_gap(b, DIRECTIONS[d]) <= half_angle


def _hemisphere(x: float, y: float, direction: str) -> bool:
    target = DIRECTIONS[direction]
    b = bearing_deg(x, y, 0.0, 0.0)
    return _angle_gap(b, target) <= 90.0


def distance_m(a: Sequence[float], b: Sequence[float]) -> float:
    """Planar XY distance in metres between two cm points; any Z is ignored. It matters for
    pipe head, not for proximity, where a 40 m climb is noise against a 400 m walk."""
    return math.dist(a[:2], b[:2]) / CM_PER_M


def distance_3d_m(a: Sequence[float], b: Sequence[float]) -> float:
    """Straight-line distance including Z, in metres.

    A separate function rather than a flag on ``distance_m``, so that the choice is spelled
    at the call site: a pipe RUN's vertical leg is real pipe to build and pump through, and
    a trunk's length includes it.
    """
    return math.dist(a, b) / CM_PER_M


def centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """Mean XY of a set of points, in the units they came in. ``None`` when empty.

    ``None`` rather than the origin: (0, 0) is the world centre every compass direction is
    measured from, so an empty set answering with it gives a plausible wrong location.
    """
    if not points:
        return None
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def bbox(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Axis-aligned extent as ``(x_min, y_min, x_max, y_max)``, in the units given.

    ``None`` when empty, for the reason ``centroid`` gives. A single point yields a
    degenerate box, which is the truth about a one-machine factory; padding it into
    something a viewport can use is the caller's decision.
    """
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def inside(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    """Even-odd test: whether ``point`` falls within ``polygon``, both in the same units."""
    x, y = point
    hit = False
    n = len(polygon)
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[i - 1]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def diameter_m(points: Sequence[tuple[float, float]]) -> float:
    """Largest pairwise distance in metres -- the honest measure of spread."""
    if len(points) < 2:
        return 0.0
    return max(
        distance_m(points[i], points[j])
        for i in range(len(points))
        for j in range(i + 1, len(points))
    )


#: A row placed by its ``x``, ``y`` and ``z`` keys in centimetres: a node row, or a
#: building's point.
PlacedT = TypeVar("PlacedT", bound=Mapping[str, object])


def _number(member: Mapping[str, object], key: str) -> float:
    value = member[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} is {value!r}, not a number")
    return value


def _word(member: Mapping[str, object], key: str) -> str:
    value = member[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} is {value!r}, not a string")
    return value


def xy_of(member: Mapping[str, object]) -> tuple[float, float]:
    """The ``x``, ``y`` of a placed row, in centimetres."""
    return _number(member, "x"), _number(member, "y")


@dataclass
class Cluster(Generic[PlacedT]):
    """A group of nearby nodes. Named by CONTENT, never by biome."""

    members: list[PlacedT]

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def centroid(self) -> tuple[float, float, float]:
        n = len(self.members)
        return (
            sum(_number(m, "x") for m in self.members) / n,
            sum(_number(m, "y") for m in self.members) / n,
            sum(_number(m, "z") for m in self.members) / n,
        )

    @property
    def diameter_m(self) -> float:
        return diameter_m([xy_of(m) for m in self.members])

    @property
    def grid_cell(self) -> str:
        cx, cy, _ = self.centroid
        return grid_cell(cx, cy)

    def kinds(self) -> dict[str, int]:
        """Node kinds present.

        Kind must never be inferred from a single member: at a 200 m link distance one real
        cluster merges 6 well satellites with a plain node 85 m away, and reading 'well' off
        the first understates that field by 120 m3/min.
        """
        return self._tally("kind")

    def purities(self) -> dict[str, int]:
        return self._tally("purity")

    def _tally(self, key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.members:
            word = _word(m, key)
            out[word] = out.get(word, 0) + 1
        return out


def cluster(nodes: Sequence[PlacedT], link_m: float = FIELD_LINK_M) -> list[Cluster[PlacedT]]:
    """Single-linkage clustering on XY, by default into the fields ``FIELD_LINK_M`` joins."""
    remaining = list(nodes)
    points = {id(node): xy_of(node) for node in remaining}
    out: list[Cluster[PlacedT]] = []
    while remaining:
        seed = remaining.pop()
        group = [seed]
        changed = True
        while changed:
            changed = False
            for cand in list(remaining):
                cp = points[id(cand)]
                if any(distance_m(cp, points[id(m)]) <= link_m for m in group):
                    group.append(cand)
                    remaining.remove(cand)
                    changed = True
        out.append(Cluster(members=group))
    out.sort(key=lambda c: (-c.size, c.centroid[1]))
    return out
