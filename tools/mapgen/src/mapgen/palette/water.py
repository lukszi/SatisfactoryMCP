"""Inland water and the shallow coast drawn over the ground, shared by both drawn layers,
and the open sea past the measured bed.

Why the feathers and the fallbacks are what they are: tools/mapgen/README.md, "Design notes";
the open sea's constants: docs/spatial-and-map.md section 26.
"""

from __future__ import annotations

import time
from typing import NamedTuple

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy import ndimage

from mapgen.gamedata.waterbodies import OCEAN_BAND_M
from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from mapgen.palette.perched import water_surfaces
from mapgen.palette.shore import OCEAN_LEVEL_M, OCEAN_REACH_M
from mapgen.terrain.fill import cosine_taper, nearest_fill
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "COAST_ABOVE_M",
    "OPEN_SEA_BLEND_M",
    "OPEN_SEA_BLEND_PULL_M",
    "OPEN_SEA_CELL",
    "OPEN_SEA_DEPTH_M",
    "OPEN_SEA_SETTLE_M",
    "OPEN_SEA_TONE_DEPTH_M",
    "OPEN_SEA_TONE_PULL_M",
    "VOID_EDGE_BLUR_M",
    "VOID_FALLOFF_M",
    "VOID_RIM",
    "WATER_DEPTH_FULL_M",
    "WATER_EDGE_BLUR_M",
    "WATER_EDGE_M",
    "OpenSea",
    "VoidPlanes",
    "drawn_water",
    "membrane",
    "open_sea",
    "void_planes",
    "water_alpha",
    "water_depth_fraction",
    "water_over",
    "water_planes",
]

#: Water is tinted by depth, clipped here: past it more depth is not more colour.
WATER_DEPTH_FULL_M = 40.0

#: Shallower than this, water and ground are mixed, so a coast is not 1 m blocks.
WATER_EDGE_M = 0.9

#: The edge softened in space too, in metres of ground, for water against a cliff.
WATER_EDGE_BLUR_M = 0.73

#: How deep the open sea reads far from any measured bed: the game's own unsculpted ocean
#: floor, where the measured bed meets level-only water, stands 61 m under the surface.
OPEN_SEA_DEPTH_M = 60.0

#: Over about this many metres the open sea's bed settles from the bed or the coast beside
#: it to ``OPEN_SEA_DEPTH_M``.
OPEN_SEA_SETTLE_M = 400.0

#: Dry ground beside the open sea is a coast its bed rises to only this far above the top of
#: the ocean's band; ground at or under that top is sea the artwork's mask left dry. The
#: field's fill holds the sea's own surface to within 0.7 m.
COAST_ABOVE_M = 1.0

#: The grid the open sea's bed is solved on, in 1 m texels per side.
OPEN_SEA_CELL = 4

#: How deep the artwork's water tones read, from its second to its brightest, in metres: the
#: median measured depth under each over the landscape's ocean. Its first tone, the open
#: sea's teal, says only "deeper than the second".
OPEN_SEA_TONE_DEPTH_M = (5.3, 3.5, 1.3)

#: A tone draws the open sea's bed towards its depth over about this many metres.
OPEN_SEA_TONE_PULL_M = 12.0

#: Within this many metres of the open sea a measured bed is blended into it, so the bed is
#: continuous in slope as well as in value; past it the measured bed is drawn as measured.
OPEN_SEA_BLEND_M = 100.0

#: Over the blend the measured bed holds the open sea's to its own depth over about this.
OPEN_SEA_BLEND_PULL_M = 25.0

#: The void's edge, softened over this many metres of ground.
VOID_EDGE_BLUR_M = 2.0

#: The void's falloff, a Gaussian this many metres wide: the open sea fades into the void,
#: and the void's edge beside the land is lit and darkens away from it, about 110 m to dark.
VOID_FALLOFF_M = 50.0

#: The strength of the light line the artwork draws round the void beside the land.
VOID_RIM = 0.85


class VoidPlanes(NamedTuple):
    """The void as drawn, each 0..255 on the field's grid. ``cover`` is how much of a texel the
    void hides; ``falloff`` how far into it the texel is, 0 at a lit edge; ``pit`` whether it
    is a pit in the land rather than the void past the world's edge; ``rim`` its edge line."""

    cover: np.ndarray
    falloff: np.ndarray
    pit: np.ndarray
    rim: np.ndarray


class OpenSea(NamedTuple):
    """The water a render draws with the open sea added, and the void beside it.

    ``level`` and ``grades`` are the drawn water planes: the open sea is water whose depth is
    read off the bed ``open_sea`` laid under it. ``void`` is the void's ``VoidPlanes``.
    """

    level: np.ndarray
    grades: np.ndarray
    void: VoidPlanes
    meta: dict

    @property
    def planes(self) -> tuple:
        return self.level, self.grades


def open_sea(field, lattice, planes, artwork_water, ocean_level_m: float) -> OpenSea:
    """The open sea and the void, with the open sea's bed written into ``lattice``.

    ``lattice`` is the run's ``(heights_dm, ground_dm)``, changed in place; ``planes`` is
    ``(level_dm, grades)`` as drawn, the field's own when None; ``artwork_water`` is
    ``artwork_planes``'s tone plane, where a plain mask reads as the open sea's teal. No-data
    texels the artwork draws as water join the sea at ``ocean_level_m``, the rest are the
    void (``void_planes``). Under the open sea, which is that and level-only water at the
    ocean's level, a bed continues the measured one beside it in value and in slope, rises to
    a dry coast, follows the artwork's tones and settles to ``OPEN_SEA_DEPTH_M`` away from
    all of them, and runs on under the void as far as the sea fades into it.
    """
    started = time.time()
    heights_dm, ground_dm = lattice
    level, grades = planes or (field._water_raster(), field._water_quality_raster())
    tones = np.asarray(artwork_water, np.uint8)
    nodata = heights_dm == hf.NODATA
    sea = nodata & ((tones > 0) | (grades != hf.WATER_DRY))
    level_m = level / np.float32(hf.DM_PER_M)
    ocean = (level != hf.NODATA) & (np.abs(level_m - ocean_level_m) <= OCEAN_BAND_M)
    unknown = (ocean & (grades == hf.WATER_LEVEL_ONLY)) | (sea & (grades == hf.WATER_DRY))
    # Read against the field's own heights: the rebuilt fill stands a metre above them.
    top = (ocean_level_m + OCEAN_BAND_M) * hf.DM_PER_M
    stored = np.asarray(field._height_dm)
    beside = (grades == hf.WATER_DRY) & ~nodata & ndimage.binary_dilation(unknown, iterations=3)
    coast = beside & (stored >= top + COAST_ABOVE_M * hf.DM_PER_M)
    low = beside & (stored <= top) & (stored != hf.NODATA)
    added = low | (sea & (grades == hf.WATER_DRY))
    unknown |= added
    measured = ocean & (grades == hf.WATER_MEASURED) & ~nodata
    step_m = field.spacing_cm / 100.0
    under = _under_the_sea(measured, ~nodata & (heights_dm < ocean_level_m * hf.DM_PER_M), step_m)
    empty = (nodata & ~sea, nodata)
    void, fringe = void_planes(empty, measured | unknown | under, step_m)
    del under
    added |= fringe
    unknown |= fringe
    level = np.where(added, np.int16(round(ocean_level_m * hf.DM_PER_M)), level).astype(np.int16)
    level_m = np.where(added, np.float32(ocean_level_m), level_m)
    seeds = measured | coast
    surface = np.where(coast, np.float32(ocean_level_m), level_m)
    depth = np.where(seeds, surface - heights_dm / np.float32(hf.DM_PER_M), 0.0)
    tone = np.where(unknown, tones, 0).astype(np.uint8)
    depth, blended = _settled(depth.astype(np.float32), (seeds, measured, unknown), tone, step_m)
    drawn = unknown | blended
    bed = (level_m[drawn] - depth[drawn]) * np.float32(hf.DM_PER_M)
    del level_m, surface, depth
    for plane in (heights_dm, ground_dm):
        if plane is not None:
            plane[drawn] = bed
    grades = np.where(unknown, np.uint8(hf.WATER_MEASURED), grades).astype(np.uint8)
    meta = {
        "rule": (
            "no-data texels the artwork draws as water are the open sea at level_m, the "
            "rest the void. Under the open sea and level-only water at the ocean's level "
            "(within band_m) a bed is drawn, a screened Poisson membrane on a cell_m grid: "
            "fixed on the measured bed past blend_m and at a dry coast (stored ground "
            "coast_above_m over the band's top; dry ground at or under it joins the sea), "
            "held to the measured bed over blend_pull_m within blend_m, drawn to the depth of "
            "the artwork's water tone (tone_depth_m) over tone_pull_m, settling to deep_m "
            "over about settle_m; the measured bed is laid back over it across blend_m, so "
            "the bed is continuous in value and slope. The water's depth is read off it. "
            "The void is a pit where its no data does not reach the field's edge, else the "
            "void past the world's edge; beside the open sea the sea fades into it over "
            "falloff_m, elsewhere its edge is lit, rimmed and darkens over falloff_m. Drawing "
            "support, not a measurement"
        ),
        "level_m": ocean_level_m,
        "band_m": OCEAN_BAND_M,
        "deep_m": OPEN_SEA_DEPTH_M,
        "settle_m": OPEN_SEA_SETTLE_M,
        "coast_above_m": COAST_ABOVE_M,
        "cell_m": OPEN_SEA_CELL * step_m,
        "blend_m": OPEN_SEA_BLEND_M,
        "blend_pull_m": OPEN_SEA_BLEND_PULL_M,
        "tone_depth_m": list(OPEN_SEA_TONE_DEPTH_M),
        "tone_pull_m": OPEN_SEA_TONE_PULL_M,
        "void_edge_m": VOID_EDGE_BLUR_M,
        "falloff_m": VOID_FALLOFF_M,
        "sea_over_no_data_texels": int(sea.sum()),
        "dry_texels_joined": int(low.sum()),
        "void_texels": int(empty[0].sum()),
        "pit_texels": int(np.count_nonzero(void.pit)),
        "fringe_texels": int(fringe.sum()),
        "bed_texels": int(unknown.sum()),
        "blended_texels": int(blended.sum()),
        "toned_texels": int((tone > 1).sum()),
        "seconds": round(time.time() - started, 1),
    }
    return OpenSea(level, grades, void, meta)


def drawn_water(field, kernel_only: bool, rivers, lattice, artwork_water):
    """The run's water as every layer draws it: ``(surfaces, open sea or None, planes)``.

    ``palette.perched.water_surfaces``, then ``open_sea`` over the run's ``lattice``.
    ``--kernel-only`` (recipe 2) has neither, and keeps the page's sea past the data.
    """
    water = water_surfaces(field, kernel_only, rivers)
    if kernel_only or artwork_water is None:
        return water, None, water.planes
    sea = open_sea(field, lattice, water.planes, artwork_water, OCEAN_LEVEL_M)
    meta = sea.meta
    print(
        f"  open sea: {meta['sea_over_no_data_texels']} texels of the artwork's sea over no "
        f"data, {meta['void_texels']} of void ({meta['pit_texels']} in pits); a bed under "
        f"{meta['bed_texels']} texels ({meta['fringe_texels']} under the void's fade, "
        f"{meta['toned_texels']} toned), {meta['blended_texels']} measured blended into it, "
        f"in {meta['seconds']}s"
    )
    return water, sea, sea.planes


def void_planes(empty, wet, step_m: float) -> tuple[VoidPlanes, np.ndarray]:
    """The void's ``VoidPlanes``, and its fringe: the void the open sea is drawn under.

    ``empty`` is ``(void, nodata)``: a pit is void not joined to the field's edge through
    ``nodata``, the rest is the void past the world's edge, a floor the fill emptied beside
    it included. Beside ``wet``, the ocean, that void fades in over ``VOID_FALLOFF_M`` with
    the sea under it; beside the land, and in every pit, its edge is lit and rimmed and
    darkens away from it.
    """
    void, nodata = empty
    labels, _count = ndimage.label(nodata)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    pit = void & ~np.isin(labels, edge[edge > 0])
    del labels
    sigma, area = VOID_FALLOFF_M / (OPEN_SEA_CELL * step_m), np.float32(OPEN_SEA_CELL**2)
    spread = ndimage.gaussian_filter(_cells(void) / area, sigma, mode="nearest")
    lit = 1.0 - np.clip(2.0 * spread - 1.0, 0.0, 1.0)
    sea = ndimage.gaussian_filter(_cells(wet) / area, sigma, mode="nearest")
    share = np.clip(sea / np.maximum(1.0 - spread, 1e-6), 0.0, 1.0) * (1.0 - _cells(pit) / area)
    fade = _fine(share * lit, void.shape)
    fringe = void & ~pit & (fade >= np.float32(0.5 / 255.0))
    soft = ndimage.gaussian_filter(
        void.astype(np.float32), VOID_EDGE_BLUR_M / step_m, mode="nearest"
    )
    cover = _u8(soft * (1.0 - fade))
    del fade
    line = np.square(4.0 * soft * (1.0 - soft)) * np.float32(VOID_RIM)
    rim = _u8(line * _fine(1.0 - share, void.shape))
    del soft, line
    falloff = _u8(_fine(1.0 - lit * (1.0 - share), void.shape))
    return VoidPlanes(cover, falloff, pit.astype(np.uint8) * np.uint8(255), rim), fringe


def membrane(values, fixed, free, scale: float, far: float, pull=None, target=None):
    """``values`` with ``free`` replaced by a screened membrane, float64.

    Fixed on ``fixed``, settling towards ``far`` over about ``scale`` cells and drawn
    towards ``target`` by ``pull``, per cell in 1 / cells squared; anything else is outside.
    A pull that changes from cell to cell bends the membrane's slope but never breaks it, as
    a fixed cell's edge does. ``terrain.fill.relax`` with no pull.
    """
    out = values.astype(np.float64).ravel().copy()
    u, k = free.ravel(), fixed.ravel()
    if not u.any():
        return out.reshape(values.shape)
    screen = 1.0 / (scale * scale)
    weight = np.full(int(u.sum()), screen)
    rhs = np.full(len(weight), screen * far)
    if pull is not None:
        weight += pull.ravel()[u]
        rhs += pull.ravel()[u] * target.ravel()[u]
    lap = _grid_laplacian(free | fixed)
    rhs -= lap[u][:, k] @ out[k]
    a_uu = (lap[u][:, u] + sp.diags(weight)).tocsr()
    jacobi = sp.diags(1.0 / a_uu.diagonal())
    out[u], _info = spla.cg(a_uu, rhs, x0=np.full(len(rhs), far), rtol=1e-6, M=jacobi)
    return out.reshape(values.shape)


def _settled(depth_m, masks, tone, step_m: float):
    """``open_sea``'s depth in metres, and the measured texels it blended.

    ``masks`` is ``(seeds, measured, unknown)``, ``tone`` the artwork's tone on ``unknown``.
    One membrane on the coarse grid, fixed on the seeds past the blend, held to the measured
    bed within it and drawn to the tones; then the measured bed laid back over it.
    """
    seeds, measured, unknown = masks
    cell_m = OPEN_SEA_CELL * step_m
    count = _cells(seeds)
    known = count > 0
    clipped = np.where(seeds, np.clip(depth_m, 0.0, OPEN_SEA_DEPTH_M), 0.0)
    values = np.where(known, _cells(clipped) / np.maximum(count, 1), OPEN_SEA_DEPTH_M)
    del clipped
    open_ = (_cells(unknown) > 0) & ~known
    reach = np.full(known.shape, np.inf)
    if open_.any():
        reach = ndimage.distance_transform_edt(~open_) * cell_m
    band = known & (_cells(seeds & ~measured) == 0) & (reach < OPEN_SEA_BLEND_M)
    toned, total = np.zeros(known.shape), np.zeros(known.shape)
    for code, metres in enumerate(OPEN_SEA_TONE_DEPTH_M, start=2):
        texels = _cells(tone == code)
        toned, total = toned + texels, total + texels * metres
    target = np.where(open_ & (toned > 0), total / np.maximum(toned, 1), values)
    held = (cell_m / OPEN_SEA_BLEND_PULL_M) ** 2
    drawn = (cell_m / OPEN_SEA_TONE_PULL_M) ** 2 * toned / OPEN_SEA_CELL**2
    pull = np.where(band, held, np.where(open_, drawn, 0.0))
    scale = OPEN_SEA_SETTLE_M / cell_m
    coarse = membrane(values, known & ~band, open_ | band, scale, OPEN_SEA_DEPTH_M, pull, target)
    coarse = nearest_fill(coarse, known | open_) if (known | open_).any() else coarse
    keep = np.where(open_, 0.0, np.where(band, 1.0 - cosine_taper(reach, OPEN_SEA_BLEND_M), 1.0))
    fine, keep = _fine(coarse, depth_m.shape), _fine(keep, depth_m.shape)
    blended = measured & (keep < np.float32(0.999))
    return np.where(seeds, keep * depth_m + (1.0 - keep) * fine, fine), blended


def _under_the_sea(ocean, low, step_m: float) -> np.ndarray:
    """``low`` ground within the shore rule's ``OCEAN_REACH_M`` of ``ocean``, which that rule
    draws as sea, to the open sea's cells. Low ground further inland is land."""
    rows, cols = ocean.shape
    near = ndimage.binary_dilation(
        _cells(ocean) > 0, iterations=max(1, round(OCEAN_REACH_M / (OPEN_SEA_CELL * step_m)))
    )
    near = np.repeat(np.repeat(near, OPEN_SEA_CELL, axis=0), OPEN_SEA_CELL, axis=1)
    return low & near[:rows, :cols]


def _grid_laplacian(active: np.ndarray) -> sp.csr_matrix:
    """The 4-neighbour graph Laplacian of the grid restricted to ``active``."""
    h, w = active.shape
    idx = np.arange(h * w).reshape(h, w)
    right = active[:, :-1] & active[:, 1:]
    down = active[:-1, :] & active[1:, :]
    a = np.concatenate([idx[:, :-1][right], idx[:-1, :][down]])
    b = np.concatenate([idx[:, 1:][right], idx[1:, :][down]])
    adj = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(h * w, h * w))
    adj = (adj + adj.T).tocsr()
    return (sp.diags(np.asarray(adj.sum(axis=1)).ravel()) - adj).tocsr()


def _cells(plane: np.ndarray) -> np.ndarray:
    """``plane`` summed over the open sea's ``OPEN_SEA_CELL`` square cells, float32."""
    cell, (rows, cols) = OPEN_SEA_CELL, plane.shape
    pad = ((0, -rows % cell), (0, -cols % cell))
    shape = ((rows + pad[0][1]) // cell, cell, (cols + pad[1][1]) // cell, cell)
    return np.pad(plane, pad).reshape(shape).sum(axis=(1, 3), dtype=np.float32)


def _fine(coarse: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """A cell grid read back on the 1 m grid, linearly, float32."""
    zoom = OPEN_SEA_CELL
    fine = ndimage.zoom(coarse.astype(np.float32), zoom, order=1, mode="nearest", grid_mode=True)
    return fine[: shape[0], : shape[1]]


def _u8(plane: np.ndarray) -> np.ndarray:
    """A share in [0, 1] as 0..255."""
    return np.clip(plane * np.float32(255.0) + np.float32(0.5), 0, 255).astype(np.uint8)


def water_alpha(z_m, water_m, wet: np.ndarray, measured: np.ndarray, blur_px: float):
    """How much of each pixel is water, in [0, 1]: coverage, a depth feather, a space blur.

    ``wet`` is the share the channel calls water, ``measured`` the share with a measured
    depth; level-only texels get full alpha.
    """
    ramp_alpha = np.clip((water_m - z_m) / WATER_EDGE_M, 0.0, 1.0)
    alpha = wet * (measured * ramp_alpha + (1.0 - measured))
    return ndimage.gaussian_filter(alpha, blur_px, mode="nearest")


def water_depth_fraction(z_m, water_m, measured: np.ndarray) -> np.ndarray:
    """How dark the water reads, in [0, 1]: measured depth where there is one, deep where not."""
    known = np.clip((water_m - z_m) / WATER_DEPTH_FULL_M, 0.0, 1.0)
    return measured * known + (1.0 - measured)


def water_over(rgb, depth, alpha, shade, shallow, deep):
    """Lay water over ground, tinted by its own depth and lit only a little."""
    tint = depth[..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (
        WATER_SHADE_FLOOR + WATER_SHADE_RANGE * shade[..., None]
    )
    weight = alpha[..., None]
    return rgb * (1 - weight) + colour * weight


def water_planes(field) -> tuple[np.ndarray | None, np.ndarray | None, str]:
    """The ``wet`` and ``measured`` 0/1 planes off ``waterq.u8.z``, and their source."""
    water = field._water_raster()
    if water is None:
        return None, None, "no water raster in this field; nothing is drawn as water"
    grades = field._water_quality_raster()
    if grades is None:
        wet = ((water != hf.NODATA) & (water > field._height_dm)).astype(np.uint8)
        return (
            wet,
            wet,
            (
                "no waterq.u8.z: this field predates the quality byte, so submersion falls "
                "back to a water surface standing above the ground, which is all such a "
                "field can say"
            ),
        )
    return (
        (grades != hf.WATER_DRY).astype(np.uint8),
        (grades == hf.WATER_MEASURED).astype(np.uint8),
        "waterq.u8.z: dry / depth measured against 1 m terrain / level known and depth not",
    )
