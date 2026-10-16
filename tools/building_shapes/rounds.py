"""Round parts: a ring whose trace sits on one circle is drawn as a regular polygon, and a
run of a ring's edges on one circle as that circle's arc, instead of the trace's wobble.
docs/building-shapes.md, "Round parts"."""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from satisfactory_mcp.core.arrays import F64Grid
from tools.building_shapes.polygons import Point, Ring

__all__ = [
    "Circle",
    "arc_points",
    "find_arcs",
    "fit_circle",
    "meets_circle",
    "on_circle",
    "regular_polygon",
]


class Circle(NamedTuple):
    x: float
    y: float
    r: float
    #: Root-mean-square distance of the fitted points from the circle.
    rms: float


def _resampled(points: list[Point], step: float, closed: bool) -> F64Grid:
    """The path through ``points`` sampled every ``step`` along its length, so a long straight
    edge weighs as much as its length and shows how far it strays from a circle."""
    pts = np.asarray(points, dtype=np.float64)
    if closed:
        pts = np.concatenate([pts, pts[:1]])
    # Through the steps' midpoints: a cell staircase's own corners stray half a cell either
    # side of the curve it follows, its midpoints hardly at all.
    pts = np.concatenate([pts[:1], (pts[1:] + pts[:-1]) / 2, pts[-1:]])
    lengths = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    along = np.concatenate([[0.0], np.cumsum(lengths)])
    at = np.arange(0.0, along[-1], step)
    return np.stack([np.interp(at, along, pts[:, 0]), np.interp(at, along, pts[:, 1])], axis=1)


def fit_circle(points: list[Point], step: float, closed: bool = True) -> Circle | None:
    """The least-squares (Kasa) circle through the path ``points``, sampled every ``step``."""
    if len(points) < 3:
        return None
    samples = _resampled(points, step, closed)
    if len(samples) < 6:
        return None
    x, y = samples[:, 0], samples[:, 1]
    a = np.stack([x, y, np.ones_like(x)], axis=1)
    solution: F64Grid = np.linalg.lstsq(a, x * x + y * y, rcond=None)[0]
    d, e, f = (float(value) for value in solution)
    cx, cy = d / 2, e / 2
    r2 = f + cx * cx + cy * cy
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    rms = math.sqrt(float(np.mean((np.hypot(x - cx, y - cy) - r) ** 2)))
    return Circle(cx, cy, r, rms)


def regular_polygon(circle: Circle, sides: int) -> Ring:
    """A regular polygon of ``sides`` with the circle's area, a flat side facing each axis
    when ``sides`` is a multiple of four, counter-clockwise."""
    step = 2 * math.pi / sides
    # The circumradius that gives the circle's own area, so the polygon neither shrinks it
    # nor grows it.
    radius = circle.r * math.sqrt(step / math.sin(step))
    return [
        (
            circle.x + radius * math.cos(step * (k + 0.5)),
            circle.y + radius * math.sin(step * (k + 0.5)),
        )
        for k in range(sides)
    ]


def _sweep(points: list[Point], circle: Circle) -> float:
    """How far round ``circle`` the path ``points`` runs, degrees, counter-clockwise: a
    counter-clockwise ring bulging out, negative where it bites in."""
    angles = np.unwrap([math.atan2(y - circle.y, x - circle.x) for x, y in points])
    return math.degrees(float(angles[-1] - angles[0]))


def find_arcs(
    trace: list[Point], kept: list[int], tolerance: float, sweep: float, radius: float
) -> list[tuple[int, int, Circle]]:
    """Runs of the counter-clockwise simplified ring ``kept`` (indices into ``trace``) whose
    trace lies on one circle of at least ``radius`` within ``tolerance`` (rms) and bulges out
    round it by at least ``sweep`` degrees, as ``(first, last, circle)`` positions in
    ``kept``; at least three edges each. Inward bites are left to the walls: on these traces
    they are mostly the closing's rounded inside corners."""
    n = len(kept)
    found: list[tuple[int, int, Circle]] = []
    i = 0
    while i < n - 2:
        best: tuple[int, Circle] | None = None
        for j in range(i + 3, n + 1):
            span = _span(trace, kept[i], kept[j % n])
            circle = fit_circle(span, tolerance, closed=False)
            if circle is None or circle.rms > tolerance:
                break
            if circle.r >= radius and _sweep(span, circle) >= sweep:
                best = (j, circle)
        if best is None:
            i += 1
            continue
        found.append((i, best[0], best[1]))
        i = best[0]
    return _joined(trace, kept, found, tolerance)


def _joined(
    trace: list[Point], kept: list[int], arcs: list[tuple[int, int, Circle]], tolerance: float
) -> list[tuple[int, int, Circle]]:
    """Back-to-back arcs on nearly one circle as one arc, where the pair still fits it within
    half as much again: a small bump on a round part splits the greedy run in two."""
    out: list[tuple[int, int, Circle]] = []
    for arc in arcs:
        if out and out[-1][1] == arc[0]:
            first, _last, before = out[-1]
            same = math.dist(before[:2], arc[2][:2]) <= 4 * tolerance
            if same and abs(before.r - arc[2].r) <= 4 * tolerance:
                span = _span(trace, kept[first], kept[arc[1] % len(kept)])
                circle = fit_circle(span, tolerance, closed=False)
                if circle is not None and circle.rms <= 1.5 * tolerance:
                    out[-1] = (first, arc[1], circle)
                    continue
        out.append(arc)
    return out


def _span(trace: list[Point], first: int, last: int) -> list[Point]:
    n = len(trace)
    count = (last - first) % n or n
    return [trace[(first + k) % n] for k in range(count + 1)]


def on_circle(circle: Circle, point: Point) -> Point:
    """``point`` moved along its radius onto ``circle``."""
    dx, dy = point[0] - circle.x, point[1] - circle.y
    length = math.hypot(dx, dy) or 1.0
    return (circle.x + dx / length * circle.r, circle.y + dy / length * circle.r)


def meets_circle(circle: Circle, at: Point, along: Point, near: Point) -> Point | None:
    """Where the line through ``at`` along ``along`` crosses ``circle``, the crossing nearer
    ``near``; None when it misses."""
    fx, fy = at[0] - circle.x, at[1] - circle.y
    a = along[0] ** 2 + along[1] ** 2
    b = 2 * (fx * along[0] + fy * along[1])
    c = fx * fx + fy * fy - circle.r**2
    disc = b * b - 4 * a * c
    if disc < 0 or a == 0:
        return None
    roots = ((-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a))
    points = [(at[0] + t * along[0], at[1] + t * along[1]) for t in roots]
    return min(points, key=lambda p: math.dist(p, near))


def arc_points(circle: Circle, start: Point, end: Point, through: Point, step: float) -> Ring:
    """Points on ``circle`` strictly between ``start`` and ``end``, going the way round that
    passes ``through``, every ``step`` degrees at most."""
    a0 = math.atan2(start[1] - circle.y, start[0] - circle.x)
    a1 = math.atan2(end[1] - circle.y, end[0] - circle.x)
    mid = math.atan2(through[1] - circle.y, through[0] - circle.x)
    ccw = (a1 - a0) % (2 * math.pi)
    if (mid - a0) % (2 * math.pi) > ccw:
        ccw -= 2 * math.pi
    count = math.ceil(abs(math.degrees(ccw)) / step)
    return [
        (
            circle.x + circle.r * math.cos(a0 + ccw * k / count),
            circle.y + circle.r * math.sin(a0 + ccw * k / count),
        )
        for k in range(1, count)
    ]
