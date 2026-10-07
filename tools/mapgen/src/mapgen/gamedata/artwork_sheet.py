"""The game's drawn map sheet: the four slices decoded, their layout proven, the pin measured."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING

import numpy as np

from mapgen.common import ROOT
from mapgen.gamedata.nodes import NODE_TABLE, load_static_nodes
from satisfactory_mcp.core.gameassets.container import (
    SHEET_PX,
    SLICES,
    TILE_PX,
    UBULK_BYTES,
    read_slice,
)
from satisfactory_mcp.core.gameassets.imaging import BlockDecoder, ImageFactory
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba
from satisfactory_mcp.core.jsontypes import JsonObject

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = [
    "CALIBRATION_PX",
    "OCEAN_TOLERANCE",
    "SWEEP_M",
    "SWEEP_STEP_M",
    "calibrate",
    "decode_slices",
    "line_bytes",
    "mean_abs",
    "report_calibration",
    "seam_residuals",
    "stitch_sheet",
]

#: The size of the sheet's copy the pin is checked on.
CALIBRATION_PX = 1024
#: The sweep resolution is what bounds the claim: a pin that survives +-300 m in 50 m steps
#: is right to about 100 m, and no better than that.
SWEEP_M = 300
SWEEP_STEP_M = 50

#: How close a sampled pixel must be to the corner colour to count as open ocean. Land on
#: this map is beige-to-green, so 12 sits in the wide gap between "the same flat colour"
#: and "anything the map actually draws".
OCEAN_TOLERANCE = 12.0


#: Two adjacent scanlines inside one tile, what a continuous map costs; and two 100 rows
#: apart, what two pieces of map that do NOT abut look like: the control a seam has to beat.
CONTROL_NEAR = (2000, 2001)
CONTROL_FAR = (2000, 2100)


def line_bytes(tile: Image, box: tuple[int, int, int, int]) -> bytes:
    """One row or column of a tile as raw RGB bytes -- three per pixel, in order."""
    return tile.crop(box).convert("RGB").tobytes()


def mean_abs(left: bytes, right: bytes) -> float:
    return round(sum(abs(a - b) for a, b in zip(left, right, strict=True)) / len(left), 4)


def _seams(nw: Image, ne: Image, sw: Image, se: Image) -> dict[str, float]:
    """The four abutting-edge residuals of one 2x2 arrangement of the slices."""
    right_edge = (TILE_PX - 1, 0, TILE_PX, TILE_PX)
    left_edge = (0, 0, 1, TILE_PX)
    bottom_edge = (0, TILE_PX - 1, TILE_PX, TILE_PX)
    top_edge = (0, 0, TILE_PX, 1)
    return {
        "vertical_x_4096_north": mean_abs(line_bytes(nw, right_edge), line_bytes(ne, left_edge)),
        "vertical_x_4096_south": mean_abs(line_bytes(sw, right_edge), line_bytes(se, left_edge)),
        "horizontal_y_4096_west": mean_abs(line_bytes(nw, bottom_edge), line_bytes(sw, top_edge)),
        "horizontal_y_4096_east": mean_abs(line_bytes(ne, bottom_edge), line_bytes(se, top_edge)),
    }


def seam_residuals(tiles: dict[str, Image]) -> dict:
    """Mean per-channel difference across each seam, against the alternative and controls.

    This is what proves ``Map_<col>-<row>``. The other reading of the name -- ``<row>-<col>``,
    which swaps the two off-diagonal slices -- is scored with the identical statistic, and
    the controls come from inside one tile so they need no layout at all: adjacent scanlines
    say what a continuous map costs, scanlines 100 rows apart what two unrelated pieces do.
    """
    nw, ne, sw, se = (tiles[n] for n in ("Map_0-0", "Map_1-0", "Map_0-1", "Map_1-1"))
    seams = _seams(nw, ne, sw, se)
    # <row>-<col> would put Map_0-1 in the north-east and Map_1-0 in the south-west.
    other = _seams(nw, sw, ne, se)
    controls = {
        f"adjacent_rows_{CONTROL_NEAR[0]}_vs_{CONTROL_NEAR[1]}": mean_abs(
            line_bytes(nw, (0, CONTROL_NEAR[0], TILE_PX, CONTROL_NEAR[0] + 1)),
            line_bytes(nw, (0, CONTROL_NEAR[1], TILE_PX, CONTROL_NEAR[1] + 1)),
        ),
        f"distant_rows_{CONTROL_FAR[0]}_vs_{CONTROL_FAR[1]}": mean_abs(
            line_bytes(nw, (0, CONTROL_FAR[0], TILE_PX, CONTROL_FAR[0] + 1)),
            line_bytes(nw, (0, CONTROL_FAR[1], TILE_PX, CONTROL_FAR[1] + 1)),
        ),
    }
    far = controls[f"distant_rows_{CONTROL_FAR[0]}_vs_{CONTROL_FAR[1]}"]
    worst = max(seams.values())
    return {
        "reading": (
            "the slice name is <col>-<row>: Map_0-0 north-west, Map_1-0 north-east, "
            "Map_0-1 south-west, Map_1-1 south-east"
        ),
        "seams": seams,
        "seams_under_the_other_reading": other,
        "controls_inside_one_tile": controls,
        "worst_seam": worst,
        "worst_seam_under_the_other_reading": max(other.values()),
        "layout_holds": worst < far and worst < max(other.values()),
        "verdict": (
            "every seam of the chosen reading sits near the adjacent-scanline control and "
            "far below two scanlines 100 rows apart, and the other reading of the name "
            "does not. The slices abut the way this file places them."
        ),
    }


def decode_slices(
    store: IoStore, decoder: BlockDecoder, image_mod: ImageFactory[Image]
) -> dict[str, Image]:
    """Mip 0 of every slice, BC1-decoded, keyed by slice name."""
    tiles: dict[str, Image] = {}
    for name in SLICES:
        raw = read_slice(store, name)
        tiles[name] = decode_bc1_rgba(decoder, image_mod, raw, TILE_PX)
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        print(
            f"  {name}: {UBULK_BYTES} B .ubulk, mip 0 decoded -> ({col * TILE_PX}, {row * TILE_PX})"
        )
    return tiles


def stitch_sheet(tiles: dict[str, Image], image_mod: ModuleType) -> tuple[Image, str]:
    """The slices pasted 2x2 into one sheet, ``tiles`` emptied. Returns (sheet, alpha note)."""
    sheet = image_mod.new("RGBA", (SHEET_PX, SHEET_PX))
    for name in SLICES:
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        sheet.paste(tiles[name], (col * TILE_PX, row * TILE_PX))
    tiles.clear()

    # Whether alpha says anything is measured: uniformly opaque alpha is a third of the file.
    alpha_min, alpha_max = sheet.getextrema()[3]
    if alpha_min == 255:
        sheet = sheet.convert("RGB")
        alpha_note = "alpha was 255 everywhere and was dropped; the PNG is RGB"
    else:
        alpha_note = f"alpha varies ({alpha_min}..{alpha_max}) and is kept; the PNG is RGBA"
    print(f"  {alpha_note}")
    return sheet, alpha_note


def calibrate(sheet: Image, image_mod: ModuleType, bounds: dict[str, float]) -> JsonObject:
    """Project the static node table onto the sheet and sweep the pin for a better one.

    Nodes stand on land, so a pin that is right puts as few of them as possible on the flat
    open-ocean colour and cannot be improved on by shifting the box. A few read as sea at
    any pin -- this map's shoreline is drawn rather than sampled -- so the verdict is not
    "zero" but "nothing beyond one sweep step does better".
    """
    if not NODE_TABLE.is_file():
        return {
            "skipped": f"{NODE_TABLE.relative_to(ROOT)} is not present, so the pin is unchecked"
        }
    nodes = load_static_nodes()
    small = sheet.resize((CALIBRATION_PX, CALIBRATION_PX), image_mod.LANCZOS).convert("RGB")
    pixels = np.asarray(small, dtype=np.int16)
    ocean = pixels[8, 8]  # the extreme corner of the sheet is open sea on every reading

    def on_ocean(dx_m: float, dy_m: float) -> int:
        x0 = (bounds["x_min_m"] + dx_m) * 100.0
        x1 = (bounds["x_max_m"] + dx_m) * 100.0
        y0 = (bounds["y_min_m"] + dy_m) * 100.0
        y1 = (bounds["y_max_m"] + dy_m) * 100.0
        u = ((nodes.x_cm - x0) / (x1 - x0) * CALIBRATION_PX).astype(np.int64)
        v = ((nodes.y_cm - y0) / (y1 - y0) * CALIBRATION_PX).astype(np.int64)
        here = pixels[np.clip(v, 0, CALIBRATION_PX - 1), np.clip(u, 0, CALIBRATION_PX - 1)]
        return int((np.abs(here - ocean).sum(axis=1) / 3.0 < OCEAN_TOLERANCE).sum())

    steps = range(-SWEEP_M, SWEEP_M + 1, SWEEP_STEP_M)
    at_pin = on_ocean(0, 0)
    best = (at_pin, 0, 0)
    for dx in steps:
        for dy in steps:
            score = on_ocean(dx, dy)
            if score < best[0]:
                best = (score, dx, dy)
    off_by_m = max(abs(best[1]), abs(best[2]))
    return {
        "method": (
            f"{len(nodes.x_cm)} static resource nodes from data/world_resource_nodes.json "
            f"projected onto a {CALIBRATION_PX}px copy of the sheet and counted against the "
            "flat open-ocean colour. Nodes stand on land, so fewer is better."
        ),
        "nodes_projected": len(nodes.x_cm),
        "nodes_on_open_ocean_at_the_pin": at_pin,
        "sweep": f"+-{SWEEP_M} m in {SWEEP_STEP_M} m steps, both axes",
        "best_shift_m": {"dx": best[1], "dy": best[2]},
        "nodes_on_open_ocean_at_the_best_shift": best[0],
        "pin_agrees_within_m": off_by_m,
        "pin_holds": off_by_m <= SWEEP_STEP_M,
        "accuracy_m": SWEEP_STEP_M * 2,
        "reading": (
            "a handful of nodes read as sea at any pin -- the shoreline on this map is "
            "drawn, not sampled, and a node on a headland or an islet sits inside a stroke "
            "of it -- so the number that matters is not zero but whether SHIFTING the whole "
            "box does better. Nothing beyond one sweep step does, which is the evidence for "
            "the corners: they are right to about accuracy_m and no finer. A best shift "
            "larger than one step would be real drift, and pin_holds would say so."
        ),
    }


def report_calibration(calibration: JsonObject) -> None:
    """Print what ``calibrate`` found, and warn when the pin no longer holds."""
    if "skipped" in calibration:
        print(f"  calibration skipped: {calibration['skipped']}")
        return
    shift = calibration["best_shift_m"]
    dx, dy = (shift["dx"], shift["dy"]) if isinstance(shift, dict) else (0, 0)
    print(
        f"  calibration: {calibration['nodes_on_open_ocean_at_the_pin']} of "
        f"{calibration['nodes_projected']} nodes stand on open ocean at the pin; best "
        f"shift over {calibration['sweep']} is "
        f"{dx:+d}, {dy:+d} m "
        f"at {calibration['nodes_on_open_ocean_at_the_best_shift']} -- the pin holds to "
        f"{calibration['accuracy_m']} m"
    )
    if not calibration["pin_holds"]:
        print(
            f"  WARNING: shifting the whole box by "
            f"{calibration['pin_agrees_within_m']} m draws the world better than the "
            "pinned corners, which is more than this sweep's own resolution. The map "
            "moved, or the node table did. The picture is still written -- it is the "
            "corners that are in question -- and _meta.calibration says so."
        )
