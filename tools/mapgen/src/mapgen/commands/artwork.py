"""Cut data/local/map.png -- the web map's base image -- out of the installed game.

    uv run --extra gen python tools/gen_map_image.py [--enhance]

Writes ``map.png``, ``tiles/``, ``tiles@2x/`` and ``map.json`` into gitignored
``data/local/`` from the player's own install; ``--enhance`` adds z6 and z7 on the GPU.
This module is the arguments, the stage order and the refusals; the stages live in
``gamedata/artwork_sheet.py``, ``enhance/`` and ``tiles/artwork_output.py``, and
docs/spatial-and-map.md §17 has the reasoning.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from mapgen.common import LOCAL_DIR, base_parser, require_gen
from mapgen.enhance.levels import ENHANCE_WORK, enhance_levels
from mapgen.enhance.upscaler import (
    ENHANCE_MODEL,
    ENHANCE_SCALE,
    EnhanceError,
    check_array_stack,
    ensure_upscaler,
)
from mapgen.gamedata.artwork_sheet import (
    calibrate,
    decode_slices,
    report_calibration,
    seam_residuals,
    stitch_sheet,
)
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.tiles.artwork_output import (
    IMAGE_NAME,
    SIDECAR_NAME,
    artwork_provenance,
    build_artwork_sidecar,
    enhancement_downgrades,
    image_block,
    install_artwork_trees,
    integrity_block,
    pinned_build,
    pinned_recipe,
)
from mapgen.tiles.recipes import ENHANCE_RECIPE, ENHANCE_RECIPES
from satisfactory_mcp.core.gameassets.container import SHEET_PX, open_container
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build, sha256_hex
from satisfactory_mcp.core.gameassets.pyramid import TILES_2X_DIR_NAME, TILES_DIR_NAME, PyramidError

#: The game's own resolution: 16 MB of PNG, which ``--size`` cuts for a slow decoder.
DEFAULT_SIZE_PX = 8192


def _parser():
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_SIZE_PX,
        choices=[SHEET_PX, SHEET_PX // 2, SHEET_PX // 4],
        help=(
            f"square edge of the written PNG (default {DEFAULT_SIZE_PX}). {SHEET_PX} is the "
            "game's own resolution; anything smaller is a Lanczos downscale of it"
        ),
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR,
        help="destination directory for map.png, map.json and tiles/ (gitignored)",
    )
    parser.add_argument(
        "--enhance",
        action="store_true",
        help=(
            f"add z6 and z7 by upscaling the sheet {ENHANCE_SCALE}x with {ENHANCE_MODEL} on "
            "the GPU. Off by default: it downloads a 45 MB binary once and needs a Vulkan "
            "device. See the module docstring"
        ),
    )
    parser.add_argument(
        "--no-tiles-2x",
        action="store_true",
        help=(
            f"skip the {TILES_2X_DIR_NAME}/ tree. On by default because a hi-dpi display is "
            "the ordinary case and the tree costs about a third again; a client that cannot "
            "find it asks for the 1x tile it already had"
        ),
    )
    parser.add_argument(
        "--esrgan-cache",
        type=Path,
        default=None,
        help=(
            "where the upscaler is kept (default: platformdirs' user cache, bin/). Outside "
            "the repository either way"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "overwrite a map.png or tiles/ this run cannot show was cut from the installed "
            "build, or replace an enhanced pyramid with a plain one"
        ),
    )
    return parser


def _refuse_stale(out_dir: Path, build_pin: str, enhance: bool) -> int | None:
    """Exit code 5 for a recipe downgrade, 3 for another build's picture, else None."""
    image_path = out_dir / IMAGE_NAME
    tiles_dir = out_dir / TILES_DIR_NAME
    # The pyramid is refused like the picture: another build's tiles are another world.
    if not (image_path.is_file() or tiles_dir.is_dir()):
        return None
    sidecar_now: dict = {}
    try:
        sidecar_now = json.loads((out_dir / SIDECAR_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        sidecar_now = {}
    if not isinstance(sidecar_now, dict):
        sidecar_now = {}
    if enhancement_downgrades(sidecar_now, enhance):
        have = pinned_recipe(sidecar_now)
        want = ENHANCE_RECIPE if enhance else 0
        print(
            f"{tiles_dir} was cut by enhancement recipe {have} -- "
            f"{ENHANCE_RECIPES.get(have, 'a recipe this checkout has never heard of')} "
            f"-- and this run would cut it with recipe {want}: "
            f"{ENHANCE_RECIPES[want]}.\n"
            "A refresh that quietly costs the reader picture they already generated is "
            "exactly the kind of drift this tool announces rather than performs. Pass "
            "--enhance to keep it, or --force to accept the plainer map."
        )
        return 5
    existing = pinned_build(sidecar_now)
    if existing != build_pin:
        there = " and ".join(str(p) for p in (image_path, tiles_dir) if p.exists())
        print(
            f"{there} already exists and this run cannot show it was cut from the "
            f"installed build.\n"
            f"  installed: {build_pin}\n"
            f"  that file: {existing or 'no sidecar, or no build recorded in it'}\n"
            "A picture from another build -- or from somewhere else entirely -- is not "
            "this tool's to replace: the repository's own tables are pinned to a build "
            "the new artwork may no longer agree with, and drift is meant to be "
            "announced rather than overwritten. Pass --force to overwrite it anyway."
        )
        return 3
    return None


def _prove_upscaler(args) -> tuple[dict | None, int | None]:
    """The binary, model and numpy settled before any decode, so a failure costs nothing."""
    if args.size != SHEET_PX:
        print(
            f"--enhance and --size {args.size} together would upscale a downscale, "
            "which is inventing detail twice. Refusing: run at "
            f"{SHEET_PX} or without --enhance."
        )
        return None, 6
    try:
        upscaler = ensure_upscaler(args.esrgan_cache)
        upscaler["numpy"], upscaler["scipy"] = check_array_stack()
    except EnhanceError as exc:
        print(exc)
        return None, 6
    print(
        f"  upscaler: {upscaler['exe']} ({ENHANCE_MODEL}, sha256 "
        f"{upscaler['sha256'][:16]}...), numpy {upscaler['numpy']} / "
        f"scipy {upscaler['scipy']}"
    )
    return upscaler, None


def main() -> int:
    args = _parser().parse_args()

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    pyooz_version = versions["pyooz"]
    import texture2ddecoder as decoder
    from PIL import Image as image_mod

    try:
        build_pin, build_raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc} -- point --game at the install holding FactoryGame/ and Engine/")
        return 1
    print(f"installed build: {build_pin}")

    out_dir: Path = args.out_dir
    if not args.force:
        code = _refuse_stale(out_dir, build_pin, args.enhance)
        if code is not None:
            return code

    upscaler = None
    if args.enhance:
        upscaler, code = _prove_upscaler(args)
        if code is not None:
            return code

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the map slices from {paks} with pyooz {pyooz_version}")
    store = open_container(args.game)
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )

    tiles = decode_slices(store, decoder, image_mod)
    layout = seam_residuals(tiles)
    for label, value in layout["seams"].items():
        print(f"  seam {label:26s} {value:8.4f}")
    for label, value in layout["controls_inside_one_tile"].items():
        print(f"  control {label:23s} {value:8.4f}")
    if not layout["layout_holds"]:
        print(
            "the seams read no better than two scanlines 100 rows apart inside one tile, "
            "so these four slices do not abut the way their names say. The layout is "
            "wrong -- a mirrored world is worse than no world. Refusing to write."
        )
        return 4

    sheet, alpha_note = stitch_sheet(tiles, image_mod)
    sheet_digest = sha256_hex(sheet.tobytes())
    calibration = calibrate(sheet, image_mod, BOUNDS_M)
    report_calibration(calibration)

    if args.size != SHEET_PX:
        sheet = sheet.resize((args.size, args.size), image_mod.LANCZOS)

    image_path = out_dir / IMAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet.save(image_path, format="PNG", optimize=True)
    written = image_path.stat().st_size
    print(f"wrote {image_path}  {args.size}x{args.size}  {written} B  ({written / 1e6:.1f} MB)")

    # Enhanced levels come from the full-resolution sheet: the preflight refused --size.
    work = out_dir / ENHANCE_WORK
    enhance = None
    if upscaler is not None:

        def run_enhance(staging: Path) -> dict:
            if work.exists():
                shutil.rmtree(work)
            try:
                return enhance_levels(sheet, image_mod, staging, upscaler, work)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        enhance = run_enhance

    try:
        tiles, tiles_2x = install_artwork_trees(
            sheet,
            image_mod,
            out_dir,
            enhance=enhance,
            with_2x=not args.no_tiles_2x,
            build_pin=build_pin,
        )
    except EnhanceError as exc:
        print(exc)
        return 6
    except PyramidError as exc:
        print(exc)
        return 1

    sidecar = build_artwork_sidecar(
        build_pin=build_pin,
        build_raw=build_raw,
        image=image_block(args.size, written, sheet.mode, alpha_note),
        integrity=integrity_block(),
        layout=layout,
        calibration=calibration,
        versions=versions,
        tiles=tiles,
        tiles_2x=tiles_2x,
        provenance=artwork_provenance(build_raw, sheet_digest, args.enhance, args.size),
    )
    sidecar_path = out_dir / SIDECAR_NAME
    sidecar_path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    print(f"wrote {sidecar_path}  {sidecar_path.stat().st_size} B")
    print(
        "  pinned at x [{x_min_m:.0f}, {x_max_m:.0f}] y [{y_min_m:.0f}, {y_max_m:.0f}] m".format(
            **BOUNDS_M
        )
    )
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0
