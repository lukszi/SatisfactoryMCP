"""Sun-independent light terms of a height surface: normals, sky view and faded horizons.

Rows run south and columns east; an azimuth is compass degrees from north. Why each constant
has its value: docs/spatial-and-map.md section 29.
"""

from __future__ import annotations

import numpy as np

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
    "crown_horizon",
    "crown_horizons",
    "crown_surface",
    "decode_horizon",
    "encode_horizon",
    "fade_weight",
    "faded_horizons",
    "horizon_reach_px",
    "march_horizon",
    "normals",
    "sky_view",
]

#: Directions stored per pixel, evenly spaced from north.
HORIZON_DIRS = 32

#: A blocker counts fully up to the first distance and not at all past the second.
FADE_M = (40.0, 150.0)

#: Crowns are porous and their far shadow diffuse, so an occluder fades sooner than rock.
OCCLUDER_FADE_M = (25.0, 80.0)

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


def fade_weight(d_m: float, fade: tuple[float, float] = FADE_M) -> float:
    """How much of a blocker's height counts at ``d_m``: 1 near, 0 past the fade."""
    return float(np.clip((fade[1] - d_m) / (fade[1] - fade[0]), 0.0, 1.0))


def horizon_reach_px(spacing_m: float, fade: tuple[float, float] = FADE_M) -> int:
    """How far a horizon looks, in pixels: the halo a block needs."""
    return int(np.ceil(fade[1] / spacing_m)) + 2


def _steps(max_px: float, fine_px: float, growth: float = STEP_GROWTH) -> list[int]:
    """Integer step lengths: every pixel to ``fine_px``, then growing geometrically."""
    out, t = [], 1.0
    while t <= max_px:
        out.append(round(t))
        t += 1.0 if t < fine_px else max(1.0, t * growth)
    return sorted(set(out))


def normals(z: np.ndarray, spacing_m: float) -> tuple[np.ndarray, np.ndarray]:
    """East and south components of the unit normal, for ``z`` with a one-pixel margin."""
    d_south, d_east = np.gradient(np.asarray(z, np.float32), spacing_m)
    d_south, d_east = d_south[1:-1, 1:-1], d_east[1:-1, 1:-1]
    inv = 1.0 / np.sqrt(d_east * d_east + d_south * d_south + 1.0)
    return (-d_east * inv).astype(np.float32), (-d_south * inv).astype(np.float32)


def _bilinear(z, r0, r1, c0, c1, oy, ox):
    iy, ix = int(np.floor(oy)), int(np.floor(ox))
    fy, fx = np.float32(oy - iy), np.float32(ox - ix)
    a = z[r0 + iy : r1 + iy, c0 + ix : c1 + ix]
    b = z[r0 + iy : r1 + iy, c0 + ix + 1 : c1 + ix + 1]
    c = z[r0 + iy + 1 : r1 + iy + 1, c0 + ix : c1 + ix]
    e = z[r0 + iy + 1 : r1 + iy + 1, c0 + ix + 1 : c1 + ix + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy


def sky_view(z: np.ndarray, halo: int, spacing_m: float, radius_m: float = SKY_RADIUS_M):
    """``1 - mean(sin(horizon))`` within ``radius_m``, for the core of ``z`` inside ``halo``."""
    z = np.asarray(z, np.float32)
    r0, r1, c0, c1 = halo, z.shape[0] - halo, halo, z.shape[1] - halo
    out = np.ones((r1 - r0, c1 - c0), np.float32)
    reach = radius_m / spacing_m
    if reach < 1.0:
        return out
    steps = np.geomspace(1.0, reach, SKY_STEPS)
    for a in range(r0, r1, STRIP_ROWS):
        b = min(a + STRIP_ROWS, r1)
        zc = z[a:b, c0:c1]
        acc = np.zeros(zc.shape, np.float32)
        for k in range(SKY_DIRS):
            theta = 2 * np.pi * k / SKY_DIRS
            best = np.zeros(zc.shape, np.float32)
            for t in steps:
                zz = _bilinear(z, a, b, c0, c1, np.sin(theta) * t, np.cos(theta) * t)
                np.maximum(best, (zz - zc) * np.float32(1.0 / (t * spacing_m)), out=best)
            acc += best / np.sqrt(1.0 + best * best)
        out[a - r0 : b - r0] = 1.0 - acc / SKY_DIRS
    return out


def crown_surface(z, top, cover=None) -> np.ndarray:
    """The ground with its crowns stood on it, each lift scaled by the pixel's crown cover.

    ``top`` is the crown top in metres, NaN where none; ``cover`` the covered share, 0 to 1,
    or None for a whole pixel wherever ``top`` is set.
    """
    z = np.asarray(z, np.float32)
    lift = np.maximum(np.nan_to_num(np.asarray(top, np.float32) - z, nan=0.0), 0.0)
    return z + (lift if cover is None else lift * np.asarray(cover, np.float32))


def _nearest(z, r0, r1, c0, c1, oy, ox):
    iy, ix = round(oy), round(ox)
    return z[r0 + iy : r1 + iy, c0 + ix : c1 + ix]


def march_horizon(z, halo, az_deg, spacing_m, occluder=None, slabs=None, fade=FADE_M):
    """The faded horizon toward one azimuth for the core of ``z``, in degrees.

    ``occluder`` (tree crowns, NaN where none) adds ``crown_horizon`` on top of the ground's,
    which is what a style that draws the crowns sees. ``slabs`` is ``(ground, min_z,
    max_z)``: the surface without what floats, which then blocks in place of ``z``, and the
    floating geometry's underside and top, NaN where nothing floats.
    """
    z = np.asarray(z, np.float32)
    core = (slice(halo, z.shape[0] - halo), slice(halo, z.shape[1] - halo))
    solid = z if slabs is None else np.asarray(slabs[0], np.float32)
    best = np.zeros(z[core].shape, np.float32)
    _march(solid, z[core], halo, az_deg, spacing_m, fade, best, slabs)
    out = np.degrees(np.arctan(best)).astype(np.float32)
    if occluder is None:
        return out
    crowns = crown_horizon(crown_surface(z, occluder), halo, az_deg, spacing_m,
                           crown_surface(solid, occluder))  # fmt: skip
    return np.maximum(out, crowns)


def crown_horizon(crown_z, halo, az_deg, spacing_m, blockers=None) -> np.ndarray:
    """The horizon of a ``crown_surface`` under ``OCCLUDER_FADE_M``, received on it, degrees.

    The receivers stand on the crown tops, so a crown is lit or shaded where it is drawn.
    """
    crown_z = np.asarray(crown_z, np.float32)
    core = (slice(halo, crown_z.shape[0] - halo), slice(halo, crown_z.shape[1] - halo))
    solid = crown_z if blockers is None else np.asarray(blockers, np.float32)
    best = np.zeros(crown_z[core].shape, np.float32)
    _march(solid, crown_z[core], halo, az_deg, spacing_m, OCCLUDER_FADE_M, best)
    return np.degrees(np.arctan(best)).astype(np.float32)


def _march(solid, zc, halo, az_deg, spacing_m, fade, best, slabs=None) -> None:
    r0, r1, c0, c1 = halo, solid.shape[0] - halo, halo, solid.shape[1] - halo
    az = np.deg2rad(az_deg)
    dr, dc = -np.cos(az), np.sin(az)
    steps = []
    for t in _steps(fade[1] / spacing_m, FINE_M / spacing_m):
        d = t * spacing_m
        w = fade_weight(d, fade)
        if w <= 0:
            break
        sample = _bilinear if t < BILINEAR_PX else _nearest
        steps.append((sample, dr * t, dc * t, np.float32(w / d)))
    for a in range(r0, r1, STRIP_ROWS):
        b = min(a + STRIP_ROWS, r1)
        near, top = zc[a - r0 : b - r0], best[a - r0 : b - r0]
        rise = np.empty(near.shape, np.float32)
        for sample, oy, ox, scale in steps:
            np.subtract(sample(solid, a, b, c0, c1, oy, ox), near, out=rise)
            rise *= scale
            np.maximum(top, rise, out=top)
            if slabs is not None:
                lo = sample(slabs[1], a, b, c0, c1, oy, ox)
                hi = sample(slabs[2], a, b, c0, c1, oy, ox)
                _raise_by_slab(top, (lo - near) * scale, (hi - near) * scale)


def _raise_by_slab(best, lo, hi):
    """A floating slab extends the horizon only where nothing open shows beneath it."""
    closed = np.isfinite(lo) & (lo <= best)
    np.maximum(best, np.where(closed, hi, best), out=best)


def faded_horizons(z, halo, spacing_m, occluder=None, slabs=None, dirs=HORIZON_DIRS,
                   fade=FADE_M) -> np.ndarray:  # fmt: skip
    """Every direction's faded horizon for the core of ``z``: ``(dirs, h, w)`` degrees."""
    return np.stack(
        [
            march_horizon(z, halo, k * 360.0 / dirs, spacing_m, occluder, slabs, fade)
            for k in range(dirs)
        ]
    )


def crown_horizons(crown_z, halo, spacing_m, blockers=None, dirs=HORIZON_DIRS) -> np.ndarray:
    """Every direction's ``crown_horizon``: ``(dirs, h, w)`` degrees."""
    return np.stack(
        [crown_horizon(crown_z, halo, k * 360.0 / dirs, spacing_m, blockers) for k in range(dirs)]
    )


def encode_horizon(deg: np.ndarray) -> np.ndarray:
    """Degrees to the stored byte: ``255 * sqrt(deg / 90)``, finest near the horizon."""
    return np.round(np.sqrt(np.clip(deg / 90.0, 0.0, 1.0)) * 255.0).astype(np.uint8)


def decode_horizon(q: np.ndarray) -> np.ndarray:
    v = np.asarray(q, np.float32) / 255.0
    return v * v * np.float32(90.0)
