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
import time
from pathlib import Path

from mapgen.common import LOCAL_DIR, base_parser, require_gen
from mapgen.gamedata.caves import write_caves
from mapgen.gamedata.mesh import MeshBounds, rasterise_cliffs, rasterise_top, read_mesh_geometry
from mapgen.gamedata.rocks import rock_pack, write_rocks
from mapgen.gamedata.sweep import drop_offsets, landscape_frame, read_baseline, sweep_levels
from mapgen.gamedata.water import artwork_water_mask, water_surface
from mapgen.terrain.field import (
    add_planes,
    compose,
    compose_top,
    describe_files,
    encode_planes,
    report_field,
)
from mapgen.terrain.sidecar import (
    build_meta,
    refuse_stale,
    report_frame,
    report_meshes,
    report_sweep,
)
from mapgen.terrain.validate import (
    NODE_TABLE,
    TERRAIN_NODE_MEDIAN_MAX_M,
    VALIDATION_TRIM_RMS_MAX_M,
    report_validation,
    report_water,
    validate,
    validate_terrain,
    validate_water,
    water_gate_failures,
)
from satisfactory_mcp.core.gameassets.container import open_container
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    install_directory,
    installed_build,
)
from satisfactory_mcp.domain.spatial import caves
from satisfactory_mcp.domain.spatial import heightfield as hf


def parse_args():
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
    return parser.parse_args()


def refusal(args, build_pin: str) -> int | None:
    """The exit code of the first refusal before any game read, or None to go on."""
    out_dir: Path = args.out_dir
    if out_dir.is_dir() and not args.force:
        refused = refuse_stale(out_dir, build_pin)
        if refused is not None:
            return refused

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
    return None


def open_game(game: Path, pyooz_version: str):
    """The container, its script objects and its asset index."""
    paks = game / "FactoryGame" / "Content" / "Paks"
    print(f"reading the world from {paks} with pyooz {pyooz_version}")
    store = open_container(game)
    scripts = ScriptObjects(paks, oodle_decompress)
    index = AssetIndex(store)
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )
    return store, scripts, index


def main() -> int:
    args = parse_args()

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
    refused = refusal(args, build_pin)
    if refused is not None:
        return refused
    store, scripts, index = open_game(args.game, pyooz_version)
    loud = not args.quiet
    timings: dict[str, float] = {}

    print("sweeping the world's packages for landscape, placements and water volumes")
    classes = ClassFacts(store, index)
    sweep = sweep_levels(store, scripts, classes, MeshBounds(store, scripts, index), loud)
    timings["sweep"] = round(sweep["seconds"], 1)
    report_sweep(sweep)
    started = time.time()
    frame = landscape_frame(sweep)
    dx, dy = drop_offsets(frame)
    timings["landscape"] = round(time.time() - started, 1)
    report_frame(frame, dx, dy)

    print("decoding the finest geometry every placed rock ships")
    meshes = read_mesh_geometry(store, scripts, index, sweep["meshes"], loud)
    timings["mesh_decode"] = round(meshes["seconds"], 1)
    report_meshes(meshes)
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

    started = time.time()
    baseline_cm, baseline_valid = read_baseline(store)
    timings["fill"] = round(time.time() - started, 1)
    print(f"  interface raster decoded, {baseline_valid.mean() * 100:.1f}% of it says something")

    started = time.time()
    field = compose(frame, cliffs, baseline_cm, baseline_valid)
    timings["compose"] = round(time.time() - started, 1)
    report_field(field)

    print("rasterising arches and foliage boulders for the top plane")
    top = rasterise_top(sweep, frame, store, scripts, index, loud)
    timings["top"] = round(top["seconds"], 1)
    top_dm, top_raised = compose_top(field["height_dm"], frame, top)
    print(
        f"  {top['arch_placements']} arch placements, {top['foliage_instances']} foliage "
        f"instances {top['foliage_by_mesh']}; raised {top_raised} texels over the ground"
    )

    print("classifying the map artwork's water and levelling it on the water volumes")
    started = time.time()
    mask = artwork_water_mask(store, texture2ddecoder, Image)
    water = water_surface(mask, sweep["water"], field["height_dm"], field["prov"])
    water_checks = validate_water(water, mask, sweep["water"])
    timings["water"] = round(time.time() - started, 1)
    report_water(water, water_checks)
    failures = water_gate_failures(water_checks)
    if failures:
        for sentence in failures:
            print(f"  {sentence}")
        print("Refusing to write a water channel that does not pass its own gates.")
        return 7

    started = time.time()
    validation = validate(field["height_dm"], field["prov"])
    timings["validate"] = round(time.time() - started, 1)
    report_validation(validation)
    whole = validation["field"]
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
    payload = encode_planes(field, water, frame, top_dm)
    timings["encode"] = round(time.time() - started, 1)
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
        files=describe_files(payload, frame),
        decoders=decoders,
        timings=timings,
    )
    add_planes(meta, frame, terrain_check, top, top_raised)
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
