"""``MaxZRaster``'s scan and fold, and a placement's faces, for numba.

``scan`` tests the sample points ``MaxZRaster._scan`` tests, with the same float operations in
the same order and precision, and counts its candidates as numpy buffers them, so every fold
falls where numpy's does. A candidate goes straight into the open fold: per texel the highest
so far, the later one on a tie, NaN once any is NaN, which is the lexsort's take-last. ``commit``
closes the fold as ``fold_heights`` does. ``band_faces`` and ``extent`` are
``terrain.maxz.faces``' numpy, triangle by triangle. Imported only when
``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.maxz_raster import MAX_SPAN, WIDE_TILES
from mapgen.jit import helper, kernel
from satisfactory_mcp.core.arrays import F32Grid, I32Grid, I64Grid, U8Grid, U16Grid

__all__ = ["band_faces", "commit", "extent", "scan"]

#: ``astype(np.int32)`` of a float outside int32, or NaN, on x86.
_INT32_LOW = -(2**31)
_INT32_END = 2.0**31

_Coords: TypeAlias = NDArray[np.floating]
#: The planes a raster folds into: height, source, and the ceiling (empty when it has none).
Planes: TypeAlias = tuple[F32Grid, U16Grid, F32Grid]
#: The open fold: height, source and mark per texel, the texels marked, and the counts
#: ``[candidates since the last fold, texels marked]``.
Fold: TypeAlias = tuple[F32Grid, U16Grid, U8Grid, I32Grid, I64Grid]
#: ``(width, height, row0)`` of the raster.
Grid: TypeAlias = tuple[int, int, int]
#: A mesh's triangles, the rows of them drawn (every row when the flag says so), and the
#: vertices an instance has.
Mesh: TypeAlias = tuple[I64Grid, I32Grid, bool, int]
#: Per sample column of a triangle: its texel and its two terms of the numerators.
Scratch: TypeAlias = tuple[I64Grid, _Coords, _Coords]
#: Representable values ``_threshold`` steps through before it gives up its test.
_STEPS = 64


@helper
def _int32(value: np.floating) -> int:
    if not (value >= -_INT32_END and value < _INT32_END):
        return _INT32_LOW
    return int(value)


@helper
def _wrap32(value: int) -> int:
    return ((value + 2**31) & 0xFFFFFFFF) - 2**31


@helper
def _insert(texel: int, height: np.float32, source: np.uint16, fold: Fold, marked: int) -> int:
    fz, fs, mark, touched, _counts = fold
    if mark[texel] == 0:
        mark[texel] = 1
        fz[texel], fs[texel] = height, source
        touched[marked] = texel
        return marked + 1
    now = fz[texel]
    if not np.isnan(now) and (np.isnan(height) or height >= now):
        fz[texel], fs[texel] = height, source
    return marked


@helper
def _close(planes: Planes, fold: Fold, marked: int) -> int:
    z, src = planes[0], planes[1]
    fz, fs, mark, touched, _counts = fold
    for k in range(marked):
        texel = touched[k]
        mark[texel] = 0
        if fz[texel] > z[texel]:
            z[texel], src[texel] = fz[texel], fs[texel]
    return 0


@kernel
def commit(planes: Planes, fold: Fold) -> None:
    """``MaxZRaster.fold_heights``: the open fold into the planes."""
    counts = fold[4]
    counts[1] = _close(planes, fold, counts[1])
    counts[0] = 0


@helper
def _corners(verts: _Coords, mesh: Mesh, k: int, frame: _Coords) -> tuple[np.floating, ...]:
    """Triangle ``k`` of the instances: x and y in texels, z in cm, for each corner."""
    tris, rows, every, stride = mesh
    count = tris.shape[0] if every else rows.shape[0]
    base = (k // count) * stride
    t = k % count if every else rows[k % count]
    a, b, c = base + tris[t, 0], base + tris[t, 1], base + tris[t, 2]
    ox, oy, step = frame[0], frame[1], frame[2]
    return (
        (verts[a, 0] - ox) / step,
        (verts[a, 1] - oy) / step,
        verts[a, 2],
        (verts[b, 0] - ox) / step,
        (verts[b, 1] - oy) / step,
        verts[b, 2],
        (verts[c, 0] - ox) / step,
        (verts[c, 1] - oy) / step,
        verts[c, 2],
    )


@helper
def _box(tri: tuple[np.floating, ...], half: np.floating) -> tuple[np.floating, ...]:
    """``add``'s bounding box ``(col0, row0, col1, row1)`` and its span, or a span of 0 for
    the triangles numpy's int32 cast drops."""
    ax, ay, _az, bx, by, _bz, cx, cy, _cz = tri
    c0 = np.floor(min(ax, bx, cx) - half)
    c1 = np.ceil(max(ax, bx, cx) + half)
    r0 = np.floor(min(ay, by, cy) - half)
    r1 = np.ceil(max(ay, by, cy) + half)
    span = max(c1 - c0, r1 - r0)
    finite = not (np.isnan(ax) or np.isnan(bx) or np.isnan(cx))
    finite = finite and not (np.isnan(ay) or np.isnan(by) or np.isnan(cy))
    if not (finite and span < _INT32_END):
        span = half - half
    return c0, r0, c1, r1, span


@helper
def _threshold(
    den: np.floating, tol: np.floating, one: np.floating
) -> tuple[np.floating, np.floating]:
    """``(sign, t)``: ``n / den >= tol`` only where ``sign * n >= t``, for every ``n``.

    Division rounds monotonically, so the test is a threshold on the numerator, the least
    one that passes: found by stepping from ``tol * den`` one representable value at a
    time. ``t`` is minus infinity, no test at all, where the steps run out.
    """
    inf = one / (one - one)
    sign = one if den > 0 else -one
    if not np.isfinite(den):
        return sign, -inf
    t = sign * (tol * den)
    if (sign * t) / den >= tol:
        for _ in range(_STEPS):
            below = np.nextafter(t, -inf)
            if not (sign * below) / den >= tol:
                return sign, t
            t = below
        return sign, -inf
    for _ in range(_STEPS):
        t = np.nextafter(t, inf)
        if (sign * t) / den >= tol:
            return sign, t
    return sign, -inf


@helper
def _columns(
    tri: tuple[np.floating, ...],
    col0: np.floating,
    count: int,
    frame: _Coords,
    width: int,
    sign: np.floating,
    scratch: Scratch,
) -> tuple[int, int]:
    """Into ``scratch``, each sample column's texel and its terms of the two numerators,
    ``(by - cy) * (gx - cx)`` and ``(cy - ay) * (gx - cx)``, times ``sign``; the first and the
    end of the columns on the raster, which are one run."""
    _ax, ay, _az, _bx, by, _bz, cx, cy, _cz = tri
    cols, across_1, across_2 = scratch
    first, end = count, 0
    for c in range(count):
        gx = (col0 + np.float32(c)) + frame[3]
        cols[c] = _int32(np.floor(gx))
        if 0 <= cols[c] < width:
            first, end = min(first, c), c + 1
        across_1[c] = sign * ((by - cy) * (gx - cx))
        across_2[c] = sign * ((cy - ay) * (gx - cx))
    return first, end


@helper
def _scan_box(
    tri: tuple[np.floating, ...],
    corner: tuple[np.floating, np.floating],
    size: tuple[int, int],
    frame: _Coords,
    grid: Grid,
    source: np.uint16,
    planes: Planes,
    fold: Fold,
    counts: tuple[int, int],
    scratch: Scratch,
) -> tuple[int, int]:
    """``MaxZRaster._scan`` of one triangle over ``size + 1`` sample points from ``corner``.

    ``l1 >= tol`` and ``l2 >= tol`` are first tested on their numerators (``_threshold``),
    so only the samples that may pass both are divided and tested as numpy tests them. A
    numerator times the sign is the sum of its terms times the sign: negation is exact.
    """
    ax, ay, az, bx, by, bz, cx, cy, cz = tri
    col0, row0 = corner
    sample, one, tol, tiny = frame[3], frame[5], frame[6], frame[7]
    width, height, first_row = grid
    ceiling = planes[2]
    pending, marked = counts
    cols, across_1, across_2 = scratch
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(den) < tiny:
        den = tiny
    sign, least = _threshold(den, tol, one)
    first, end = _columns(tri, col0, size[0] + 1, frame, width, sign, scratch)
    for r in range(size[1] + 1):
        gy = (row0 + np.float32(r)) + sample
        row = _wrap32(_int32(np.floor(gy)) - first_row)
        if row < 0 or row >= height:
            continue
        down_1 = sign * ((cx - bx) * (gy - cy))
        down_2 = sign * ((ax - cx) * (gy - cy))
        for c in range(first, end):
            signed_1 = across_1[c] + down_1
            signed_2 = across_2[c] + down_2
            if signed_1 >= least and signed_2 >= least:
                l1 = (sign * signed_1) / den
                l2 = (sign * signed_2) / den
                l3 = (one - l1) - l2
                if l1 >= tol and l2 >= tol and l3 >= tol:
                    pending += 1
                    z = np.float32((l1 * az + l2 * bz) + l3 * cz)
                    texel = row * width + cols[c]
                    if ceiling.shape[0] == 0 or z < ceiling[texel]:
                        marked = _insert(texel, z, source, fold, marked)
    return pending, marked


@helper
def _bucket(span: int) -> int:
    size = 1
    while size < span:
        size *= 2
    return size


@kernel
def scan(
    verts: _Coords,
    mesh: Mesh,
    frame: _Coords,
    grid: Grid,
    source: np.uint16,
    planes: Planes,
    fold: Fold,
    flush: int,
) -> None:
    """``MaxZRaster.add_indexed`` of ``mesh``'s triangles on each instance in ``verts``.

    ``frame`` is ``(origin x, origin y, scale, sample, 0.5, 1, -1e-6, 1e-12)`` in the vertices'
    precision: numpy's operands once its weak Python floats are cast.
    """
    width, height, first_row = grid
    counts = fold[4]
    pending, marked = counts[0], counts[1]
    tris, rows, every, stride = mesh
    instances = verts.shape[0] // stride if stride else 0
    triangles = instances * (tris.shape[0] if every else rows.shape[0])
    wide = np.empty(triangles, np.int64)
    corner = np.empty_like(frame[:2])
    scratch = (
        np.empty(MAX_SPAN + 1, np.int64),
        np.empty(MAX_SPAN + 1, frame.dtype),
        np.empty(MAX_SPAN + 1, frame.dtype),
    )
    n_wide = 0
    for k in range(triangles):
        tri = _corners(verts, mesh, k, frame)
        c0, r0, _c1, _r1, span = _box(tri, frame[4])
        if span > MAX_SPAN:
            wide[n_wide] = k
            n_wide += 1
        elif span > 0:
            size = _bucket(int(span))
            got = _scan_box(
                tri,
                (c0, r0),
                (size, size),
                frame,
                grid,
                source,
                planes,
                fold,
                (pending, marked),
                scratch,
            )
            pending, marked = got
    tile_w, tile_h = min(MAX_SPAN, width - 1), min(MAX_SPAN, height - 1)
    tiles = 0
    for w in range(n_wide):
        tri = _corners(verts, mesh, wide[w], frame)
        c0, r0, c1, r1, _span = _box(tri, frame[4])
        first_c, first_r = max(float(c0), 0.0), max(float(r0), float(first_row))
        last_c = min(float(c1), float(width - 1))
        last_r = min(float(r1), float(first_row + height - 1))
        if not (first_c <= last_c and first_r <= last_r):
            continue
        for tile_r in range(int(first_r), int(last_r) + 1, tile_h + 1):
            for tile_c in range(int(first_c), int(last_c) + 1, tile_w + 1):
                corner[0], corner[1] = tile_c, tile_r
                got = _scan_box(
                    tri,
                    (corner[0], corner[1]),
                    (tile_w, tile_h),
                    frame,
                    grid,
                    source,
                    planes,
                    fold,
                    (pending, marked),
                    scratch,
                )
                pending, marked = got
                tiles += 1
                if tiles % WIDE_TILES == 0 and pending > flush:
                    pending, marked = 0, _close(planes, fold, marked)
    if pending > flush:
        pending, marked = 0, _close(planes, fold, marked)
    counts[0], counts[1] = pending, marked


@kernel
def band_faces(
    world: _Coords, tris: I64Grid, bounds: F32Grid, facing: int, rise: float
) -> tuple[I32Grid, I32Grid]:
    """``faces.band_faces`` as row numbers of ``tris``: those reaching ``bounds`` (low and high
    Y) that face up, and those facing down whose top clears the lowest vertex by ``rise``
    (NaN: none)."""
    up = np.empty(tris.shape[0], np.int32)
    down = np.empty(tris.shape[0], np.int32)
    n_up = n_down = 0
    sign = np.float32(facing)
    floor = np.float32(np.nan)
    if facing != 0 and not np.isnan(rise) and world.shape[0]:
        lowest = world[0, 2]
        for v in range(world.shape[0]):
            if np.isnan(world[v, 2]) or world[v, 2] < lowest:
                lowest = world[v, 2]
                if np.isnan(lowest):
                    break
        floor = np.float32(np.float64(lowest) + rise)
    for t in range(tris.shape[0]):
        a, b, c = tris[t, 0], tris[t, 1], tris[t, 2]
        ya, yb, yc = world[a, 1], world[b, 1], world[c, 1]
        if np.isnan(ya) or np.isnan(yb) or np.isnan(yc):
            continue
        if not (max(ya, yb, yc) >= bounds[0] and min(ya, yb, yc) <= bounds[1]):
            continue
        if facing == 0:
            up[n_up] = t
            n_up += 1
            continue
        ux, uy = world[b, 0] - world[a, 0], yb - ya
        vx, vy = world[c, 0] - world[a, 0], yc - ya
        normal = (ux * vy - uy * vx) * sign
        if normal > 0:
            up[n_up] = t
            n_up += 1
        elif normal < 0:
            za, zb, zc = world[a, 2], world[b, 2], world[c, 2]
            if not (np.isnan(za) or np.isnan(zb) or np.isnan(zc)) and max(za, zb, zc) > floor:
                down[n_down] = t
                n_down += 1
    return up[:n_up], down[:n_down]


@kernel
def extent(world: _Coords, tris: I64Grid, rows: I32Grid, frame: _Coords) -> _Coords:
    """``faces.extent``: the lowest and highest x, then y, of the corners of ``tris``' ``rows``
    in texels, NaN when any is."""
    out = np.empty(4, world.dtype)
    out[0] = out[2] = np.inf
    out[1] = out[3] = -np.inf
    for t in rows:
        for k in range(3):
            v = tris[t, k]
            for axis in range(2):
                at = (world[v, axis] - frame[axis]) / frame[2]
                low, high = out[2 * axis], out[2 * axis + 1]
                if np.isnan(at) or np.isnan(low):
                    out[2 * axis] = out[2 * axis + 1] = np.nan
                else:
                    out[2 * axis], out[2 * axis + 1] = min(low, at), max(high, at)
    return out
