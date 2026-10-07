"""Sun-independent light terms of a height surface: normals, sky view and faded horizons.

Rows run south and columns east; an azimuth is compass degrees from north. Why each constant
has its value: docs/spatial-and-map.md section 29. The march and the sky view run as numba
kernels, or CUDA ones, unless ``mapgen.jit`` selects this numpy, their reference
(docs/map/renders.md section 41). Geometry with open space beneath it is marched by
``lighting/spans.py`` on the same steps and samplers.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.jit import gpu_on, kernels_on
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid

if TYPE_CHECKING:
    from mapgen.lighting.kernels import Offsets

__all__ = [
    "BILINEAR_PX",
    "FADE_M",
    "FINE_M",
    "HORIZON_DIRS",
    "OCCLUDER_FADE_M",
    "SKY_DIRS",
    "SKY_RADIUS_M",
    "SKY_STEPS",
    "STEP_GROWTH",
    "STRIP_ROWS",
    "Fade",
    "Sampler",
    "Step",
    "bilinear",
    "crown_horizon",
    "crown_horizons",
    "crown_surface",
    "decode_horizon",
    "encode_horizon",
    "fade_weight",
    "faded_horizons",
    "horizon_reach_px",
    "kernel_offsets",
    "march_horizon",
    "march_steps",
    "nearest",
    "normals",
    "sky_view",
    "step_lengths",
]

#: Where a blocker counts fully, and where it stops counting, in metres.
Fade: TypeAlias = tuple[float, float]

#: ``z[r0 + oy : r1 + oy, c0 + ox : c1 + ox]``, read at a fractional offset.
Sampler: TypeAlias = Callable[[F32Grid, int, int, int, int, float, float], NDArray[np.floating]]

#: One step of the march: its sampler, its offset in rows and columns, its weight over distance.
Step: TypeAlias = tuple[Sampler, float, float, np.float32]

#: A float32 raster known to be 2-D, which is what lets numpy's stubs type its gradient.
_Plane: TypeAlias = np.ndarray[tuple[int, int], np.dtype[np.float32]]

#: Directions stored per pixel, evenly spaced from north.
HORIZON_DIRS = 32

#: A blocker counts fully up to the first distance and not at all past the second.
FADE_M: Fade = (40.0, 150.0)

#: Crowns are porous and their far shadow diffuse, so an occluder fades sooner than rock.
OCCLUDER_FADE_M: Fade = (25.0, 80.0)

#: Single-pixel steps up to this distance, then steps growing by STEP_GROWTH.
FINE_M = 44.0
STEP_GROWTH = 0.01

#: The sky-view term: radius, directions and geometric steps.
SKY_RADIUS_M = 10.0
SKY_DIRS = 16
SKY_STEPS = 14

#: Steps shorter than this many pixels sample bilinearly, longer ones the nearest pixel.
BILINEAR_PX = 16

#: Rows the march and the sky view finish before the next: a strip's arrays stay in cache.
STRIP_ROWS = 64


def fade_weight(d_m: float, fade: Fade = FADE_M) -> float:
    """How much of a blocker's height counts at ``d_m``: 1 near, 0 past the fade."""
    return float(np.clip((fade[1] - d_m) / (fade[1] - fade[0]), 0.0, 1.0))


def horizon_reach_px(spacing_m: float, fade: Fade = FADE_M) -> int:
    """How far a horizon looks, in pixels: the halo a block needs."""
    return int(np.ceil(fade[1] / spacing_m)) + 2


def step_lengths(max_px: float, fine_px: float, growth: float = STEP_GROWTH) -> list[int]:
    """Integer step lengths: every pixel to ``fine_px``, then growing geometrically."""
    out: list[int] = []
    t = 1.0
    while t <= max_px:
        out.append(round(t))
        t += 1.0 if t < fine_px else max(1.0, t * growth)
    return sorted(set(out))


def normals(z: NDArray[np.floating], spacing_m: float) -> tuple[F32Grid, F32Grid]:
    """East and south components of the unit normal, for ``z`` with a one-pixel margin.

    NaN is no data: beside it a pixel takes the difference toward its side that has a height,
    and a pixel with neither side, or no height of its own, stands flat.
    """
    plane: _Plane = np.asarray(z, np.float32)
    holes = np.isnan(plane)
    if holes.any():
        plane = np.asarray(np.where(holes, np.float32(0.0), plane), np.float32)
    d_south, d_east = np.gradient(plane, spacing_m)
    d_south, d_east = d_south[1:-1, 1:-1], d_east[1:-1, 1:-1]
    if holes.any():
        d_south = _beside_holes(plane, holes, 0, d_south, spacing_m)
        d_east = _beside_holes(plane, holes, 1, d_east, spacing_m)
    inv = 1.0 / np.sqrt(d_east * d_east + d_south * d_south + 1.0)
    return (-d_east * inv).astype(np.float32), (-d_south * inv).astype(np.float32)


def _beside_holes(
    plane: F32Grid, holes: BoolMask, axis: int, central: F32Grid, spacing_m: float
) -> F32Grid:
    """The core's slope along ``axis``: ``central`` where both neighbours have a height, else
    the one-sided difference toward the one that has, else 0, and 0 on a hole."""
    inner, before, after = slice(1, -1), slice(None, -2), slice(2, None)
    core = (inner, inner)
    back, ahead = (
        ((before, inner), (after, inner)) if axis == 0 else ((inner, before), (inner, after))
    )
    has_back, has_ahead, here = ~holes[back], ~holes[ahead], plane[core]
    step = np.float32(spacing_m)
    one_sided = np.where(
        has_ahead,
        (plane[ahead] - here) / step,
        np.where(has_back, (here - plane[back]) / step, np.float32(0.0)),
    )
    slope = np.where(has_back & has_ahead, central, one_sided)
    return np.where(holes[core], np.float32(0.0), slope).astype(np.float32)


def _split(offset: float) -> tuple[int, np.float32]:
    """A fractional offset as whole pixels and the float32 remainder ``bilinear`` weighs."""
    whole = int(np.floor(offset))
    return whole, np.float32(offset - whole)


def bilinear(
    z: F32Grid, r0: int, r1: int, c0: int, c1: int, oy: float, ox: float
) -> NDArray[np.floating]:
    (iy, fy), (ix, fx) = _split(oy), _split(ox)
    a = z[r0 + iy : r1 + iy, c0 + ix : c1 + ix]
    b = z[r0 + iy : r1 + iy, c0 + ix + 1 : c1 + ix + 1]
    c = z[r0 + iy + 1 : r1 + iy + 1, c0 + ix : c1 + ix]
    e = z[r0 + iy + 1 : r1 + iy + 1, c0 + ix + 1 : c1 + ix + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy


def sky_view(
    z: NDArray[np.floating], halo: int, spacing_m: float, radius_m: float = SKY_RADIUS_M
) -> F32Grid:
    """``1 - mean(sin(horizon))`` within ``radius_m``, for the core of ``z`` inside ``halo``."""
    z = np.asarray(z, np.float32)
    r0, r1, c0, c1 = halo, z.shape[0] - halo, halo, z.shape[1] - halo
    out = np.ones((r1 - r0, c1 - c0), np.float32)
    reach = radius_m / spacing_m
    if reach < 1.0:
        return out
    steps = np.geomspace(1.0, reach, SKY_STEPS)
    if kernels_on():
        _compiled_sky_view(z, halo, spacing_m, steps, out)
        return out
    for a in range(r0, r1, STRIP_ROWS):
        b = min(a + STRIP_ROWS, r1)
        zc = z[a:b, c0:c1]
        acc = np.zeros(zc.shape, np.float32)
        for k in range(SKY_DIRS):
            theta = 2 * np.pi * k / SKY_DIRS
            best = np.zeros(zc.shape, np.float32)
            for t in steps:
                zz = bilinear(z, a, b, c0, c1, np.sin(theta) * t, np.cos(theta) * t)
                np.maximum(best, (zz - zc) * np.float32(1.0 / (t * spacing_m)), out=best)
            acc += best / np.sqrt(1.0 + best * best)
        out[a - r0 : b - r0] = 1.0 - acc / SKY_DIRS
    return out


def _compiled_sky_view(
    z: F32Grid, halo: int, spacing_m: float, steps: NDArray[np.float64], out: F32Grid
) -> None:
    """``sky_view``'s loop as a kernel, fed the offsets and scales that loop works out."""
    from mapgen.lighting import kernels

    thetas = [2 * np.pi * k / SKY_DIRS for k in range(SKY_DIRS)]
    oy = [np.sin(theta) * t for theta in thetas for t in steps]
    ox = [np.cos(theta) * t for theta in thetas for t in steps]
    offsets = kernel_offsets(oy, ox, [True] * len(oy), (SKY_DIRS, len(steps)), halo)
    scale = np.array([np.float32(1.0 / (t * spacing_m)) for t in steps], np.float32)
    if gpu_on():
        from mapgen.lighting import gpu

        gpu.sky_view(np.ascontiguousarray(z), halo, offsets, scale, out)
    else:
        kernels.sky_view(np.ascontiguousarray(z), halo, offsets, scale, out)


def kernel_offsets(
    oy: Sequence[float],
    ox: Sequence[float],
    smooth: Sequence[bool],
    shape: tuple[int, ...],
    halo: int,
) -> Offsets:
    """Each step's sample position as the kernels take it, split the way ``bilinear`` and
    ``nearest`` split it. The kernels read unchecked, so a halo the steps overrun is refused.
    """
    parts = [
        [_split(o) if b else (round(o), np.float32(0.0)) for o, b in zip(axis, smooth, strict=True)]
        for axis in (oy, ox)
    ]
    iy, ix = (np.array([w for w, _f in axis], np.int64).reshape(shape) for axis in parts)
    fy, fx = (np.array([f for _w, f in axis], np.float32).reshape(shape) for axis in parts)
    reach = int(max(np.abs(iy).max(initial=0), np.abs(ix).max(initial=0))) + 1
    if reach > halo:
        raise ValueError(f"a halo of {halo} px is short of the {reach} px the steps reach")
    return iy, ix, fy, fx, 1 - fy, 1 - fx


def crown_surface(
    z: NDArray[np.floating], top: NDArray[np.floating], cover: NDArray[np.floating] | None = None
) -> F32Grid:
    """The ground with its crowns stood on it, each lift scaled by the pixel's crown cover.

    ``top`` is the crown top in metres, NaN where none; ``cover`` the covered share, 0 to 1,
    or None for a whole pixel wherever ``top`` is set.
    """
    z = np.asarray(z, np.float32)
    lift = np.maximum(np.nan_to_num(np.asarray(top, np.float32) - z, nan=0.0), 0.0)
    return z + (lift if cover is None else lift * np.asarray(cover, np.float32))


def nearest(z: F32Grid, r0: int, r1: int, c0: int, c1: int, oy: float, ox: float) -> F32Grid:
    iy, ix = round(oy), round(ox)
    return z[r0 + iy : r1 + iy, c0 + ix : c1 + ix]


def march_horizon(
    z: NDArray[np.floating],
    halo: int,
    az_deg: float,
    spacing_m: float,
    occluder: F32Grid | None = None,
    fade: Fade = FADE_M,
) -> F32Grid:
    """The faded horizon toward one azimuth for the core of ``z``, in degrees.

    ``occluder`` (tree crowns, NaN where none) adds ``crown_horizon`` on top of the ground's,
    which is what a style that draws the crowns sees.
    """
    z = np.asarray(z, np.float32)
    best = _march(z, z, halo, az_deg, spacing_m, fade)
    out = np.degrees(np.arctan(best)).astype(np.float32)
    if occluder is None:
        return out
    crowns = crown_horizon(crown_surface(z, occluder), halo, az_deg, spacing_m)
    return np.maximum(out, crowns)


def crown_horizon(
    crown_z: NDArray[np.floating],
    halo: int,
    az_deg: float,
    spacing_m: float,
    blockers: NDArray[np.floating] | None = None,
) -> F32Grid:
    """The horizon of a ``crown_surface`` under ``OCCLUDER_FADE_M``, received on it, degrees.

    The receivers stand on the crown tops, so a crown is lit or shaded where it is drawn.
    """
    crown_z = np.asarray(crown_z, np.float32)
    solid = crown_z if blockers is None else np.asarray(blockers, np.float32)
    best = _march(solid, crown_z, halo, az_deg, spacing_m, OCCLUDER_FADE_M)
    return np.degrees(np.arctan(best)).astype(np.float32)


def march_steps(az_deg: float, spacing_m: float, fade: Fade) -> list[Step]:
    """Toward ``az_deg``, every step that still counts: its sampler, offset and weight."""
    az = np.deg2rad(az_deg)
    dr, dc = -np.cos(az), np.sin(az)
    steps: list[Step] = []
    for t in step_lengths(fade[1] / spacing_m, FINE_M / spacing_m):
        d = t * spacing_m
        w = fade_weight(d, fade)
        if w <= 0:
            break
        sample = bilinear if t < BILINEAR_PX else nearest
        steps.append((sample, dr * t, dc * t, np.float32(w / d)))
    return steps


def _march(
    solid: F32Grid,
    z: F32Grid,
    halo: int,
    az_deg: float,
    spacing_m: float,
    fade: Fade,
) -> F32Grid:
    """The tangent of the faded horizon over ``solid`` for the core of ``z``, the receivers."""
    r0, r1, c0, c1 = halo, solid.shape[0] - halo, halo, solid.shape[1] - halo
    zc = z[halo : z.shape[0] - halo, halo : z.shape[1] - halo]
    best = np.zeros(zc.shape, np.float32)
    steps = march_steps(az_deg, spacing_m, fade)
    if kernels_on():
        _compiled_march(solid, z, halo, steps, best)
        return best
    for a in range(r0, r1, STRIP_ROWS):
        b = min(a + STRIP_ROWS, r1)
        near, top = zc[a - r0 : b - r0], best[a - r0 : b - r0]
        rise = np.empty(near.shape, np.float32)
        for sample, oy, ox, scale in steps:
            np.subtract(sample(solid, a, b, c0, c1, oy, ox), near, out=rise)
            rise *= scale
            np.maximum(top, rise, out=top)
    return best


def _compiled_march(
    solid: F32Grid, z: F32Grid, halo: int, steps: list[Step], best: F32Grid
) -> None:
    """``_march``'s loop as a kernel, fed the steps it works out; on the GPU when the switch
    says so."""
    from mapgen.lighting import kernels

    smooth = [sample is bilinear for sample, _oy, _ox, _scale in steps]
    oy, ox = [s[1] for s in steps], [s[2] for s in steps]
    offsets = kernel_offsets(oy, ox, smooth, (len(steps),), halo)
    scale = np.array([s[3] for s in steps], np.float32)
    args = (np.ascontiguousarray(solid), np.ascontiguousarray(z), halo, np.array(smooth),
            offsets, scale, best)  # fmt: skip
    if gpu_on():
        from mapgen.lighting import gpu

        gpu.march(*args)
    else:
        kernels.march(*args)


def faded_horizons(
    z: NDArray[np.floating],
    halo: int,
    spacing_m: float,
    occluder: F32Grid | None = None,
    dirs: int = HORIZON_DIRS,
    fade: Fade = FADE_M,
) -> F32Grid:
    """Every direction's faded horizon for the core of ``z``: ``(dirs, h, w)`` degrees."""
    return np.stack(
        [march_horizon(z, halo, k * 360.0 / dirs, spacing_m, occluder, fade) for k in range(dirs)]
    )


def crown_horizons(
    crown_z: NDArray[np.floating],
    halo: int,
    spacing_m: float,
    blockers: NDArray[np.floating] | None = None,
    dirs: int = HORIZON_DIRS,
) -> F32Grid:
    """Every direction's ``crown_horizon``: ``(dirs, h, w)`` degrees."""
    return np.stack(
        [crown_horizon(crown_z, halo, k * 360.0 / dirs, spacing_m, blockers) for k in range(dirs)]
    )


def encode_horizon(deg: NDArray[np.floating]) -> U8Grid:
    """Degrees to the stored byte: ``255 * sqrt(deg / 90)``, finest near the horizon."""
    return np.round(np.sqrt(np.clip(deg / 90.0, 0.0, 1.0)) * 255.0).astype(np.uint8)


def decode_horizon(q: NDArray[np.integer]) -> F32Grid:
    """The stored byte back to degrees."""
    v: F32Grid = np.asarray(q, np.float32) / 255.0
    squared: F32Grid = v * v
    return squared * np.float32(90.0)
