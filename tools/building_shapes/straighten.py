"""Straight walls out of a cell-edge trace: edges near 0, 45 or 90 degrees in the building's
own frame snap to exactly that, runs along one wall merge into one line fitted to the whole
staircase, small jogs and corner bevels go. Why and with which tolerances:
docs/building-shapes.md, "Straight walls"."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import NamedTuple

from tools.building_shapes.polygons import Point, Ring, intersect, tidy
from tools.building_shapes.rounds import Circle, arc_points, find_arcs, meets_circle, on_circle

__all__ = ["Line", "StraightOptions", "dp_indices", "straighten"]

#: Walls meeting further than this many cells from the traced corner are joined by a step.
_MEET = 8.0


class StraightOptions(NamedTuple):
    """Tolerances in grid cells, angles in degrees."""

    #: Douglas-Peucker tolerance the walls are read from, before any snapping.
    fine: float = 1.0
    #: An edge this close to 0 or 90 degrees snaps to it; to 45 degrees, ``snap_diagonal``.
    snap_angle: float = 12.0
    snap_diagonal: float = 12.0
    #: A wall step no deeper than this between two parallel walls is merged away.
    jog: float = 1.5
    #: A bevel no longer than this between two square walls is cut back to a corner.
    bevel: float = 6.0
    #: A wall this close to a parent's parallel wall is put on it.
    share: float = 1.5
    #: Edges shorter than this are dropped.
    min_edge: float = 0.5
    #: A run of edges on one circle (``arc_rms`` cells from it, at least ``arc_sweep``
    #: degrees round, ``arc_radius`` cells or more) is drawn as that circle's arc, a vertex
    #: every ``arc_step`` degrees.
    arc_rms: float = 0.25
    arc_sweep: float = 60.0
    arc_radius: float = 8.0
    arc_step: float = 15.0


class Line(NamedTuple):
    """A snapped wall: ``axis`` degrees in ``[0, 180)`` and its offset along the normal
    ``(-sin axis, cos axis)``."""

    axis: int
    offset: float


class _Group(NamedTuple):
    heading: int | None  # snapped heading in [0, 360), None for a free edge
    first: int  # trace index the group starts at
    last: int  # trace index it ends at
    offset: float  # along the heading's normal, snapped groups only
    weight: float  # wall length behind the offset
    circle: Circle | None = None  # the circle an arc group lies on


def dp_indices(points: list[Point], tolerance: float) -> list[int]:
    """Douglas-Peucker on a closed ring, as the kept indices in order."""
    n = len(points)
    if n <= 4:
        return list(range(n))
    x0, y0 = points[0]
    far = max(range(n), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
    fx, fy = points[far]
    start = max(range(n), key=lambda i: (points[i][0] - fx) ** 2 + (points[i][1] - fy) ** 2)
    keep = {start, far}
    stack = [(start, (far - start) % n), (far, (start - far) % n)]
    while stack:
        first, span = stack.pop()
        if span < 2:
            continue
        ax, ay = points[first]
        bx, by = points[(first + span) % n]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst, at = -1.0, -1
        for step in range(1, span):
            px, py = points[(first + step) % n]
            if length == 0:
                dist = math.hypot(px - ax, py - ay)
            else:
                dist = abs(dx * (ay - py) - dy * (ax - px)) / length
            if dist > worst:
                worst, at = dist, step
        if worst > tolerance:
            keep.add((first + at) % n)
            stack.extend(((first, at), ((first + at) % n, span - at)))
    return sorted(keep)


def _from_sharpest(trace: list[Point], kept: list[int]) -> list[int]:
    """``kept`` rolled to start at its sharpest corner, which no wall or arc runs through."""
    n = len(kept)

    def turn(i: int) -> float:
        (ax, ay), (bx, by) = trace[kept[i - 1]], trace[kept[i]]
        cx, cy = trace[kept[(i + 1) % n]]
        ux, uy, vx, vy = bx - ax, by - ay, cx - bx, cy - by
        return abs(math.atan2(ux * vy - uy * vx, ux * vx + uy * vy))

    start = max(range(n), key=turn)
    return kept[start:] + kept[:start]


def _snapped(dx: float, dy: float, square: float, diagonal: float) -> int | None:
    heading = math.degrees(math.atan2(dy, dx)) % 360
    nearest = round(heading / 90) * 90
    if abs(heading - nearest) <= square:
        return int(nearest % 360)
    nearest = round((heading - 45) / 90) * 90 + 45
    return int(nearest % 360) if abs(heading - nearest) <= diagonal else None


def _normal(heading: int) -> Point:
    a = math.radians(heading)
    return (-math.sin(a), math.cos(a))


def _span(trace: list[Point], first: int, last: int) -> list[Point]:
    n = len(trace)
    count = (last - first) % n or n
    return [trace[(first + k) % n] for k in range(count + 1)]


def _wall(trace: list[Point], first: int, last: int, heading: int) -> tuple[float, float]:
    """The offset of the staircase from ``first`` to ``last`` along ``heading``'s normal,
    weighted by each step's run along the heading (risers count for nothing), and that run."""
    a = math.radians(heading)
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux
    total = weight = 0.0
    points = _span(trace, first, last)
    for (x0, y0), (x1, y1) in pairwise(points):
        run = abs((x1 - x0) * ux + (y1 - y0) * uy)
        total += run * ((x0 + x1) / 2 * nx + (y0 + y1) / 2 * ny)
        weight += run
    if weight == 0:
        x, y = points[0]
        return x * nx + y * ny, 0.0
    return total / weight, weight


def _groups(
    trace: list[Point], kept: list[int], options: StraightOptions, cell: float
) -> list[_Group]:
    """The simplified edges, consecutive ones with one snapped heading merged into a wall and
    runs on one circle into an arc."""
    arcs = find_arcs(
        trace, kept, options.arc_rms * cell, options.arc_sweep, options.arc_radius * cell
    )
    on_arc: dict[int, tuple[int, Circle]] = {}
    for number, (first, last, circle) in enumerate(arcs):
        for k in range(first, last):
            on_arc[k] = (number, circle)
    groups: list[_Group] = []
    previous: tuple[int, Circle] | None = None
    for i, first in enumerate(kept):
        last = kept[(i + 1) % len(kept)]
        arc = on_arc.get(i)
        if arc is not None:
            if previous is not None and previous[0] == arc[0]:
                first = groups.pop().first
            groups.append(_Group(None, first, last, 0.0, 0.0, arc[1]))
        else:
            (x0, y0), (x1, y1) = trace[first], trace[last]
            heading = _snapped(x1 - x0, y1 - y0, options.snap_angle, options.snap_diagonal)
            if groups and heading is not None and groups[-1].heading == heading:
                first = groups.pop().first
            if heading is None:
                groups.append(_Group(None, first, last, 0.0, 0.0))
            else:
                offset, weight = _wall(trace, first, last, heading)
                groups.append(_Group(heading, first, last, offset, weight))
        previous = arc
    head, tail = groups[0], groups[-1]
    if len(groups) > 2 and head.heading is not None and head.heading == tail.heading:
        offset, weight = _wall(trace, tail.first, head.last, head.heading)
        groups = [_Group(head.heading, tail.first, head.last, offset, weight), *groups[1:-1]]
    return groups


def _merge_jogs(trace: list[Point], groups: list[_Group], jog: float) -> list[_Group]:
    """Two walls with one heading, a step of at most ``jog`` apart, become one wall."""
    changed = True
    while changed and len(groups) > 4:
        changed = False
        n = len(groups)
        for i in range(n):
            a, step, b = groups[i], groups[(i + 1) % n], groups[(i + 2) % n]
            if a.heading is None or a.heading != b.heading or step.circle is not None:
                continue
            (sx, sy), (ex, ey) = trace[step.first], trace[step.last]
            if abs(a.offset - b.offset) > jog or math.hypot(ex - sx, ey - sy) > 2 * jog:
                continue
            weight = a.weight + b.weight
            offset = (a.offset * a.weight + b.offset * b.weight) / weight if weight else a.offset
            merged = _Group(a.heading, a.first, b.last, offset, weight)
            rest = [groups[(i + 3 + k) % n] for k in range(n - 3)]
            groups = [merged, *rest]
            changed = True
            break
    return groups


def _cut_bevels(trace: list[Point], groups: list[_Group], bevel: float) -> list[_Group]:
    """A short edge between two walls at right angles is a closing bevel: drop it."""
    out = list(groups)
    i = 0
    while i < len(out) and len(out) > 4:
        n = len(out)
        a, cut, b = out[i - 1], out[i], out[(i + 1) % n]
        if a.heading is not None and b.heading is not None and (b.heading - a.heading) % 180 == 90:
            (sx, sy), (ex, ey) = trace[cut.first], trace[cut.last]
            short = math.hypot(ex - sx, ey - sy) <= bevel and cut.circle is None
            slanted = cut.heading is None or cut.heading % 90 == 45
            if cut.heading != a.heading and short and slanted:
                del out[i]
                continue
        i += 1
    return out


def _share(groups: list[_Group], guides: list[Line], share: float) -> list[_Group]:
    """Walls within ``share`` of a parent's parallel wall are put on it."""
    out: list[_Group] = []
    for group in groups:
        if group.heading is not None:
            axis = group.heading % 180
            sign = 1.0 if group.heading < 180 else -1.0
            near = [
                g for g in guides if g.axis == axis and abs(g.offset * sign - group.offset) <= share
            ]
            if near:
                best = min(near, key=lambda g: abs(g.offset * sign - group.offset))
                group = group._replace(offset=best.offset * sign)
        out.append(group)
    return out


def _vertices(trace: list[Point], groups: list[_Group], cell: float, step: float) -> Ring:
    """Each junction of consecutive groups as one corner (or two, where walls do not meet),
    with an arc's own vertices between the junctions at its ends."""
    n = len(groups)
    joints: list[Ring] = []
    for i, a in enumerate(groups):
        b = groups[(i + 1) % n]
        (x0, y0), (x1, y1) = trace[a.last], trace[b.first]
        corner = ((x0 + x1) / 2, (y0 + y1) / 2)
        joints.append(_junction(trace, a, b, corner, cell))
    out: Ring = []
    for i in range(n):
        out.extend(joints[i])
        after = groups[(i + 1) % n]
        if after.circle is not None:
            middle = trace[
                (after.first + (after.last - after.first) % len(trace) // 2) % len(trace)
            ]
            start, end = joints[i][-1], joints[(i + 1) % n][0]
            out.extend(arc_points(after.circle, start, end, middle, step))
    return out


def _on_wall(group: _Group, point: Point) -> Point:
    nx, ny = _normal(group.heading or 0)
    gap = group.offset - (point[0] * nx + point[1] * ny)
    return (point[0] + gap * nx, point[1] + gap * ny)


def _direction(trace: list[Point], group: _Group) -> Point:
    if group.heading is not None:
        a = math.radians(group.heading)
        return (math.cos(a), math.sin(a))
    (x0, y0), (x1, y1) = trace[group.first], trace[group.last]
    return (x1 - x0, y1 - y0)


def _junction(trace: list[Point], a: _Group, b: _Group, corner: Point, cell: float) -> Ring:
    if a.circle is not None or b.circle is not None:
        return _arc_junction(a, b, corner, cell)
    if a.heading is None and b.heading is None:
        return [corner]
    u, v = _direction(trace, a), _direction(trace, b)
    sine = abs(u[0] * v[1] - u[1] * v[0]) / (math.hypot(*u) * math.hypot(*v) or 1.0)
    pa = _on_wall(a, corner) if a.heading is not None else trace[a.first]
    pb = _on_wall(b, corner) if b.heading is not None else trace[b.last]
    if sine > math.sin(math.radians(20)):
        meet = intersect(pa, u, pb, v)
        if meet is not None and math.dist(meet, corner) <= _MEET * cell:
            return [meet]
    if a.heading is None:
        return [pb]
    if b.heading is None:
        return [pa]
    return [pa, pb]


def _arc_junction(a: _Group, b: _Group, corner: Point, cell: float) -> Ring:
    """Where an arc meets its neighbour: where a wall crosses the circle near the corner, else
    the corner put on each in turn."""
    for arc, wall in ((a, b), (b, a)):
        if arc.circle is not None and wall.heading is not None:
            angle = math.radians(wall.heading)
            along = (math.cos(angle), math.sin(angle))
            meet = meets_circle(arc.circle, _on_wall(wall, corner), along, corner)
            if meet is not None and math.dist(meet, corner) <= _MEET * cell:
                return [meet]
    return [_placed(a, corner), _placed(b, corner)]


def _placed(group: _Group, corner: Point) -> Point:
    if group.circle is not None:
        return on_circle(group.circle, corner)
    return _on_wall(group, corner) if group.heading is not None else corner


def straighten(
    trace: list[Point], cell: float, options: StraightOptions, guides: list[Line]
) -> tuple[Ring, list[Line]]:
    """``trace`` (a counter-clockwise cell-edge loop) with straight walls, and those walls."""
    kept = dp_indices(trace, options.fine * cell)
    if len(kept) < 3:
        return list(trace), []
    kept = _from_sharpest(trace, kept)
    groups = _groups(trace, kept, options, cell)
    groups = _merge_jogs(trace, groups, options.jog * cell)
    groups = _cut_bevels(trace, groups, options.bevel * cell)
    groups = _share(groups, guides, options.share * cell)
    ring = tidy(_vertices(trace, groups, cell, options.arc_step), options.min_edge * cell)
    lines = [
        Line(g.heading % 180, g.offset if g.heading < 180 else -g.offset)
        for g in groups
        if g.heading is not None
    ]
    return ring, lines
