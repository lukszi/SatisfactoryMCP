"""The field's water channel: the artwork's plan shape, levelled on the water volumes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, TypedDict

import numpy as np
import numpy.typing as npt
from scipy import ndimage

from mapgen.gamedata.frame import GRID_PX
from mapgen.gamedata.water.actors import WATER_SURFACE_CLASSES, box_texels, water_box_tops
from mapgen.gamedata.water.rivers import RIVER_CLASS
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I16Grid, I32Grid, U8Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX, SLICES, TILE_PX, read_slice
from satisfactory_mcp.core.gameassets.imaging import BlockDecoder, ImageFactory
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba
from satisfactory_mcp.domain.spatial import heightfield as hf

if TYPE_CHECKING:
    from PIL.Image import Image

    from satisfactory_mcp.core.gameassets.iostore import IoStore

__all__ = [
    "LOWER_BODY_STEP_M",
    "VOID_ARTWORK_LUMA_MAX",
    "WATER_ARTWORK_BANDS",
    "WATER_ARTWORK_BLUE_OVER_RED",
    "WaterSurface",
    "artwork_planes",
    "artwork_water_mask",
    "lower_bodies",
    "water_surface",
]

#: The artwork classifier: blue minus red on the game's own map sheet, one threshold. The
#: histogram is bimodal with nothing between the modes, and at this value it called 3 of the
#: node table's 626 rows -- all of which stand on dry ground -- water.
WATER_ARTWORK_BLUE_OVER_RED = 25

#: The artwork draws a pit, and the void past the world's edge, black to a flat grey (Rec. 601
#: luma 0 to about 80); its ground is beige or white, 140 and up. docs/spatial-and-map.md §26.
VOID_ARTWORK_LUMA_MAX = 110

#: The artwork's water comes in four flat tones, G - R about 32, 48, 64 and 78 from the open
#: sea's teal to the brightest cyan; these split them. docs/spatial-and-map.md §26.
WATER_ARTWORK_BANDS = (40, 56, 70)

#: Rows of the 1 m grid classified at a time, so the sheet is never held as integers whole.
_ROWS_AT_ONCE = 500

#: A box top more than this under a texel's level is another, lower body (§38).
LOWER_BODY_STEP_M = 2.0


class WaterSurface(TypedDict):
    """``water_surface``'s level and quality planes, and the tallies the sidecar records."""

    level_m: F32Grid
    quality: U8Grid
    boxes_rasterised: int
    bodies: int
    artwork_texels: int
    uncovered_texels: int
    orphan_bodies: int
    dropped_standing_out_texels: int
    water_texels: int
    measured_texels: int
    level_only_texels: int
    depth_p50_m: float | None
    depth_p90_m: float | None


def _grid_index() -> I32Grid:
    """The artwork sheet's pixel nearest each 1 m texel, along one axis."""
    return np.clip((np.arange(GRID_PX) * SHEET_PX / GRID_PX).astype(np.int32), 0, SHEET_PX - 1)


def artwork_planes(sheet: Image | npt.ArrayLike) -> tuple[U8Grid, BoolMask]:
    """The decoded artwork sheet on this file's 1 m grid: ``(water, void)``.

    ``water`` is ``artwork_water_mask``'s classifier as a uint8 band: 0 where dry, else 1 for
    the open sea's tone to ``len(WATER_ARTWORK_BANDS) + 1`` for the brightest. ``void`` is
    ground drawn darker than ``VOID_ARTWORK_LUMA_MAX`` that is not water. Nearest-neighbour,
    as that function.
    """
    pixels = np.asarray(sheet, np.uint8)
    index = _grid_index()
    water = np.zeros((GRID_PX, GRID_PX), np.uint8)
    void = np.zeros((GRID_PX, GRID_PX), bool)
    for start in range(0, GRID_PX, _ROWS_AT_ONCE):
        rows = pixels[index[start : start + _ROWS_AT_ONCE]][:, index].astype(np.int32)
        wet = rows[..., 2] - rows[..., 0] >= WATER_ARTWORK_BLUE_OVER_RED
        band = 1 + np.digitize(rows[..., 1] - rows[..., 0], WATER_ARTWORK_BANDS)
        luma = (299 * rows[..., 0] + 587 * rows[..., 1] + 114 * rows[..., 2]) // 1000
        water[start : start + len(rows)] = np.where(wet, band, 0)
        void[start : start + len(rows)] = ~wet & (luma <= VOID_ARTWORK_LUMA_MAX)
    return water, void


def artwork_water_mask(
    store: IoStore, decoder: BlockDecoder, image_mod: ImageFactory[Image]
) -> BoolMask:
    """The game's own map artwork, classified into water, on this file's 1 m grid.

    ``B - R``, because the artwork's water is the only blue thing on it: terrain, cliffs,
    biome tints and the grid are all warm. Nearest-neighbour down to the 1 m grid, because
    8192 px over the same 7500 m box is 0.92 m to the pixel and interpolating a hard-edged
    mask would only invent a soft one.

    The slices come from the container through ``core.gameassets.container.read_slice``, not
    from ``map.png``, so this stage does not depend on that generator having been run.
    """
    sheet = np.zeros((SHEET_PX, SHEET_PX), dtype=bool)
    for name in SLICES:
        raw = read_slice(store, name)
        image = decode_bc1_rgba(decoder, image_mod, raw, TILE_PX).convert("RGB")
        pixels: U8Grid = np.asarray(image)
        blue_over_red = pixels[:, :, 2].astype(np.int16) - pixels[:, :, 0].astype(np.int16)
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        sheet[row * TILE_PX : (row + 1) * TILE_PX, col * TILE_PX : (col + 1) * TILE_PX] = (
            blue_over_red >= WATER_ARTWORK_BLUE_OVER_RED
        )
    index = _grid_index()
    return sheet[index][:, index]


def lower_bodies(
    level_dm: I16Grid,
    grades: U8Grid,
    height_dm: I16Grid,
    boxes: Iterable[tuple[str, Sequence[float]]],
    ocean_m: float,
) -> tuple[I16Grid, int]:
    """``level_dm`` with the higher box tops over a lower body given back to it, and how many.

    The field levels a texel at the highest box top over it, so inside the rectangle where a
    higher body's box reaches over a lower one the lower water stands at the higher top. Each
    surface box with a level of its own, lowest first, floods from the wet texels at its top
    every measured texel joined to them, under it, with ground below the top and a level more
    than ``LOWER_BODY_STEP_M`` above it. A river's AABB has no level of its own, and the box
    levels of the ocean (``ocean_m`` within a metre) are section 26's.
    """
    out = np.array(level_dm, copy=True)
    measured = (np.asarray(grades) == hf.WATER_MEASURED) & (out != hf.NODATA)
    wet = (np.asarray(grades) != hf.WATER_DRY) & (out != hf.NODATA)
    step = round(LOWER_BODY_STEP_M * hf.DM_PER_M)
    levelled = WATER_SURFACE_CLASSES - {RIVER_CLASS}
    taken = 0
    for box in sorted((box for name, box in boxes if name in levelled), key=lambda b: b[5]):
        top = int(np.round(np.float32(box[5] / 100.0) * hf.DM_PER_M))
        texels = box_texels(box, (out.shape[0], out.shape[1]))
        if texels is None or abs(top - ocean_m * hf.DM_PER_M) <= hf.DM_PER_M:
            continue
        level = out[texels]
        seeds = wet[texels] & (level == top)
        under = measured[texels] & (np.asarray(height_dm[texels]) < top) & (level > top + step)
        if not (seeds.any() and under.any()):
            continue
        parts, _count = ndimage.label(seeds | under, structure=np.ones((3, 3), bool))
        reached = np.unique(parts[seeds])
        take = under & np.isin(parts, reached[reached > 0])
        level[take] = top
        taken += int(take.sum())
    return out, taken


def water_surface(
    mask: BoolMask,
    boxes: Iterable[tuple[str, Sequence[float]]],
    height_dm: I16Grid,
    prov: U8Grid,
) -> WaterSurface:
    """The artwork's plan shape given the water volumes' level, and what is left unknown.

    A wet texel's level is the highest box top over it, else its drawn body's median covered
    top; a body no box reaches is dropped. Ground measured at 1 m at or above the level is no
    water. Over the fill layer the depth is unknown, and ``waterq.u8.z`` says so (§19).
    """
    tops, used = water_box_tops(boxes)
    covered = np.isfinite(tops)
    labelled, bodies = ndimage.label(mask, structure=np.ones((3, 3), bool))

    level = np.where(mask & covered, tops, np.nan).astype(np.float32)
    orphan = mask & ~covered
    if orphan.any() and bodies:
        where = np.nonzero(mask & covered)
        medians = np.asarray(
            ndimage.median(tops[where], labels=labelled[where], index=np.arange(1, bodies + 1)),
            dtype=np.float32,
        )
        lookup = np.concatenate([[np.nan], medians]).astype(np.float32)
        level[orphan] = lookup[labelled[orphan]]

    terrain_m = np.where(height_dm == hf.NODATA, np.nan, height_dm / hf.DM_PER_M).astype(np.float32)
    # Both cliff values: a depth is knowable wherever the ground under the water was
    # measured at 1 m, and whether a source vertex landed in the texel has nothing to do
    # with that. Listing only 4 would call three quarters of the cliff province
    # depth-unknown over a distinction that exists for the renderer.
    measurable = ((prov == hf.PROV_LANDSCAPE) | np.isin(prov, hf.PROV_CLIFF_VALUES)) & np.isfinite(
        terrain_m
    )
    standing_out = measurable & np.isfinite(level) & (level <= terrain_m)
    level[standing_out] = np.nan

    wet = np.isfinite(level)
    quality = np.where(
        wet, np.where(measurable, hf.WATER_MEASURED, hf.WATER_LEVEL_ONLY), hf.WATER_DRY
    ).astype(np.uint8)
    depths = (level - terrain_m)[wet & measurable]
    return {
        "level_m": level,
        "quality": quality,
        "boxes_rasterised": used,
        "bodies": int(bodies),
        "artwork_texels": int(mask.sum()),
        "uncovered_texels": int(orphan.sum()),
        "orphan_bodies": int((mask & ~covered & ~np.isfinite(level)).sum()),
        "dropped_standing_out_texels": int(standing_out.sum()),
        "water_texels": int(wet.sum()),
        "measured_texels": int((quality == hf.WATER_MEASURED).sum()),
        "level_only_texels": int((quality == hf.WATER_LEVEL_ONLY).sum()),
        "depth_p50_m": round(float(np.median(depths)), 3) if depths.size else None,
        "depth_p90_m": round(float(np.percentile(depths, 90)), 3) if depths.size else None,
    }
