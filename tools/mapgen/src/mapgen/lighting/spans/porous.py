"""The trees as a porous medium: what a ray toward the sun keeps of the light through them.

Each tree pixel is a shell of leaves ``LEAF_SHELL_M`` deep under its top, of optical depth
``-ln(1 - cover)``. A ray at the sun path's elevation in a direction takes, from each sample it
climbs through, that share of the shell's height times its depth, under the crowns' fade or
the Titan canopy's. The cell stores the shade, ``1 - exp(-depth)``, folded at that elevation as
a band is (``fold``). The loop runs as numba's or CUDA's kernel unless ``mapgen.jit`` selects
this numpy, their reference. docs/map/light-and-crowns.md section 29, "A porous canopy".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.jit import gpu_on, kernels_on
from mapgen.lighting.horizon import (
    FADE_M,
    FINE_M,
    OCCLUDER_FADE_M,
    STRIP_ROWS,
    Fade,
    bilinear,
    fade_weight,
    kernel_offsets,
    step_lengths,
)
from mapgen.lighting.spans.march import (
    SpanRuns,
    band_target,
    quad,
    quad_offsets,
    rows_with_spans,
    runs_of,
    span_steps,
    step_reach,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "CANOPY_COVER_MIN",
    "CANOPY_FLOOR_DEG",
    "COVER_MAX",
    "LEAF_SHELL_M",
    "SHADE_MIN",
    "THICK_MIN_M",
    "PorousSurface",
    "PorousSteps",
    "depth_of",
    "fold",
    "march_porous",
    "porous_block",
    "porous_steps",
    "porous_surface",
]

#: How deep under its top a tree pixel's leaves lie.
LEAF_SHELL_M = 2.0
#: The densest cover's share of the light it stops: the leaves let the rest through.
COVER_MAX = 0.8
#: A half-resolution cell at least this covered receives on its canopy top, else the ground.
CANOPY_COVER_MIN = 0.5
#: A canopy cell's least stored value, so 0 can mean that no tree covers it; degrees.
CANOPY_FLOOR_DEG = 0.5
#: The least shade that counts as folded in (``light_tiles.HZ_FOLDED_QUALITY``).
SHADE_MIN = 1.0 / 255.0
#: The thinnest span a share of height is taken of, metres.
THICK_MIN_M = np.float32(0.1)

_SOFT_DEG = 6.0
_HALF = np.float32(0.5)


class PorousSurface(NamedTuple):
    """What a porous march reads, one grid with a halo: the receivers, the trees' underside
    and top (NaN where none), the crowns' and the Titan canopy's optical depth, and the runs
    of columns with a tree."""

    z: F32Grid
    lo: F32Grid
    hi: F32Grid
    tau_crowns: F32Grid
    tau_titans: F32Grid
    runs: SpanRuns


class PorousSteps(NamedTuple):
    """A direction's steps as the kernels take them: which sample bilinearly, the offsets,
    the quads' whole offsets, and per step the stretch's near and far end and the two fades'
    weights; and the rows past a receiver's they read (``march.step_reach``)."""

    smooth: BoolMask
    offsets: tuple[I64Grid, I64Grid, F32Grid, F32Grid, F32Grid, F32Grid]
    quads: tuple[I64Grid, I64Grid]
    per_step: F32Grid
    reach: int


def porous_block() -> JsonObject:
    """The porous model's constants, as the light axis records them."""
    return {
        "leaf_shell_m": LEAF_SHELL_M,
        "cover_max": COVER_MAX,
        "canopy_cover_min": CANOPY_COVER_MIN,
        "shade_min": round(SHADE_MIN, 6),
        "fades_m": {"crowns": list(OCCLUDER_FADE_M), "titans": list(FADE_M)},
        "cell": (
            "shade 1 - exp(-depth) at path_el_deg, folded as a band: el + soft * (s - 1/2), "
            "an open cell too; read back at its own path_el_deg whatever the sun's elevation"
        ),
    }


def depth_of(cover: F32Grid) -> F32Grid:
    """A cover's optical depth: ``-ln(1 - min(cover, COVER_MAX))``."""
    kept = np.float32(1.0) - np.minimum(cover, np.float32(COVER_MAX))
    return (-np.log(kept)).astype(np.float32)


def porous_surface(
    z: F32Grid, lo: F32Grid, hi: F32Grid, tau_crowns: F32Grid, tau_titans: F32Grid
) -> PorousSurface:
    """A ``PorousSurface``, its planes contiguous float32."""
    z, lo, hi, tau_crowns, tau_titans = (
        np.ascontiguousarray(p, np.float32) for p in (z, lo, hi, tau_crowns, tau_titans)
    )
    return PorousSurface(z, lo, hi, tau_crowns, tau_titans, runs_of(lo))


def porous_steps(az_deg: float, spacing_m: float, halo: int, titans: bool) -> PorousSteps:
    """The steps toward ``az_deg`` as far as the farther fade present reaches."""
    reach: Fade = FADE_M if titans else OCCLUDER_FADE_M
    steps = span_steps(az_deg, spacing_m, reach)
    ts = step_lengths(reach[1] / spacing_m, FINE_M / spacing_m)
    per_step = np.array(
        [
            [
                s.near_m,
                s.far_m,
                fade_weight(t * spacing_m, OCCLUDER_FADE_M),
                fade_weight(t * spacing_m, FADE_M),
            ]
            for s, t in zip(steps, ts, strict=False)
        ],
        np.float32,
    ).reshape(-1, 4)
    smooth = [s.sample is bilinear for s in steps]
    oy, ox = [s.oy for s in steps], [s.ox for s in steps]
    offsets = kernel_offsets(oy, ox, smooth, (len(steps),), halo)
    quads = quad_offsets(oy, ox)
    return PorousSteps(np.array(smooth, bool), offsets, quads, per_step, step_reach(steps))


def march_porous(surface: PorousSurface, halo: int, az_deg: float, spacing_m: float) -> F32Grid:
    """The optical depth a ray toward ``az_deg`` at the sun path's elevation there takes
    through the trees, for the core of ``surface`` inside ``halo``; 0 where none is in reach."""
    z = surface.z
    r0, r1, c0, c1 = halo, z.shape[0] - halo, halo, z.shape[1] - halo
    acc = np.zeros((r1 - r0, c1 - c0), np.float32)
    titans = bool(np.any(surface.tau_titans))
    steps = porous_steps(az_deg, spacing_m, halo, titans)
    te = band_target(az_deg)[0]
    counts, reach = rows_with_spans(surface.lo), steps.reach
    if kernels_on():
        _compiled(surface, halo, steps, te, acc, (counts, reach))
        return acc
    iy, ix, _fy, _fx, _gy, _gx = steps.offsets
    for a in range(r0, r1, STRIP_ROWS):
        b = min(a + STRIP_ROWS, r1)
        if not counts[min(b + reach, len(counts) - 1)] > counts[max(a - reach, 0)]:
            continue
        near, out = z[a:b, c0:c1], acc[a - r0 : b - r0]
        for k in range(len(steps.per_step)):
            low, high = quad(surface.lo, surface.hi, (a, b), (c0, c1), *_quad_at(steps, k))
            found = np.isfinite(low)
            if not found.any():
                continue
            near_m, far_m, w_crowns, w_titans = steps.per_step[k]
            dl, dh = low - near, high - near
            v = np.minimum(dh, te * far_m) - np.maximum(dl, te * near_m)
            share = np.minimum(v / np.maximum(dh - dl, THICK_MIN_M), np.float32(1.0))
            at = (int(iy[k]), int(ix[k]))
            part = _tap(surface.tau_crowns, steps, k, at, (a, b, c0, c1)) * w_crowns + _tap(
                surface.tau_titans, steps, k, at, (a, b, c0, c1)
            ) * w_titans
            out += np.where(found & (v > 0), share * part, np.float32(0.0))
    return acc


def _quad_at(steps: PorousSteps, k: int) -> tuple[int, int]:
    return int(steps.quads[0][k]), int(steps.quads[1][k])


def _tap(
    plane: F32Grid, steps: PorousSteps, k: int, at: tuple[int, int], box: tuple[int, int, int, int]
) -> F32Grid:
    """``plane`` read at step ``k`` as the kernels read it: bilinear, or the nearest pixel."""
    a, b, c0, c1 = box
    iy, ix = at
    _iy, _ix, fy, fx, gy, gx = steps.offsets
    p = plane[a + iy : b + iy, c0 + ix : c1 + ix]
    if not steps.smooth[k]:
        return p
    q = plane[a + iy : b + iy, c0 + ix + 1 : c1 + ix + 1]
    d = plane[a + iy + 1 : b + iy + 1, c0 + ix : c1 + ix]
    e = plane[a + iy + 1 : b + iy + 1, c0 + ix + 1 : c1 + ix + 1]
    return ((p * gx[k] + q * fx[k]) * gy[k] + (d * gx[k] + e * fx[k]) * fy[k]).astype(np.float32)


def _compiled(
    surface: PorousSurface,
    halo: int,
    steps: PorousSteps,
    te: np.float32,
    acc: F32Grid,
    rows: tuple[I64Grid, int],
) -> None:
    """``march_porous``' loop as a kernel; on the GPU when the switch says so."""
    planes = (surface.z, surface.lo, surface.hi, surface.tau_crowns, surface.tau_titans)
    runs = (surface.runs.row, surface.runs.start, surface.runs.end)
    if gpu_on():
        from mapgen.lighting.spans import gpu

        gpu.march_porous(planes, halo, steps[:4], (te, THICK_MIN_M), acc, rows, runs)
        return
    from mapgen.lighting.spans import kernels

    ray = (te, THICK_MIN_M)
    kernels.march_porous(
        planes, halo, steps.smooth, steps.offsets, steps.quads, steps.per_step, ray, acc, rows, runs
    )


def fold(depth: F32Grid, el: float) -> tuple[F32Grid, BoolMask]:
    """A cell from its optical depth: the shade ``1 - exp(-depth)`` folded at ``el``, the sun
    path's elevation, as a band is, an open cell too (``el - soft / 2``), so a bilinear read
    between cells is a bilinear read of their shade. And where it shades, ``SHADE_MIN`` on."""
    shade = (np.float32(1.0) - np.exp(-depth)).astype(np.float32)
    kept = shade >= np.float32(SHADE_MIN)
    folded = np.float32(el) + np.float32(_SOFT_DEG) * (shade - _HALF)
    return folded.astype(np.float32), kept
