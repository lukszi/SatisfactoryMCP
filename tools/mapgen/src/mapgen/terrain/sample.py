"""Sampling the 1 m field onto the output frame: the kernels, their taps and the direct mask."""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.mesh import DIRECT_SAMPLES_MIN
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "DIRECT_SAMPLES_PER_TEXEL",
    "STENCIL_WHOLE",
    "ClassMix",
    "PchipTaps",
    "class_taps",
    "direct_weight",
    "frame_coordinates",
    "grid_position",
    "pchip_1d",
    "pchip_slope",
    "resample",
    "resample_pchip",
    "sample_coverage",
    "sample_noise",
    "sample_plain",
    "sample_surface",
    "taps_cubic",
    "taps_linear",
    "taps_pchip",
]

# --------------------------------------------------------------------------------------
# The direct regime: which output texels are entitled to the triangles, and how the two
# regimes are joined.
# --------------------------------------------------------------------------------------

#: How many source vertices the ground under one OUTPUT texel has to have contributed before
#: that texel's height is a measurement rather than an interpolation across a triangle wider
#: than itself. The rule is the field generator's, imported rather than retyped; this file
#: only evaluates it at a different spacing. ``density.u8.z`` counts per 1 m texel, so the
#: test against a 0.229 m texel is ``density >= 1 / 0.229**2``, i.e. 19 of them.
DIRECT_SAMPLES_PER_TEXEL = DIRECT_SAMPLES_MIN


# --------------------------------------------------------------------------------------
# The direct regime: which texels the triangles are allowed to answer, and the surface
# underneath them where they are not.
# --------------------------------------------------------------------------------------


def direct_weight(field, spacing_m: float) -> tuple[np.ndarray | None, dict]:
    """Where the geometry outvotes the kernel, as a feathered 0..255 mask at 1 m.

    ``None`` when the field carries no ``density.u8.z``, which is not "no samples anywhere"
    and must never be read as one: a field written before the plane existed knows nothing
    about its own density, so the caller refuses rather than assumes.

    The rule is the generator's: **one source vertex under the output texel**. The plane
    counts per 1 m texel, so the test is scaled by the output texel's own area, and that is
    why fewer texels qualify at 0.229 m than at 0.458 m.

    A **mask and not a weight**. What decides that the rocks are drawn is their own coverage
    of the pixel; what this decides is what to CALL the answer -- a measurement, or the plane
    of a triangle wider than a texel -- and a provenance label is a yes or a no per texel, so
    it is read nearest and never blurred. On the texels where the worst rims are the density
    is zero by construction, so a weight built from it could not reach them anyway.
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
    need = DIRECT_SAMPLES_PER_TEXEL / (spacing_m * spacing_m)
    qualifies = density >= min(need, 255.0)
    share = float(qualifies.mean())
    cliff = np.isin(field._prov, hf.PROV_CLIFF_VALUES)
    return (qualifies.astype(np.uint8) * 255), {
        "plane": hf.DENSITY_NAME,
        "rule": (
            f"at least {DIRECT_SAMPLES_PER_TEXEL:g} source vertex under an output texel of "
            f"{spacing_m:.4f} m, i.e. density >= {need:.2f} per 1 m texel"
        ),
        "samples_min_per_output_texel": DIRECT_SAMPLES_PER_TEXEL,
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


def frame_coordinates(size: int) -> tuple[np.ndarray, np.ndarray]:
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


def grid_position(coordinate: np.ndarray, origin: float, spacing: float, limit: int):
    """Where a run of world coordinates falls on a raster's index axis, clamped to it.

    Clamped rather than masked: the frame is half a metre wider than the field's last vertex
    on two sides, so masking would invent a half-pixel strip of no-data down the east and
    south edges. Past the edge the nearest vertex is what the field's own reader gives too.
    """
    return np.clip((coordinate - origin) / spacing, 0.0, limit - 1.0)


def taps_linear(position: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The two flanking indices and their weights: ``(2, N)`` each. Plain bilinear.

    What the cubic kernel falls back to where its stencil runs off the data, and what every
    category plane is sampled with: a coverage fraction outside [0, 1] is not one.
    """
    low = np.minimum(np.floor(position).astype(np.int64), max(limit - 2, 0))
    fraction = (position - low).astype(np.float32)
    index = np.stack([low, np.minimum(low + 1, limit - 1)])
    return index, np.stack([1.0 - fraction, fraction])


def taps_cubic(position: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The four indices around a position and their Catmull-Rom weights: ``(4, N)`` each.

    Cubic convolution with a = -1/2, the interpolating member of that family: it passes
    through every sample it is given and it is **C1**. The hillshade is a function of the
    first derivative, and a C0 kernel sampled at half its own texel spacing rules the relief
    into 1 m squares. The weights sum to exactly one, which is what lets the no-data
    bookkeeping below use their sum as a completeness test.

    Indices are clamped to the grid, so a stencil hanging off the edge repeats the edge
    vertex -- the same answer the field's own reader gives past its last row.
    """
    base = np.floor(position).astype(np.int64)
    t = (position - base).astype(np.float32)
    index = np.stack([np.clip(base + offset, 0, limit - 1) for offset in (-1, 0, 1, 2)])
    weight = np.stack(
        [
            0.5 * t * (t * (2.0 - t) - 1.0),
            0.5 * (t * t * (3.0 * t - 5.0) + 2.0),
            0.5 * t * (t * (4.0 - 3.0 * t) + 1.0),
            0.5 * t * t * (t - 1.0),
        ]
    )
    return index, weight


class PchipTaps(tuple):
    """``(index, t)``: the four clamped indices around each position and its cell fraction.

    A type of its own because PCHIP's weights depend on the data, so ``sample_surface`` has
    to know it was handed positions rather than weights.
    """

    __slots__ = ()

    def __new__(cls, index, t):
        return super().__new__(cls, (index, t))


def taps_pchip(position: np.ndarray, limit: int) -> PchipTaps:
    """The same four indices as ``taps_cubic``, with the fraction instead of weights."""
    base = np.floor(position).astype(np.int64)
    t = (position - base).astype(np.float32)
    index = np.stack([np.clip(base + offset, 0, limit - 1) for offset in (-1, 0, 1, 2)])
    return PchipTaps(index, t)


def pchip_slope(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Fritsch-Butland: the harmonic mean of two secants, zero unless they agree in sign."""
    agree = left * right > 0
    total = np.where(agree, left + right, np.float32(1.0))
    return np.where(agree, 2.0 * left * right / total, np.float32(0.0)).astype(np.float32)


def pchip_1d(p0, p1, p2, p3, t):
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


def resample_pchip(raster: np.ndarray, rows: PchipTaps, cols: PchipTaps, nodata: int):
    """Separable PCHIP onto the output grid. Returns ``(values, whole)``.

    x first over the contiguous slab of source rows, then y, as ``resample`` does.
    ``whole`` is true where all sixteen texels under the stencil have a value.
    """
    (row_index, row_t), (col_index, col_t) = rows, cols
    low, high = int(row_index.min()), int(row_index.max())
    slab = raster[low : high + 1]
    known = slab != nodata
    values = np.where(known, slab, 0).astype(np.float32)
    across = pchip_1d(*(values[:, col_index[tap]] for tap in range(4)), col_t[None, :])
    across_whole = np.logical_and.reduce([known[:, col_index[tap]] for tap in range(4)])
    picked = [row_index[tap] - low for tap in range(4)]
    total = pchip_1d(*(across[index] for index in picked), row_t[:, None])
    whole = np.logical_and.reduce([across_whole[index] for index in picked])
    return total.astype(np.float32), whole


def resample(raster: np.ndarray, rows, cols, nodata: int | None):
    """Separable interpolation of ``raster`` onto the output grid. Returns (sum, weight).

    Separable, and in that order: the output rows a band needs come from one CONTIGUOUS run
    of source rows, so the row axis is a slice and only the column axis is a gather.
    Interpolating in x first over that short slab and in y second over the result is four
    gathers of the small array and four of the large one, against sixteen of the large one
    if the 4x4 stencil were evaluated directly.

    The no-data bookkeeping rides along: every tap is multiplied by whether its texel had a
    value, and the weights come back separately, so the caller can tell a whole stencil
    (weight exactly one) from a partial one from nothing at all. ``nodata`` of ``None`` says
    the raster has no holes -- a category coverage plane -- and skips it.
    """
    (row_index, row_weight), (col_index, col_weight) = rows, cols
    low, high = int(row_index.min()), int(row_index.max())
    slab = raster[low : high + 1]
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


#: How far the cubic stencil's own weights may fall from one before this file stops
#: believing it. They sum to one exactly wherever every texel under the stencil has a value,
#: so anything below this is a stencil straddling the edge of the data, where a kernel with
#: negative lobes has no business extrapolating.
STENCIL_WHOLE = 1.0 - 1e-4


def sample_surface(raster: np.ndarray, smooth_taps, linear, nodata: int):
    """A height raster on the output grid: the smooth kernel inside the data, bilinear at its edge.

    Returns ``(values, missing)``. ``smooth_taps`` is a ``(rows, cols)`` pair of
    ``taps_pchip`` or of ``taps_cubic``. Where the 4x4 stencil is whole the C1 answer is
    used; where it is not the 2x2 answer is; and where even that has nothing under it the
    caller paints the page's sea.
    """
    if isinstance(smooth_taps[0], PchipTaps):
        smooth, whole = resample_pchip(raster, *smooth_taps, nodata)
    else:
        smooth, smooth_weight = resample(raster, *smooth_taps, nodata)
        whole = smooth_weight >= STENCIL_WHOLE
    flat, flat_weight = resample(raster, *linear, nodata)
    missing = flat_weight <= 0.0
    near = flat / np.where(missing, 1.0, flat_weight)
    return np.where(whole, smooth, near), missing


def sample_plain(raster: np.ndarray, taps) -> np.ndarray:
    """A raster with no holes in it, interpolated onto the output grid. Nothing clipped.

    The weights of either kernel sum to one and there is no no-data to renormalise around,
    so the weighted sum IS the answer. For the two rasters this file makes itself: the
    artwork's signed high pass and the feathered province mask, neither of which is a
    coverage and neither of which may be clipped into [0, 1] on the way through.
    """
    return resample(raster, *taps, None)[0]


def sample_coverage(plane: np.ndarray, taps) -> np.ndarray:
    """What fraction of the ground under each output pixel is in some category, in [0, 1].

    Bilinear and never cubic: a category is a yes or a no on a 1 m grid, and what is wanted
    from it is coverage. A kernel with negative lobes would answer -0.06 of a texel wet,
    which is not a thing a texel can be.
    """
    return np.clip(sample_plain(plane, taps), 0.0, 1.0)


def class_taps(plane: np.ndarray, taps) -> list[tuple[np.ndarray, np.ndarray]]:
    """The four bilinear taps of a class plane: ``(class, weight)``, weight 0 on class 0."""
    (row_index, row_weight), (col_index, col_weight) = taps
    out = []
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

    def __init__(self, found, fallback: int):
        first = np.full(found[0][0].shape, fallback, np.uint8)
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

    def of(self, table: np.ndarray) -> np.ndarray:
        out = np.asarray(table, np.float32)[self.first]
        if len(self.mixed[0]):
            acc = sum(w[:, None] * np.asarray(table, np.float32)[c] for c, w in self.taps)
            out[self.mixed] = acc / self.total[:, None]
        return out


def sample_noise(fields, rows: np.ndarray, cols: np.ndarray, size: int) -> np.ndarray:
    """The octaves added up at these output pixels, as a multiplier around 1."""
    out = np.ones((len(rows), len(cols)), np.float32)
    for field, amount in fields:
        side = field.shape[0]
        u = (cols.astype(np.float32) + 0.5) * side / size
        v = (rows.astype(np.float32) + 0.5) * side / size
        out += amount * field[np.ix_(v.astype(np.int64) % side, u.astype(np.int64) % side)]
    return out
