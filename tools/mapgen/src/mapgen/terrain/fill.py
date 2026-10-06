"""The render's base heights, rebuilt where the field is coarse or empty.

Imported by ``mapgen.commands.renders`` and ``mapgen.commands.check_fill``; nothing here reads
the game or writes a file. Three steps on the 1 m lattice the kernel samples:

1. the fill province is re-read from the float16 interface raster: Gaussian, then cubic,
   then a constant bias;
2. the band where it meets the landscape gets the landscape's residual carried across it
   by a harmonic solve, cosine-tapered to nothing at ``SEAM_BAND_M``;
3. interior holes get a biharmonic fill, or a harmonic one where that leaves the range of
   its own border, unless the artwork draws the hole as a pit.

Rock texels are never read as a constraint and never written. Empty ground connected to
the edge of the field stays empty, and so does a pit: the render draws the open sea or the
void there. The numbers behind every constant are in docs/spatial-and-map.md section 26.
"""

from __future__ import annotations

import time

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy import ndimage

from mapgen.gamedata.frame import BASELINE_BOX_CM
from mapgen.gamedata.level.fill_raster import BASELINE_PATH, read_baseline
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "HOLE_FALLBACK_M",
    "HOLE_MAX_TEXELS",
    "HOLE_RING_TEXELS",
    "PIT_FLOOR_M",
    "PIT_SHARE",
    "RASTER_BIAS_M",
    "RASTER_SIGMA_TEXELS",
    "SEAM_BAND_M",
    "SOLVE_HALO",
    "SOLVE_TILE",
    "SOURCE_HOLE",
    "SOURCE_LAND",
    "SOURCE_NAMES",
    "SOURCE_NONE",
    "SOURCE_PIT",
    "SOURCE_RASTER",
    "SOURCE_ROCK",
    "SOURCE_SEAM",
    "WET_BELOW_M",
    "WET_IGNORE_M",
    "blend_seam",
    "cosine_taper",
    "fill_field",
    "fill_from_raster",
    "fill_holes",
    "ground_lattice",
    "nearest_fill",
    "pits",
    "raster_positions",
    "rebuild_lattice",
    "reconstruct_raster",
    "relax",
    "solve",
    "terrain_lattice",
    "tiled_harmonic",
]

#: The interface raster's reconstruction, chosen on held-out landscape.
RASTER_SIGMA_TEXELS = 1.0
RASTER_BIAS_M = 1.0

#: How far the landscape's residual is carried into the fill province, in metres.
SEAM_BAND_M = 48

#: Holes larger than this are left empty, as is anything touching the field's edge.
HOLE_MAX_TEXELS = 200_000

#: An interior no-data hole is a pit, left empty, when the artwork draws at least this share
#: of it as void: 0.88 to 0.95 for the crater and the abyss pits, 0.5 to 0.7 for a crack
#: under its white outline, 0.44 and less for the holes it draws as ground.
PIT_SHARE = 0.5

#: Ground lower than this is a pit's floor, not ground the map shows: the landscape's own
#: lowest height (-254 to -258 m) and the deepest abyss walls, 93% drawn as void.
PIT_FLOOR_M = -200.0

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
SOURCE_PIT = 6
SOURCE_NAMES = {
    SOURCE_NONE: "no data: drawn as the open sea or the void, as the artwork has it",
    SOURCE_LAND: "landscape, measured",
    SOURCE_ROCK: "cliff province, copied unchanged",
    SOURCE_RASTER: "fill, rebuilt from the interface raster",
    SOURCE_SEAM: "fill inside the seam band",
    SOURCE_HOLE: "interior hole, filled",
    SOURCE_PIT: "a hole or a pit's floor the artwork draws as void, left empty",
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


def relax(values, known, unknown, scale: float, far: float) -> np.ndarray:
    """``values`` with ``unknown`` replaced by a membrane that settles towards ``far``.

    The screened Poisson equation: its border's offset from ``far`` dies away over about
    ``scale`` texels. Known texels are Dirichlet, anything else outside the domain. The
    screening keeps the system well conditioned, so conjugate gradients solve it at any size.
    """
    out = values.astype(np.float64).ravel().copy()
    u, k = unknown.ravel(), known.ravel()
    if not u.any():
        return out.reshape(values.shape)
    lap = _laplacian(known | unknown)
    screen = 1.0 / (scale * scale)
    a_uu = (lap[u][:, u] + screen * sp.eye(int(u.sum()))).tocsr()
    rhs = screen * far - lap[u][:, k] @ out[k]
    jacobi = sp.diags(1.0 / a_uu.diagonal())
    out[u], _info = spla.cg(a_uu, rhs, x0=np.full(len(rhs), far), rtol=1e-6, M=jacobi)
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


def pits(nodata: np.ndarray, void: np.ndarray, floor=None) -> np.ndarray:
    """The pits: each region of no data and ``floor`` ground the artwork draws as void over
    ``PIT_SHARE`` of. Of one that reaches the field's edge only the floor, as the rest is
    left empty anyway."""
    floor = np.zeros(nodata.shape, bool) if floor is None else floor
    labels, count = ndimage.label(nodata | floor)
    if not count:
        return np.zeros(nodata.shape, bool)
    share = ndimage.mean(void, labels, np.arange(1, count + 1))
    keep = np.concatenate([[False], share >= PIT_SHARE])
    edge = np.zeros(count + 1, bool)
    edge[np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))] = True
    return keep[labels] & (floor | ~edge[labels])


def fill_holes(ground_m, known, hole_max: int = HOLE_MAX_TEXELS, keep_out=None):
    """Biharmonic fill of every unknown component that is small and off the field's edge.

    Returns ``(filled copy, hole mask, stats)``. Anything else unknown stays NaN, and so
    does ``keep_out``, which bounds the solve like ground outside it.
    """
    out = ground_m.astype(np.float32).copy()
    unknown = ~known if keep_out is None else ~known & ~keep_out
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
        ring = ndimage.binary_dilation(hole, iterations=2) & anchor
        if not ring.any():  # walled in by a pit: nothing beside it to span
            continue
        values = np.nan_to_num(out[rs, cs]).astype(np.float64)
        got = solve(values, anchor, hole, 2)
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
    void=None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """The rebuilt lattices: ``(heights_dm, ground_dm, source, meta)``.

    ``ground_dm`` is the kernel's lattice as ``ground_lattice`` and ``terrain_lattice``
    made it (cliff removed, landscape at 7.8 mm). ``heights_dm`` is that ground with the
    cliff province's own heights put back unchanged. Both are float32 with ``nodata``.
    ``void`` is where the artwork draws void (``gamedata.water.channel.artwork_planes``): the
    no-data holes and the ground below ``PIT_FLOOR_M`` it draws as pits are left empty.
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

    floor = (height_dm != nodata) & (height_dm <= np.float32(PIT_FLOOR_M * 10.0))
    pit = np.zeros(ground.shape, bool) if void is None else pits(height_dm == nodata, void, floor)
    ground[pit] = np.nan
    known = ~np.isnan(ground)
    ground, holes, hole_stats = fill_holes(ground, known, keep_out=pit)
    level = np.where(water_dm == nodata, np.nan, water_dm / 10.0).astype(np.float32)
    wet = holes & (water_quality > 0) & ~np.isnan(level)
    wet &= ground - np.nan_to_num(level) < WET_IGNORE_M
    ground[wet] = np.minimum(ground[wet], level[wet] - WET_BELOW_M)
    tick("holes")

    # Measured texels are copied from the inputs rather than round-tripped through metres.
    ground_out = np.where(np.isnan(ground), np.float32(nodata), ground * 10.0).astype(np.float32)
    ground_out[land] = ground_dm[land]
    ground_out[pit] = nodata
    heights_dm = np.where(rock, height_dm, ground_out).astype(np.float32)
    heights_dm[pit] = nodata
    source = np.zeros((rows, cols), np.uint8)
    source[land] = SOURCE_LAND
    source[fill] = SOURCE_RASTER
    source[band] = SOURCE_SEAM
    source[holes] = SOURCE_HOLE
    source[rock] = SOURCE_ROCK
    source[heights_dm == nodata] = SOURCE_NONE
    source[pit] = SOURCE_PIT
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
        "pits": {
            "share": PIT_SHARE,
            "floor_m": PIT_FLOOR_M,
            "holes": int(ndimage.label(pit)[1]),
            "texels": int(pit.sum()),
            "floor_texels": int((pit & floor).sum()),
        },
        "wet_hole_texels_clamped": int(wet.sum()),
        "share_of_the_field_pct": shares,
        "seconds": timings,
        "open_sea": (
            "left empty, and no depth is invented in the lattice: the render draws the "
            "artwork's water there as the open sea and the rest as the void"
        ),
    }
    return heights_dm, ground_out, source, meta


def ground_lattice(field, heights: np.ndarray) -> tuple[np.ndarray, dict]:
    """The same heights with the CLIFF province removed, which is the surface underneath.

    This is the kernel regime's real input. Interpolating the whole field over a rim
    reconstructs the **fold**: a texel just outside a rock is still a cliff-top height,
    because a cliff-top texel is one of the four the stencil reads, so the drop stays where
    the 1 m lattice put it at any output resolution. Interpolating the lattice UNDERNEATH --
    the landscape and the fill, which are continuous surfaces the game evaluates itself --
    puts the ground where the ground is and lets the rasterised rock decide its own
    silhouette on top of it.

    The holes this leaves are handled by the sampler: where the 4x4 stencil is not whole it
    falls back to 2x2, where nothing under it is known it says so, and the caller
    substitutes the whole field's fold there -- inside a formation, where the rock covers the
    pixel and answers it anyway.
    """
    cliff = np.isin(field._prov, hf.PROV_CLIFF_VALUES)
    ground = np.where(cliff, np.float32(hf.NODATA), heights).astype(np.float32)
    known = field._height_dm != hf.NODATA
    return ground, {
        "role": (
            "the landscape and fill lattices with the cliff province removed, which is what "
            "the kernel regime interpolates. Interpolating the composed field instead "
            "reconstructs its 1 m fold, and a rim reconstructed from a fold is a 1 m "
            "staircase at any output resolution."
        ),
        "removed_share_of_the_field": round(100 * float(cliff.mean()), 2),
        "lattice_share_of_the_field": round(100 * float((known & ~cliff).mean()), 2),
        "where_it_knows_nothing": (
            "inside a formation big enough that no landscape texel survives under it. There "
            "the whole field's own fold stands in, and the rock's coverage is 1, so the rock "
            "is the answer either way"
        ),
    }


def terrain_lattice(field, ground: np.ndarray) -> tuple[np.ndarray, dict]:
    """``ground`` with its landscape replaced by ``terrain.u16.z``, in float decimetres.

    Written wherever the bare landscape has a sample and the province is landscape or
    cliff, so the lattice under a rock is the real terrain rather than a hole. Fill keeps
    its value for ``map_fill`` to rebuild. A field without the plane comes back unchanged.
    """
    plane = field._plane(hf.TERRAIN_NAME) if hasattr(field, "_plane") else None
    grid = getattr(field, "_terrain_grid", None)
    if plane is None or grid is None or grid.get("row_off") is None:
        return ground, {"absent": f"no usable {hf.TERRAIN_NAME}; the decimetre plane is drawn"}
    rows, cols = plane.shape
    window = (
        slice(grid["row_off"], grid["row_off"] + rows),
        slice(grid["col_off"], grid["col_off"] + cols),
    )
    raw = np.asarray(plane)
    z_dm = ((raw.astype(np.float32) - grid["zero"]) / grid["units_per_m"] + grid["offset_m"]) * (
        hf.DM_PER_M
    )
    prov = np.asarray(field._prov)[window]
    use = (raw != 0) & np.isin(prov, (hf.PROV_LANDSCAPE, *hf.PROV_CLIFF_VALUES))
    out = ground.copy()
    target = out[window]
    target[use] = z_dm[use]
    landscape = use & (prov == hf.PROV_LANDSCAPE)
    moved = np.abs(z_dm[landscape] - np.asarray(field._height_dm)[window][landscape])
    return out, {
        "plane": hf.TERRAIN_NAME,
        "vertical_step_m": round(1.0 / grid["units_per_m"], 5),
        "landscape_texels": int(landscape.sum()),
        "under_cliff_texels": int((use & ~landscape).sum()),
        "landscape_moved_max_m": round(float(moved.max()) / hf.DM_PER_M, 4) if moved.size else 0.0,
    }


def fill_from_raster(field, ground, raster_m, raster_ok, void=None) -> tuple:
    """``fill_field`` on this field: ``(heights_dm, ground_dm, meta)``."""
    shape = field._height_dm.shape
    quality = field._water_quality_raster()
    water = field._water_raster()
    heights, rebuilt, _source, meta = fill_field(
        ground_dm=ground,
        height_dm=np.asarray(field._height_dm),
        prov=np.asarray(field._prov),
        water_quality=np.zeros(shape, np.uint8) if quality is None else np.asarray(quality),
        water_dm=np.full(shape, hf.NODATA, np.int16) if water is None else np.asarray(water),
        raster_m=raster_m,
        raster_ok=raster_ok,
        field_origin_cm=(field.x0_cm, field.y0_cm),
        spacing_cm=field.spacing_cm,
        raster_box_cm=BASELINE_BOX_CM,
        nodata=hf.NODATA,
        fill_value=hf.PROV_FILL,
        rock_values=hf.PROV_CLIFF_VALUES,
        void=void,
    )
    return heights, rebuilt, {"raster": BASELINE_PATH.rsplit("/", 1)[-1], **meta}


def rebuild_lattice(field, ground, store, void=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """The interface raster read out of the container, then ``fill_from_raster``."""
    z_cm, ok = read_baseline(store)
    return fill_from_raster(field, ground, z_cm / np.float32(100.0), ok, void)
