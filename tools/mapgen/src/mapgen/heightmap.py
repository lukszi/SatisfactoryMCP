"""Cut a real 1 m heightmap of this world out of the installed game.

    uv run --extra gen python tools/gen_world_heightmap.py

Five stages fuse into one field: the cooked UE Landscape heightfield (a
``LandscapeComponent``'s ``GrassData`` blob opens with 128x128 uint16 height samples, one
per 1 m quad), the rock meshes' own cooked geometry rasterised as a max-Z overlay, the
2048 px ``HeightData_Test`` interface raster as fill outside the landscape frame, and the
game's own water actors for where the water is and how high it stands. The landscape is the
sculpted terrain and nothing else -- every cliff, mesa and boulder is a placed static mesh
-- so the overlay is what makes the tail of the error distribution bearable, and the run
proves that per build by sampling the finished field at every static resource node and
refusing to write if the trimmed RMS misses ``VALIDATION_TRIM_RMS_MAX_M``.

It writes ``data/local/heightmap/``, six files, about 18 MB::

    height.i16.z  7500x7500 int16 decimetres, row-delta + zlib, -32768 = no data
    prov.u8.z     0 no-data, 1 landscape, 3 fill, 4 cliff interpolated, 5 cliff direct
    density.u8.z  source vertices per texel over the cliff layer, clamped at 255
    water.i16.z   water surface Z, same grid and no-data
    waterq.u8.z   0 dry, 1 water with a measured depth, 2 water whose depth is unknowable
    meta.json     georeference, game build, generator version, coverage, measured accuracy

``--caves`` writes only the cave masks, to ``data/local/caves/`` (docs/spatial-and-map.md
section 23). ``rocks.npz`` and ``rocks.json`` are every rock's collision mesh for exact
heights; ``--rocks`` adds only them to an existing field (section 24).

The georeference is ``x_cm = -324700 + col*100``, ``y_cm = -375000 + row*100``, and it is
**vertex-aligned**: a texel's height belongs to that point exactly, not to a cell around it.
The two cliff provenance values are one layer split by how the texel was answered, so a
reader that knows only 4 sees 5 as "not landscape, not fill, not no-data" and is right. The
codec lives in ``satisfactory_mcp.domain.spatial.heightfield`` and the container reader in
``satisfactory_mcp.core.gameassets``; both are imported rather than reimplemented, and
``ooz`` is imported inside the latter so a machine without the ``gen`` extra still imports
every module and runs the tests.

Everything written here is derived from Coffee Stain's cooked assets, read out of the
reader's own install into a gitignored directory: the generator is committed and its output
never is.
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.common import LOCAL_DIR, ROOT, base_parser, require_gen
from mapgen.gamedata.biome import REGION_TABLE
from mapgen.gamedata.caves import write_caves
from mapgen.gamedata.frame import (
    BASELINE_BOX_CM,
    GRID_PX,
    ORIGIN_X_CM,
    ORIGIN_Y_CM,
    SPACING_CM,
    Z6_TEXEL_M,
    Z7_TEXEL_M,
    sample_grid,
)
from mapgen.gamedata.mesh import (
    ARCH_MARK,
    CLIFF_SOURCES,
    DIRECT_SAMPLES_MIN,
    OVERSIZE_CM,
    WATER_SURFACE_CLASSES,
    MeshBounds,
    rasterise_cliffs,
    rasterise_top,
    read_mesh_geometry,
)
from mapgen.gamedata.rocks import rock_pack, write_rocks
from mapgen.gamedata.sweep import (
    BASELINE_OFFSET_CM,
    BASELINE_PX,
    BASELINE_SCALE_CM_PER_RAW,
    FILL_FLOOR_CM,
    LANDSCAPE_N,
    LANDSCAPE_PER_UNIT,
    LANDSCAPE_SCALE_CM,
    LANDSCAPE_ZERO,
    drop_offsets,
    landscape_frame,
    read_baseline,
    sweep_levels,
)
from satisfactory_mcp.core.gameassets.container import (
    SHEET_PX,
    SLICES,
    TILE_PX,
    open_container,
    read_slice,
)
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    ScriptObjects,
)
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    files_digest,
    install_directory,
    installed_build,
    read_str_path,
    sha256_hex,
)
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba
from satisfactory_mcp.core.gameassets.versions import (
    HEIGHTFIELD_GENERATOR_VERSION,
)
from satisfactory_mcp.domain.spatial import caves
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The interface raster's own resolution, for the accuracy the fill layer inherits.
FILL_HORIZONTAL_M = 7500.0 / BASELINE_PX


FILL_VERTICAL_M = BASELINE_SCALE_CM_PER_RAW / 255.0 / 100.0


#: The artwork classifier: blue minus red on the game's own map sheet, one threshold. The
#: histogram is bimodal with nothing between the modes, and at this value it called 3 of the
#: node table's 626 rows -- all of which stand on dry ground -- water.
WATER_ARTWORK_BLUE_OVER_RED = 25


#: The four gates the water stage refuses to write past, each aimed at one way the recipe
#: can come apart without looking wrong: dry nodes read as water means the colour classifier
#: drifted or the sheet moved; Spire Coast recall is the region that exposed the flatness
#: detector this stage replaced; an ocean level far from the median top of the ocean-spline
#: boxes means the level is coming from the wrong boxes; and artwork water standing over no
#: box at all is what a misregistration of more than a few texels looks like from here.
WATER_FP_MAX = 0.01


WATER_SPIRE_RECALL_MIN = 0.95


WATER_OCEAN_TOLERANCE_M = 0.5


WATER_UNCOVERED_MAX = 0.01


#: The class whose boxes are the ocean, and the region whose recall is the gate. Both are
#: names in data the run reads rather than judgements this file makes.
WATER_OCEAN_CLASS = "BPW_OceanSplineTool_02_C"


WATER_GATE_REGION = "Spire Coast"


#: The node table the run validates against, and the gate it has to clear. This pipeline
#: measures 0.368 m trimmed RMS and the interface raster alone manages 1.08 m, so 0.5 m is
#: the band in which a decode regression cannot pass as a refresh.
NODE_TABLE = ROOT / "data" / "world_resource_nodes.json"


VALIDATION_TRIM = 0.90


VALIDATION_TRIM_RMS_MAX_M = 0.5


#: How many nodes a provenance layer needs before its measured accuracy is believed rather
#: than its derived quantisation step quoted. Nodes are not spread evenly, and the fill
#: layer in particular is mostly ocean where nothing stands.
ACCURACY_MIN_SAMPLES = 30


#: Bumped when the pipeline changes what it writes, so a sidecar dates its own field. 3 is
#: the cliff layer taking the Nanite leaf over the collision hull, the ``density.u8.z``
#: plane, and the provenance value that says which side of one sample per texel a cliff
#: texel is on. 4 adds ``terrain.u16.z`` and ``top.i16.z`` beside an unchanged ground. 5
#: samples the rock and top rasters at the vertex the reader reads, not the texel centre.
#: The number lives in ``core.gameassets.versions``, where the server reads it too.
GENERATOR_VERSION = HEIGHTFIELD_GENERATOR_VERSION


#: The bare terrain against nodes standing on the landscape layer, median absolute.
TERRAIN_NODE_MEDIAN_MAX_M = 0.3


#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "game", "game_version_pinned")


def compose_top(height_dm: np.ndarray, frame: dict, top: dict) -> tuple[np.ndarray, int]:
    """``height_dm`` max-folded with the overlay, and how many texels the overlay raised."""
    dx, dy = drop_offsets(frame)
    out = height_dm.copy()
    window = out[dy : dy + frame["height"], dx : dx + frame["width"]]
    over_dm = np.clip(np.round(np.nan_to_num(top["z_cm"], nan=-1e9) / 10.0), -32767, 32767)
    over_dm = np.where(np.isfinite(top["z_cm"]), over_dm, hf.NODATA).astype(np.int16)
    raise_ = (over_dm != hf.NODATA) & ((window == hf.NODATA) | (over_dm > window))
    window[raise_] = over_dm[raise_]
    return out, int(raise_.sum())


def validate_terrain(frame: dict, prov: np.ndarray) -> dict:
    """The bare terrain against the nodes standing on the landscape layer."""
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    z = np.array([n["z"] for n in nodes], float) / 100.0
    col = np.round((x - frame["x0_cm"]) / frame["scale_cm"]).astype(int)
    row = np.round((y - frame["y0_cm"]) / frame["scale_cm"]).astype(int)
    inside = (col >= 0) & (col < frame["width"]) & (row >= 0) & (row < frame["height"])
    gc = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    gr = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    rc, cc = np.clip(row, 0, frame["height"] - 1), np.clip(col, 0, frame["width"] - 1)
    good = inside & frame["good"][rc, cc] & (prov[gr, gc] == hf.PROV_LANDSCAPE)
    errors = np.abs(z[good] - frame["z_cm"][rc[good], cc[good]] / 100.0)
    return {
        "n": int(good.sum()),
        "medabs_m": round(float(np.median(errors)), 4) if errors.size else None,
        "p90_m": round(float(np.percentile(errors, 90)), 4) if errors.size else None,
        "gate_m": TERRAIN_NODE_MEDIAN_MAX_M,
    }


def baseline_indices() -> tuple[np.ndarray, np.ndarray]:
    """Which baseline texel each output column and row falls in. Nearest, never blended.

    The fill is 3.66 m data read at 1 m, so an interpolation would draw a smooth surface out
    of a raster that has none and hide the coarseness the provenance byte declares.
    """
    x0, x1, y0, y1 = BASELINE_BOX_CM
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    bi = np.clip(
        ((columns - x0) / (x1 - x0) * BASELINE_PX - 0.5).round().astype(int), 0, BASELINE_PX - 1
    )
    bj = np.clip(
        ((rows - y0) / (y1 - y0) * BASELINE_PX - 0.5).round().astype(int), 0, BASELINE_PX - 1
    )
    return bi, bj


# --------------------------------------------------------------------------------------
# Stage 5, part two: the artwork's plan shape, the boxes' level, and the combine.
# --------------------------------------------------------------------------------------


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


def validate_water(surface: dict, mask: np.ndarray, boxes: list) -> dict:
    """The four gates, each measured against something this stage did not make.

    Returns every number whether it passes or not; ``main`` decides what to do about it. A
    gate that could not find its own reference reports ``None`` and is treated as a failure:
    "the check did not run" and "the check passed" are different things.
    """
    wet = surface["quality"] != hf.WATER_DRY
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    col = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    row = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)

    region = region_mask(WATER_GATE_REGION)
    recall = None
    region_truth = 0
    if region is not None and (region & mask).any():
        region_truth = int((region & mask).sum())
        recall = float((wet & region & mask).sum() / region_truth)

    tops = [box[5] / 100.0 for name, box in boxes if name == WATER_OCEAN_CLASS]
    labelled, count = ndimage.label(mask, structure=np.ones((3, 3), bool))
    ocean_level = None
    ocean_reference = None
    if tops and count:
        sizes = np.bincount(labelled.ravel())
        biggest = int(np.argmax(sizes[1:]) + 1)
        values = surface["level_m"][(labelled == biggest) & wet]
        ocean_reference = float(np.median(tops))
        if values.size:
            ocean_level = float(np.median(values))

    return {
        "dry_node_false_positive": {
            "nodes": len(nodes),
            "called_water": int(wet[row, col].sum()),
            "fraction": round(float(wet[row, col].mean()), 6),
            "gate_max": WATER_FP_MAX,
            "against": str(NODE_TABLE.relative_to(ROOT)).replace("\\", "/"),
            "why": (
                "every static resource node stands on dry ground, so a node the channel "
                "calls water is a false positive with no interpretation needed"
            ),
        },
        "region_recall": {
            "region": WATER_GATE_REGION,
            "artwork_texels": region_truth,
            "recall": None if recall is None else round(recall, 6),
            "gate_min": WATER_SPIRE_RECALL_MIN,
            "against": ("data/region_names.json, the game's own map areas downsampled to 256 m"),
            "why": (
                "this region is where the flatness detector this stage replaced scored "
                "worst -- 35.8% recall against the artwork -- so it is the one that says "
                "whether the replacement actually replaced it"
            ),
        },
        "ocean_level": {
            "assigned_m": None if ocean_level is None else round(ocean_level, 3),
            "box_median_m": None if ocean_reference is None else round(ocean_reference, 3),
            "boxes": len(tops),
            "offset_m": (
                None
                if ocean_level is None or ocean_reference is None
                else round(abs(ocean_level - ocean_reference), 4)
            ),
            "gate_max_m": WATER_OCEAN_TOLERANCE_M,
            "why": (
                f"the largest drawn body is the ocean and every {WATER_OCEAN_CLASS} box "
                "states the ocean's own surface, so the two have to agree or the level is "
                "being taken from the wrong volumes"
            ),
        },
        "artwork_over_a_box": {
            "uncovered_texels": surface["uncovered_texels"],
            "fraction": round(surface["uncovered_texels"] / max(surface["artwork_texels"], 1), 6),
            "gate_max": WATER_UNCOVERED_MAX,
            "why": (
                "the mask and the volumes are two descriptions of one world, so water the "
                "artwork draws where no volume stands means they have come apart -- which "
                "is what a misregistered sheet looks like from here"
            ),
        },
    }


def water_gate_failures(checks: dict) -> list[str]:
    """Which of the four gates did not pass, as sentences. Empty means write the field."""
    failures = []
    node = checks["dry_node_false_positive"]
    if node["fraction"] > node["gate_max"]:
        failures.append(
            f"the channel calls {node['called_water']} of {node['nodes']} static resource "
            f"nodes water ({node['fraction'] * 100:.2f}%), past the {node['gate_max'] * 100:.0f}% "
            "gate. Every one of those nodes stands on dry ground, so the artwork classifier "
            "or the sheet's registration has moved."
        )
    region = checks["region_recall"]
    if region["recall"] is None or region["recall"] < region["gate_min"]:
        measured = "unmeasurable" if region["recall"] is None else f"{region['recall'] * 100:.1f}%"
        failures.append(
            f"{region['region']} recall against the artwork is {measured}, under the "
            f"{region['gate_min'] * 100:.0f}% gate. That region is what exposed the detector "
            "this stage replaced, and it is exposing this one."
        )
    ocean = checks["ocean_level"]
    if ocean["offset_m"] is None or ocean["offset_m"] > ocean["gate_max_m"]:
        failures.append(
            "the ocean's assigned level "
            + (
                "could not be measured at all"
                if ocean["offset_m"] is None
                else f"is {ocean['offset_m']:.2f} m from the median {WATER_OCEAN_CLASS} box top"
            )
            + f", past the {ocean['gate_max_m']} m gate."
        )
    covered = checks["artwork_over_a_box"]
    if covered["fraction"] > covered["gate_max"]:
        failures.append(
            f"{covered['fraction'] * 100:.2f}% of the artwork's water stands over no water "
            f"volume at all, past the {covered['gate_max'] * 100:.0f}% gate. The mask and "
            "the boxes have stopped describing the same world."
        )
    return failures


# --------------------------------------------------------------------------------------
# Composition, validation, and what gets written.
# --------------------------------------------------------------------------------------


def compose(frame: dict, cliffs: dict, baseline_cm: np.ndarray, valid: np.ndarray) -> dict:
    """Fuse the layers into the output grid: fill, then landscape, then cliff over both.

    The fill is everywhere the interface raster says anything, so it goes down first and is
    the answer only where nothing better arrives. The landscape drops in index-aligned over
    its own frame. The cliff overlay then wins any texel where real geometry stands above
    the sculpted ground, and any texel the landscape left as a hole: a cave mouth's rock is
    still a measurement.
    """
    dx, dy = drop_offsets(frame)
    bi, bj = baseline_indices()

    z_m = np.where(valid, baseline_cm / 100.0, np.nan).astype(np.float32)[np.ix_(bj, bi)]
    prov = np.where(np.isnan(z_m), hf.PROV_NODATA, hf.PROV_FILL).astype(np.uint8)

    land_m = np.where(frame["good"], frame["z_cm"] / 100.0, np.nan).astype(np.float32)
    sub_prov = np.where(frame["good"], hf.PROV_LANDSCAPE, hf.PROV_NODATA).astype(np.uint8)

    cliff_m = (cliffs["z_cm"] / 100.0).astype(np.float32)
    take = np.isfinite(cliff_m) & (~np.isfinite(land_m) | (cliff_m > land_m))
    sub_z = np.where(take, cliff_m, land_m)
    # Two cliff values, one layer, equally accurate at 1 m: 5 says a source vertex landed in
    # this texel, 4 says the rasteriser reached it by interpolating the plane of a triangle
    # wider than the texel. The split exists for renders drawing finer than 1 m.
    direct = take & (cliffs["density"] >= DIRECT_SAMPLES_MIN)
    sub_prov = np.where(take, hf.PROV_CLIFF, sub_prov)
    sub_prov = np.where(direct, hf.PROV_CLIFF_DIRECT, sub_prov).astype(np.uint8)
    sub_density = np.where(take, np.minimum(cliffs["density"], 255), 0).astype(np.uint8)

    window = np.isfinite(sub_z)
    z_m[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_z[window]
    prov[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_prov[window]
    density = np.zeros((GRID_PX, GRID_PX), np.uint8)
    density[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_density[window]

    known = np.isfinite(z_m)
    height_dm = np.where(known, np.clip(np.round(z_m * 10.0), -32767, 32767), hf.NODATA)
    error = np.abs(height_dm.astype(np.float32) / 10.0 - z_m)[known]
    cliff = np.isin(prov, hf.PROV_CLIFF_VALUES)
    return {
        "height_dm": height_dm.astype(np.int16),
        "prov": prov,
        "density": density,
        "drop": (dx, dy),
        "coverage": {
            hf.PROV_NAMES[value]: float((prov == value).mean())
            for value in (
                hf.PROV_NODATA,
                hf.PROV_LANDSCAPE,
                hf.PROV_FILL,
                hf.PROV_CLIFF,
                hf.PROV_CLIFF_DIRECT,
            )
        },
        "cliff_texels": int(cliff.sum()),
        "cliff_direct_fraction": float((prov == hf.PROV_CLIFF_DIRECT).sum() / max(cliff.sum(), 1)),
        "density_p50": int(np.median(density[cliff])) if cliff.any() else 0,
        "z_range_m": [float(np.nanmin(z_m)), float(np.nanmax(z_m))],
        "quantisation_max_m": float(error.max()),
        "quantisation_rms_m": float(np.sqrt((error**2).mean())),
    }


def error_stats(errors: np.ndarray, total: int) -> dict:
    """Median offset, then the spread about it: median absolute, P90, and trimmed RMS.

    The offset is removed because a constant bias is a georeference question and this pass
    guards the decode. The trim is about caves: about 44 nodes sit UNDER the surface, in
    cave mouths, arches and overhangs that no single-valued heightmap can represent, so an
    untrimmed RMS measures the map's topology rather than this file's arithmetic.
    """
    finite = errors[~np.isnan(errors)]
    if finite.size == 0:
        return {"n": 0, "coverage": 0.0}
    offset = float(np.median(finite))
    spread = np.sort(np.abs(finite - offset))
    trimmed = spread[: max(int(spread.size * VALIDATION_TRIM), 1)]
    return {
        "n": int(finite.size),
        "coverage": round(finite.size / total, 4),
        "offset_m": round(offset, 4),
        "medabs_m": round(float(np.median(spread)), 4),
        "p90_m": round(float(np.percentile(spread, 90)), 4),
        "trim90_rms_m": round(float(np.sqrt((trimmed**2).mean())), 4),
        "under_1m": int((spread < 1.0).sum()),
    }


def validate(height_dm: np.ndarray, prov: np.ndarray) -> dict:
    """Measure the built field against the static node table, whole and per layer.

    The whole-field number is the gate; the per-layer ones go in the sidecar so a reading can
    quote the accuracy of the layer that answered it, this field being a fifth of a metre
    good in the middle and four metres good at the edge.
    """
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    z = np.array([n["z"] for n in nodes], float) / 100.0
    errors = z - sample_grid(height_dm, x, y)
    col = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    row = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    layers = prov[row, col]
    per_layer = {}
    for value in (hf.PROV_LANDSCAPE, hf.PROV_FILL, hf.PROV_CLIFF, hf.PROV_CLIFF_DIRECT):
        pick = layers == value
        per_layer[hf.PROV_NAMES[value]] = error_stats(
            np.where(pick, errors, np.nan), int(pick.sum())
        )
    # The cliff province whole as well as split: the whole is what a field before the split
    # measured, so keeping it is what makes two runs' cliff accuracy comparable.
    cliff = np.isin(layers, hf.PROV_CLIFF_VALUES)
    per_layer["cliff, both"] = error_stats(np.where(cliff, errors, np.nan), int(cliff.sum()))
    return {
        "against": str(NODE_TABLE.relative_to(ROOT)).replace("\\", "/"),
        "nodes": len(nodes),
        "field": error_stats(errors, len(nodes)),
        "per_layer": per_layer,
        "method": (
            "every static resource node's own Z against the field read at its coordinate, "
            "with the median offset removed and the worst 10% trimmed. The trim is not "
            "cosmetic: about 44 nodes sit under the surface in caves, arches and overhangs, "
            "which no single-valued heightmap can represent and which the interface raster "
            "fails by the same test."
        ),
        "gate_m": VALIDATION_TRIM_RMS_MAX_M,
        "reference": (
            "the workflow that proved this pipeline measured 0.368 m trimmed RMS and "
            "0.210 m median absolute on this set; the interface raster alone measures "
            "1.080 m and 0.854 m, and its own scale and offset were fitted on these nodes."
        ),
    }


def accuracy_block(validation: dict) -> dict:
    """What each provenance value means, and how well it was measured to do.

    ``accuracy_m`` is the measured median absolute error where enough nodes fell on that
    layer to mean anything and the layer's own vertical step where they did not, and
    ``accuracy_from`` says which of the two it is.
    """
    derived = {
        hf.PROV_LANDSCAPE: (1.0, LANDSCAPE_SCALE_CM / LANDSCAPE_PER_UNIT / 100.0),
        hf.PROV_CLIFF: (1.0, 0.0),
        hf.PROV_CLIFF_DIRECT: (1.0, 0.0),
        hf.PROV_FILL: (FILL_HORIZONTAL_M, FILL_VERTICAL_M),
    }
    notes = {
        hf.PROV_LANDSCAPE: (
            "the cooked UE Landscape heightfield: a true 1 m grid, 7.8 mm vertical "
            "quantisation, no resampling anywhere between the component and this texel"
        ),
        hf.PROV_CLIFF: (
            "rasterised triangles from a placed rock or cliff, at a texel NO SOURCE VERTEX "
            "landed in: the height is this file's own plane interpolation across a triangle "
            "wider than the texel. Exactly as accurate as value 5 at 1 m, and not a "
            "measurement below it -- which is the only thing the two values distinguish"
        ),
        hf.PROV_CLIFF_DIRECT: (
            "rasterised triangles from a placed rock or cliff, at a texel at least one "
            "source vertex landed in. density.u8.z says how many. This is where a render "
            "finer than 1 m is reading geometry rather than a kernel"
        ),
        hf.PROV_FILL: (
            "the 2048 px HeightData_Test interface raster, outside the landscape frame. "
            "3.66 m horizontally and 3.897 m per quantisation step: this is the old "
            "baseline unchanged, and it is the coarsest thing in the field"
        ),
    }
    out: dict[str, dict] = {
        str(hf.PROV_NODATA): {
            "name": hf.PROV_NAMES[hf.PROV_NODATA],
            "accuracy_m": None,
            "note": (
                "open ocean past the landscape edge, and two cave-mouth blobs. Explicit, "
                "never zero-filled: say nothing here."
            ),
        }
    }
    for value, (horizontal, vertical) in derived.items():
        name = hf.PROV_NAMES[value]
        measured = validation["per_layer"].get(name, {})
        enough = measured.get("n", 0) >= ACCURACY_MIN_SAMPLES
        # A cliff split too thin to believe falls back to the province WHOLE rather than to
        # a derived step: a rasterised triangle has no vertical quantisation, so the derived
        # floor of 0.1 m would be a better number than the layer has ever measured.
        fallback = validation["per_layer"].get("cliff, both", {})
        pooled = value in hf.PROV_CLIFF_VALUES and fallback.get("n", 0) >= ACCURACY_MIN_SAMPLES
        if enough:
            accuracy, source = (
                measured["medabs_m"],
                (
                    f"measured: median absolute error over {measured['n']} static resource "
                    "nodes that fell on this layer"
                ),
            )
        elif pooled:
            accuracy, source = (
                fallback["medabs_m"],
                (
                    f"measured over the cliff province WHOLE ({fallback['n']} nodes), because "
                    f"only {measured.get('n', 0)} fell on this half of it and that is fewer "
                    f"than {ACCURACY_MIN_SAMPLES}. The two halves differ in what a finer render "
                    "may claim, not in how accurate they are at 1 m"
                ),
            )
        else:
            accuracy, source = (
                round(max(vertical, 0.1), 3),
                (
                    f"derived: this layer's own vertical step, because only "
                    f"{measured.get('n', 0)} nodes fell on it and that is fewer than "
                    f"{ACCURACY_MIN_SAMPLES}"
                ),
            )
        out[str(value)] = {
            "name": name,
            "horizontal_m": round(horizontal, 4),
            "vertical_step_m": round(vertical, 4),
            "accuracy_m": accuracy,
            "accuracy_from": source,
            "measured": measured,
            "note": notes[value],
        }
    return out


def build_meta(
    *,
    build_pin: str,
    build_raw: dict,
    sweep: dict,
    frame: dict,
    meshes: dict,
    cliffs: dict,
    field: dict,
    water: dict,
    water_checks: dict,
    validation: dict,
    files: dict,
    decoders: dict[str, str],
    timings: dict,
) -> dict:
    """The sidecar the loader reads, plus the provenance a reader needs to date the field."""
    dx, dy = field["drop"]
    return {
        "description": (
            "A 1 m terrain heightfield of the Satisfactory world, cut from the reader's own "
            "installed game by tools/gen_world_heightmap.py. All of it is local: data/local/ "
            "is gitignored and no terrain raster is ever committed to this repository."
        ),
        "generator": "tools/gen_world_heightmap.py",
        "generator_version": GENERATOR_VERSION,
        "transcribed": datetime.now(UTC).date().isoformat(),
        "grid": {
            "width": GRID_PX,
            "height": GRID_PX,
            "spacing_cm": SPACING_CM,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "georeference": (
                f"x_cm = {ORIGIN_X_CM:.0f} + col*{SPACING_CM:.0f}, "
                f"y_cm = {ORIGIN_Y_CM:.0f} + row*{SPACING_CM:.0f}"
            ),
            "alignment": (
                "vertex-aligned: a texel's height belongs to that point exactly, not to a "
                "cell around it, so a reader rounds to the nearest vertex rather than "
                "flooring into a cell"
            ),
            "axes": "game axes -- +X east, +Y south, so row 0 is the northern edge",
        },
        "units": "decimetres above sea level, int16",
        "nodata": hf.NODATA,
        "files": files,
        "digest": files_digest({name: entry["sha256"] for name, entry in files.items()}),
        "provenance": accuracy_block(validation),
        "coverage": {
            **{k: round(v, 6) for k, v in field["coverage"].items()},
            "known": round(1.0 - field["coverage"][hf.PROV_NAMES[hf.PROV_NODATA]], 6),
        },
        "z_range_m": [round(v, 2) for v in field["z_range_m"]],
        "density": {
            "file": hf.DENSITY_NAME,
            "content": ("source vertices per texel, clamped at 255, zero outside the cliff layer"),
            "rule": (
                f"a texel with at least {DIRECT_SAMPLES_MIN} sample is provenance "
                f"{hf.PROV_CLIFF_DIRECT} (direct); below that it is {hf.PROV_CLIFF} "
                "(interpolated). Counted after the facing cull, so a vertex on the "
                "underside of a rock does not make the ground beneath it a measurement"
            ),
            "cliff_texels": field["cliff_texels"],
            "direct_fraction": round(field["cliff_direct_fraction"], 4),
            "median_samples_per_cliff_texel": field["density_p50"],
            "z6_texel_m": round(Z6_TEXEL_M, 4),
            "z7_texel_m": round(Z7_TEXEL_M, 4),
            "why": (
                "a renderer drawing finer than this field's own 1 m spacing has to decide "
                "whether it is resampling a measurement or an interpolant, and nothing "
                "else in the field can tell it. The honest claim this supports is about "
                "DENSITY and never about accuracy: the geometry ladder from the collision "
                "hull to the Nanite leaf is worth half a point of frac_lt_0.25m and moves "
                "p90 by nothing, because the cliff province's error is topological -- a "
                "max-Z field answering with a cave roof over the floor a probe stands on"
            ),
        },
        "container": {
            "quantisation_max_m": round(field["quantisation_max_m"], 4),
            "quantisation_rms_m": round(field["quantisation_rms_m"], 4),
            "why": (
                "int16 decimetres. The rounding costs the RMS above, which is an eighth of "
                "the field's own measured accuracy; int32 would double the file for nothing."
            ),
        },
        "validation": validation,
        "sources": {
            "game": {
                "install": "the reader's own Satisfactory install",
                "licence": (
                    "Coffee Stain Studios' own cooked assets, read locally. Not committed, "
                    "not redistributed, and served to localhost only."
                ),
                "game_version_pinned": build_pin,
                "game_version_raw": {
                    key: build_raw.get(key)
                    for key in ("Changelist", "BranchName", "BuildId", "GameVersion")
                },
            },
            "landscape": {
                "class": "LandscapeComponent",
                "derivation": (
                    f"GrassData height, {LANDSCAPE_N}x{LANDSCAPE_N} uint16 per component; "
                    f"world_z_cm = (h - {LANDSCAPE_ZERO:.0f})/{LANDSCAPE_PER_UNIT:.0f}"
                    f"*{frame['scale_cm']:.0f} + {frame['origin_z_cm']:.0f}"
                ),
                "components": frame["components"],
                "frame": [frame["width"], frame["height"]],
                "frame_origin_cm": [frame["x0_cm"], frame["y0_cm"]],
                "drop_texels": [dx, dy],
                "resampling": (
                    "none. The frame origin sits a whole number of texels from this grid's, "
                    "which the run asserts rather than rounds into, so every landscape "
                    "sample is its own texel"
                ),
                "coverage_of_frame": round(frame["coverage"], 4),
                "holes": {
                    "texels": frame["hole_texels"],
                    "blobs": frame["hole_blobs"],
                    "note": (
                        "raw == 0 inside a component that is present: a landscape hole or "
                        "cave mouth, left as no data rather than read as -255 m"
                    ),
                },
            },
            "cliffs": {
                "class": "StaticMesh render data / BodySetup / FTriangleMeshImplicitObject",
                "recipe": (
                    "the finest description each rock mesh ships, over the set of meshes "
                    "the cooked collision hull defines: the Nanite leaf level where there "
                    "is one, LOD 0 where there is not, the hull itself where neither "
                    "parses. The MESH SET and every placement cull are unchanged from the "
                    "hull-only field, so a before and after differ in triangles alone"
                ),
                "sources": meshes["by_source"],
                "source_order": list(CLIFF_SOURCES),
                "why_not_nanite_only": (
                    "25 rock meshes carry no Nanite resource at all -- sea rocks, corals, "
                    "part of the cave interior set -- and a Nanite-only layer loses about "
                    "365,000 texels against the hull layer while still looking like a field"
                ),
                "why_not_every_mesh": (
                    "extending past the hull-equivalent set costs 1.66 points of "
                    "frac_lt_0.25m and 10.9 m of p90 on 839,506 foliage probes: the 120 "
                    "meshes with no cooked hull are cave pillars, cave holes and merged "
                    "cave floors, and a max-Z field that starts drawing roofs gets worse at "
                    "'where is the ground' no matter how fine its triangles are"
                ),
                "hull_derivation": (
                    "cooked Chaos collision trimesh, found by searching for the 267 (0x10B) "
                    "marker, never at a fixed offset; NumVerts float32 triples then NumTris "
                    "index triples, width chosen by validating max(index) < NumVerts"
                ),
                "nanite_derivation": (
                    "FNaniteResources: root pages inline in the StaticMesh export's tail, "
                    "streaming pages out of the .ubulk the Zen BulkDataMap names, decoded "
                    "in pure Python by core.gameassets.nanite. Leaf clusters only; "
                    "positions and topology only; cluster seams welded at 10 um"
                ),
                "rock_meshes_seen": meshes["wanted"],
                "meshes_decoded": len(meshes["geometry"]),
                "meshes_without_cooked_trimesh": len(meshes["failures"]),
                "closed_manifolds": meshes["closed_manifolds"],
                "closed_manifold_check": (
                    f"{meshes['closed_manifolds']} of {len(meshes['geometry'])} HULLS "
                    "satisfy NumTris == 2*NumVerts - 4 exactly, and "
                    f"{meshes['nanite_closed']} of {meshes['nanite_checked']} Nanite "
                    "decodes have zero boundary edges once cluster seams are welded. The "
                    "rest are the open shells -- cave walls, floors, ceilings and merged "
                    "arch pieces -- and are meant to be. One bit wrong in the strip decode "
                    "shatters the second number into thousands of boundary edges, which is "
                    "what makes it worth computing"
                ),
                "vertices": meshes["verts"],
                "triangles_in_source": meshes["tris"],
                "hull_triangles_in_source": meshes["hull_triangles"],
                "placements_total": cliffs["placements_total"],
                "placements_rasterised": cliffs["placements_used"],
                "placements_dropped": cliffs["dropped"],
                "triangles_rasterised": cliffs["triangles"],
                "vertex_samples_rasterised": cliffs["samples"],
                "triangles_outside_bounds": cliffs["triangles_out_of_bounds"],
                "exclusions": (
                    "NodeMeshActor_C, because predicting a resource node's Z from the mesh "
                    "drawn under it would be circular and the node table is what validates "
                    f"this field; anything whose basename contains '{ARCH_MARK}', because a "
                    "max-Z field puts an arch roof over the ground beneath it and masking "
                    "them improved every metric on all three validation sets; and anything "
                    f"whose scaled extent exceeds {OVERSIZE_CM:.0f} cm, which is the sky "
                    "dome and the ocean shells"
                ),
            },
            "fill": {
                "asset": "/Game/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test",
                "derivation": (
                    f"{BASELINE_PX}x{BASELINE_PX} float16 mip 0; "
                    f"z_cm = {BASELINE_SCALE_CM_PER_RAW:.4f}*raw + {BASELINE_OFFSET_CM:.4f}, "
                    "from a robust fit against the 626 static nodes (569 inliers, 1.07 m RMS)"
                ),
                "nodata_rule": (
                    f"decoded z > {FILL_FLOOR_CM / 100:.0f} m, NOT raw > 0. The blank value "
                    "decodes to about -522 m, so the naive test leaks 138,481 texels of "
                    "blank into the field as a false sea floor"
                ),
                "role": "outside the landscape frame only; the old baseline, unchanged",
            },
            "water": {
                "recipe": (
                    "the game's own map artwork for the plan shape, the cooked water "
                    "volumes' own bounding boxes for the level. Neither source is asked "
                    "for what it does not know: the artwork has no Z at all, and the "
                    "volumes are far too sparse to draw a coastline with"
                ),
                "shape": {
                    "asset": (
                        "/Game/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/Map_<c>-<r>, "
                        "the same four BC1 slices tools/gen_map_image.py draws"
                    ),
                    "classifier": f"blue - red >= {WATER_ARTWORK_BLUE_OVER_RED} on the 8192 sheet",
                    "registration": (
                        "(0, 0) sheet pixels, measured by a +/-2 px sweep rather than "
                        "assumed. The sheet's box and this grid's are the same 7500 m "
                        "square, so the resample is nearest at 0.92 m to the pixel"
                    ),
                    "artwork_water_km2": round(water["artwork_texels"] / 1e6, 4),
                },
                "level": {
                    "actors": sum(sweep["water_actors"].values()),
                    "by_class": dict(sorted(sweep["water_actors"].items())),
                    "with_a_world_box": len(sweep["water"]),
                    "without_a_box": len(sweep["water_boxless"]),
                    "boxless": [
                        {"class": cls, "actor": actor, "cell": cell}
                        for cls, actor, cell in sweep["water_boxless"]
                    ],
                    "box_sources": dict(sorted(sweep["water_box_sources"].items())),
                    "surface_classes": sorted(WATER_SURFACE_CLASSES),
                    "boxes_rasterised": water["boxes_rasterised"],
                    "rule": (
                        "the highest surface-class box top standing over the texel. A box "
                        "top is a surface, so where several overlap in plan the highest is "
                        "the one visible from above; one median per drawn body was measured "
                        "to invent up to 157 m of depth over 0.06 km2, because the ocean "
                        "and its rivers are one drawn shape spanning 141 m of box top"
                    ),
                    "oracle": (
                        "the save's 23 water extractors all sit inside a volume box and "
                        "stand on its top to within 0.005 cm, which is what says a box top "
                        "is the water surface rather than merely near it"
                    ),
                },
                "combine": {
                    "bodies": water["bodies"],
                    "texels_with_no_box_over_them": water["uncovered_texels"],
                    "texels_dropped_as_ground_above_the_level": water[
                        "dropped_standing_out_texels"
                    ],
                    "water_km2": round(water["water_texels"] / 1e6, 4),
                    "depth_measured_km2": round(water["measured_texels"] / 1e6, 4),
                    "depth_unknown_km2": round(water["level_only_texels"] / 1e6, 4),
                    "depth_p50_m": water["depth_p50_m"],
                    "depth_p90_m": water["depth_p90_m"],
                    "unknown_depth_rule": (
                        "where the ground under the water is the fill layer or no data, "
                        "the depth is not knowable and waterq.u8.z says so. Nothing may "
                        "gate on water > terrain there: the fill raster's 3.9 m step "
                        "routinely rounds above a sea surface 17 m down, which reads the "
                        "open ocean as dry"
                    ),
                },
                "validation": water_checks,
                "accuracy_m": 0.05,
                "supersedes": (
                    "a flatness detector over the interface raster, which found 20.4% of "
                    "the sheet as water against the artwork's 39.0% -- 46.7% recall, 35.8% "
                    "over Spire Coast -- and invented plateau lakes on flat mesas. Its "
                    "failures were structural: 3.9 m of quantisation against 2.1 m of "
                    "water, rivers below the raster's resolution, and a fill province where "
                    "the terrain it compared against IS the water surface"
                ),
                "role": (
                    "INFORMATION ONLY. Nothing downstream moves ground because of it, and "
                    "that stays measured rather than assumed: a lake gate built on the old "
                    "detector made the field worse, nodes trim90 0.93 against 0.77."
                ),
            },
        },
        "sweep": {
            "packages": sweep["packages"],
            "unreadable": sweep["unreadable"],
            "malformed_components": sweep["malformed_components"],
            "placements": len(sweep["placements"]),
            "distinct_meshes": len(sweep["meshes"]),
        },
        "decoders": {
            "oodle": {
                "name": "pyooz",
                "version": decoders.get("pyooz", "unknown"),
                "import_name": "ooz",
                "licence": "GPL-3.0",
                "role": (
                    "container block decompression, offline, at generation time only. An "
                    "OPTIONAL dependency: the `gen` extra in pyproject.toml, pinned exactly "
                    "because it decides these bytes, and asked for on the command line -- "
                    "`uv run --extra gen python tools/gen_world_heightmap.py`. It is "
                    "imported at module scope nowhere, and lazily inside one function of "
                    "satisfactory_mcp.core.gameassets.iostore, so the server and the test "
                    "suite run with it absent. No part of it is in the output."
                ),
            },
            "texture": {
                "name": "texture2ddecoder",
                "version": decoders.get("texture2ddecoder", "unknown"),
                "pillow": decoders.get("pillow", "unknown"),
                "role": (
                    "BC1 blocks of the four map slices, for the water channel's plan shape. "
                    "The same two the map image is drawn with, and optional in the same "
                    "way: both are handed to core.gameassets.textures as arguments, so this "
                    "file imports neither at module scope."
                ),
            },
            "container": (
                "satisfactory_mcp.core.gameassets.iostore's IoStore reader, imported by "
                "name. It was tools/gen_world_collectibles.py's, imported by file path, "
                "until the four generators that read the same container came to share one "
                "copy of it."
            ),
            "codec": "satisfactory_mcp.domain.spatial.heightfield, imported so there is one",
        },
        "timings_s": timings,
        "known_defects": [
            (
                "about a fifth of the nominal box is no-data: open ocean past the landscape "
                "edge plus two cave-mouth blobs. Explicit, never zero-filled."
            ),
            (
                "the cave and overhang tail is irreducible. About 44 nodes sit UNDER the "
                "surface, and no single-valued heightmap can represent them; the interface "
                "raster fails 51 by the same test. A two-layer field is the principled fix "
                "and costs one extra rasteriser pass."
            ),
            (
                "21 of the rock meshes ship no cooked trimesh (CTF_UseSimpleAndComplex): "
                "SM_RockPile_*, SM_Cave_Pillar_*, SmoothRock_01 and a few others. Small and "
                "rare; their AggGeom convex hulls are still open for a later pass."
            ),
            (
                f"{len(sweep['water_boxless'])} water actors ship no bounding box at all -- "
                "FGWaterVolume brushes with no cooked BrushBodySetup, one FGRiverSpline, and "
                "two developer backdrop planes with no transform. The artwork mask carries "
                "their plan shape and neighbouring volumes carry their level; what is left "
                f"over is {water['uncovered_texels']} texels of drawn water standing over no "
                "box at all, which take their body's median."
            ),
            (
                "the water channel states a LEVEL everywhere and a DEPTH only where the "
                f"ground under it was measured at 1 m: {water['level_only_texels'] / 1e6:.2f} "
                "km2 of it, most of the ocean, is depth-unknown. It is information only."
            ),
        ],
        "staleness": (
            "sources.game.game_version_pinned is the build this field was cut from, in the "
            "same shape data/resource_nodes.json uses, so a field and a node table from "
            "different builds are comparable on sight. tools/gen_world_heightmap.py refuses "
            "to overwrite this directory unless the sidecar names the build then installed; "
            "--force says it anyway. Terrain moves every patch, and the standing rule is "
            "that a pinned artifact announces drift rather than answering silently wrong."
        ),
    }


def pinned_build(meta: dict) -> str | None:
    """The build an existing sidecar names, or None if it names none."""
    return read_str_path(meta, PIN_PATH)


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="destination directory for the rasters and meta.json (gitignored)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a field this run cannot show was cut from the installed build",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-stage progress lines")
    parser.add_argument(
        "--caves",
        action="store_true",
        help="write only the cave masks to --caves-dir, reading the field at --field",
    )
    parser.add_argument("--caves-dir", type=Path, default=LOCAL_DIR / caves.DIR_NAME)
    parser.add_argument(
        "--rocks",
        action="store_true",
        help="write only the collision pack into the field at --field, beside its planes",
    )
    parser.add_argument("--field", type=Path, default=LOCAL_DIR / hf.DIR_NAME)
    args = parser.parse_args()

    # Three of the extra: the water channel's plan shape is the map sheet's own BC1 slices,
    # so this generator decodes textures as well as container blocks.
    decoders = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    pyooz_version = decoders["pyooz"]
    import texture2ddecoder
    from PIL import Image

    try:
        build_pin, build_raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc} -- point --game at the install holding FactoryGame/ and Engine/")
        return 1
    print(f"installed build: {build_pin}")
    if args.caves:
        return write_caves(args, build_pin, build_raw)
    if args.rocks:
        return write_rocks(args, build_pin, build_raw)

    out_dir: Path = args.out_dir
    if out_dir.is_dir() and not args.force:
        existing: dict = {}
        try:
            loaded = json.loads((out_dir / hf.META_NAME).read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError, TypeError):
            existing = {}
        pinned = pinned_build(existing)
        if pinned != build_pin:
            print(
                f"{out_dir} already exists and this run cannot show it was cut from the "
                f"installed build.\n"
                f"  installed: {build_pin}\n"
                f"  that field: {pinned or 'no meta.json, or no build recorded in it'}\n"
                "Terrain moves every patch and the repository's own tables are pinned to a "
                "build the new field may no longer agree with, so drift is announced rather "
                "than overwritten. Pass --force to overwrite it anyway."
            )
            return 3

    if not NODE_TABLE.is_file():
        print(
            f"{NODE_TABLE} is not present, so this run could not validate the field it "
            "built. A heightmap nobody measured is not one this file will write."
        )
        return 4

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the world from {paks} with pyooz {pyooz_version}")
    store = open_container(args.game)
    scripts = ScriptObjects(paks, oodle_decompress)
    index = AssetIndex(store)
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )
    loud = not args.quiet
    timings: dict[str, float] = {}

    # ---- stages 1, 2 and the water actors: one sweep ------------------------------------
    print("sweeping the world's packages for landscape, placements and water volumes")
    classes = ClassFacts(store, index)
    water_meshes = MeshBounds(store, scripts, index)
    sweep = sweep_levels(store, scripts, classes, water_meshes, loud)
    timings["sweep"] = round(sweep["seconds"], 1)
    print(
        f"  {sweep['packages']} packages in {sweep['seconds']:.0f}s: "
        f"{len(sweep['components'])} landscape components, {len(sweep['placements'])} "
        f"placements over {len(sweep['meshes'])} distinct meshes "
        f"({sweep['unreadable']} unreadable, {sweep['malformed_components']} malformed)"
    )
    print(
        f"  {sum(sweep['water_actors'].values())} water actors over "
        f"{len(sweep['water_actors'])} classes, {len(sweep['water'])} with a world box, "
        f"{len(sweep['water_boxless'])} without"
    )

    started = time.time()
    frame = landscape_frame(sweep)
    dx, dy = drop_offsets(frame)
    timings["landscape"] = round(time.time() - started, 1)
    print(
        f"  landscape {frame['width']}x{frame['height']} m at "
        f"({frame['x0_cm']:.0f}, {frame['y0_cm']:.0f}) cm, {frame['coverage'] * 100:.1f}% "
        f"covered, {frame['hole_texels']} hole texels in {frame['hole_blobs']} blobs"
    )
    print(f"  drops into the output grid at texel ({dx}, {dy}), exactly -- no resampling")
    print(f"  {frame['seam_disagreements']} shared edge samples disagree between components")

    # ---- stage 3: cliff collision ------------------------------------------------------
    print("decoding the finest geometry every placed rock ships")
    meshes = read_mesh_geometry(store, scripts, index, sweep["meshes"], loud)
    timings["mesh_decode"] = round(meshes["seconds"], 1)
    print(
        f"  {len(meshes['geometry'])}/{meshes['wanted']} rock meshes decoded in "
        f"{meshes['seconds']:.0f}s: {meshes['verts']} vertices, {meshes['tris']} triangles "
        f"({meshes['hull_triangles']} in the hulls they replace), sources "
        f"{meshes['by_source']}"
    )
    print(
        f"  {meshes['closed_manifolds']} hulls satisfy the Euler relation; "
        f"{meshes['nanite_closed']}/{meshes['nanite_checked']} Nanite decodes have zero "
        "boundary edges after welding"
    )
    if not meshes["geometry"]:
        print(
            "not one rock mesh decoded. The cooked collision layout changed, which is the "
            "whole of what makes this field better than the interface raster. Refusing."
        )
        return 5
    print("rasterising them into a 1 m max-Z overlay")
    cliffs = rasterise_cliffs(sweep, meshes["geometry"], frame, loud)
    timings["rasterise"] = round(cliffs["seconds"], 1)
    print(
        f"  {cliffs['placements_used']}/{cliffs['placements_total']} placements, "
        f"{cliffs['triangles'] / 1e6:.1f} M triangles in {cliffs['seconds']:.0f}s; "
        f"dropped {cliffs['dropped']}"
    )

    # ---- stage 4: fill -----------------------------------------------------------------
    started = time.time()
    baseline_cm, baseline_valid = read_baseline(store)
    timings["fill"] = round(time.time() - started, 1)
    print(f"  interface raster decoded, {baseline_valid.mean() * 100:.1f}% of it says something")

    # ---- compose -----------------------------------------------------------------------
    started = time.time()
    field = compose(frame, cliffs, baseline_cm, baseline_valid)
    timings["compose"] = round(time.time() - started, 1)
    for name, fraction in field["coverage"].items():
        print(f"  {name:>10}: {fraction * 100:6.2f}% of the box")
    print(
        f"  z range {field['z_range_m'][0]:.1f} .. {field['z_range_m'][1]:.1f} m; "
        f"int16-decimetre quantisation RMS {field['quantisation_rms_m']:.4f} m"
    )
    print(
        f"  cliff province {field['cliff_texels']} texels, "
        f"{field['cliff_direct_fraction'] * 100:.1f}% with a source vertex in them "
        f"(median {field['density_p50']} samples per cliff texel)"
    )

    # ---- the top overlay: arches and foliage boulders over the ground -------------------
    print("rasterising arches and foliage boulders for the top plane")
    top = rasterise_top(sweep, frame, store, scripts, index, loud)
    timings["top"] = round(top["seconds"], 1)
    top_dm, top_raised = compose_top(field["height_dm"], frame, top)
    print(
        f"  {top['arch_placements']} arch placements, {top['foliage_instances']} foliage "
        f"instances {top['foliage_by_mesh']}; raised {top_raised} texels over the ground"
    )

    # ---- stage 5: water, after the terrain it is measured against ----------------------
    print("classifying the map artwork's water and levelling it on the water volumes")
    started = time.time()
    mask = artwork_water_mask(store, texture2ddecoder, Image)
    water = water_surface(mask, sweep["water"], field["height_dm"], field["prov"])
    water_checks = validate_water(water, mask, sweep["water"])
    timings["water"] = round(time.time() - started, 1)
    print(
        f"  artwork water {water['artwork_texels'] / 1e6:.3f} km2 over "
        f"{water['bodies']} bodies; {water['boxes_rasterised']} surface boxes rasterised"
    )
    print(
        f"  channel {water['water_texels'] / 1e6:.3f} km2: "
        f"{water['measured_texels'] / 1e6:.3f} km2 with a measured depth, "
        f"{water['level_only_texels'] / 1e6:.3f} km2 depth-unknown; dropped "
        f"{water['dropped_standing_out_texels'] / 1e6:.3f} km2 where the ground stands above it"
    )
    node_check = water_checks["dry_node_false_positive"]
    region_check = water_checks["region_recall"]
    ocean_check = water_checks["ocean_level"]
    covered_check = water_checks["artwork_over_a_box"]
    print(
        f"    dry-node false positives {node_check['called_water']}/{node_check['nodes']} "
        f"= {node_check['fraction'] * 100:.2f}% (gate {node_check['gate_max'] * 100:.0f}%)"
    )
    print(
        f"    {region_check['region']} recall "
        + (
            "unmeasurable"
            if region_check["recall"] is None
            else f"{region_check['recall'] * 100:.2f}%"
        )
        + f" (gate {region_check['gate_min'] * 100:.0f}%)"
    )
    print(
        f"    ocean level {ocean_check['assigned_m']} m against {ocean_check['boxes']} box "
        f"tops at {ocean_check['box_median_m']} m: offset {ocean_check['offset_m']} m "
        f"(gate {ocean_check['gate_max_m']} m)"
    )
    print(
        f"    artwork water over no box {covered_check['uncovered_texels']} texels "
        f"= {covered_check['fraction'] * 100:.4f}% (gate {covered_check['gate_max'] * 100:.0f}%)"
    )
    failures = water_gate_failures(water_checks)
    if failures:
        for sentence in failures:
            print(f"  {sentence}")
        print("Refusing to write a water channel that does not pass its own gates.")
        return 7

    started = time.time()
    validation = validate(field["height_dm"], field["prov"])
    timings["validate"] = round(time.time() - started, 1)
    whole = validation["field"]
    print(
        f"  validated on {whole['n']}/{validation['nodes']} nodes: trimmed RMS "
        f"{whole['trim90_rms_m']:.3f} m, median absolute {whole['medabs_m']:.3f} m, "
        f"P90 {whole['p90_m']:.2f} m, {whole['under_1m']} within a metre"
    )
    for name, stats in validation["per_layer"].items():
        if stats["n"]:
            print(
                f"    {name:>10}: n={stats['n']:3d} medabs {stats['medabs_m']:.3f} m, "
                f"trimmed RMS {stats['trim90_rms_m']:.3f} m"
            )
    if whole["trim90_rms_m"] > VALIDATION_TRIM_RMS_MAX_M:
        print(
            f"trimmed RMS is {whole['trim90_rms_m']:.3f} m against a gate of "
            f"{VALIDATION_TRIM_RMS_MAX_M} m. Something in the decode moved: the workflow "
            "that proved this pipeline measured 0.368 m, and a field this far out would be "
            "a plausible-looking raster that is quietly metres wrong. Refusing to write."
        )
        return 6

    terrain_check = validate_terrain(frame, field["prov"])
    print(
        f"  bare terrain on {terrain_check['n']} landscape nodes: median absolute "
        f"{terrain_check['medabs_m']} m (gate {TERRAIN_NODE_MEDIAN_MAX_M} m)"
    )
    if terrain_check["medabs_m"] is None or terrain_check["medabs_m"] > TERRAIN_NODE_MEDIAN_MAX_M:
        print("the bare terrain plane does not sit on the nodes. Refusing to write it.")
        return 8

    started = time.time()
    water_dm = np.where(
        np.isfinite(water["level_m"]),
        np.clip(np.round(np.nan_to_num(water["level_m"]) * hf.DM_PER_M), -32767, 32767),
        hf.NODATA,
    ).astype(np.int16)
    payload = {
        hf.HEIGHT_NAME: hf.encode_i16(field["height_dm"]),
        hf.PROV_NAME: hf.encode_u8(field["prov"]),
        hf.WATER_NAME: hf.encode_i16(water_dm),
        hf.WATER_QUALITY_NAME: hf.encode_u8(water["quality"]),
        hf.DENSITY_NAME: hf.encode_u8(field["density"]),
        hf.TERRAIN_NAME: hf.encode_u16(frame["raw"]),
        hf.TOP_NAME: hf.encode_i16(top_dm),
    }
    timings["encode"] = round(time.time() - started, 1)
    files = {
        hf.HEIGHT_NAME: {
            "content": f"{GRID_PX}x{GRID_PX} int16 decimetres, row-delta then zlib",
            "bytes": len(payload[hf.HEIGHT_NAME]),
        },
        hf.PROV_NAME: {
            "content": (
                "which layer answered each texel: 0 no-data, 1 landscape, 3 fill, "
                "4 cliff geometry interpolated across a triangle wider than the texel, "
                "5 cliff geometry with at least one source vertex in the texel. A reader "
                "that knows only 4 sees 5 as 'not landscape, not fill, not no-data', which "
                "is what 4 meant before the split. zlib, no delta"
            ),
            "bytes": len(payload[hf.PROV_NAME]),
        },
        hf.DENSITY_NAME: {
            "content": (
                "source vertices per texel over the cliff layer, clamped at 255, zero "
                "elsewhere. The interface between the geometry and anything that draws it. "
                "zlib, no delta"
            ),
            "bytes": len(payload[hf.DENSITY_NAME]),
        },
        hf.WATER_NAME: {
            "content": "water surface Z, same grid, same no-data. Information only",
            "bytes": len(payload[hf.WATER_NAME]),
        },
        hf.WATER_QUALITY_NAME: {
            "content": (
                f"{hf.WATER_DRY} dry, {hf.WATER_MEASURED} water with a depth measured "
                f"against 1 m terrain, {hf.WATER_LEVEL_ONLY} water whose level is known and "
                "whose depth is not. zlib, no delta"
            ),
            "bytes": len(payload[hf.WATER_QUALITY_NAME]),
        },
        hf.TERRAIN_NAME: {
            "content": (
                f"{frame['width']}x{frame['height']} uint16 raw landscape units on terrain_grid, "
                "0 = hole or outside the landscape, row-delta over the int16 bits then zlib"
            ),
            "bytes": len(payload[hf.TERRAIN_NAME]),
        },
        hf.TOP_NAME: {
            "content": (
                f"{GRID_PX}x{GRID_PX} int16 decimetres: height.i16.z max-folded with arch and "
                "foliage-boulder collision trimeshes, row-delta then zlib"
            ),
            "bytes": len(payload[hf.TOP_NAME]),
        },
    }
    hashes = {name: sha256_hex(payload[name]) for name in files}
    for name, entry in files.items():
        entry["sha256"] = hashes[name]
    meta = build_meta(
        build_pin=build_pin,
        build_raw=build_raw,
        sweep=sweep,
        frame=frame,
        meshes=meshes,
        cliffs=cliffs,
        field=field,
        water=water,
        water_checks=water_checks,
        validation=validation,
        files=files,
        decoders=decoders,
        timings=timings,
    )
    meta["planes"] = {
        "ground": hf.HEIGHT_NAME,
        "terrain": hf.TERRAIN_NAME,
        "top": hf.TOP_NAME,
    }
    meta["terrain_grid"] = {
        "width": frame["width"],
        "height": frame["height"],
        "spacing_cm": frame["scale_cm"],
        "x0_cm": frame["x0_cm"],
        "y0_cm": frame["y0_cm"],
        "zero": LANDSCAPE_ZERO,
        "units_per_m": LANDSCAPE_PER_UNIT * 100.0 / LANDSCAPE_SCALE_CM,
        "offset_m": frame["origin_z_cm"] / 100.0,
        "georeference": "x_cm = x0_cm + col*spacing_cm; z_m = (raw - zero) / units_per_m + offset_m",
        "seam_disagreements": frame["seam_disagreements"],
        "seam_rule": "stitched in sweep order; a later component overwrites the shared edge",
        "validation": terrain_check,
    }
    meta["top"] = {
        key: top[key]
        for key in (
            "arch_placements",
            "foliage_instances",
            "foliage_by_mesh",
            "meshes_without_trimesh",
            "triangles",
        )
    }
    meta["top"]["raised_texels"] = top_raised
    print("packing every rock's collision mesh for exact heights")
    started = time.time()
    payload.update(rock_pack(store, scripts, index, classes, sweep, build_pin, build_raw))
    timings["rocks"] = round(time.time() - started, 1)
    payload[hf.META_NAME] = json.dumps(meta, indent=1).encode("utf-8")
    written = install_directory(out_dir, payload)
    total = sum(written.values())
    print(f"wrote {out_dir}  {total} B  ({total / 1e6:.1f} MB)")
    for name, size in written.items():
        print(f"  {name:>14}  {size:>10} B")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0
