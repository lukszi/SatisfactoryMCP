"""The heightfield's planes: fill, landscape and cliff fused onto the 1 m grid, encoded and
described layer by layer for the sidecar."""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.frame import (
    BASELINE_BOX_CM,
    GRID_PX,
    ORIGIN_X_CM,
    ORIGIN_Y_CM,
    SPACING_CM,
    Z6_TEXEL_M,
    Z7_TEXEL_M,
)
from mapgen.gamedata.level.fill_raster import (
    BASELINE_OFFSET_CM,
    BASELINE_PX,
    BASELINE_SCALE_CM_PER_RAW,
    FILL_FLOOR_CM,
)
from mapgen.gamedata.level.landscape import (
    LANDSCAPE_N,
    LANDSCAPE_PER_UNIT,
    LANDSCAPE_SCALE_CM,
    LANDSCAPE_ZERO,
    drop_offsets,
)
from mapgen.gamedata.meshes import CLIFF_SOURCES, DIRECT_SAMPLES_MIN
from mapgen.gamedata.placements import ARCH_MARK, OVERSIZE_CM
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The interface raster's own resolution, for the accuracy the fill layer inherits.
FILL_HORIZONTAL_M = 7500.0 / BASELINE_PX


FILL_VERTICAL_M = BASELINE_SCALE_CM_PER_RAW / 255.0 / 100.0


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


def report_field(field: dict) -> None:
    """The composed field's coverage, range and cliff province, one progress line each."""
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


def encode_planes(field: dict, water: dict, frame: dict, top_dm: np.ndarray) -> dict[str, bytes]:
    """Every plane of the field, encoded with the shipped codec, keyed by file name."""
    water_dm = np.where(
        np.isfinite(water["level_m"]),
        np.clip(np.round(np.nan_to_num(water["level_m"]) * hf.DM_PER_M), -32767, 32767),
        hf.NODATA,
    ).astype(np.int16)
    return {
        hf.HEIGHT_NAME: hf.encode_i16(field["height_dm"]),
        hf.PROV_NAME: hf.encode_u8(field["prov"]),
        hf.WATER_NAME: hf.encode_i16(water_dm),
        hf.WATER_QUALITY_NAME: hf.encode_u8(water["quality"]),
        hf.DENSITY_NAME: hf.encode_u8(field["density"]),
        hf.TERRAIN_NAME: hf.encode_u16(frame["raw"]),
        hf.TOP_NAME: hf.encode_i16(top_dm),
    }


def describe_files(payload: dict[str, bytes], frame: dict) -> dict:
    """The sidecar's ``files`` block: what each plane holds, its size and its hash."""
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
    return files


def add_planes(meta: dict, frame: dict, terrain_check: dict, top: dict, top_raised: int) -> None:
    """The ``planes``, ``terrain_grid`` and ``top`` blocks, appended after ``build_meta``'s."""
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


def landscape_source(frame: dict, field: dict) -> dict:
    """The sidecar's ``sources.landscape`` block: the cooked heightfield, dropped unresampled."""
    dx, dy = field["drop"]
    return {
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
    }


def cliff_source(meshes: dict, cliffs: dict) -> dict:
    """The sidecar's ``sources.cliffs`` block: the rock geometry and what was culled."""
    return {
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
    }


def fill_source() -> dict:
    """The sidecar's ``sources.fill`` block: the interface raster outside the frame."""
    return {
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
    }


def density_block(field: dict) -> dict:
    """The sidecar's ``density`` block: the plane a finer render reads."""
    return {
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
    }


def container_block(field: dict) -> dict:
    """The sidecar's ``container`` block: what int16 decimetres cost."""
    return {
        "quantisation_max_m": round(field["quantisation_max_m"], 4),
        "quantisation_rms_m": round(field["quantisation_rms_m"], 4),
        "why": (
            "int16 decimetres. The rounding costs the RMS above, which is an eighth of "
            "the field's own measured accuracy; int32 would double the file for nothing."
        ),
    }
