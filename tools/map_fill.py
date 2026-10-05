"""The render's base heights, rebuilt where the field is coarse or empty.

Imported by ``tools/gen_map_renders.py`` and ``tools/check_map_fill.py``; nothing here reads
the game or writes a file. Three steps on the 1 m lattice the kernel samples:

1. the fill province is re-read from the float16 interface raster: Gaussian, then cubic,
   then a constant bias;
2. the band where it meets the landscape gets the landscape's residual carried across it
   by a harmonic solve, cosine-tapered to nothing at ``SEAM_BAND_M``;
3. interior holes get a biharmonic fill, or a harmonic one where that leaves the range of
   its own border.

Rock texels are never read as a constraint and never written. Empty ground connected to
the edge of the field stays empty, so the render paints the page's sea there. The numbers
behind every constant are in docs/spatial-and-map.md section 26.
"""

from __future__ import annotations

import time

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy import ndimage

#: The interface raster's reconstruction, chosen on held-out landscape.
RASTER_SIGMA_TEXELS = 1.0
RASTER_BIAS_M = 1.0

#: How far the landscape's residual is carried into the fill province, in metres.
SEAM_BAND_M = 48

#: Holes larger than this are left empty, as is anything touching the field's edge.
HOLE_MAX_TEXELS = 200_000

#: Context around a hole that the solve is anchored to, in texels.
HOLE_RING_TEXELS = 16

#: How far a biharmonic fill may leave its border's range before the harmonic one is used.
HOLE_FALLBACK_M = 2.0

#: Filled ground under water stands at least this far below the surface.
WET_BELOW_M = 0.5

#: A water level this far under the filled ground is a stray water box, not a floor.
WET_IGNORE_M = 20.0

#: Tiles of the band solve, and the halo each is solved with.
SOLVE_TILE = 1024
SOLVE_HALO = 96

#: What each texel of the rebuilt lattice is, for the sidecar's tally.
SOURCE_NONE, SOURCE_LAND, SOURCE_ROCK, SOURCE_RASTER, SOURCE_SEAM, SOURCE_HOLE = range(6)
SOURCE_NAMES = {
    SOURCE_NONE: "no data: drawn as the page's sea",
    SOURCE_LAND: "landscape, measured",
    SOURCE_ROCK: "cliff province, copied unchanged",
    SOURCE_RASTER: "fill, rebuilt from the interface raster",
    SOURCE_SEAM: "fill inside the seam band",
    SOURCE_HOLE: "interior hole, filled",
}


def raster_positions(
    count: int, origin_cm: float, spacing_cm: float, lo_cm: float, hi_cm: float, px: int
):
    """Fractional raster coordinates (texel centres) of a run of field vertices."""
    world = origin_cm + np.arange(count, dtype=np.float64) * spacing_cm
    return (world - lo_cm) / (hi_cm - lo_cm) * px - 0.5


def nearest_fill(values: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Every unknown texel takes its nearest known neighbour's value."""
    index = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    return values[tuple(index)]


def reconstruct_raster(z_m, ok, rows, cols, chunk: int = 512):
    """The raster read at fractional ``rows`` x ``cols``: ``(metres, whole)``, float32.

    ``whole`` is true where every texel under the bilinear footprint says something.
    """
    padded = nearest_fill(np.where(ok, z_m, 0.0), ok)
    smooth = ndimage.gaussian_filter(padded.astype(np.float64), RASTER_SIGMA_TEXELS, mode="nearest")
    coeffs = ndimage.spline_filter(smooth, order=3, mode="nearest")
    okf = ok.astype(np.float32)
    out = np.empty((len(rows), len(cols)), np.float32)
    whole = np.empty((len(rows), len(cols)), bool)
    for start in range(0, len(rows), chunk):
        rr, cc = np.meshgrid(rows[start : start + chunk], cols, indexing="ij")
        out[start : start + chunk] = ndimage.map_coordinates(
            coeffs, [rr, cc], order=3, mode="nearest", prefilter=False
        )
        whole[start : start + chunk] = (
            ndimage.map_coordinates(okf, [rr, cc], order=1, mode="nearest") > 0.999
        )
    return out + np.float32(RASTER_BIAS_M), whole


def _laplacian(active: np.ndarray) -> sp.csr_matrix:
    """Graph Laplacian of the 4-neighbour grid restricted to ``active``."""
    h, w = active.shape
    idx = np.arange(h * w).reshape(h, w)
    right = active[:, :-1] & active[:, 1:]
    down = active[:-1, :] & active[1:, :]
    a = np.concatenate([idx[:, :-1][right], idx[:-1, :][down]])
    b = np.concatenate([idx[:, 1:][right], idx[1:, :][down]])
    adj = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(h * w, h * w))
    adj = (adj + adj.T).tocsr()
    return (sp.diags(np.asarray(adj.sum(axis=1)).ravel()) - adj).tocsr()


def solve(values, known, unknown, order: int) -> np.ndarray:
    """``values`` with ``unknown`` replaced by the harmonic (1) or biharmonic (2) fill.

    Known texels are Dirichlet; anything neither known nor unknown is outside the domain.
    """
    u = unknown.ravel()
    if not u.any():
        return values.astype(np.float64)
    lap = _laplacian(known | unknown)
    op = lap if order == 1 else (lap.T @ lap).tocsr()
    k = known.ravel()
    a_uu = op[u][:, u]
    rhs = -(op[u][:, k] @ values.ravel()[k].astype(np.float64))
    # A cluster with no known neighbour makes the block singular; pin it weakly.
    x = spla.spsolve((a_uu + 1e-9 * sp.eye(a_uu.shape[0])).tocsc(), rhs)
    out = values.astype(np.float64).ravel().copy()
    out[u] = x
    return out.reshape(values.shape)


def tiled_harmonic(values, known, unknown) -> np.ndarray:
    """``solve(order=1)`` over the field in tiles with a halo, so each system stays small."""
    out = values.astype(np.float32).copy()
    rows, cols = values.shape
    for r in range(0, rows, SOLVE_TILE):
        for c in range(0, cols, SOLVE_TILE):
            block = (slice(r, min(r + SOLVE_TILE, rows)), slice(c, min(c + SOLVE_TILE, cols)))
            if not unknown[block].any():
                continue
            rs = slice(max(r - SOLVE_HALO, 0), min(r + SOLVE_TILE + SOLVE_HALO, rows))
            cs = slice(max(c - SOLVE_HALO, 0), min(c + SOLVE_TILE + SOLVE_HALO, cols))
            got = solve(values[rs, cs], known[rs, cs], unknown[rs, cs], 1)
            inner = got[
                r - rs.start : block[0].stop - rs.start, c - cs.start : block[1].stop - cs.start
            ]
            mask = unknown[block]
            out[block][mask] = inner[mask]
    return out


def cosine_taper(distance: np.ndarray, band: float) -> np.ndarray:
    """1 at the seam, 0 at ``band``, with zero slope at both ends."""
    return (0.5 * (1.0 + np.cos(np.pi * np.clip(distance / band, 0.0, 1.0)))).astype(np.float32)


def blend_seam(rec, land_m, land, target, band_m: float = SEAM_BAND_M):
    """``rec`` on ``target`` with the landscape's residual carried across the seam.

    Returns ``(values on target, band mask)``. ``land_m`` is read where ``land`` is true.
    """
    distance = ndimage.distance_transform_edt(~land).astype(np.float32)
    band = target & (distance < band_m)
    residual = np.where(land, land_m - rec, 0.0).astype(np.float32)
    known = (land & (distance == 0)) | (target & (distance >= band_m))
    known &= ndimage.binary_dilation(band, iterations=int(band_m) + 2)
    carried = tiled_harmonic(residual, known, band)
    blended = rec + np.where(band, carried * cosine_taper(distance, band_m), 0.0)
    return blended.astype(np.float32), band


def fill_holes(ground_m, known, hole_max: int = HOLE_MAX_TEXELS):
    """Biharmonic fill of every unknown component that is small and off the field's edge.

    Returns ``(filled copy, hole mask, stats)``. Anything else unknown stays NaN.
    """
    out = ground_m.astype(np.float32).copy()
    unknown = ~known
    labels, count = ndimage.label(unknown)
    holes = np.zeros(unknown.shape, bool)
    stats = {"holes": 0, "texels": 0, "harmonic_fallback": 0, "overshoot_max_m": 0.0}
    if not count:
        return out, holes, stats
    sizes = ndimage.sum(unknown, labels, np.arange(1, count + 1))
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    border = set(np.unique(edge).tolist()) - {0}
    ring_n = HOLE_RING_TEXELS
    for label, box in enumerate(ndimage.find_objects(labels), start=1):
        if box is None or label in border or sizes[label - 1] > hole_max:
            continue
        rs = slice(max(box[0].start - ring_n, 0), min(box[0].stop + ring_n, out.shape[0]))
        cs = slice(max(box[1].start - ring_n, 0), min(box[1].stop + ring_n, out.shape[1]))
        hole = labels[rs, cs] == label
        anchor = known[rs, cs] & ndimage.binary_dilation(hole, iterations=ring_n)
        if not anchor.any():
            continue
        values = np.nan_to_num(out[rs, cs]).astype(np.float64)
        got = solve(values, anchor, hole, 2)
        ring = ndimage.binary_dilation(hole, iterations=2) & anchor
        lo, hi = values[ring].min(), values[ring].max()
        if got[hole].max() > hi + HOLE_FALLBACK_M or got[hole].min() < lo - HOLE_FALLBACK_M:
            got = solve(values, anchor, hole, 1)
            stats["harmonic_fallback"] += 1
        over = max(float(got[hole].max() - hi), float(lo - got[hole].min()), 0.0)
        stats["overshoot_max_m"] = max(stats["overshoot_max_m"], round(over, 3))
        out[rs, cs][hole] = got[hole]
        holes[rs, cs] |= hole
        stats["holes"] += 1
        stats["texels"] += int(hole.sum())
    return out, holes, stats


def fill_field(
    *,
    ground_dm,
    height_dm,
    prov,
    water_quality,
    water_dm,
    raster_m,
    raster_ok,
    field_origin_cm: tuple[float, float],
    spacing_cm: float,
    raster_box_cm: tuple[float, float, float, float],
    nodata: int,
    fill_value: int,
    rock_values: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """The rebuilt lattices: ``(heights_dm, ground_dm, source, meta)``.

    ``ground_dm`` is the kernel's lattice as ``ground_lattice`` and ``terrain_lattice``
    made it (cliff removed, landscape at 7.8 mm). ``heights_dm`` is that ground with the
    cliff province's own heights put back unchanged. Both are float32 with ``nodata``.
    """
    timings: dict[str, float] = {}
    clock = time.perf_counter()

    def tick(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[name] = round(now - clock, 1)
        clock = now

    rows, cols = ground_dm.shape
    rock = np.isin(prov, rock_values)
    fill = prov == fill_value
    ground = np.where(ground_dm == nodata, np.nan, ground_dm / 10.0).astype(np.float32)
    land = ~np.isnan(ground) & ~fill
    x0, x1, y0, y1 = raster_box_cm
    px = raster_m.shape[0]
    fr = raster_positions(rows, field_origin_cm[1], spacing_cm, y0, y1, px)
    fc = raster_positions(cols, field_origin_cm[0], spacing_cm, x0, x1, px)
    rec, whole = reconstruct_raster(raster_m, raster_ok, fr, fc)
    ground[fill] = rec[fill]
    tick("raster")

    dry_fill = fill & (water_quality == 0)
    blended, band = blend_seam(rec, np.nan_to_num(ground), land & whole, dry_fill & whole)
    ground[band] = blended[band]
    del blended, rec
    tick("seam")

    known = ~np.isnan(ground)
    ground, holes, hole_stats = fill_holes(ground, known)
    level = np.where(water_dm == nodata, np.nan, water_dm / 10.0).astype(np.float32)
    wet = holes & (water_quality > 0) & ~np.isnan(level)
    wet &= ground - np.nan_to_num(level) < WET_IGNORE_M
    ground[wet] = np.minimum(ground[wet], level[wet] - WET_BELOW_M)
    tick("holes")

    # Measured texels are copied from the inputs rather than round-tripped through metres.
    ground_out = np.where(np.isnan(ground), np.float32(nodata), ground * 10.0).astype(np.float32)
    ground_out[land] = ground_dm[land]
    heights_dm = np.where(rock, height_dm, ground_out).astype(np.float32)
    source = np.zeros((rows, cols), np.uint8)
    source[land] = SOURCE_LAND
    source[fill] = SOURCE_RASTER
    source[band] = SOURCE_SEAM
    source[holes] = SOURCE_HOLE
    source[rock] = SOURCE_ROCK
    source[heights_dm == nodata] = SOURCE_NONE
    tick("compose")
    shares = {
        SOURCE_NAMES[key]: round(100 * float((source == key).mean()), 3) for key in SOURCE_NAMES
    }
    meta = {
        "raster_sigma_texels": RASTER_SIGMA_TEXELS,
        "raster_bias_m": RASTER_BIAS_M,
        "seam_band_m": SEAM_BAND_M,
        "hole_max_texels": HOLE_MAX_TEXELS,
        "hole_fallback_m": HOLE_FALLBACK_M,
        "holes": hole_stats,
        "wet_hole_texels_clamped": int(wet.sum()),
        "share_of_the_field_pct": shares,
        "seconds": timings,
        "open_sea": "left empty: the render paints the page's sea, and no depth is invented",
    }
    return heights_dm, ground_out, source, meta
