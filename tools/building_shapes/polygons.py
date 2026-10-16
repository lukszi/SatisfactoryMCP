"""Plain polygon helpers for the outline regulariser: rasterising a ring onto a trace's grid,
overlap scores, and tidying a ring's vertices."""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from satisfactory_mcp.core.arrays import BoolMask, F64Grid

__all__ = [
    "Grid",
    "Point",
    "Ring",
    "covered",
    "intersect",
    "iou",
    "nearest_on",
    "pulled_inside",
    "ring_area",
    "tidy",
]

Point = tuple[float, float]
#: A polygon as ``(x, y)`` vertices, first not repeated at the end.
Ring = list[Point]


class Grid(NamedTuple):
    """A boolean raster and where it sits: cell ``(row, col)`` is centred on
    ``(origin[0] + col * cell, origin[1] + row * cell)``."""

    mask: BoolMask
    origin: Point
    cell: float


def covered(rings: list[Ring], grid: Grid) -> BoolMask:
    """The cells of ``grid`` whose centre lies inside any of ``rings`` (even-odd per ring)."""
    rows, cols = grid.mask.shape
    xs: F64Grid = grid.origin[0] + np.arange(cols, dtype=np.float64) * grid.cell
    ys: F64Grid = grid.origin[1] + np.arange(rows, dtype=np.float64) * grid.cell
    out = np.zeros((rows, cols), dtype=bool)
    for ring in rings:
        if len(ring) < 3:
            continue
        pts = np.asarray(ring, dtype=np.float64)
        c0, c1 = np.searchsorted(xs, [pts[:, 0].min(), pts[:, 0].max()])
        r0, r1 = np.searchsorted(ys, [pts[:, 1].min(), pts[:, 1].max()])
        if c1 <= c0 or r1 <= r0:
            continue
        px: F64Grid
        py: F64Grid
        px, py = np.meshgrid(xs[c0:c1], ys[r0:r1])
        inside: BoolMask = np.zeros(px.shape, dtype=bool)
        ax, ay = pts[:, 0], pts[:, 1]
        bx, by = np.roll(ax, -1), np.roll(ay, -1)
        for x1, y1, x2, y2 in zip(ax, ay, bx, by, strict=True):
            if y1 == y2:
                continue
            spans: BoolMask = (y1 > py) != (y2 > py)
            at: F64Grid = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            inside ^= spans & (px < at)
        out[r0:r1, c0:c1] |= inside
    return out


def iou(a: BoolMask, b: BoolMask) -> float:
    union = int((a | b).sum())
    return int((a & b).sum()) / union if union else 1.0


def ring_area(ring: Ring) -> float:
    """Signed area, positive counter-clockwise."""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True):
        total += x0 * y1 - x1 * y0
    return total / 2


def intersect(p: Point, u: Point, q: Point, v: Point) -> Point | None:
    """Where the line through ``p`` along ``u`` meets the one through ``q`` along ``v``."""
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / den
    return (p[0] + t * u[0], p[1] + t * u[1])


def tidy(ring: Ring, min_edge: float, straight: float = 1e-3) -> Ring:
    """Drop vertices closer than ``min_edge`` to the previous one and vertices on a straight
    run (turning by less than ``straight`` radians), until nothing changes."""
    out = list(ring)
    changed = True
    while changed and len(out) > 3:
        changed = False
        kept: Ring = []
        for point in out:
            if kept and math.dist(kept[-1], point) < min_edge:
                changed = True
                continue
            kept.append(point)
        if len(kept) > 3 and math.dist(kept[0], kept[-1]) < min_edge:
            kept.pop()
            changed = True
        out = kept
        n = len(out)
        if n <= 3:
            break
        for i in range(n):
            (ax, ay), (bx, by), (cx, cy) = out[i - 1], out[i], out[(i + 1) % n]
            ux, uy, vx, vy = bx - ax, by - ay, cx - bx, cy - by
            turn = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
            if abs(turn) < straight or abs(abs(turn) - math.pi) < straight:
                del out[i]
                changed = True
                break
    return out


def _inside(point: Point, ring: Ring) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def nearest_on(point: Point, ring: Ring) -> Point:
    """The point of ``ring``'s boundary nearest ``point``."""
    best, gap = point, math.inf
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1], strict=True):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else ((point[0] - ax) * dx + (point[1] - ay) * dy) / length
        at = (ax + min(1.0, max(0.0, t)) * dx, ay + min(1.0, max(0.0, t)) * dy)
        if math.dist(at, point) < gap:
            best, gap = at, math.dist(at, point)
    return best


def pulled_inside(ring: Ring, parents: list[Ring]) -> Ring:
    """``ring`` with every vertex outside all of ``parents`` moved onto the nearest parent
    edge, so a band drawn over its parent does not show past it."""
    if not parents:
        return list(ring)
    out: Ring = []
    for point in ring:
        if any(_inside(point, parent) for parent in parents):
            out.append(point)
            continue
        options = [nearest_on(point, parent) for parent in parents]
        out.append(min(options, key=lambda at: math.dist(at, point)))
    return out
