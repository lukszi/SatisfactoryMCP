"""The field's water channel: the artwork's plan shape levelled on the water volumes, and
the sidecar block that records how."""

from __future__ import annotations

import json
import math

import numpy as np
from scipy import ndimage

from mapgen.gamedata.biome import REGION_TABLE
from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.mesh import WATER_SURFACE_CLASSES
from satisfactory_mcp.core.gameassets.container import SHEET_PX, SLICES, TILE_PX, read_slice
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba
from satisfactory_mcp.domain.spatial import heightfield as hf

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


def artwork_planes(sheet) -> tuple[np.ndarray, np.ndarray]:
    """The decoded artwork sheet on this file's 1 m grid: ``(water, void)``.

    ``water`` is ``artwork_water_mask``'s classifier as a uint8 band: 0 where dry, else 1 for
    the open sea's tone to ``len(WATER_ARTWORK_BANDS) + 1`` for the brightest. ``void`` is
    ground drawn darker than ``VOID_ARTWORK_LUMA_MAX`` that is not water. Nearest-neighbour,
    as that function.
    """
    pixels = np.asarray(sheet, np.uint8)
    index = np.clip((np.arange(GRID_PX) * SHEET_PX / GRID_PX).astype(np.int32), 0, SHEET_PX - 1)
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


def artwork_water_mask(store, decoder, image_mod) -> np.ndarray:
    """The game's own map artwork, classified into water, on this file's 1 m grid.

    ``B - R``, because the artwork's water is the only blue thing on it: terrain, cliffs,
    biome tints and the grid are all warm. Nearest-neighbour down to the 1 m grid, because
    8192 px over the same 7500 m box is 0.92 m to the pixel and interpolating a hard-edged
    mask would only invent a soft one.

    The slices come from the container through ``tools/gen_map_image.py``'s reader, not from
    ``map.png``, so this stage does not depend on that generator having been run.
    """
    sheet = np.zeros((SHEET_PX, SHEET_PX), dtype=bool)
    for name in SLICES:
        raw = read_slice(store, name)
        pixels = np.asarray(decode_bc1_rgba(decoder, image_mod, raw, TILE_PX).convert("RGB"))
        blue_over_red = pixels[:, :, 2].astype(np.int16) - pixels[:, :, 0].astype(np.int16)
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        sheet[row * TILE_PX : (row + 1) * TILE_PX, col * TILE_PX : (col + 1) * TILE_PX] = (
            blue_over_red >= WATER_ARTWORK_BLUE_OVER_RED
        )
    index = np.clip((np.arange(GRID_PX) * SHEET_PX / GRID_PX).astype(np.int32), 0, SHEET_PX - 1)
    return sheet[index][:, index]


def water_box_tops(boxes: list[tuple[str, tuple[float, ...]]]) -> tuple[np.ndarray, int]:
    """The highest surface-class box top standing over each texel, in metres, or ``nan``.

    A box's top IS the surface of the volume it bounds, so where several overlap in plan the
    highest is the one visible from above. The save's 23 water extractors all sit inside a
    volume and every one of them stands on its box's top to within 0.005 cm.
    """
    tops = np.full((GRID_PX, GRID_PX), np.nan, np.float32)
    used = 0
    for name, box in boxes:
        if name not in WATER_SURFACE_CLASSES:
            continue
        x0, y0, _z0, x1, y1, z1 = box
        # Vertex-aligned, so a texel is covered when its own point lies inside the box.
        col0 = max(0, math.ceil((x0 - ORIGIN_X_CM) / SPACING_CM))
        col1 = min(GRID_PX, math.floor((x1 - ORIGIN_X_CM) / SPACING_CM) + 1)
        row0 = max(0, math.ceil((y0 - ORIGIN_Y_CM) / SPACING_CM))
        row1 = min(GRID_PX, math.floor((y1 - ORIGIN_Y_CM) / SPACING_CM) + 1)
        if col1 <= col0 or row1 <= row0:
            continue
        used += 1
        window = tops[row0:row1, col0:col1]
        top = np.float32(z1 / 100.0)
        np.maximum(window, top, out=window, where=np.isfinite(window))
        window[~np.isfinite(window)] = top
    return tops, used


def region_mask(name: str) -> np.ndarray | None:
    """One named region of ``data/region_names.json``, on this grid. Independent evidence.

    That table is derived from the game's own ``FGMapAreaTexture`` -- exact area boundaries
    at 1.83 m, downsampled to 256 m -- and from nothing in this pipeline, which is the only
    reason a recall measured against it means anything. ``None`` if the table or the name is
    missing: a gate that cannot find its own reference must say so rather than pass.
    """
    if not REGION_TABLE.is_file():
        return None
    table = json.loads(REGION_TABLE.read_text(encoding="utf-8"))
    letters = {region: key for key, region in table["legend"].items()}
    if name not in letters:
        return None
    letter = letters[name]
    grid = table["region_grid"]
    meta = table["grid_meta"]
    cells = np.array([[1 if ch == letter else 0 for ch in row] for row in grid], dtype=bool)
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    ci = np.clip(((columns - meta["x0"]) / meta["cell"]).astype(int), 0, meta["nx"] - 1)
    ri = np.clip(((rows - meta["y0"]) / meta["cell"]).astype(int), 0, meta["ny"] - 1)
    return cells[ri][:, ci]


def water_surface(mask: np.ndarray, boxes: list, height_dm: np.ndarray, prov: np.ndarray) -> dict:
    """The artwork's plan shape given the water volumes' level, and what is left unknown.

    The level of a wet texel is the highest box top over it; where nothing covers it -- 17
    texels of 18.3 million on build 495413 -- the median of its own drawn body's covered
    tops stands in, and a body with no box anywhere is dropped rather than guessed at. Then
    the one gate: where the ground was measured at 1 m and stands above that level there is
    no water, which is a rock in a lake and takes 0.04 km2 off the mask. Where the ground is
    the fill layer or nothing at all no such test is possible in either direction, so the
    texel is water whose depth this file does not know, and ``waterq.u8.z`` says that rather
    than a subtraction implying it.
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
