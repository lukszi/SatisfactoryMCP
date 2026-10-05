"""Inland water and the shallow coast drawn over the ground, shared by both drawn layers,
and the open sea past the measured bed.

Why the feathers and the fallbacks are what they are: tools/mapgen/README.md, "Design notes";
the open sea's constants: docs/spatial-and-map.md section 26.
"""

from __future__ import annotations

import time
from typing import NamedTuple

import numpy as np
from scipy import ndimage

from mapgen.gamedata.waterbodies import OCEAN_BAND_M
from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from mapgen.palette.perched import water_surfaces
from mapgen.palette.shore import OCEAN_LEVEL_M
from mapgen.terrain.fill import nearest_fill, relax
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "COAST_ABOVE_M",
    "OPEN_SEA_CELL",
    "OPEN_SEA_DEPTH_M",
    "OPEN_SEA_SETTLE_M",
    "VOID_EDGE_BLUR_M",
    "WATER_DEPTH_FULL_M",
    "WATER_EDGE_BLUR_M",
    "WATER_EDGE_M",
    "OpenSea",
    "drawn_water",
    "open_sea",
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

#: The void's edge, softened over this many metres of ground.
VOID_EDGE_BLUR_M = 2.0


class OpenSea(NamedTuple):
    """The water a render draws with the open sea added, and the void beside it.

    ``level`` and ``grades`` are the drawn water planes: the open sea is water whose depth is
    read off the bed ``open_sea`` laid under it. ``void`` is the void's soft cover, 0..255.
    """

    level: np.ndarray
    grades: np.ndarray
    void: np.ndarray
    meta: dict

    @property
    def planes(self) -> tuple:
        return self.level, self.grades


def open_sea(field, lattice, planes, artwork_water, ocean_level_m: float) -> OpenSea:
    """The open sea and the void, with the open sea's bed written into ``lattice``.

    ``lattice`` is the run's ``(heights_dm, ground_dm)``, changed in place; ``planes`` is
    ``(level_dm, grades)`` as drawn, the field's own when None. No-data texels the artwork
    draws as water join the sea at ``ocean_level_m``, the rest are the void. Under the open
    sea, which is that and level-only water at the ocean's level, a bed continues the
    measured one beside it, rises to a dry coast and settles to ``OPEN_SEA_DEPTH_M`` away
    from both, so no edge of the data is drawn as a line across the sea.
    """
    started = time.time()
    heights_dm, ground_dm = lattice
    level, grades = planes or (field._water_raster(), field._water_quality_raster())
    nodata = heights_dm == hf.NODATA
    sea = nodata & (artwork_water | (grades != hf.WATER_DRY))
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
    level = np.where(added, np.int16(round(ocean_level_m * hf.DM_PER_M)), level).astype(np.int16)
    level_m = np.where(added, np.float32(ocean_level_m), level_m)
    step_m = field.spacing_cm / 100.0
    void = (nodata & ~sea).astype(np.float32) * np.float32(255.0)
    void = ndimage.gaussian_filter(void, VOID_EDGE_BLUR_M / step_m, mode="nearest")
    void = np.clip(void + 0.5, 0, 255).astype(np.uint8)
    seeds = (ocean & (grades == hf.WATER_MEASURED) & ~nodata) | coast
    surface = np.where(coast, np.float32(ocean_level_m), level_m)
    depth = np.where(seeds, surface - heights_dm / np.float32(hf.DM_PER_M), 0.0)
    depth = _settled(depth.astype(np.float32), seeds, unknown, step_m)
    bed = (level_m[unknown] - depth[unknown]) * np.float32(hf.DM_PER_M)
    del level_m, surface, depth
    for plane in (heights_dm, ground_dm):
        if plane is not None:
            plane[unknown] = bed
    grades = np.where(unknown, np.uint8(hf.WATER_MEASURED), grades).astype(np.uint8)
    meta = {
        "rule": (
            "no-data texels the artwork draws as water are the open sea at level_m, the "
            "rest the void, softened over void_edge_m. Under the open sea and level-only "
            "water at the ocean's level (within band_m) a bed is drawn: the measured bed "
            "beside it, the surface at a dry coast (stored ground coast_above_m over the "
            "band's top; dry ground at or under it joins the sea), settling to deep_m over "
            "about settle_m (a screened Poisson membrane on a cell_m grid), and the water's "
            "depth is read off it. Drawing support, not a measurement"
        ),
        "level_m": ocean_level_m,
        "band_m": OCEAN_BAND_M,
        "deep_m": OPEN_SEA_DEPTH_M,
        "settle_m": OPEN_SEA_SETTLE_M,
        "coast_above_m": COAST_ABOVE_M,
        "cell_m": OPEN_SEA_CELL * step_m,
        "void_edge_m": VOID_EDGE_BLUR_M,
        "sea_over_no_data_texels": int(sea.sum()),
        "dry_texels_joined": int(low.sum()),
        "void_texels": int((nodata & ~sea).sum()),
        "bed_texels": int(unknown.sum()),
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
    print(
        f"  open sea: {sea.meta['sea_over_no_data_texels']} texels of the artwork's sea over "
        f"no data, {sea.meta['void_texels']} of void; a bed under {sea.meta['bed_texels']} "
        f"texels in {sea.meta['seconds']}s"
    )
    return water, sea, sea.planes


def _settled(depth_m, seeds, unknown, step_m: float) -> np.ndarray:
    """``open_sea``'s depth in metres: the seeds' own, the membrane everywhere else."""
    cell, (rows, cols) = OPEN_SEA_CELL, depth_m.shape
    pad = ((0, -rows % cell), (0, -cols % cell))
    shape = ((rows + pad[0][1]) // cell, cell, (cols + pad[1][1]) // cell, cell)
    clipped = np.clip(depth_m, 0.0, OPEN_SEA_DEPTH_M).astype(np.float32)
    count = np.pad(seeds, pad).reshape(shape).sum(axis=(1, 3))
    total = np.pad(np.where(seeds, clipped, 0.0), pad).reshape(shape).sum(axis=(1, 3))
    known = count > 0
    values = np.where(known, total / np.maximum(count, 1), OPEN_SEA_DEPTH_M)
    open_ = np.pad(unknown, pad).reshape(shape).any(axis=(1, 3)) & ~known
    scale = OPEN_SEA_SETTLE_M / (cell * step_m)
    coarse = relax(values, known, open_, scale, OPEN_SEA_DEPTH_M).astype(np.float32)
    coarse = nearest_fill(coarse, known | open_) if (known | open_).any() else coarse
    fine = ndimage.zoom(coarse, cell, order=1, mode="nearest", grid_mode=True)[:rows, :cols]
    return np.where(seeds, clipped, fine)


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
