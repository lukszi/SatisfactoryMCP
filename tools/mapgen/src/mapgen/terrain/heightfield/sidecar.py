"""The heightfield's record of a run: ``meta.json``, the staleness guard that reads it back,
and the progress lines that print the same counts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.terrain.heightfield.field import FieldLayers
from mapgen.terrain.heightfield.sidecar_blocks import (
    FileEntry,
    accuracy_block,
    cliff_source,
    container_block,
    density_block,
    fill_source,
    landscape_source,
    water_source,
)
from mapgen.terrain.heightfield.validate import FieldValidation, WaterChecks
from satisfactory_mcp.core.gameassets.provenance import files_digest, read_str_path
from satisfactory_mcp.core.gameassets.versions import HEIGHTFIELD_GENERATOR_VERSION
from satisfactory_mcp.core.jsontypes import JsonValue
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "GENERATOR_VERSION",
    "PIN_PATH",
    "build_meta",
    "pinned_build",
    "refuse_stale",
    "report_frame",
    "report_meshes",
    "report_sweep",
]

#: Bumped when the pipeline changes what it writes, so a sidecar dates its own field (history:
#: docs/map/heightfield.md). It lives in ``core.gameassets.versions``, where the server reads it.
GENERATOR_VERSION = HEIGHTFIELD_GENERATOR_VERSION

#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "game", "game_version_pinned")


def _grid_block() -> dict[str, object]:
    """The 1 m grid every plane is on: size, spacing, origin and how to read a texel."""
    return {
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
    }


def _game_source(build_pin: str, build_raw: Mapping[str, object]) -> dict[str, object]:
    """``sources.game``: the install the field was cut from, and its build."""
    return {
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
    }


def _decoders_block(decoders: Mapping[str, str]) -> dict[str, object]:
    """The decoders the planes were read with, their versions, and why each is optional."""
    return {
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
    }


def _known_defects(sweep: dict, water: dict) -> list[str]:
    """What the field is known to get wrong, with the counts this run measured."""
    return [
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
    ]


def build_meta(
    *,
    build_pin: str,
    build_raw: Mapping[str, object],
    sweep: dict,
    frame: dict,
    meshes: dict,
    cliffs: dict,
    field: FieldLayers,
    water: dict,
    water_checks: WaterChecks,
    validation: FieldValidation,
    files: dict[str, FileEntry],
    decoders: dict[str, str],
    timings: dict[str, float],
) -> dict[str, object]:
    """The sidecar the loader reads, plus the provenance a reader needs to date the field."""
    return {
        "description": (
            "A 1 m terrain heightfield of the Satisfactory world, cut from the reader's own "
            "installed game by tools/gen_world_heightmap.py. All of it is local: data/local/ "
            "is gitignored and no terrain raster is ever committed to this repository."
        ),
        "generator": "tools/gen_world_heightmap.py",
        "generator_version": GENERATOR_VERSION,
        "transcribed": datetime.now(UTC).date().isoformat(),
        "grid": _grid_block(),
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
        "density": density_block(field),
        "container": container_block(field),
        "validation": validation,
        "sources": {
            "game": _game_source(build_pin, build_raw),
            "landscape": landscape_source(frame, field),
            "cliffs": cliff_source(meshes, cliffs),
            "fill": fill_source(),
            "water": water_source(sweep, water, water_checks),
        },
        "sweep": {
            "packages": sweep["packages"],
            "unreadable": sweep["unreadable"],
            "malformed_components": sweep["malformed_components"],
            "placements": len(sweep["placements"]),
            "distinct_meshes": len(sweep["meshes"]),
        },
        "decoders": _decoders_block(decoders),
        "timings_s": timings,
        "known_defects": _known_defects(sweep, water),
        "staleness": (
            "sources.game.game_version_pinned is the build this field was cut from, in the "
            "same shape data/resource_nodes.json uses, so a field and a node table from "
            "different builds are comparable on sight. tools/gen_world_heightmap.py refuses "
            "to overwrite this directory unless the sidecar names the build then installed; "
            "--force says it anyway. Terrain moves every patch, and the standing rule is "
            "that a pinned artifact announces drift rather than answering silently wrong."
        ),
    }


def pinned_build(meta: JsonValue) -> str | None:
    """The build an existing sidecar names, or None if it names none."""
    return read_str_path(meta, PIN_PATH)


def refuse_stale(out_dir: Path, build_pin: str) -> int | None:
    """3 if ``out_dir`` holds a field this run cannot show was cut from ``build_pin``."""
    try:
        existing: JsonValue = json.loads((out_dir / hf.META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        existing = {}
    pinned = pinned_build(existing if isinstance(existing, dict) else {})
    if pinned == build_pin:
        return None
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


def report_sweep(sweep: dict) -> None:
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


def report_frame(frame: dict, dx: int, dy: int) -> None:
    print(
        f"  landscape {frame['width']}x{frame['height']} m at "
        f"({frame['x0_cm']:.0f}, {frame['y0_cm']:.0f}) cm, {frame['coverage'] * 100:.1f}% "
        f"covered, {frame['hole_texels']} hole texels in {frame['hole_blobs']} blobs"
    )
    print(f"  drops into the output grid at texel ({dx}, {dy}), exactly -- no resampling")
    print(f"  {frame['seam_disagreements']} shared edge samples disagree between components")


def report_meshes(meshes: dict) -> None:
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
