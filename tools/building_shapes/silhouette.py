"""A mesh's top-down silhouette as a few simplified polygons.

Triangles are sampled onto a grid, the grid is closed and hole-filled, each sizeable blob's
outer edge is traced along cell edges, and the trace is simplified (Douglas-Peucker) until it
fits a vertex budget. docs/building-shapes.md has why it is done this way round.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import ndimage

from satisfactory_mcp.core.arrays import BoolMask, F64Grid

__all__ = [
    "MAX_SAMPLES_PER_EDGE",
    "Ring",
    "blobs",
    "disk_mask",
    "rasterise",
    "silhouette",
    "simplify_ring",
    "simplify_to_budget",
    "top_heights",
    "trace_outer",
    "traced",
]

#: A polygon as ``(x, y)`` vertices, first not repeated at the end.
Ring = list[tuple[float, float]]

#: A triangle longer than this many cells is sampled at this many steps per edge anyway.
MAX_SAMPLES_PER_EDGE = 512


def _barycentric(steps: int) -> F64Grid:
    """Every ``(a, b, c)`` with ``a + b + c = 1`` on a ``steps``-division lattice."""
    weights = [
        (i / steps, j / steps, (steps - i - j) / steps)
        for i in range(steps + 1)
        for j in range(steps + 1 - i)
    ]
    return np.asarray(weights, dtype=np.float64)


def top_heights(triangles: F64Grid, cell: float) -> tuple[F64Grid, tuple[float, float]]:
    """The highest ``z`` over each cell, ``-inf`` where nothing is, and the grid's corner.

    ``triangles`` is ``(n, 3, 3)``, or ``(n, 3, 2)`` for a footprint alone (every touched cell
    then reads 0). Each triangle is sampled on a lattice no coarser than a cell, so a wall
    seen edge-on (zero area from above) still marks the line it stands on.
    """
    flat = triangles[:, :, :2]
    low = flat.reshape(-1, 2).min(axis=0) - 2 * cell
    high = flat.reshape(-1, 2).max(axis=0) + 2 * cell
    shape = (math.ceil((high[1] - low[1]) / cell), math.ceil((high[0] - low[0]) / cell))
    top = np.full(shape, -np.inf)
    edges = np.stack(
        [
            np.linalg.norm(flat[:, 1] - flat[:, 0], axis=1),
            np.linalg.norm(flat[:, 2] - flat[:, 1], axis=1),
            np.linalg.norm(flat[:, 0] - flat[:, 2], axis=1),
        ],
        axis=1,
    ).max(axis=1)
    steps = np.clip(np.ceil(edges / cell).astype(np.int64), 1, MAX_SAMPLES_PER_EDGE)
    dims = triangles.shape[2]
    for count in np.unique(steps):
        chosen = triangles[steps == count]
        points = np.einsum("kv,tvd->tkd", _barycentric(int(count)), chosen).reshape(-1, dims)
        cols = ((points[:, 0] - low[0]) / cell).astype(np.int64)
        rows = ((points[:, 1] - low[1]) / cell).astype(np.int64)
        heights = points[:, 2] if dims == 3 else np.zeros(len(points))
        np.maximum.at(top, (rows, cols), heights)
    return top, (float(low[0]), float(low[1]))


def rasterise(triangles: F64Grid, cell: float) -> tuple[BoolMask, tuple[float, float]]:
    """Cells any of ``triangles`` touches, and the grid's lower-left corner."""
    top, corner = top_heights(triangles, cell)
    return np.isfinite(top), corner


def disk_mask(radius: int) -> BoolMask:
    span = np.arange(-radius, radius + 1)
    return (span[:, None] ** 2 + span[None, :] ** 2) <= radius * radius


def trace_outer(mask: BoolMask) -> list[tuple[int, int]]:
    """The outer boundary of one 4-connected, hole-free blob, as grid corners ``(col, row)``.

    Walks cell edges with the blob on the left. Where two edges leave one corner (two cells
    touching diagonally) the sharper left turn is taken, which keeps the loop simple; the
    longest loop found is the outer one.
    """
    padded = np.pad(mask, 1)
    out: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    rows, cols = np.nonzero(padded)
    for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
        x, y = c - 1, r - 1  # back to unpadded corner coordinates
        if not padded[r - 1, c]:  # empty below (lower y): edge runs +x
            out[(x, y)].append((x + 1, y))
        if not padded[r, c + 1]:  # empty to the right: edge runs +y
            out[(x + 1, y)].append((x + 1, y + 1))
        if not padded[r + 1, c]:  # empty above: edge runs -x
            out[(x + 1, y + 1)].append((x, y + 1))
        if not padded[r, c - 1]:  # empty to the left: edge runs -y
            out[(x, y + 1)].append((x, y))
    loops: list[list[tuple[int, int]]] = []
    while out:
        start = next(iter(out))
        loop: list[tuple[int, int]] = []
        here, heading = start, (0, 0)
        while here in out:
            loop.append(here)
            options = out[here]
            nxt = _leftmost(here, heading, options)
            options.remove(nxt)
            if not options:
                del out[here]
            heading = (nxt[0] - here[0], nxt[1] - here[1])
            here = nxt
            if here == start:
                break
        loops.append(loop)
    return max(loops, key=len)


def _leftmost(
    here: tuple[int, int], heading: tuple[int, int], options: list[tuple[int, int]]
) -> tuple[int, int]:
    """The option turning furthest left from ``heading``; the first on a tie."""
    best, score = options[0], -2
    for to in options:
        turn = _left_turn(heading, (to[0] - here[0], to[1] - here[1]))
        if turn > score:
            best, score = to, turn
    return best


def _left_turn(heading: tuple[int, int], to: tuple[int, int]) -> int:
    """How far left ``to`` turns from ``heading``: 1 left, 0 straight, -1 right."""
    return heading[0] * to[1] - heading[1] * to[0]


def _collinear_free(ring: Ring) -> Ring:
    """Drop vertices on a straight run, which a cell-edge trace is mostly made of."""
    kept: Ring = []
    n = len(ring)
    for i, (x, y) in enumerate(ring):
        px, py = ring[i - 1]
        nx, ny = ring[(i + 1) % n]
        if (x - px) * (ny - y) - (y - py) * (nx - x) != 0:
            kept.append((x, y))
    return kept


def _dp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on an open polyline, both ends kept."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        ax, ay = points[first]
        bx, by = points[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst, at = -1.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if length == 0:
                dist = math.hypot(px - ax, py - ay)
            else:
                dist = abs(dx * (ay - py) - dy * (ax - px)) / length
            if dist > worst:
                worst, at = dist, i
        if at >= 0 and worst > tolerance:
            keep[at] = True
            stack.extend(((first, at), (at, last)))
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_ring(ring: Ring, tolerance: float) -> Ring:
    """Douglas-Peucker on a closed ring, split at its two mutually farthest-ish vertices."""
    if len(ring) <= 4:
        return list(ring)
    x0, y0 = ring[0]
    far = max(range(len(ring)), key=lambda i: (ring[i][0] - x0) ** 2 + (ring[i][1] - y0) ** 2)
    fx, fy = ring[far]
    start = max(range(len(ring)), key=lambda i: (ring[i][0] - fx) ** 2 + (ring[i][1] - fy) ** 2)
    rolled = ring[start:] + ring[:start]
    mid = (far - start) % len(ring)
    first = _dp(rolled[: mid + 1], tolerance)
    second = _dp([*rolled[mid:], rolled[0]], tolerance)
    return first[:-1] + second[:-1]


def simplify_to_budget(ring: Ring, max_vertices: int, tolerance: float) -> Ring:
    """``simplify_ring`` at the smallest tolerance from ``tolerance`` up that fits the budget."""
    out = simplify_ring(ring, tolerance)
    while len(out) > max_vertices:
        tolerance *= 1.25
        out = simplify_ring(ring, tolerance)
    return out


def blobs(mask: BoolMask, radius: int, opening: int = 0) -> BoolMask:
    """``mask`` padded by ``radius``, opened (thin parts dropped), closed and hole-filled."""
    grid = np.pad(mask, radius)
    if opening:
        grid = ndimage.binary_opening(grid, structure=disk_mask(opening))
    grid = ndimage.binary_closing(grid, structure=disk_mask(radius))
    return ndimage.binary_fill_holes(grid)


def traced(
    grid: BoolMask,
    corner: tuple[float, float],
    cell: float,
    max_vertices: int,
    min_fraction: float,
    min_cells: int = 0,
) -> list[Ring]:
    """Each sizeable blob of ``grid`` traced and simplified, largest first, in grid units."""
    labels, count = ndimage.label(grid)
    if not count:
        return []
    areas = np.bincount(labels.ravel())[1:]
    rings: list[Ring] = []
    x0, y0 = corner
    for blob in np.argsort(areas)[::-1]:
        if areas[blob] < max(min_fraction * areas.max(), min_cells):
            break
        corners = trace_outer(labels == blob + 1)
        traced = _collinear_free([(float(c), float(r)) for c, r in corners])
        simple = simplify_to_budget(traced, max_vertices, tolerance=1.0)
        # Half a cell back: a sample on an edge marks the cell past it, so a bare trace
        # would grow the outline by one cell on its high sides only.
        rings.append([(c * cell + x0 - cell / 2, r * cell + y0 - cell / 2) for c, r in simple])
    return rings


def silhouette(
    triangles: F64Grid,
    cell: float,
    close: float,
    max_vertices: int,
    min_fraction: float = 0.02,
) -> list[Ring]:
    """The simplified outer outlines of ``triangles`` seen from above, largest first.

    ``close`` (same unit as ``cell``) bridges gaps between parts; a blob under
    ``min_fraction`` of the largest one's area is dropped as a stray.
    """
    if len(triangles) == 0:
        return []
    grid, (x0, y0) = rasterise(triangles, cell)
    radius = max(1, round(close / cell))
    corner = (x0 - radius * cell, y0 - radius * cell)
    return traced(blobs(grid, radius), corner, cell, max_vertices, min_fraction)
