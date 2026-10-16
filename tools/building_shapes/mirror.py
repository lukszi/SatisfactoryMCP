"""Mirror symmetry about a line along the building's own x or y axis: found on the
footprint's raster, then imposed on the rings that already nearly have it.
docs/building-shapes.md, "Mirror symmetry"."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from satisfactory_mcp.core.arrays import BoolMask, I64Grid
from tools.building_shapes.polygons import Grid, Point, Ring, covered, iou, tidy

__all__ = ["Axis", "mirror_axes", "mirror_score", "symmetrise"]

#: Axis offsets searched, in cells either side of the raster's middle.
_SEARCH = 20


class Axis(NamedTuple):
    """The line ``x = at`` (``dim`` 0) or ``y = at`` (``dim`` 1), world units."""

    dim: int
    at: float


def _flipped(mask: BoolMask, dim: int, twice: int) -> BoolMask:
    """``mask`` mirrored about the line through index ``twice / 2`` along ``dim`` (x = cols)."""
    axis = 1 - dim
    size = mask.shape[axis]
    out = np.zeros_like(mask)
    src: I64Grid = np.arange(size, dtype=np.int64)
    dst: I64Grid = twice - src
    ok: BoolMask = (dst >= 0) & (dst < size)
    if axis == 1:
        out[:, dst[ok]] = mask[:, src[ok]]
    else:
        out[dst[ok], :] = mask[src[ok], :]
    return out


def mirror_score(grid: Grid, axis: Axis) -> float:
    """How well ``grid``'s mask matches its own mirror image about ``axis`` (IoU)."""
    twice = round(2 * (axis.at - grid.origin[axis.dim]) / grid.cell)
    return iou(grid.mask, _flipped(grid.mask, axis.dim, twice))


def mirror_axes(grid: Grid, threshold: float) -> list[Axis]:
    """The best mirror line along each axis, kept where it scores ``threshold`` or more."""
    found: list[Axis] = []
    for dim in (0, 1):
        spread = np.nonzero(grid.mask.any(axis=dim))[0]
        if not len(spread):
            continue
        middle = int(spread[0] + spread[-1])
        best, at = -1.0, middle
        for twice in range(middle - 2 * _SEARCH, middle + 2 * _SEARCH + 1):
            score = iou(grid.mask, _flipped(grid.mask, dim, twice))
            if score > best:
                best, at = score, twice
        if best >= threshold:
            found.append(Axis(dim, grid.origin[dim] + at * grid.cell / 2))
    return found


def _reflect(point: Point, axis: Axis) -> Point:
    if axis.dim == 0:
        return (2 * axis.at - point[0], point[1])
    return (point[0], 2 * axis.at - point[1])


def _mirrored(ring: Ring, axis: Axis) -> Ring:
    return [_reflect(p, axis) for p in reversed(ring)]


def _side(point: Point, axis: Axis) -> float:
    return point[axis.dim] - axis.at


def _half(ring: Ring, axis: Axis, keep: float) -> list[Point] | None:
    """The part of ``ring`` on the ``keep`` side (+1 or -1) of the axis, as the open path
    from one crossing to the other; None unless the ring crosses the axis exactly twice."""
    n = len(ring)
    path: list[Point] = []
    crossings = 0
    start = None
    for i in range(n):
        if keep * _side(ring[i], axis) <= 0 and keep * _side(ring[(i + 1) % n], axis) > 0:
            start = i
            crossings += 1
    if crossings != 1 or start is None:
        return None
    for step in range(n + 1):
        a, b = ring[(start + step) % n], ring[(start + step + 1) % n]
        sa, sb = keep * _side(a, axis), keep * _side(b, axis)
        if sa > 0:
            path.append(a)
        if (sa > 0) != (sb > 0):
            t = sa / (sa - sb)
            meet = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            path.append(_on_axis(meet, axis))
            if sa > 0:
                return path
    return None


def _on_axis(point: Point, axis: Axis) -> Point:
    return (axis.at, point[1]) if axis.dim == 0 else (point[0], axis.at)


def _whole(path: list[Point], axis: Axis) -> Ring:
    """A half path (crossing to crossing) and its mirror image as one ring."""
    back = [_reflect(p, axis) for p in reversed(path[1:-1])]
    return [*path, *back]


def _score(ring: Ring, grid: Grid) -> int:
    cells = covered([ring], grid)
    return int((cells & grid.mask).sum()) - int((cells & ~grid.mask).sum())


def symmetrise(rings: list[Ring], grid: Grid, axis: Axis, min_edge: float) -> list[Ring]:
    """``rings`` made exactly symmetric about ``axis``: a ring across it keeps its better half
    and that half's mirror image, a ring on one side takes its partner's mirror image."""
    out = list(rings)
    eps = grid.cell / 4
    done: set[int] = set()
    for i, ring in enumerate(out):
        if i in done:
            continue
        sides = [_side(p, axis) for p in ring]
        if min(sides) < -eps and max(sides) > eps:
            options: list[tuple[int, Ring]] = []
            for keep in (1.0, -1.0):
                path = _half(ring, axis, keep)
                if path is not None:
                    whole = tidy(_whole(path, axis), min_edge)
                    options.append((_score(whole, grid), whole))
            if options:
                out[i] = max(options, key=lambda o: o[0])[1]
            done.add(i)
            continue
        mirror = _mirrored(ring, axis)
        target = covered([mirror], grid)
        partner = max(
            (j for j in range(len(out)) if j != i and j not in done),
            key=lambda j: iou(target, covered([out[j]], grid)),
            default=None,
        )
        if partner is not None and iou(target, covered([out[partner]], grid)) > 0.5:
            out[partner] = mirror
            done.update((i, partner))
    return out
