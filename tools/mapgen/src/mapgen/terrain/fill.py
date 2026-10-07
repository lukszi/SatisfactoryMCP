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
the edge of the field stays empty, and so does a pit and the fill past the artwork's world
rim (``terrain.void``): the render draws the open sea or the void there. The numbers behind
every constant are in docs/map/renders.md section 26.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import NamedTuple

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.frame import FILL_RASTER_BOX_CM
from mapgen.gamedata.level.fill_raster import FILL_RASTER_PATH, read_fill_raster
from mapgen.terrain.solve import jacobi_cg
from mapgen.terrain.void import (
    PIT_FLOOR_M,
    PIT_SHARE,
    RIM_CORE_TEXELS,
    RIM_REACH_TEXELS,
    edge_labels,
    pits,
    void_past_rim,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, U8Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "HOLE_FALLBACK_M",
    "HOLE_MAX_TEXELS",
    "HOLE_RING_TEXELS",
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
    "SOURCE_RIM",
    "SOURCE_ROCK",
    "SOURCE_SEAM",
    "WET_BELOW_M",
    "WET_IGNORE_M",
    "FillResult",
    "HoleFill",
    "blend_seam",
    "cosine_taper",
    "fill_field",
    "fill_from_raster",
    "fill_holes",
    "ground_lattice",
    "harmonic_fill",
    "nearest_fill",
    "raster_positions",
    "rebuild_lattice",
    "reconstruct_raster",
    "relax",
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
SOURCE_PIT, SOURCE_RIM = 6, 7
SOURCE_NAMES = {
    SOURCE_NONE: "no data: drawn as the open sea or the void, as the artwork has it",
    SOURCE_LAND: "landscape, measured",
    SOURCE_ROCK: "cliff province, copied unchanged",
    SOURCE_RASTER: "fill, rebuilt from the interface raster",
    SOURCE_SEAM: "fill inside the seam band",
    SOURCE_HOLE: "interior hole, filled",
    SOURCE_PIT: "a hole or a pit's floor the artwork draws as void, left empty",
    SOURCE_RIM: "fill past the artwork's world rim, left empty: drawn as the void",
}


@dataclass
class HoleFill:
    """What ``fill_holes`` did: holes and texels filled, harmonic fallbacks, worst overshoot."""

    holes: int = 0
    texels: int = 0
    harmonic_fallback: int = 0
    overshoot_max_m: float = 0.0


class FillResult(NamedTuple):
    """``fill_field``: heights with the cliff put back, the ground under it, each texel's
    ``SOURCE_*``, and the sidecar's record."""

    heights_dm: F32Grid
    ground_dm: F32Grid
    source: U8Grid
    meta: JsonObject


class _Provinces(NamedTuple):
    """Which texels each step owns, for composing the lattices and their source plane."""

    land: BoolMask
    fill: BoolMask
    band: BoolMask
    holes: BoolMask
    rock: BoolMask
    pit: BoolMask
    rim: BoolMask


def raster_positions(count: int, origin_cm: float, spacing_cm: float, lo_cm: float,
                     hi_cm: float, px: int) -> F64Grid:  # fmt: skip
    """Fractional raster coordinates (texel centres) of a run of field vertices."""
    world = origin_cm + np.arange(count, dtype=np.float64) * spacing_cm
    return (world - lo_cm) / (hi_cm - lo_cm) * px - 0.5


def nearest_fill(values: NDArray[np.floating], known: BoolMask) -> NDArray[np.floating]:
    """Every unknown texel takes its nearest known neighbour's value."""
    index = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    return values[tuple(index)]


def reconstruct_raster(z_m: NDArray[np.floating], ok: BoolMask, rows: F64Grid, cols: F64Grid,
                       chunk: int = 512) -> tuple[F32Grid, BoolMask]:  # fmt: skip
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


def _laplacian(active: BoolMask) -> sp.csr_matrix:
    """Graph Laplacian of the 4-neighbour grid restricted to ``active``."""
    h, w = int(active.shape[0]), int(active.shape[1])
    idx = np.arange(h * w).reshape(h, w)
    right = active[:, :-1] & active[:, 1:]
    down = active[:-1, :] & active[1:, :]
    a = np.concatenate([idx[:, :-1][right], idx[:-1, :][down]])
    b = np.concatenate([idx[:, 1:][right], idx[1:, :][down]])
    adj = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(h * w, h * w))
    adj = (adj + adj.T).tocsr()
    return (sp.diags(np.asarray(adj.sum(axis=1)).ravel()) - adj).tocsr()


def harmonic_fill(values: NDArray[np.floating], known: BoolMask, unknown: BoolMask,
                  order: int) -> F64Grid:  # fmt: skip
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


def relax(values: NDArray[np.floating], known: BoolMask, unknown: BoolMask, scale: float,
          far: float, pull: NDArray[np.floating] | None = None,
          target: NDArray[np.floating] | None = None) -> F64Grid:  # fmt: skip
    """``values`` with ``unknown`` replaced by a membrane that settles towards ``far``.

    The screened Poisson equation: its border's offset from ``far`` dies away over about
    ``scale`` texels. Known texels are Dirichlet, anything else outside the domain. The
    screening keeps the system well conditioned, so conjugate gradients solve it at any size.
    ``pull`` (per texel, in 1 / texels squared) also draws it towards ``target``: a pull that
    changes from texel to texel bends the membrane's slope but never breaks it.
    """
    out = values.astype(np.float64).ravel().copy()
    u, k = unknown.ravel(), known.ravel()
    if not u.any():
        return out.reshape(values.shape)
    screen = 1.0 / (scale * scale)
    weight = np.full(int(u.sum()), screen)
    rhs = np.full(len(weight), screen * far)
    if pull is not None:
        if target is None:
            raise ValueError("a pull needs the target it pulls towards")
        weight += pull.ravel()[u]
        rhs += pull.ravel()[u] * target.ravel()[u]
    lap = _laplacian(known | unknown)
    rhs -= lap[u][:, k] @ out[k]
    a_uu = (lap[u][:, u] + sp.diags(weight)).tocsr()
    out[u] = jacobi_cg(a_uu, rhs, np.full(len(rhs), far), rtol=1e-6)
    return out.reshape(values.shape)


def tiled_harmonic(values: F32Grid, known: BoolMask, unknown: BoolMask) -> F32Grid:
    """``harmonic_fill(order=1)`` over the field in tiles with a halo, so each system stays small."""
    out = values.astype(np.float32).copy()
    rows, cols = values.shape
    for r in range(0, rows, SOLVE_TILE):
        for c in range(0, cols, SOLVE_TILE):
            block = (slice(r, min(r + SOLVE_TILE, rows)), slice(c, min(c + SOLVE_TILE, cols)))
            if not unknown[block].any():
                continue
            rs = slice(max(r - SOLVE_HALO, 0), min(r + SOLVE_TILE + SOLVE_HALO, rows))
            cs = slice(max(c - SOLVE_HALO, 0), min(c + SOLVE_TILE + SOLVE_HALO, cols))
            got = harmonic_fill(values[rs, cs], known[rs, cs], unknown[rs, cs], 1)
            inner = got[
                r - rs.start : block[0].stop - rs.start, c - cs.start : block[1].stop - cs.start
            ]
            mask = unknown[block]
            out[block][mask] = inner[mask]
    return out


def cosine_taper(distance: NDArray[np.floating], band: float) -> F32Grid:
    """1 at the seam, 0 at ``band``, with zero slope at both ends."""
    return (0.5 * (1.0 + np.cos(np.pi * np.clip(distance / band, 0.0, 1.0)))).astype(np.float32)


def blend_seam(rec: NDArray[np.floating], land_m: NDArray[np.floating], land: BoolMask,
               target: BoolMask, band_m: float = SEAM_BAND_M) -> tuple[F32Grid, BoolMask]:  # fmt: skip
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


def fill_holes(ground_m: NDArray[np.floating], known: BoolMask, hole_max: int = HOLE_MAX_TEXELS,
               keep_out: BoolMask | None = None) -> tuple[F32Grid, BoolMask, HoleFill]:  # fmt: skip
    """Biharmonic fill of every unknown component that is small and off the field's edge.

    Returns ``(filled copy, hole mask, what was filled)``. Anything else unknown stays NaN,
    and so does ``keep_out``, which bounds the solve like ground outside it.
    """
    out = ground_m.astype(np.float32).copy()
    unknown = ~known if keep_out is None else ~known & ~keep_out
    labels, count = ndimage.label(unknown)
    holes = np.zeros(unknown.shape, bool)
    stats = HoleFill()
    if not count:
        return out, holes, stats
    sizes = ndimage.sum(unknown, labels, np.arange(1, count + 1))
    border = set(edge_labels(labels).tolist())
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
        got = harmonic_fill(values, anchor, hole, 2)
        lo, hi = values[ring].min(), values[ring].max()
        if got[hole].max() > hi + HOLE_FALLBACK_M or got[hole].min() < lo - HOLE_FALLBACK_M:
            got = harmonic_fill(values, anchor, hole, 1)
            stats.harmonic_fallback += 1
        over = max(float(got[hole].max() - hi), float(lo - got[hole].min()), 0.0)
        stats.overshoot_max_m = max(stats.overshoot_max_m, round(over, 3))
        out[rs, cs][hole] = got[hole]
        holes[rs, cs] |= hole
        stats.holes += 1
        stats.texels += int(hole.sum())
    return out, holes, stats


def _raster_on_field(raster_m: NDArray[np.floating], raster_ok: BoolMask, shape: tuple[int, int],
                     field_origin_cm: tuple[float, float], spacing_cm: float,
                     raster_box_cm: tuple[float, float, float, float]) -> tuple[F32Grid, BoolMask]:  # fmt: skip
    """The interface raster rebuilt at every field vertex, and where it is whole."""
    rows, cols = shape
    x0, x1, y0, y1 = raster_box_cm
    px = raster_m.shape[0]
    fr = raster_positions(rows, field_origin_cm[1], spacing_cm, y0, y1, px)
    fc = raster_positions(cols, field_origin_cm[0], spacing_cm, x0, x1, px)
    return reconstruct_raster(raster_m, raster_ok, fr, fc)


def _wet_holes(ground: F32Grid, holes: BoolMask, water_dm: NDArray[np.number],
               water_quality: NDArray[np.integer], nodata: int) -> BoolMask:  # fmt: skip
    """Hold the filled holes under water below its surface; returns the texels it moved."""
    level = np.where(water_dm == nodata, np.nan, water_dm / 10.0).astype(np.float32)
    wet = holes & (water_quality > 0) & ~np.isnan(level)
    wet &= ground - np.nan_to_num(level) < WET_IGNORE_M
    ground[wet] = np.minimum(ground[wet], level[wet] - WET_BELOW_M)
    return wet


def _composed(ground: F32Grid, ground_dm: NDArray[np.number], height_dm: NDArray[np.number],
              nodata: int, where: _Provinces) -> tuple[F32Grid, F32Grid, U8Grid]:  # fmt: skip
    """``(heights_dm, ground_dm, source)``: measured texels copied from the inputs rather
    than round-tripped through metres, the cliff put back, the pits and the rim emptied."""
    ground_out = np.where(np.isnan(ground), np.float32(nodata), ground * 10.0).astype(np.float32)
    ground_out[where.land] = ground_dm[where.land]
    ground_out[where.pit | where.rim] = nodata
    heights_dm = np.where(where.rock, height_dm, ground_out).astype(np.float32)
    heights_dm[where.pit | where.rim] = nodata
    source = np.zeros(heights_dm.shape, np.uint8)
    source[where.land] = SOURCE_LAND
    source[where.fill] = SOURCE_RASTER
    source[where.band] = SOURCE_SEAM
    source[where.holes] = SOURCE_HOLE
    source[where.rock] = SOURCE_ROCK
    source[heights_dm == nodata] = SOURCE_NONE
    source[where.pit] = SOURCE_PIT
    source[where.rim] = SOURCE_RIM
    return heights_dm, ground_out, source


def _rim_meta(rim: BoolMask) -> JsonObject:
    """The sidecar's record of the fill left empty past the artwork's world rim."""
    return {
        "core_texels": RIM_CORE_TEXELS,
        "reach_texels": RIM_REACH_TEXELS,
        "regions": int(ndimage.label(rim)[1]),
        "texels": int(rim.sum()),
    }


def _fill_meta(source: U8Grid, holes: HoleFill, empty: tuple[BoolMask, BoolMask, BoolMask],
               wet: BoolMask, timings: dict[str, float]) -> JsonObject:  # fmt: skip
    pit, floor, rim = empty
    shares: JsonObject = {
        SOURCE_NAMES[key]: round(100 * float((source == key).mean()), 3) for key in SOURCE_NAMES
    }
    return {
        "raster_sigma_texels": RASTER_SIGMA_TEXELS,
        "raster_bias_m": RASTER_BIAS_M,
        "seam_band_m": SEAM_BAND_M,
        "hole_max_texels": HOLE_MAX_TEXELS,
        "hole_fallback_m": HOLE_FALLBACK_M,
        "holes": asdict(holes),
        "pits": {
            "share": PIT_SHARE,
            "floor_m": PIT_FLOOR_M,
            "holes": int(ndimage.label(pit)[1]),
            "texels": int(pit.sum()),
            "floor_texels": int((pit & floor).sum()),
        },
        "past_the_rim": _rim_meta(rim),
        "wet_hole_texels_clamped": int(wet.sum()),
        "share_of_the_field_pct": shares,
        "seconds": {**timings},
        "open_sea": (
            "left empty, and no depth is invented in the lattice: the render draws the "
            "artwork's water there as the open sea and the rest as the void"
        ),
    }


class _Timings:
    """Seconds per stage, each stage timed from the end of the one before."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self._clock = time.perf_counter()

    def tick(self, name: str) -> None:
        now = time.perf_counter()
        self.seconds[name] = round(now - self._clock, 1)
        self._clock = now


def fill_field(
    *,
    ground_dm: NDArray[np.number],
    height_dm: NDArray[np.number],
    prov: NDArray[np.integer],
    water_quality: NDArray[np.integer],
    water_dm: NDArray[np.number],
    raster_m: NDArray[np.floating],
    raster_ok: BoolMask,
    field_origin_cm: tuple[float, float],
    spacing_cm: float,
    raster_box_cm: tuple[float, float, float, float],
    nodata: int,
    fill_value: int,
    rock_values: tuple[int, ...],
    void: BoolMask | None = None,
) -> FillResult:
    """The rebuilt lattices: ``(heights_dm, ground_dm, source, meta)``.

    ``ground_dm`` is the kernel's lattice as ``ground_lattice`` and ``terrain_lattice``
    made it (cliff removed, landscape at 7.8 mm). ``heights_dm`` is that ground with the
    cliff province's own heights put back unchanged. Both are float32 with ``nodata``.
    ``void`` is where the artwork draws void (``gamedata.water.channel.artwork_planes``): the
    fill past its world rim (``terrain.void.void_past_rim``), the no-data holes and the
    ground below ``PIT_FLOOR_M`` it draws as pits are left empty.
    """
    timings = _Timings()
    rock = np.isin(prov, rock_values)
    fill = prov == fill_value
    rim = fill & void_past_rim(void) if void is not None else np.zeros(fill.shape, bool)
    fill &= ~rim
    ground = np.where((ground_dm == nodata) | rim, np.nan, ground_dm / 10.0).astype(np.float32)
    land = ~np.isnan(ground) & ~fill
    timings.tick("rim")
    rec, whole = _raster_on_field(
        raster_m, raster_ok, ground_dm.shape, field_origin_cm, spacing_cm, raster_box_cm
    )
    ground[fill] = rec[fill]
    timings.tick("raster")

    dry_fill = fill & (water_quality == 0)
    blended, band = blend_seam(rec, np.nan_to_num(ground), land & whole, dry_fill & whole)
    ground[band] = blended[band]
    del blended, rec
    timings.tick("seam")

    pit, floor = pits(height_dm, (height_dm == nodata) | rim, void)
    pit &= ~rim
    ground[pit] = np.nan
    ground, holes, hole_fill = fill_holes(ground, ~np.isnan(ground), keep_out=pit | rim)
    wet = _wet_holes(ground, holes, water_dm, water_quality, nodata)
    timings.tick("holes")

    where = _Provinces(land, fill, band, holes, rock, pit, rim)
    heights_dm, ground_out, source = _composed(ground, ground_dm, height_dm, nodata, where)
    timings.tick("compose")
    meta = _fill_meta(source, hole_fill, (pit, floor, rim), wet, timings.seconds)
    return FillResult(heights_dm, ground_out, source, meta)


def ground_lattice(field: hf.Field, heights: F32Grid) -> tuple[F32Grid, JsonObject]:
    """The same heights with the CLIFF province removed: the surface under the rocks.

    The kernel regime interpolates this and never the composed field, whose 1 m fold comes
    back as a staircase at any output resolution (docs/spatial-and-map.md section 20). Where
    it knows nothing, inside a formation, the caller substitutes the fold and the rock answers.
    """
    cliff = np.isin(np.asarray(field.provenance_plane), hf.PROV_CLIFF_VALUES)
    ground = np.where(cliff, np.float32(hf.NODATA), heights).astype(np.float32)
    known = np.asarray(field.height_dm) != hf.NODATA
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


def terrain_lattice(field: hf.Field, ground: F32Grid) -> tuple[F32Grid, JsonObject]:
    """``ground`` with its landscape replaced by ``terrain.u16.z``, in float decimetres.

    Written wherever the bare landscape has a sample and the province is landscape or
    cliff, so the lattice under a rock is the real terrain rather than a hole. Fill keeps
    its value for ``rebuild_lattice`` to rebuild. A field without the plane comes back unchanged.
    """
    plane = field.plane(hf.TERRAIN_NAME)
    grid = field.terrain_grid
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
    prov = np.asarray(field.provenance_plane)[window]
    use = (raw != 0) & np.isin(prov, (hf.PROV_LANDSCAPE, *hf.PROV_CLIFF_VALUES))
    out = ground.copy()
    target = out[window]
    target[use] = z_dm[use]
    landscape = use & (prov == hf.PROV_LANDSCAPE)
    moved = np.abs(z_dm[landscape] - np.asarray(field.height_dm)[window][landscape])
    return out, {
        "plane": hf.TERRAIN_NAME,
        "vertical_step_m": round(1.0 / grid["units_per_m"], 5),
        "landscape_texels": int(landscape.sum()),
        "under_cliff_texels": int((use & ~landscape).sum()),
        "landscape_moved_max_m": round(float(moved.max()) / hf.DM_PER_M, 4) if moved.size else 0.0,
    }


def fill_from_raster(field: hf.Field, ground: F32Grid, raster_m: NDArray[np.floating],
                     raster_ok: BoolMask, void: BoolMask | None = None
                     ) -> tuple[F32Grid, F32Grid, JsonObject]:  # fmt: skip
    """``fill_field`` on this field: ``(heights_dm, ground_dm, meta)``."""
    heights_dm = np.asarray(field.height_dm)
    shape = heights_dm.shape
    quality = field.water_quality_raster()
    water = field.water_raster()
    filled = fill_field(
        ground_dm=ground,
        height_dm=heights_dm,
        prov=np.asarray(field.provenance_plane),
        water_quality=np.zeros(shape, np.uint8) if quality is None else np.asarray(quality),
        water_dm=np.full(shape, hf.NODATA, np.int16) if water is None else np.asarray(water),
        raster_m=raster_m,
        raster_ok=raster_ok,
        field_origin_cm=(field.x0_cm, field.y0_cm),
        spacing_cm=field.spacing_cm,
        raster_box_cm=FILL_RASTER_BOX_CM,
        nodata=hf.NODATA,
        fill_value=hf.PROV_FILL,
        rock_values=hf.PROV_CLIFF_VALUES,
        void=void,
    )
    meta: JsonObject = {"raster": FILL_RASTER_PATH.rsplit("/", 1)[-1], **filled.meta}
    return filled.heights_dm, filled.ground_dm, meta


def rebuild_lattice(field: hf.Field, ground: F32Grid, store: IoStore, void: BoolMask | None = None
                    ) -> tuple[F32Grid, F32Grid, JsonObject]:  # fmt: skip
    """The interface raster read out of the container, then ``fill_from_raster``."""
    z_cm, ok = read_fill_raster(store)
    return fill_from_raster(field, ground, z_cm / np.float32(100.0), ok, void)
