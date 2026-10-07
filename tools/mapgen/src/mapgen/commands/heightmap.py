"""Cut a real 1 m heightmap of this world out of the installed game.

The cooked landscape, every placed rock's own geometry folded max-Z, the ``HeightData_Test``
interface raster outside the landscape frame and the game's water actors fuse into one field,
measured on every static resource node before it is written. ``--caves`` and ``--rocks`` write
only the cave masks or the collision pack. ``meta.json`` says what each plane holds, and
docs/spatial-and-map.md sections 19 and 22 to 24 have the design.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path

from mapgen.commands.caves import write_caves
from mapgen.commands.rocks import write_rocks
from mapgen.common import LOCAL_DIR, Refusal, base_parser, require_gen
from mapgen.gamedata.install import GameReader, missing_container, open_game
from mapgen.gamedata.level.fill_raster import read_fill_raster
from mapgen.gamedata.level.landscape import drop_offsets, landscape_frame
from mapgen.gamedata.level.sweep import sweep_levels
from mapgen.gamedata.meshes import MeshBounds, read_mesh_geometry
from mapgen.gamedata.nodes import NODE_TABLE
from mapgen.gamedata.rocks.cliffs import rasterise_cliffs, rasterise_top
from mapgen.gamedata.rocks.collision_pack import encode_rock_pack
from mapgen.gamedata.water.channel import artwork_water_mask, water_surface
from mapgen.terrain.heightfield import field, sidecar, sidecar_blocks, validate
from mapgen.terrain.heightfield.field import FieldLayers
from mapgen.terrain.heightfield.validate import FieldValidation, TerrainCheck, WaterChecks
from satisfactory_mcp.core.arrays import I16Grid
from satisfactory_mcp.core.gameassets.container import paks_dir
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    install_directory,
    installed_build,
)
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.domain.spatial.heightfield import cave_masks as caves


@dataclasses.dataclass
class _Run:
    """One field build: the opened install, whether to print progress, the stage timings."""

    reader: GameReader
    loud: bool
    timings: dict[str, float] = dataclasses.field(default_factory=dict)

    def timed(self, stage: str, started: float) -> None:
        self.timings[stage] = round(time.time() - started, 1)


def parse_args() -> argparse.Namespace:
    parser = base_parser((__doc__ or "").partition("\n")[0])
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
    return parser.parse_args()


def refusal(args: argparse.Namespace, build_pin: str) -> int | None:
    """The exit code of the first refusal before any game read, or None to go on."""
    out_dir: Path = args.out_dir
    if out_dir.is_dir() and not args.force:
        refused = sidecar.refuse_stale(out_dir, build_pin)
        if refused is not None:
            return refused

    if not NODE_TABLE.is_file():
        print(
            f"{NODE_TABLE} is not present, so this run could not validate the field it "
            "built. A heightmap nobody measured is not one this file will write."
        )
        return 4

    if (missing := missing_container(args.game)) is not None:
        print(missing)
        return 1
    return None


def open_reader(game: Path, pyooz_version: str) -> GameReader:
    """The opened install, with the two lines that say what was opened."""
    print(f"reading the world from {paks_dir(game)} with pyooz {pyooz_version}")
    reader = open_game(game)
    store = reader.store
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )
    return reader


def _landscape(run: _Run) -> tuple[dict, dict]:
    """The level sweep, and the landscape stitched from it into one frame."""
    reader = run.reader
    print("sweeping the world's packages for landscape, placements and water volumes")
    bounds = MeshBounds(reader.store, reader.scripts, reader.index)
    sweep = sweep_levels(reader.store, reader.scripts, reader.classes, bounds, run.loud)
    run.timings["sweep"] = round(sweep["seconds"], 1)
    sidecar.report_sweep(sweep)
    started = time.time()
    frame = landscape_frame(sweep)
    dx, dy = drop_offsets(frame)
    run.timed("landscape", started)
    sidecar.report_frame(frame, dx, dy)
    return sweep, frame


def _cliffs(run: _Run, sweep: dict, frame: dict) -> tuple[dict, dict]:
    """Every placed rock's finest geometry, folded into the 1 m max-Z overlay."""
    reader = run.reader
    print("decoding the finest geometry every placed rock ships")
    meshes = read_mesh_geometry(
        reader.store, reader.scripts, reader.index, sweep["meshes"], run.loud
    )
    run.timings["mesh_decode"] = round(meshes["seconds"], 1)
    sidecar.report_meshes(meshes)
    if not meshes["geometry"]:
        raise Refusal(
            5,
            "not one rock mesh decoded. The cooked collision layout changed, which is the "
            "whole of what makes this field better than the interface raster. Refusing.",
        )
    print("rasterising them into a 1 m max-Z overlay")
    cliffs = rasterise_cliffs(sweep, meshes["geometry"], frame, run.loud)
    run.timings["rasterise"] = round(cliffs["seconds"], 1)
    print(
        f"  {cliffs['placements_used']}/{cliffs['placements_total']} placements, "
        f"{cliffs['triangles'] / 1e6:.1f} M triangles in {cliffs['seconds']:.0f}s; "
        f"dropped {cliffs['dropped']}"
    )
    return meshes, cliffs


def _compose(run: _Run, frame: dict, cliffs: dict) -> FieldLayers:
    """The fill raster decoded, and the three layers fused onto the output grid."""
    started = time.time()
    fill_cm, fill_valid = read_fill_raster(run.reader.store)
    run.timed("fill", started)
    print(f"  interface raster decoded, {fill_valid.mean() * 100:.1f}% of it says something")
    started = time.time()
    fused = field.compose_field(frame, cliffs, fill_cm, fill_valid)
    run.timed("compose", started)
    field.report_field(fused)
    return fused


def _top(run: _Run, sweep: dict, frame: dict, fused: FieldLayers) -> tuple[dict, I16Grid, int]:
    """Arches and foliage boulders, folded over the field into the ``top`` plane."""
    print("rasterising arches and foliage boulders for the top plane")
    top = rasterise_top(sweep, frame, run.reader, run.loud)
    run.timings["top"] = round(top["seconds"], 1)
    top_dm, top_raised = field.fold_top_overlay(fused["height_dm"], frame, top)
    print(
        f"  {top['arch_placements']} arch placements, {top['foliage_instances']} foliage "
        f"instances {top['foliage_by_mesh']}; raised {top_raised} texels over the ground"
    )
    return top, top_dm, top_raised


def _water(run: _Run, sweep: dict, fused: FieldLayers) -> tuple[dict, WaterChecks]:
    """The water channel, refused unless it passes its own four gates."""
    import texture2ddecoder
    from PIL import Image

    print("classifying the map artwork's water and levelling it on the water volumes")
    started = time.time()
    mask = artwork_water_mask(run.reader.store, texture2ddecoder, Image)
    water = water_surface(mask, sweep["water"], fused["height_dm"], fused["prov"])
    water_checks = validate.validate_water(water, mask, sweep["water"])
    run.timed("water", started)
    validate.report_water(water, water_checks)
    failures = validate.water_gate_failures(water_checks)
    if failures:
        for sentence in failures:
            print(f"  {sentence}")
        raise Refusal(7, "Refusing to write a water channel that does not pass its own gates.")
    return water, water_checks


def _validate(run: _Run, frame: dict, fused: FieldLayers) -> tuple[FieldValidation, TerrainCheck]:
    """The field on the node table, and the bare terrain on the landscape's nodes."""
    started = time.time()
    validation = validate.validate_field(fused["height_dm"], fused["prov"])
    run.timed("validate", started)
    validate.report_validation(validation)
    whole = validation["field"]
    if whole["trim90_rms_m"] > validate.VALIDATION_TRIM_RMS_MAX_M:
        raise Refusal(
            6,
            f"trimmed RMS is {whole['trim90_rms_m']:.3f} m against a gate of "
            f"{validate.VALIDATION_TRIM_RMS_MAX_M} m. Something in the decode moved: the "
            "workflow that proved this pipeline measured 0.368 m, and a field this far out "
            "would be a plausible-looking raster that is quietly metres wrong. Refusing to "
            "write.",
        )
    terrain_check = validate.validate_terrain(frame, fused["prov"])
    print(
        f"  bare terrain on {terrain_check['n']} landscape nodes: median absolute "
        f"{terrain_check['medabs_m']} m (gate {validate.TERRAIN_NODE_MEDIAN_MAX_M} m)"
    )
    medabs = terrain_check["medabs_m"]
    if medabs is None or medabs > validate.TERRAIN_NODE_MEDIAN_MAX_M:
        raise Refusal(8, "the bare terrain plane does not sit on the nodes. Refusing to write it.")
    return validation, terrain_check


def build_field(
    args: argparse.Namespace,
    reader: GameReader,
    build_pin: str,
    build_raw: JsonObject,
    decoders: dict[str, str],
) -> int:
    """Every stage of the field, then its planes, sidecar and collision pack written."""
    run = _Run(reader, not args.quiet)
    sweep, frame = _landscape(run)
    meshes, cliffs = _cliffs(run, sweep, frame)
    fused = _compose(run, frame, cliffs)
    top, top_dm, top_raised = _top(run, sweep, frame, fused)
    water, water_checks = _water(run, sweep, fused)
    validation, terrain_check = _validate(run, frame, fused)
    started = time.time()
    payload = field.encode_planes(fused, water, frame, top_dm)
    run.timed("encode", started)
    meta = sidecar.build_meta(
        build_pin=build_pin,
        build_raw=build_raw,
        sweep=sweep,
        frame=frame,
        meshes=meshes,
        cliffs=cliffs,
        field=fused,
        water=water,
        water_checks=water_checks,
        validation=validation,
        files=sidecar_blocks.describe_files(payload, frame),
        decoders=decoders,
        timings=run.timings,
    )
    sidecar_blocks.add_planes(meta, frame, terrain_check, top, top_raised)
    print("packing every rock's collision mesh for exact heights")
    started = time.time()
    payload.update(encode_rock_pack(reader, sweep, build_pin, build_raw))
    run.timed("rocks", started)
    payload[hf.META_NAME] = json.dumps(meta, indent=1).encode("utf-8")
    written = install_directory(args.out_dir, payload)
    total = sum(written.values())
    print(f"wrote {args.out_dir}  {total} B  ({total / 1e6:.1f} MB)")
    for name, size in written.items():
        print(f"  {name:>14}  {size:>10} B")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


def main() -> int:
    args = parse_args()

    # Three of the extra: the water channel's plan shape is the map sheet's own BC1 slices,
    # so this generator decodes textures as well as container blocks.
    decoders = require_gen("ooz", "texture2ddecoder", "PIL.Image")
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

    refused = refusal(args, build_pin)
    if refused is not None:
        return refused
    try:
        return build_field(
            args, open_reader(args.game, decoders["pyooz"]), build_pin, build_raw, decoders
        )
    except Refusal as stopped:
        print(stopped.message)
        return stopped.code
