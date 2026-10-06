"""The heightfield's gates: the built field and its water against what it did not make, and
the accuracy each layer was measured to have."""

from __future__ import annotations

import json

import numpy as np
from scipy import ndimage

from mapgen.common import ROOT
from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM, sample_grid
from mapgen.gamedata.ground.biome import region_mask
from mapgen.gamedata.level.landscape import LANDSCAPE_PER_UNIT, LANDSCAPE_SCALE_CM
from mapgen.gamedata.nodes import NODE_TABLE
from mapgen.gamedata.water.actors import WATER_SURFACE_CLASSES
from mapgen.gamedata.water.channel import WATER_ARTWORK_BLUE_OVER_RED
from mapgen.terrain.field import FILL_HORIZONTAL_M, FILL_VERTICAL_M
from satisfactory_mcp.domain.spatial import heightfield as hf

#: How many nodes a provenance layer needs before its measured accuracy is believed rather
#: than its derived quantisation step quoted. Nodes are not spread evenly, and the fill
#: layer in particular is mostly ocean where nothing stands.
ACCURACY_MIN_SAMPLES = 30


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


#: The gate the run has to clear against the node table. This pipeline measures 0.368 m
#: trimmed RMS and the interface raster alone manages 1.08 m, so 0.5 m is the band in
#: which a decode regression cannot pass as a refresh.
VALIDATION_TRIM = 0.90


VALIDATION_TRIM_RMS_MAX_M = 0.5


#: The bare terrain against nodes standing on the landscape layer, median absolute.
TERRAIN_NODE_MEDIAN_MAX_M = 0.3


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


def report_water(water: dict, checks: dict) -> None:
    """The water channel's summary and its four gates, one progress line each."""
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
    node_check = checks["dry_node_false_positive"]
    region_check = checks["region_recall"]
    ocean_check = checks["ocean_level"]
    covered_check = checks["artwork_over_a_box"]
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


def report_validation(validation: dict) -> None:
    """The node validation, whole and per layer, one progress line each."""
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


def water_source(sweep: dict, water: dict, water_checks: dict) -> dict:
    """The sidecar's ``sources.water`` block: how the channel was made and how it measured."""
    return {
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
            "texels_dropped_as_ground_above_the_level": water["dropped_standing_out_texels"],
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
    }
