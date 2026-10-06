"""The biome raster, its calibration against the artwork sheet, and the region table."""

from __future__ import annotations

import json
from types import ModuleType
from typing import TYPE_CHECKING, TypeAlias, TypedDict, cast

import numpy as np
from scipy import ndimage

from mapgen.common import ROOT
from mapgen.gamedata.frame import BOUNDS_M, GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I32Grid, U8Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.core.gameassets.maparea import NO_MANS_LAND, MapAreaError, read_map_areas
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

if TYPE_CHECKING:
    from PIL import Image

    from satisfactory_mcp.core.gameassets.iostore import IoStore
    from satisfactory_mcp.core.gameassets.packages import ScriptObjects

__all__ = [
    "CALIBRATION_MARGIN",
    "CALIBRATION_PX",
    "CALIBRATION_SCALES",
    "CALIBRATION_SHIFT_M",
    "CALIBRATION_STEP_M",
    "REGION_TABLE",
    "boundary_mask",
    "calibrate_biome",
    "pinned_box",
    "read_biome",
    "region_mask",
    "region_table_is_current",
    "sample_area",
]

#: A box in world cm: ``(x0, x1, y0, y1)``.
Box: TypeAlias = tuple[float, float, float, float]


class RegionGridMeta(TypedDict):
    """``grid_meta``: the coarse grid's corner and cell in world cm, its size, the void letter."""

    x0: float
    y0: float
    cell: float
    nx: int
    ny: int
    void: str


class RegionTable(TypedDict):
    """The parts of ``data/region_names.json`` read here: one letter per 256 m cell."""

    _meta: JsonObject
    grid_meta: RegionGridMeta
    legend: dict[str, str]
    region_grid: list[str]


# --------------------------------------------------------------------------------------
# The calibration: where the biome raster's 4096 texels go, which the asset does not say.
# --------------------------------------------------------------------------------------

#: The sheet the pin is scored against, and the resolution the scoring runs at. The artwork
#: is the only picture in this repository whose corners have already been measured, which is
#: what makes it the ruler here rather than another thing to calibrate.
CALIBRATION_PX = 1024
CALIBRATION_SHIFT_M = 600
CALIBRATION_STEP_M = 200
CALIBRATION_SCALES = (0.95, 1.05)

#: How much better than its neighbours the pin has to read before this file believes it.
#: 1.15 is well inside the measured gap -- 2.28 against 1.42 -- and well outside the noise.
CALIBRATION_MARGIN = 1.15

#: The committed region table, which ``tools/gen_region_names.py`` derives from this same
#: raster. Its agreement with this decode is therefore a staleness check and not evidence
#: about the pin, which is what ``region_table_is_current`` below makes it.
REGION_TABLE = ROOT / "data" / "region_names.json"


def read_biome(store: IoStore, scripts: ScriptObjects) -> dict:
    """The map-area raster as a renderer wants it: a numpy square and a name per index.

    The decode, the shape checks and the index -> ``Area_*`` resolution are
    ``core.gameassets.maparea``'s. This adds the raster as a numpy array to index a colour
    table with, and each index flattened to the STEM -- ``Area_RedJungle`` rather than
    ``Area_RedJungle_2`` -- because ``BIOME_COLOURS`` is one colour per kind of ground.
    """
    try:
        areas = read_map_areas(store, scripts)
    except MapAreaError as exc:
        raise SystemExit(
            f"{exc} The satellite layer has no other source for what grows where, so "
            "nothing here can be trusted until that is looked at."
        ) from exc
    raster = np.frombuffer(areas.texels, dtype=np.uint8).reshape(areas.width, areas.width)
    names = [None if area is None else area.stem for area in areas.areas]
    return {
        "width": areas.width,
        "area": raster,
        "palette": [tuple(entry) for entry in areas.palette],
        "names": names,
        # The exact asset per index, beside the stem: the staleness check reads a name map
        # keyed by asset, and ``Area_crater_1`` and ``Area_crater_2`` are one stem.
        "assets_by_index": [None if area is None else area.asset for area in areas.areas],
        "assets": list(areas.assets),
        "distinct_areas": sorted({n for n in names if n and n != NO_MANS_LAND}),
    }


def boundary_mask(labels: U8Grid) -> BoolMask:
    """Where one area meets another, grown by one so a one-pixel drift still overlaps."""
    edge = np.zeros(labels.shape, bool)
    edge[:-1, :] |= labels[:-1, :] != labels[1:, :]
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    return ndimage.binary_dilation(edge)


def sample_area(area: U8Grid, box: Box, size: int) -> U8Grid:
    """The raster resampled onto a ``size`` square over the artwork frame, given a pin.

    ``box`` is the pin under test -- where the raster's own corners are supposed to be --
    while the output grid is always the artwork square, the frame the ruler is in.
    """
    x0, x1, y0, y1 = box
    ax = BOUNDS_M["x_min_m"] * 100 + (np.arange(size) + 0.5) * (
        (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    )
    ay = BOUNDS_M["y_min_m"] * 100 + (np.arange(size) + 0.5) * (
        (BOUNDS_M["y_max_m"] - BOUNDS_M["y_min_m"]) * 100 / size
    )
    width = area.shape[0]
    u = ((ax[None, :] - x0) / (x1 - x0) * width).astype(np.int64)
    v = ((ay[:, None] - y0) / (y1 - y0) * width).astype(np.int64)
    u = np.broadcast_to(u, (size, size))
    v = np.broadcast_to(v, (size, size))
    inside = (u >= 0) & (u < width) & (v >= 0) & (v < width)
    out = np.full((size, size), 255, np.uint8)
    out[inside] = area[np.clip(v, 0, width - 1)[inside], np.clip(u, 0, width - 1)[inside]]
    return out


def pinned_box(dx_cm: float, dy_cm: float, scale: float) -> Box:
    """The artwork square, shifted and scaled about its own centre."""
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = (x1 - x0) / 2 * scale
    return (cx + dx_cm - half, cx + dx_cm + half, cy + dy_cm - half, cy + dy_cm + half)


def _edge_strength(sheet: Image.Image, image_mod: ModuleType) -> F32Grid:
    """The sheet's grey edge strength at ``CALIBRATION_PX``."""
    grey = np.asarray(
        sheet.convert("L").resize((CALIBRATION_PX, CALIBRATION_PX), image_mod.LANCZOS),
        np.float32,
    )
    gy, gx = np.gradient(ndimage.gaussian_filter(grey, 1.0))
    return np.hypot(gx, gy)


def calibrate_biome(biome: dict, sheet: Image.Image, image_mod: ModuleType) -> JsonObject:
    """Score the pin by the artwork's own edges, and sweep for one that beats it.

    The statistic is the ratio of the sheet's mean edge strength ON the biome raster's area
    boundaries to its mean edge strength everywhere: a boundary pinned right lies on a shore
    or a scarp the map draws, one pinned wrong on flat fill, and a ratio cannot be won by a
    pin that simply produces more boundary. The ruler is the sheet this run decoded out of the
    container, so the pin is scored on every run. ``biome`` needs ``width`` and ``area``.
    """
    edge = _edge_strength(sheet, image_mod)
    everywhere = float(edge.mean())

    def ratio(dx_cm: float, dy_cm: float, scale: float) -> float:
        labels = sample_area(biome["area"], pinned_box(dx_cm, dy_cm, scale), CALIBRATION_PX)
        return float(edge[boundary_mask(labels)].mean() / everywhere)

    at_pin = ratio(0.0, 0.0, 1.0)
    shifts = range(
        -CALIBRATION_SHIFT_M * 100, CALIBRATION_SHIFT_M * 100 + 1, CALIBRATION_STEP_M * 100
    )
    # The pin is NOT in this maximum: the question is whether anything ELSE does better, so
    # the rival set is every candidate that is not the pin itself.
    best = (0.0, 0, 0)
    for dx in shifts:
        for dy in shifts:
            if dx == 0 and dy == 0:
                continue
            score = ratio(dx, dy, 1.0)
            if score > best[0]:
                best = (score, dx, dy)
    scales = {f"x{scale:.2f}": ratio(0.0, 0.0, scale) for scale in CALIBRATION_SCALES}
    rivals = max([best[0], *scales.values()])
    return {
        "method": (
            "the artwork sheet's mean edge strength over the biome raster's area boundaries, "
            "divided by its mean edge strength everywhere. A boundary pinned right sits on a "
            "shore or a scarp the map draws; one pinned wrong sits on flat fill. A ratio, so "
            "a pin cannot win it by making more boundary."
        ),
        "ruler": (
            f"the game's own {SHEET_PX} px map sheet, decoded from its four BC1 slices in "
            "this same run"
        ),
        "resolution_px": CALIBRATION_PX,
        "edge_ratio_at_the_pin": round(at_pin, 4),
        "sweep": (
            f"+-{CALIBRATION_SHIFT_M} m in {CALIBRATION_STEP_M} m steps at true scale, plus "
            + ", ".join(f"x{scale:.2f}" for scale in CALIBRATION_SCALES)
        ),
        "best_rival_shift_m": {"dx": best[1] / 100, "dy": best[2] / 100},
        "edge_ratio_at_the_best_rival_shift": round(best[0], 4),
        "edge_ratio_at_other_scales": {key: round(value, 4) for key, value in scales.items()},
        "margin_over_the_best_rival": round(at_pin / rivals, 4) if rivals else None,
        "margin_required": CALIBRATION_MARGIN,
        "pin_holds": at_pin >= rivals * CALIBRATION_MARGIN,
        "pin": dict(BOUNDS_M),
        "metres_per_texel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / biome["width"], 4),
        "reading": (
            "the raster spans exactly the square the artwork does. Every rival -- a shift in "
            "either direction, a box 5% larger, a box 5% smaller -- reads far below it, "
            "which is what makes this a measurement rather than a preference. pin_holds "
            "false would mean the texture moved, and the run says so instead of quietly "
            "colouring the world off its own biomes."
        ),
    }


def _region_table() -> RegionTable | None:
    """``data/region_names.json``, or ``None`` when it is not there."""
    if not REGION_TABLE.is_file():
        return None
    return cast(RegionTable, json.loads(REGION_TABLE.read_text(encoding="utf-8")))


def _cell_texels(
    cell: tuple[int, int], meta: RegionGridMeta, width: int
) -> tuple[slice, slice] | None:
    """The biome texels one region-table cell ``(row, col)`` covers, or None when it is empty."""
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    j, i = cell
    u = [round((meta["x0"] + (i + k) * meta["cell"] - x0) / (x1 - x0) * width) for k in (0, 1)]
    v = [round((meta["y0"] + (j + k) * meta["cell"] - y0) / (y1 - y0) * width) for k in (0, 1)]
    u = [max(0, min(width, value)) for value in u]
    v = [max(0, min(width, value)) for value in v]
    if u[1] <= u[0] or v[1] <= v[0]:
        return None
    return slice(v[0], v[1]), slice(u[0], u[1])


def region_table_is_current(biome: dict) -> JsonObject:
    """Is the committed 256 m region table still this raster's own majority downsample?

    ``data/region_names.json`` is derived from THIS asset by ``tools/gen_region_names.py``,
    so agreement is not evidence about the pin: it is either 100% or the committed table was
    cut from a different build, and this is the run that notices. The name policy is read out
    of the table's own ``_meta`` rather than imported, so this stays a check on the artifact.
    """
    table = _region_table()
    if table is None:
        return {"skipped": f"{REGION_TABLE.name} is not present, so nothing was compared"}
    display = (table.get("_meta") or {}).get("area_display_names")
    if not isinstance(display, dict):
        return {"skipped": f"{REGION_TABLE.name} carries no _meta.area_display_names to read"}
    meta, grid, legend = table["grid_meta"], table["region_grid"], table["legend"]
    width, assets = biome["width"], biome["assets_by_index"]

    compared = agree = 0
    disagreements: dict[str, int] = {}
    for j, row in enumerate(grid):
        for i, letter in enumerate(row):
            if letter == meta["void"]:
                continue
            texels = _cell_texels((j, i), meta, width)
            if texels is None:
                continue
            values, counts = np.unique(biome["area"][texels], return_counts=True)
            asset = assets[int(values[counts.argmax()])]
            compared += 1
            want = legend[letter]
            got = display.get(asset or "", display.get("", want))
            if got == want:
                agree += 1
            else:
                key = f"{want} -> {got}"
                disagreements[key] = disagreements.get(key, 0) + 1
    worst = sorted(disagreements.items(), key=lambda kv: -kv[1])[:5]
    largest: list[JsonValue] = [f"{key} ({count} cells)" for key, count in worst]
    return {
        "source": "data/region_names.json, derived from this same asset by gen_region_names.py",
        "cells_compared": compared,
        "cells_agreeing": agree,
        "agreement_pct": round(100 * agree / compared, 1) if compared else None,
        "largest_disagreements": largest,
        "table_is_current": compared > 0 and agree == compared,
        "reading": (
            "100% or the committed region table was cut from a different build of this "
            "asset, and the fix is to re-run tools/gen_region_names.py. Not a pin and never "
            "was: the corners come from the edge ratio next door."
        ),
    }


def region_mask(name: str) -> BoolMask | None:
    """One named region of ``data/region_names.json``, on this grid. Independent evidence.

    That table is derived from the game's own ``FGMapAreaTexture`` -- exact area boundaries
    at 1.83 m, downsampled to 256 m -- and from nothing in this pipeline, which is the only
    reason a recall measured against it means anything. ``None`` if the table or the name is
    missing: a gate that cannot find its own reference must say so rather than pass.
    """
    table = _region_table()
    if table is None:
        return None
    letters = {region: key for key, region in table["legend"].items()}
    if name not in letters:
        return None
    letter = letters[name]
    grid = table["region_grid"]
    meta = table["grid_meta"]
    cells = np.array([[1 if ch == letter else 0 for ch in row] for row in grid], dtype=bool)
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    ci: I32Grid = np.clip(((columns - meta["x0"]) / meta["cell"]).astype(int), 0, meta["nx"] - 1)
    ri: I32Grid = np.clip(((rows - meta["y0"]) / meta["cell"]).astype(int), 0, meta["ny"] - 1)
    return cells[ri][:, ci]
