"""Sampling the 1 m field onto the output frame: the kernels, their taps and the direct mask.

The gathers run as numba kernels unless ``mapgen.jit`` selects this numpy, their reference
(docs/map/renders.md section 41).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple, TypeAlias

import numpy as np
from numpy.typing import ArrayLike, NDArray

from mapgen.cache import Plane
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.meshes import DIRECT_SAMPLES_MIN
from mapgen.jit import kernels_on
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "STENCIL_WHOLE",
    "AxisTaps",
    "ClassMix",
    "PchipTaps",
    "Taps",
    "class_taps",
    "direct_mask",
    "frame_coordinates",
    "grid_position",
    "patch_noise",
    "pchip_1d",
    "pchip_slope",
    "reads_nothing",
    "resample",
    "resample_pchip",
    "sample_coverage",
    "sample_plain",
    "sample_surface",
    "taps_cubic",
    "taps_footprint",
    "taps_linear",
    "taps_pchip",
]

#: One axis's taps: ``(index, weight)``, each ``(taps, N)`` -- the source rows or columns
#: each output pixel reads, and how much of each (float32).
AxisTaps: TypeAlias = tuple[I64Grid, NDArray[np.floating]]
#: The row taps, then the column taps.
Taps: TypeAlias = tuple[AxisTaps, AxisTaps]


class PchipTaps(NamedTuple):
    """The four clamped indices around each position and its cell fraction.

    A type of its own because PCHIP's weights depend on the data, so ``sample_surface`` has
    to know it was handed positions rather than weights.
    """

    indices: I64Grid
    fraction: F32Grid


#: How far the cubic stencil's own weights may fall from one before this file stops
#: believing it. They sum to one exactly wherever every texel under the stencil has a value,
#: so anything below this is a stencil straddling the edge of the data, where a kernel with
#: negative lobes has no business extrapolating.
STENCIL_WHOLE = 1.0 - 1e-4


# --------------------------------------------------------------------------------------
# The direct regime's provenance: which drawn texels are measurements.
# --------------------------------------------------------------------------------------


def direct_mask(field: hf.Field, spacing_m: float) -> tuple[U8Grid | None, JsonObject]:
    """Where the geometry was sampled finer than the output texel: a 0/255 mask at 1 m.

    ``None`` when the field has no ``density.u8.z``, which is not "no samples anywhere": the
    caller refuses rather than assumes. The rule is the field's, one source vertex
    (``DIRECT_SAMPLES_MIN``) under the output texel, scaled by its area. A mask and not a
    weight: it names what was drawn, never gates it, so it is read nearest and never blurred
    (docs/map/renders.md section 20).
    """
    density = field.density_raster()
    if density is None:
        return None, {
            "absent": (
                f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its "
                "texels are measurements and which are the rasteriser interpolating across "
                "a triangle wider than a texel. That is the only thing the two-regime "
                "sampler switches on."
            )
        }
    need = DIRECT_SAMPLES_MIN / (spacing_m * spacing_m)
    qualifies: BoolMask = density >= min(need, 255.0)
    share = float(qualifies.mean())
    cliff = np.isin(np.asarray(field.provenance_plane), hf.PROV_CLIFF_VALUES)
    return (qualifies.astype(np.uint8) * 255), {
        "plane": hf.DENSITY_NAME,
        "rule": (
            f"at least {DIRECT_SAMPLES_MIN:g} source vertex under an output texel of "
            f"{spacing_m:.4f} m, i.e. density >= {need:.2f} per 1 m texel"
        ),
        "samples_min_per_output_texel": DIRECT_SAMPLES_MIN,
        "density_min_per_field_texel": round(float(need), 2),
        "qualifying_share_of_the_field": round(100 * share, 3),
        "qualifying_share_of_the_cliff_province": round(
            100 * float(qualifies[cliff].mean()) if cliff.any() else 0.0, 2
        ),
        "role": (
            "1 where the cliff geometry sampled the ground finer than this render draws it, "
            "0 where it did not. Provenance and not a gate: what decides that the rocks are "
            "drawn is their own coverage of the pixel, and this decides what to CALL what "
            "was drawn -- a measurement, or the plane of a triangle wider than a texel."
        ),
    }


# --------------------------------------------------------------------------------------
# Sampling the field onto the output frame.
# --------------------------------------------------------------------------------------


def frame_coordinates(size: int) -> tuple[F64Grid, F64Grid]:
    """Pixel-centre world coordinates, centimetres, for a ``size`` square on the frame.

    Row 0 is the northern edge and column 0 the western one, which is the artwork sheet's
    order and the heightfield's: game +Y is south, so the smallest y is the top of the
    picture. Nothing here flips anything.
    """
    x = BOUNDS_M["x_min_m"] * 100 + (np.arange(size, dtype=np.float64) + 0.5) * (
        (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    )
    y = BOUNDS_M["y_min_m"] * 100 + (np.arange(size, dtype=np.float64) + 0.5) * (
        (BOUNDS_M["y_max_m"] - BOUNDS_M["y_min_m"]) * 100 / size
    )
    return x, y


def grid_position(coordinate: F64Grid, origin: float, spacing: float, limit: int) -> F64Grid:
    """Where a run of world coordinates falls on a raster's index axis, clamped to it.

    Clamped rather than masked: the frame is half a metre wider than the field's last vertex
    on two sides, so masking would invent a half-pixel strip of no-data down the east and
    south edges. Past the edge the nearest vertex is what the field's own reader gives too.
    """
    return np.clip((coordinate - origin) / spacing, 0.0, limit - 1.0)


def taps_linear(position: F64Grid, limit: int) -> AxisTaps:
    """The two flanking indices and their weights: ``(2, N)`` each. Plain bilinear.

    What the cubic kernel falls back to where its stencil runs off the data, and what every
    category plane is sampled with: a coverage fraction outside [0, 1] is not one.
    """
    low = np.minimum(np.floor(position).astype(np.int64), max(limit - 2, 0))
    fraction = (position - low).astype(np.float32)
    index = np.stack([low, np.minimum(low + 1, limit - 1)])
    return index, np.stack([1.0 - fraction, fraction])


def taps_footprint(position: F64Grid, width: float, limit: int) -> AxisTaps:
    """A pixel ``width`` texels wide as taps: each texel it covers, weighted by the overlap.

    A texel is the cell of its vertex, half a texel either side. Where the pixel is no wider
    than a texel this is ``taps_linear``, so a sheet finer than the grid samples as before.
    """
    if width <= 1.0:
        return taps_linear(position, limit)
    lo = position - width / 2.0
    first = np.floor(lo + 0.5).astype(np.int64)
    index = first[None, :] + np.arange(int(np.ceil(width)) + 1)[:, None]
    inside = np.minimum(position + width / 2.0, index + 0.5) - np.maximum(lo, index - 0.5)
    weight = (np.clip(inside, 0.0, None) / width).astype(np.float32)
    return np.clip(index, 0, limit - 1), weight


def _four_taps(position: F64Grid, limit: int) -> tuple[I64Grid, F32Grid]:
    """The four indices around each position, clamped to the grid, and its cell fraction."""
    base = np.floor(position).astype(np.int64)
    t = (position - base).astype(np.float32)
    index = np.stack([np.clip(base + offset, 0, limit - 1) for offset in (-1, 0, 1, 2)])
    return index, t


def taps_cubic(position: F64Grid, limit: int) -> AxisTaps:
    """The four indices around a position and their Catmull-Rom weights: ``(4, N)`` each.

    Cubic convolution with a = -1/2, the interpolating member of that family: it passes
    through every sample it is given and it is **C1**. The hillshade is a function of the
    first derivative, and a C0 kernel sampled at half its own texel spacing rules the relief
    into 1 m squares. The weights sum to exactly one, which is what lets the no-data
    bookkeeping below use their sum as a completeness test.

    Indices are clamped to the grid, so a stencil hanging off the edge repeats the edge
    vertex -- the same answer the field's own reader gives past its last row.
    """
    index, t = _four_taps(position, limit)
    weight = np.stack(
        [
            0.5 * t * (t * (2.0 - t) - 1.0),
            0.5 * (t * t * (3.0 * t - 5.0) + 2.0),
            0.5 * t * (t * (4.0 - 3.0 * t) + 1.0),
            0.5 * t * t * (t - 1.0),
        ]
    )
    return index, weight


def taps_pchip(position: F64Grid, limit: int) -> PchipTaps:
    """The same four indices as ``taps_cubic``, with the fraction instead of weights."""
    return PchipTaps(*_four_taps(position, limit))


def pchip_slope(left: F32Grid, right: F32Grid) -> F32Grid:
    """Fritsch-Butland: the harmonic mean of two secants, zero unless they agree in sign."""
    agree = left * right > 0
    total = np.where(agree, left + right, np.float32(1.0))
    return np.where(agree, 2.0 * left * right / total, np.float32(0.0)).astype(np.float32)


def pchip_1d(
    p0: F32Grid, p1: F32Grid, p2: F32Grid, p3: F32Grid, t: F32Grid
) -> NDArray[np.floating]:
    """Cubic Hermite between ``p1`` and ``p2``; never leaves ``[min, max]`` of the two."""
    middle = p2 - p1
    d1 = pchip_slope(p1 - p0, middle)
    d2 = pchip_slope(middle, p3 - p2)
    t2 = t * t
    t3 = t2 * t
    return (
        (2.0 * t3 - 3.0 * t2 + 1.0) * p1
        + (t3 - 2.0 * t2 + t) * d1
        + (3.0 * t2 - 2.0 * t3) * p2
        + (t3 - t2) * d2
    )


def _pchip_taps(values: Sequence[F32Grid], t: F32Grid) -> NDArray[np.floating]:
    return pchip_1d(values[0], values[1], values[2], values[3], t)


def _slab(
    raster: Plane, row_index: I64Grid, col_index: I64Grid
) -> tuple[NDArray[np.generic], int, I64Grid]:
    """The block of ``raster`` the taps read: the run of rows between their first and last,
    cut to the columns likewise. Returns it, its first row, and the column taps into it."""
    low, left = int(row_index.min()), int(col_index.min())
    block: NDArray[np.generic] = raster[
        low : int(row_index.max()) + 1, left : int(col_index.max()) + 1
    ]
    return block, low, col_index - left


def _contiguous(slab: NDArray[np.generic]) -> NDArray[np.generic]:
    """The source block as the kernels take it: one C-ordered array, a view where it is one.

    numba has no float16 arrays, so those come as the float32 that numpy's path reads too.
    """
    return np.ascontiguousarray(slab, np.float32 if slab.dtype == np.float16 else None)


def _axis(index: I64Grid, weight: NDArray[np.floating]) -> tuple[I64Grid, NDArray[np.floating]]:
    """One axis's taps as the kernels take them: a plain pair of C-ordered arrays."""
    return np.ascontiguousarray(index), np.ascontiguousarray(weight)


def resample_pchip(
    raster: Plane, rows: PchipTaps, cols: PchipTaps, nodata: int
) -> tuple[F32Grid, BoolMask]:
    """Separable PCHIP onto the output grid. Returns ``(values, whole)``.

    x first over the contiguous slab of source rows, then y, as ``resample`` does.
    ``whole`` is true where all sixteen texels under the stencil have a value.
    """
    (row_index, row_t), (col_index, col_t) = rows, cols
    slab, low, col_index = _slab(raster, row_index, col_index)
    if kernels_on():
        from mapgen.terrain import kernels

        rows_in, cols_in = _axis(row_index, row_t), _axis(col_index, col_t)
        return kernels.pchip(_contiguous(slab), nodata, low, rows_in, cols_in)
    known = slab != nodata
    values = np.where(known, slab, 0).astype(np.float32)
    across = _pchip_taps([values[:, col_index[tap]] for tap in range(4)], col_t[None, :])
    across_whole = np.logical_and.reduce([known[:, col_index[tap]] for tap in range(4)])
    picked = [row_index[tap] - low for tap in range(4)]
    total = _pchip_taps([across[index] for index in picked], row_t[:, None])
    whole = np.logical_and.reduce([across_whole[index] for index in picked])
    return total.astype(np.float32), whole


def resample(
    raster: Plane, rows: AxisTaps, cols: AxisTaps, nodata: int | None
) -> tuple[F32Grid, F32Grid]:
    """Separable interpolation of ``raster`` onto the output grid. Returns (sum, weight).

    Separable, and in that order: the output rows a band needs come from one CONTIGUOUS run
    of source rows, so the row axis is a slice, cut to the columns the taps read, and only the
    column axis is a gather. Interpolating in x first over that short slab and in y second
    over the result is four gathers of the small array and four of the large one, against
    sixteen of the large one if the 4x4 stencil were evaluated directly.

    The no-data bookkeeping rides along: every tap is multiplied by whether its texel had a
    value, and the weights come back separately, so the caller can tell a whole stencil
    (weight exactly one) from a partial one from nothing at all. ``nodata`` of ``None`` says
    the raster has no holes -- a category coverage plane -- and skips it.
    """
    (row_index, row_weight), (col_index, col_weight) = rows, cols
    slab, low, col_index = _slab(raster, row_index, col_index)
    if kernels_on():
        from mapgen.terrain import kernels

        holes = nodata is not None
        rows_in, cols_in = _axis(row_index, row_weight), _axis(col_index, col_weight)
        return kernels.separable(
            _contiguous(slab), nodata if holes else 0, holes, True, low, rows_in, cols_in
        )
    values = slab.astype(np.float32)
    known = None if nodata is None else (slab != nodata).astype(np.float32)
    if known is not None:
        values = values * known

    across = np.zeros((slab.shape[0], col_index.shape[1]), np.float32)
    across_weight = np.zeros_like(across)
    for tap in range(col_index.shape[0]):
        across += col_weight[tap] * values[:, col_index[tap]]
        if known is None:
            across_weight += col_weight[tap]
        else:
            across_weight += col_weight[tap] * known[:, col_index[tap]]

    total = np.zeros((row_index.shape[1], col_index.shape[1]), np.float32)
    total_weight = np.zeros_like(total)
    for tap in range(row_index.shape[0]):
        picked = row_index[tap] - low
        total += row_weight[tap][:, None] * across[picked]
        total_weight += row_weight[tap][:, None] * across_weight[picked]
    return total, total_weight


def sample_surface(
    raster: Plane, smooth_taps: Taps | tuple[PchipTaps, PchipTaps], linear: Taps, nodata: int
) -> tuple[F32Grid, BoolMask]:
    """A height raster on the output grid: the smooth kernel inside the data, bilinear at its edge.

    Returns ``(values, missing)``. ``smooth_taps`` is a ``(rows, cols)`` pair of
    ``taps_pchip`` or of ``taps_cubic``. Where the 4x4 stencil is whole the C1 answer is
    used; where it is not the 2x2 answer is; and where even that has nothing under it the
    caller paints the page's sea.
    """
    rows, cols = smooth_taps
    if isinstance(rows, PchipTaps) and isinstance(cols, PchipTaps):
        smooth, whole = resample_pchip(raster, rows, cols, nodata)
    else:
        smooth, smooth_weight = resample(raster, rows, cols, nodata)
        whole = smooth_weight >= STENCIL_WHOLE
    flat, flat_weight = resample(raster, *linear, nodata)
    missing = flat_weight <= 0.0
    near = flat / np.where(missing, 1.0, flat_weight)
    return np.where(whole, smooth, near), missing


def reads_nothing(raster: Plane, taps: Taps) -> bool:
    """True when every texel these taps read is zero, so any sample of it is 0.0."""
    (row_index, _rows), (col_index, _cols) = taps
    return not _slab(raster, row_index, col_index)[0].any()


def sample_plain(raster: Plane, taps: Taps) -> F32Grid:
    """A raster with no holes in it, interpolated onto the output grid. Nothing clipped.

    The weights of either kernel sum to one and there is no no-data to renormalise around,
    so the weighted sum IS the answer. For the two rasters this file makes itself: the
    artwork's signed high pass and the feathered province mask, neither of which is a
    coverage and neither of which may be clipped into [0, 1] on the way through.

    ``resample``'s sum alone, in its order, so the same bits without the weight planes.
    """
    (row_index, row_weight), (col_index, col_weight) = taps
    shape = (row_index.shape[1], col_index.shape[1])
    slab, low, col_index = _slab(raster, row_index, col_index)
    if not slab.any():
        return np.zeros(shape, np.float32)
    if kernels_on():
        from mapgen.terrain import kernels

        rows_in, cols_in = _axis(row_index, row_weight), _axis(col_index, col_weight)
        return kernels.separable(_contiguous(slab), 0, False, False, low, rows_in, cols_in)[0]
    values = slab.astype(np.float32)
    across = np.zeros((values.shape[0], shape[1]), np.float32)
    for tap in range(col_index.shape[0]):
        across += col_weight[tap] * values[:, col_index[tap]]
    total = np.zeros(shape, np.float32)
    for tap in range(row_index.shape[0]):
        total += row_weight[tap][:, None] * across[row_index[tap] - low]
    return total


def sample_coverage(plane: Plane, taps: Taps) -> F32Grid:
    """What fraction of the ground under each output pixel is in some category, in [0, 1].

    Bilinear and never cubic: a category is a yes or a no on a 1 m grid, and what is wanted
    from it is coverage. A kernel with negative lobes would answer -0.06 of a texel wet,
    which is not a thing a texel can be.
    """
    return np.clip(sample_plain(plane, taps), 0.0, 1.0)


#: A class plane's bilinear taps: ``(class, weight)`` for each of the four.
ClassTaps: TypeAlias = list[tuple[NDArray[np.integer], F32Grid]]


def class_taps(plane: NDArray[np.integer], taps: Taps) -> ClassTaps:
    """The four bilinear taps of a class plane: ``(class, weight)``, weight 0 on class 0."""
    (row_index, row_weight), (col_index, col_weight) = taps
    out: ClassTaps = []
    for i in range(2):
        for j in range(2):
            cls = plane[np.ix_(row_index[i], col_index[j])]
            weight = row_weight[i][:, None] * col_weight[j][None, :]
            out.append((cls, np.where(cls > 0, weight, np.float32(0.0)).astype(np.float32)))
    return out


class ClassMix:
    """Rows of a per-class table at each pixel, mixed over the non-zero taps.

    A pixel whose non-zero taps agree takes that class's row exactly; one with none takes
    row ``fallback``. Only the mixed pixels are weighted, so a band costs one row array.
    """

    def __init__(self, found: ClassTaps, fallback: int) -> None:
        first: NDArray[np.integer] = np.full(found[0][0].shape, fallback, np.uint8)
        for cls, weight in reversed(found):
            first = np.where(weight > 0, cls, first)
        uniform = np.ones(first.shape, bool)
        for cls, weight in found:
            uniform &= (weight == 0) | (cls == first)
        self.first, self.mixed = first, np.nonzero(~uniform)
        self.taps = [(cls[self.mixed], weight[self.mixed]) for cls, weight in found]
        self.total = np.maximum(sum(w for _c, w in self.taps), np.float32(1e-6))

    def classes(self) -> set[int]:
        return set(np.unique(self.first).tolist()) | {
            int(c) for cls, _w in self.taps for c in np.unique(cls)
        }

    def of(self, table: ArrayLike) -> F32Grid:
        out = np.asarray(table, np.float32)[self.first]
        if len(self.mixed[0]):
            acc = sum(w[:, None] * np.asarray(table, np.float32)[c] for c, w in self.taps)
            out[self.mixed] = acc / self.total[:, None]
        return out


_MASK64 = (1 << 64) - 1
_PRIMES = (0x9E3779B97F4A7C15, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9)
_FMIX = (np.uint64(0xFF51AFD7ED558CCD), np.uint64(0xC4CEB9FE1A85EC53))


def _lattice(i: I64Grid, j: I64Grid, seed: int) -> F32Grid:
    """A value in [0, 1) per integer lattice point: a hash of the point and ``seed``."""
    mixed = (seed * _PRIMES[2]) & _MASK64
    h = i.astype(np.uint64) * np.uint64(_PRIMES[0]) + j.astype(np.uint64) * np.uint64(_PRIMES[1])
    h = h ^ np.uint64(mixed)
    for mult in _FMIX:
        h = (h ^ (h >> np.uint64(33))) * mult
    h = h ^ (h >> np.uint64(33))
    return (h >> np.uint64(40)).astype(np.float32) / np.float32(1 << 24)


def patch_noise(
    x_m: ArrayLike, y_m: ArrayLike, octaves: Sequence[Sequence[float]], seed: int
) -> F32Grid:
    """Value noise in [0, 1] at points in metres from the frame's corner: per octave
    ``(wavelength m, amount)`` a hashed lattice blended by smoothstep, mixed by amount. The
    lattice is hashed once over the points' extent and its corners gathered from it."""
    xs, ys = np.broadcast_arrays(np.asarray(x_m, np.float64), np.asarray(y_m, np.float64))
    total = np.zeros(xs.shape, np.float32)
    if not xs.size:
        return total
    for k, (wavelength, amount) in enumerate(octaves):
        u, v = xs / wavelength, ys / wavelength
        i, j = np.floor(u), np.floor(v)
        su, sv = (t * t * (3.0 - 2.0 * t) for t in (u - i, v - j))
        ii, jj = i.astype(np.int64), j.astype(np.int64)
        i0, j0 = int(ii.min()), int(jj.min())
        width = int(ii.max()) - i0 + 2
        cols, rows = np.arange(i0, i0 + width), np.arange(j0, int(jj.max()) + 2)
        table = _lattice(cols[None, :], rows[:, None], seed + k).ravel()
        at = (jj - j0) * width + (ii - i0)
        corner = [table[at + offset] for offset in (0, 1, width, width + 1)]
        near = corner[0] + (corner[1] - corner[0]) * su
        far = corner[2] + (corner[3] - corner[2]) * su
        total += np.float32(amount) * (near + (far - near) * sv).astype(np.float32)
    return total / np.float32(sum(amount for _w, amount in octaves))
