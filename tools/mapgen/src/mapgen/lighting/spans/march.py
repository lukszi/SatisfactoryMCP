"""Spans: geometry with open space beneath it, as the horizon march and the sky view see it.

An arch, a rock overhang or a tree crown stands over what is beneath it with an underside and
a top. Along a direction the ground blocks as ``horizon`` marches it; a span sample blocks only
the tangents between its underside and its top as the receiver sees them. Where that reaches
down to what already blocks, it merges into the horizon; otherwise it is a band above it, and
light passes beneath. One band is kept per direction. The loops run as numba kernels unless
``mapgen.jit`` selects this numpy, their reference. docs/map/light-and-crowns.md section 29,
"Arches as spans".
"""

from __future__ import annotations

import functools
from functools import partial
from types import ModuleType
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from numpy.typing import NDArray

from mapgen.jit import gpu_on, kernels_on
from mapgen.lighting.horizon import (
    FADE_M,
    FINE_M,
    HORIZON_DIRS,
    SKY_DIRS,
    SKY_RADIUS_M,
    SKY_STEPS,
    STRIP_ROWS,
    Fade,
    Sampler,
    bilinear,
    fade_weight,
    kernel_offsets,
    march_steps,
    step_lengths,
)
from mapgen.lighting.sun import game_sun
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid
from satisfactory_mcp.core.jsontypes import JsonObject

if TYPE_CHECKING:
    from mapgen.lighting.kernels import Offsets

__all__ = [
    "BAND_GAP_DEG",
    "OFF_PATH_EL_DEG",
    "Bands",
    "KernelSteps",
    "SpanRuns",
    "SpanStep",
    "SpanSurface",
    "as_degrees",
    "band_target",
    "kernel_steps",
    "march_spans",
    "path_elevation",
    "rows_with_spans",
    "sky_view_spans",
    "span_block",
    "span_steps",
    "span_surface",
    "step_reach",
]

#: The sun disc, degrees: a gap narrower than it between two samples is no gap, and a band
#: ending within it above the horizon joins the horizon.
BAND_GAP_DEG = 6.0

#: The elevation a band is kept nearest where the sun's path never reaches a direction.
OFF_PATH_EL_DEG = 45.0


class SpanRuns(NamedTuple):
    """Per row, the runs of columns where a sample's four pixels (``_quad``) hold a span:
    ``start[row[r] : row[r + 1]]`` to ``end`` (exclusive). The kernels visit only these."""

    row: I64Grid
    start: I64Grid
    end: I64Grid


class SpanSurface(NamedTuple):
    """What a span march reads, one grid with a halo, metres: the receivers, what blocks as
    ground, the spans' underside and top (NaN where none), the tops a span that merges into
    the horizon blocks with, and the runs of columns with a span."""

    z: F32Grid
    solid: F32Grid
    lo: F32Grid
    hi: F32Grid
    tops: F32Grid
    runs: SpanRuns


class SpanStep(NamedTuple):
    """A step of a span march: ``horizon.march_steps``' sampler, offsets and scale, the stretch
    of the ray it stands for in metres, and its fade weight."""

    sample: Sampler
    oy: float
    ox: float
    scale: np.float32
    near_m: np.float32
    far_m: np.float32
    weight: np.float32


class Bands(NamedTuple):
    """A direction's march: the horizon, the band above it (NaN where none), in degrees, and
    where a span was in reach at all."""

    horizon: F32Grid
    lo: F32Grid
    hi: F32Grid
    seen: BoolMask


def span_surface(
    z: F32Grid, solid: F32Grid, lo: F32Grid, hi: F32Grid, ground: F32Grid | None = None
) -> SpanSurface:
    """A ``SpanSurface``; the tops are the spans' top over ``ground``, as it is drawn, which is
    the solid surface unless the spans are marched alone over open ground."""
    under = solid if ground is None else ground
    tops = np.where(np.isfinite(hi), np.fmax(hi, under), under).astype(np.float32)
    plane = partial(np.ascontiguousarray, dtype=np.float32)
    return SpanSurface(plane(z), plane(solid), plane(lo), plane(hi), tops, _runs(lo))


def _runs(lo: F32Grid) -> SpanRuns:
    """The ``SpanRuns`` of an underside plane."""
    f = np.isfinite(lo)
    quad = (f[:-1, :-1] | f[:-1, 1:]) | (f[1:, :-1] | f[1:, 1:])
    edges = np.diff(np.pad(quad, ((0, 0), (1, 1))).view(np.int8), axis=1)
    rows, start = np.nonzero(edges == 1)
    end = np.nonzero(edges == -1)[1]
    per_row = np.bincount(rows, minlength=quad.shape[0])
    row = np.concatenate([[0], np.cumsum(per_row)]).astype(np.int64)
    return SpanRuns(row, start.astype(np.int64), end.astype(np.int64))


def _planes(surface: SpanSurface) -> tuple[F32Grid, F32Grid, F32Grid, F32Grid, F32Grid]:
    """A ``SpanSurface`` as the kernels take it: a plain tuple of its five planes."""
    return surface.z, surface.solid, surface.lo, surface.hi, surface.tops


def _run_arrays(surface: SpanSurface) -> tuple[I64Grid, I64Grid, I64Grid]:
    """A surface's ``SpanRuns`` as the kernels take them: a plain tuple."""
    runs = surface.runs
    return runs.row, runs.start, runs.end


def span_block() -> JsonObject:
    """The span model's constants, as the light axis records them."""
    return {
        "bands": "one per direction: the band nearest the sun path's elevation there",
        "gap_deg": BAND_GAP_DEG,
        "atlas": "a band folded into its direction's cell at path_el_deg; the bake reads it whole",
        "path_el_deg": [
            round(path_elevation(k * 360.0 / HORIZON_DIRS), 2) for k in range(HORIZON_DIRS)
        ],
    }


@functools.cache
def path_elevation(az_deg: float) -> float:
    """The sun path's elevation where its azimuth is ``az_deg``, or ``OFF_PATH_EL_DEG``."""
    hours = np.arange(0.0, 24.0, 0.005)
    path = np.array([game_sun(float(h)) for h in hours])
    off = np.abs((path[:, 0] - az_deg + 180.0) % 360.0 - 180.0)
    near = np.flatnonzero((path[:, 1] > 0) & (off < 0.5))
    if not near.size:
        return OFF_PATH_EL_DEG
    return float(path[near[np.argmin(off[near])], 1])


def band_target(az_deg: float) -> tuple[np.float32, np.float32]:
    """The tangent a band is kept nearest at ``az_deg``, and the sun disc's width there."""
    te = np.float32(np.tan(np.radians(path_elevation(az_deg))))
    return te, np.float32((1.0 + te * te) * np.radians(BAND_GAP_DEG))


def _stretches(ts: NDArray[np.floating]) -> tuple[F64Grid, F64Grid]:
    """Per step, the ray from halfway back to the previous step to halfway on to the next."""
    t = np.asarray(ts, np.float64)
    prev = np.concatenate([[0.0], t[:-1]])
    after = np.concatenate([t[1:], [t[-1] + (t[-1] - prev[-1])]])
    return (t + prev) / 2, (t + after) / 2


def span_steps(az_deg: float, spacing_m: float, fade: Fade) -> list[SpanStep]:
    """``horizon.march_steps`` toward ``az_deg``, each with its stretch and fade weight."""
    ts = step_lengths(fade[1] / spacing_m, FINE_M / spacing_m)
    near, far = _stretches(np.array(ts, np.float64))
    out: list[SpanStep] = []
    for k, (sample, oy, ox, scale) in enumerate(march_steps(az_deg, spacing_m, fade)):
        weight = np.float32(fade_weight(ts[k] * spacing_m, fade))
        out.append(
            SpanStep(
                sample,
                oy,
                ox,
                scale,
                np.float32(near[k] * spacing_m),
                np.float32(far[k] * spacing_m),
                weight,
            )
        )
    return out


def _quad(
    surface: SpanSurface, rows: tuple[int, int], cols: tuple[int, int], oy: float, ox: float
) -> tuple[F32Grid, F32Grid]:
    """Of the four pixels around the sample, as ``bilinear`` reads them, the span whose top is
    highest, reaching down over those of the others it overlaps: its underside and top, NaN
    where none floats. A thin span is never stepped over on a diagonal, one object's pixels
    are read whole, and two spans one above the other are never read as one."""
    (a, b), (c0, c1) = rows, cols
    iy, ix = int(np.floor(oy)), int(np.floor(ox))
    corners = ((0, 0), (0, 1), (1, 0), (1, 1))

    def at(p: F32Grid, corner: tuple[int, int]) -> F32Grid:
        dy, dx = corner
        return p[a + iy + dy : b + iy + dy, c0 + ix + dx : c1 + ix + dx]

    low, high = at(surface.lo, corners[0]), at(surface.hi, corners[0])
    for corner in corners[1:]:
        top = at(surface.hi, corner)
        take = (top > high) | (np.isnan(high) & ~np.isnan(top))
        low = np.where(take, at(surface.lo, corner), low)
        high = np.where(take, top, high)
    for corner in corners:
        under, top = at(surface.lo, corner), at(surface.hi, corner)
        np.fmin(low, np.where((under <= high) & (top >= low), under, np.nan), out=low)
    return low, high


def _tangents(
    lo: F32Grid, hi: F32Grid, near: F32Grid, near_m: np.float32, far_m: np.float32
) -> tuple[F32Grid, F32Grid]:
    """The tangents a span sample blocks over the whole stretch of ray it stands for: its
    underside seen from the stretch's far end when above the receiver, its top from the near."""
    dl, dh = lo - near, hi - near
    tl = np.where(dl > 0, dl / far_m, dl / near_m).astype(np.float32)
    th = np.where(dh > 0, dh / near_m, dh / far_m).astype(np.float32)
    return tl, th


def _distance(lo: F32Grid, hi: F32Grid, t: np.float32) -> F32Grid:
    """How far tangent ``t`` is from the band ``[lo, hi]``: 0 inside it."""
    return np.where(t < lo, lo - t, np.where(t > hi, t - hi, np.float32(0.0))).astype(np.float32)


class _Strip(NamedTuple):
    """One strip's rows of the core and the arrays the march raises in place over them."""

    rows: tuple[int, int]
    cols: tuple[int, int]
    near: F32Grid
    best: F32Grid
    lo: F32Grid
    hi: F32Grid
    seen: BoolMask


def _span_step(
    surface: SpanSurface, strip: _Strip, step: SpanStep, target: tuple[np.float32, np.float32]
) -> None:
    """One step's span samples over a strip: merged into the horizon, or into the band."""
    (a, b), (c0, c1) = strip.rows, strip.cols
    low, high = _quad(surface, strip.rows, strip.cols, step.oy, step.ox)
    found = np.isfinite(low)
    if not found.any():
        return
    tl, th = _tangents(low, high, strip.near, step.near_m, step.far_m)
    strip.seen[...] |= found
    best = strip.best
    merge = found & (tl * step.weight <= best)
    if merge.any():
        exact = (
            step.sample(surface.tops, a, b, c0, c1, step.oy, step.ox) - strip.near
        ) * step.scale
        np.maximum(best, np.where(merge, exact, best), out=best)
    free = found & ~merge
    if not free.any():
        return
    te, gap = target
    sl, sh = (tl * step.weight).astype(np.float32), (th * step.weight).astype(np.float32)
    have = np.isfinite(strip.lo)
    overlap = free & have & (sl <= strip.hi + gap) & (sh >= strip.lo - gap)
    nearer = _distance(sl, sh, te) < _distance(strip.lo, strip.hi, te)
    take = free & ~overlap & (~have | nearer)
    strip.lo[...] = np.where(overlap, np.minimum(strip.lo, sl), np.where(take, sl, strip.lo))
    strip.hi[...] = np.where(overlap, np.maximum(strip.hi, sh), np.where(take, sh, strip.hi))


def rows_with_spans(lo: F32Grid) -> NDArray[np.int64]:
    """Per row, how many rows up to it hold a span: a row range's count in two reads."""
    return np.concatenate([[0], np.cumsum(np.isfinite(lo).any(axis=1))]).astype(np.int64)


def as_degrees(t: NDArray[np.floating]) -> F32Grid:
    """Tangents as degrees: numpy's arctangent, which every path takes on the host."""
    return np.degrees(np.arctan(t)).astype(np.float32)


def _finish(best: F32Grid, lo: F32Grid, hi: F32Grid) -> tuple[F32Grid, F32Grid, F32Grid]:
    """A band reaching down to the horizon joins it; the rest float, however narrow the sky
    beneath them, which the shade reads as the sun disc's share. Degrees."""
    late = np.isfinite(lo) & (lo <= best)
    np.maximum(best, np.where(late, hi, best), out=best)
    floating = np.isfinite(lo) & ~late
    nan = np.float32(np.nan)
    band_lo = np.where(floating, as_degrees(np.where(floating, lo, 0)), nan).astype(np.float32)
    band_hi = np.where(floating, as_degrees(np.where(floating, hi, 0)), nan).astype(np.float32)
    return as_degrees(best), band_lo, band_hi


def march_spans(
    surface: SpanSurface, halo: int, az_deg: float, spacing_m: float, fade: Fade = FADE_M
) -> Bands:
    """The faded horizon toward ``az_deg`` over ``surface.solid`` with the spans in it, and the
    one band floating above it that is nearest the sun path's elevation there.

    Where no span is in reach this is ``horizon.march_horizon`` over the solid surface, to the
    bit; a strip with none skips the span work.
    """
    z = surface.z
    r0, r1, c0, c1 = halo, z.shape[0] - halo, halo, z.shape[1] - halo
    zc = z[r0:r1, c0:c1]
    best = np.zeros(zc.shape, np.float32)
    lo = np.full(zc.shape, np.inf, np.float32)
    hi = np.full(zc.shape, -np.inf, np.float32)
    seen = np.zeros(zc.shape, bool)
    steps = span_steps(az_deg, spacing_m, fade)
    target = band_target(az_deg)
    reach = step_reach(steps)
    rows = rows_with_spans(surface.lo)
    if kernels_on():
        _compiled_march(surface, halo, steps, target, (best, lo, hi, seen), (rows, reach))
    else:
        for a in range(r0, r1, STRIP_ROWS):
            b = min(a + STRIP_ROWS, r1)
            core = slice(a - r0, b - r0)
            strip = _Strip((a, b), (c0, c1), zc[core], best[core], lo[core], hi[core], seen[core])
            spans_near = rows[min(b + reach, len(rows) - 1)] > rows[max(a - reach, 0)]
            rise = np.empty(strip.near.shape, np.float32)
            for step in steps:
                np.subtract(
                    step.sample(surface.solid, a, b, c0, c1, step.oy, step.ox), strip.near, out=rise
                )
                rise *= step.scale
                np.maximum(strip.best, rise, out=strip.best)
                if spans_near:
                    _span_step(surface, strip, step, target)
    horizon, band_lo, band_hi = _finish(best, lo, hi)
    return Bands(horizon, band_lo, band_hi, seen)


def _compiled_march(
    surface: SpanSurface,
    halo: int,
    steps: list[SpanStep],
    target: tuple[np.float32, np.float32],
    out: tuple[F32Grid, F32Grid, F32Grid, BoolMask],
    rows: tuple[NDArray[np.int64], int],
) -> None:
    """``march_spans``' loop as a kernel, fed the steps it works out; on the GPU when the
    switch says so."""
    fed = kernel_steps(steps, halo)
    _span_kernels().march_spans(
        _planes(surface), halo, *fed, target, out, rows, _run_arrays(surface)
    )


class KernelSteps(NamedTuple):
    """``span_steps`` as the kernels take them: which sample bilinearly, the offsets
    (``horizon.kernel_offsets``), each sample's quad offsets, and per step its scale,
    stretch near and far end, and fade weight."""

    smooth: BoolMask
    offsets: Offsets
    quads: tuple[I64Grid, I64Grid]
    per_step: F32Grid


def kernel_steps(steps: list[SpanStep], halo: int) -> KernelSteps:
    """``steps`` as the kernels take them."""
    smooth = [step.sample is bilinear for step in steps]
    oy, ox = [s.oy for s in steps], [s.ox for s in steps]
    offsets = kernel_offsets(oy, ox, smooth, (len(steps),), halo)
    per_step = np.array([[s.scale, s.near_m, s.far_m, s.weight] for s in steps], np.float32)
    return KernelSteps(np.array(smooth), offsets, _quads(oy, ox), per_step)


def step_reach(steps: list[SpanStep]) -> int:
    """The rows a step reads past its receiver's, with the quad's: how near a span must be."""
    return int(np.ceil(max((abs(s.oy) for s in steps), default=0.0))) + 2


def _span_kernels() -> ModuleType:
    """The span kernels the switch selects: CUDA's or numba's, imported on first use."""
    if gpu_on():
        from mapgen.lighting.spans import gpu

        return gpu
    from mapgen.lighting.spans import kernels

    return kernels


def _quads(oy: list[float], ox: list[float]) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Each sample's whole-pixel offsets to the four pixels ``_quad`` reads."""
    return (
        np.floor(np.array(oy, np.float64)).astype(np.int64),
        np.floor(np.array(ox, np.float64)).astype(np.int64),
    )


def sky_view_spans(
    surface: SpanSurface, halo: int, spacing_m: float, radius_m: float = SKY_RADIUS_M
) -> F32Grid:
    """``horizon.sky_view`` over the solid surface with the spans: a span that reaches down to
    the horizon raises it, one floating above it costs ``sin(hi) - sin(lo)`` of the sky.

    Where no span is in reach this is ``sky_view``, to the bit.
    """
    z = surface.z
    r0, r1, c0, c1 = halo, z.shape[0] - halo, halo, z.shape[1] - halo
    out = np.ones((r1 - r0, c1 - c0), np.float32)
    reach = radius_m / spacing_m
    if reach < 1.0:
        return out
    steps = np.geomspace(1.0, reach, SKY_STEPS)
    near_t, far_t = _stretches(steps)
    span = int(np.ceil(reach)) + 2
    rows = rows_with_spans(surface.lo)
    if kernels_on():
        thetas = [2 * np.pi * k / SKY_DIRS for k in range(SKY_DIRS)]
        oy = [np.sin(theta) * t for theta in thetas for t in steps]
        ox = [np.cos(theta) * t for theta in thetas for t in steps]
        shape = (SKY_DIRS, len(steps))
        offsets = kernel_offsets(oy, ox, [True] * len(oy), shape, halo)
        per_step = np.array(
            [
                [
                    np.float32(1.0 / (t * spacing_m)),
                    np.float32(n * spacing_m),
                    np.float32(f * spacing_m),
                ]
                for t, n, f in zip(steps, near_t, far_t, strict=True)
            ],
            np.float32,
        )
        qy, qx = (q.reshape(shape) for q in _quads(oy, ox))
        _span_kernels().sky_view_spans(
            _planes(surface),
            halo,
            offsets,
            (qy, qx),
            per_step,
            out,
            (rows, span),
            _run_arrays(surface),
        )
        return out
    for a in range(r0, r1, STRIP_ROWS):
        b = min(a + STRIP_ROWS, r1)
        spans_near = rows[min(b + span, len(rows) - 1)] > rows[max(a - span, 0)]
        out[a - r0 : b - r0] = _sky_strip(
            surface, (a, b), (c0, c1), spacing_m, (steps, near_t, far_t), spans_near
        )
    return out


def _sky_strip(
    surface: SpanSurface,
    rows: tuple[int, int],
    cols: tuple[int, int],
    spacing_m: float,
    steps: tuple[F64Grid, F64Grid, F64Grid],
    spans_near: bool,
) -> F32Grid:
    """One strip of ``sky_view_spans``."""
    (a, b), (c0, c1) = rows, cols
    zc = surface.z[a:b, c0:c1]
    acc = np.zeros(zc.shape, np.float32)
    for k in range(SKY_DIRS):
        theta = 2 * np.pi * k / SKY_DIRS
        best = np.zeros(zc.shape, np.float32)
        lo = np.full(zc.shape, np.inf, np.float32)
        hi = np.full(zc.shape, -np.inf, np.float32)
        for t, near_t, far_t in zip(*steps, strict=True):
            oy, ox = np.sin(theta) * t, np.cos(theta) * t
            scale = np.float32(1.0 / (t * spacing_m))
            zz = bilinear(surface.solid, a, b, c0, c1, oy, ox)
            np.maximum(best, (zz - zc) * scale, out=best)
            if not spans_near:
                continue
            low, high = _quad(surface, rows, cols, oy, ox)
            found = np.isfinite(low)
            if not found.any():
                continue
            near_m, far_m = np.float32(near_t * spacing_m), np.float32(far_t * spacing_m)
            tl, th = _tangents(low, high, zc, near_m, far_m)
            merge = found & (tl <= best)
            if merge.any():
                exact = (bilinear(surface.tops, a, b, c0, c1, oy, ox) - zc) * scale
                np.maximum(best, np.where(merge, exact, best), out=best)
            free = found & ~merge
            np.minimum(lo, np.where(free, tl, lo), out=lo)
            np.maximum(hi, np.where(free, th, hi), out=hi)
        late = np.isfinite(lo) & (lo <= best)
        np.maximum(best, np.where(late, hi, best), out=best)
        acc += best / np.sqrt(1.0 + best * best)
        floating = np.isfinite(lo) & ~late
        if floating.any():
            lo_s, hi_s = np.where(floating, lo, 0), np.where(floating, hi, 0)
            band = hi_s / np.sqrt(1.0 + hi_s * hi_s) - lo_s / np.sqrt(1.0 + lo_s * lo_s)
            acc += np.where(floating, band, np.float32(0.0)).astype(np.float32)
    return (1.0 - acc / SKY_DIRS).astype(np.float32)
