"""The heightfield sidecar's blocks: files, sources, density, container and accuracy."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from mapgen.gamedata.frame import GRID_PX, Z6_TEXEL_M, Z7_TEXEL_M
from mapgen.gamedata.level.fill_raster import (
    FILL_FLOOR_CM,
    FILL_RASTER_OFFSET_CM,
    FILL_RASTER_PX,
    FILL_RASTER_SCALE_CM_PER_RAW,
)
from mapgen.gamedata.level.landscape import (
    LANDSCAPE_N,
    LANDSCAPE_PER_UNIT,
    LANDSCAPE_SCALE_CM,
    LANDSCAPE_ZERO,
)
from mapgen.gamedata.meshes import CLIFF_SOURCES, DIRECT_SAMPLES_MIN
from mapgen.gamedata.placements import ARCH_MARK, OVERSIZE_CM
from mapgen.gamedata.water.actors import WATER_SURFACE_CLASSES
from mapgen.gamedata.water.channel import WATER_ARTWORK_BLUE_OVER_RED
from mapgen.terrain.heightfield.field import FILL_HORIZONTAL_M, FILL_VERTICAL_M, FieldLayers
from mapgen.terrain.heightfield.validate import (
    ACCURACY_MIN_SAMPLES,
    ErrorStats,
    FieldValidation,
    TerrainCheck,
    WaterChecks,
)
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.domain.spatial import heightfield as hf

if TYPE_CHECKING:
    from mapgen.gamedata.level.landscape import LandscapeFrame
    from mapgen.gamedata.level.sweep import Sweep
    from mapgen.gamedata.meshes import MeshGeometry
    from mapgen.gamedata.rocks.cliffs import CliffRaster, TopOverlay
    from mapgen.gamedata.water.channel import WaterSurface

__all__ = [
    "FileEntry",
    "accuracy_block",
    "add_planes",
    "cliff_source",
    "container_block",
    "density_block",
    "describe_files",
    "fill_source",
    "landscape_source",
    "water_source",
]


class FileEntry(TypedDict):
    """One plane in the sidecar's ``files`` block."""

    content: str
    bytes: int
    sha256: str


def _file_contents(frame: LandscapeFrame) -> dict[str, str]:
    """What each plane holds, in the order the sidecar lists them."""
    return {
        hf.HEIGHT_NAME: f"{GRID_PX}x{GRID_PX} int16 decimetres, row-delta then zlib",
        hf.PROV_NAME: (
            "which layer answered each texel: 0 no-data, 1 landscape, 3 fill, "
            "4 cliff geometry interpolated across a triangle wider than the texel, "
            "5 cliff geometry with at least one source vertex in the texel. A reader "
            "that knows only 4 sees 5 as 'not landscape, not fill, not no-data', which "
            "is what 4 meant before the split. zlib, no delta"
        ),
        hf.DENSITY_NAME: (
            "source vertices per texel over the cliff layer, clamped at 255, zero "
            "elsewhere. The interface between the geometry and anything that draws it. "
            "zlib, no delta"
        ),
        hf.WATER_NAME: "water surface Z, same grid, same no-data. Information only",
        hf.WATER_QUALITY_NAME: (
            f"{hf.WATER_DRY} dry, {hf.WATER_MEASURED} water with a depth measured "
            f"against 1 m terrain, {hf.WATER_LEVEL_ONLY} water whose level is known and "
            "whose depth is not. zlib, no delta"
        ),
        hf.TERRAIN_NAME: (
            f"{frame['width']}x{frame['height']} uint16 raw landscape units on terrain_grid, "
            "0 = hole or outside the landscape, row-delta over the int16 bits then zlib"
        ),
        hf.TOP_NAME: (
            f"{GRID_PX}x{GRID_PX} int16 decimetres: height.i16.z max-folded with arch and "
            "foliage-boulder collision trimeshes, row-delta then zlib"
        ),
    }


def describe_files(payload: dict[str, bytes], frame: LandscapeFrame) -> dict[str, FileEntry]:
    """The sidecar's ``files`` block: what each plane holds, its size and its hash."""
    return {
        name: {"content": content, "bytes": len(payload[name]), "sha256": sha256_hex(payload[name])}
        for name, content in _file_contents(frame).items()
    }


def add_planes(meta: dict[str, object], frame: LandscapeFrame, terrain_check: TerrainCheck, top: TopOverlay,
               top_raised: int) -> None:  # fmt: skip
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
    kept = (
        "arch_placements",
        "foliage_instances",
        "foliage_by_mesh",
        "meshes_without_trimesh",
        "triangles",
    )
    meta["top"] = {**{key: top[key] for key in kept}, "raised_texels": top_raised}


def landscape_source(frame: LandscapeFrame, field: FieldLayers) -> dict[str, object]:
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


def cliff_source(meshes: MeshGeometry, cliffs: CliffRaster) -> dict[str, object]:
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


def fill_source() -> dict[str, object]:
    """The sidecar's ``sources.fill`` block: the interface raster outside the frame."""
    return {
        "asset": "/Game/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test",
        "derivation": (
            f"{FILL_RASTER_PX}x{FILL_RASTER_PX} float16 mip 0; "
            f"z_cm = {FILL_RASTER_SCALE_CM_PER_RAW:.4f}*raw + {FILL_RASTER_OFFSET_CM:.4f}, "
            "from a robust fit against the 626 static nodes (569 inliers, 1.07 m RMS)"
        ),
        "nodata_rule": (
            f"decoded z > {FILL_FLOOR_CM / 100:.0f} m, NOT raw > 0. The blank value "
            "decodes to about -522 m, so the naive test leaks 138,481 texels of "
            "blank into the field as a false sea floor"
        ),
        "role": "outside the landscape frame only; the old baseline, unchanged",
    }


def density_block(field: FieldLayers) -> dict[str, object]:
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


def container_block(field: FieldLayers) -> dict[str, object]:
    """The sidecar's ``container`` block: what int16 decimetres cost."""
    return {
        "quantisation_max_m": round(field["quantisation_max_m"], 4),
        "quantisation_rms_m": round(field["quantisation_rms_m"], 4),
        "why": (
            "int16 decimetres. The rounding costs the RMS above, which is an eighth of "
            "the field's own measured accuracy; int32 would double the file for nothing."
        ),
    }


#: Each provenance value's horizontal resolution and vertical step, in metres.
_LAYER_STEPS = {
    hf.PROV_LANDSCAPE: (1.0, LANDSCAPE_SCALE_CM / LANDSCAPE_PER_UNIT / 100.0),
    hf.PROV_CLIFF: (1.0, 0.0),
    hf.PROV_CLIFF_DIRECT: (1.0, 0.0),
    hf.PROV_FILL: (FILL_HORIZONTAL_M, FILL_VERTICAL_M),
}

_LAYER_NOTES = {
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


def _layer_accuracy(measured: ErrorStats, pooled: ErrorStats,
                    vertical: float, cliff: bool) -> tuple[float | None, str]:  # fmt: skip
    """A layer's accuracy and where it came from: measured on its own nodes, on the cliff
    province whole, or its own vertical step."""
    if measured["n"] >= ACCURACY_MIN_SAMPLES:
        return measured.get("medabs_m"), (
            f"measured: median absolute error over {measured['n']} static resource "
            "nodes that fell on this layer"
        )
    # A cliff split too thin to believe falls back to the province WHOLE rather than to
    # a derived step: a rasterised triangle has no vertical quantisation, so the derived
    # floor of 0.1 m would be a better number than the layer has ever measured.
    if cliff and pooled["n"] >= ACCURACY_MIN_SAMPLES:
        return pooled.get("medabs_m"), (
            f"measured over the cliff province WHOLE ({pooled['n']} nodes), because "
            f"only {measured['n']} fell on this half of it and that is fewer "
            f"than {ACCURACY_MIN_SAMPLES}. The two halves differ in what a finer render "
            "may claim, not in how accurate they are at 1 m"
        )
    return round(max(vertical, 0.1), 3), (
        f"derived: this layer's own vertical step, because only "
        f"{measured['n']} nodes fell on it and that is fewer than "
        f"{ACCURACY_MIN_SAMPLES}"
    )


def accuracy_block(validation: FieldValidation) -> dict[str, object]:
    """What each provenance value means, and how well it was measured to do.

    ``accuracy_m`` is the measured median absolute error where enough nodes fell on that
    layer to mean anything and the layer's own vertical step where they did not, and
    ``accuracy_from`` says which of the two it is.
    """
    nothing: ErrorStats = {"n": 0, "coverage": 0.0}
    out: dict[str, object] = {
        str(hf.PROV_NODATA): {
            "name": hf.PROV_NAMES[hf.PROV_NODATA],
            "accuracy_m": None,
            "note": (
                "open ocean past the landscape edge, and two cave-mouth blobs. Explicit, "
                "never zero-filled: say nothing here."
            ),
        }
    }
    pooled = validation["per_layer"].get("cliff, both", nothing)
    for value, (horizontal, vertical) in _LAYER_STEPS.items():
        name = hf.PROV_NAMES[value]
        found = validation["per_layer"].get(name)
        measured = nothing if found is None else found
        cliff = value in hf.PROV_CLIFF_VALUES
        accuracy, source = _layer_accuracy(measured, pooled, vertical, cliff)
        out[str(value)] = {
            "name": name,
            "horizontal_m": round(horizontal, 4),
            "vertical_step_m": round(vertical, 4),
            "accuracy_m": accuracy,
            "accuracy_from": source,
            "measured": {} if found is None else found,
            "note": _LAYER_NOTES[value],
        }
    return out


def water_source(sweep: Sweep, water: WaterSurface, water_checks: WaterChecks) -> dict[str, object]:
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
