"""Draw the rendered base-map layers of this world from the 1 m heightfield and the game's data.

    python -m mapgen renders [--layer painted] [--size 2048] [--restyle] ...

What each layer is, and why: docs/map/renders.md sections 17, 20, 25, 26 and 40, docs/map/painted.md sections 27 and 28 and docs/map/light-and-crowns.md section 29.
"""

from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path
from typing import NamedTuple, cast

from mapgen.common import LOCAL_DIR, RENDERS_DIR_NAME, Refusal, base_parser, require_gen
from mapgen.gamedata.frame import RENDER_PX
from mapgen.gamedata.ground.paint_store import PAINT_DIR
from mapgen.palette.styles import LAYER_STYLES, SHORE_OPTICS
from mapgen.render.draw.compose import DRAW_STAGE, GroundInputs, render_layers
from mapgen.render.draw.drawpool import add_draw_flags, draw_threads
from mapgen.render.draw.light import (
    LightingRun,
    add_light_flags,
    claim_scratch,
    crown_tops,
    light_run,
)
from mapgen.render.draw.stream import RenderOut, RenderStream
from mapgen.render.run.extras import remove_run_caches
from mapgen.render.run.inuse import IN_USE, add_in_use_flag, in_use_refusal
from mapgen.render.run.prepare import BIOME_LAYERS, Prepared, Setup, prepare
from mapgen.terrain.measure import RegimeCoverage, SeamTrace, measured_lines
from mapgen.terrain.rasters import DIRECT_SUBSAMPLES
from mapgen.terrain.sample import taps_cubic, taps_pchip
from mapgen.tiles.cutter import TileStream
from mapgen.tiles.imaging import load_imaging
from mapgen.tiles.layer_meta import LayerDraw, layer_sidecar
from mapgen.tiles.pyramid import add_worker_flags, layer_dir, pool_sizes, tree_text
from mapgen.tiles.recipes import RECIPE_KERNEL_ONLY
from mapgen.tiles.sidecar import RENDER_SIDECAR_NAME
from satisfactory_mcp.core.gameassets.pyramid import PyramidError
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The layers this command draws, in the order they are cut; ``--layer`` restricts it.
LAYERS = ("terrain", "satellite", "painted", "relief", "relief-dark")

#: Exit code of a run whose tiles could not be cut into place.
CUT_FAILED = 1


def main() -> int:
    args = build_parser().parse_args()
    layers: tuple[str, ...] = tuple(dict.fromkeys(args.layer)) if args.layer else LAYERS
    renders: Path = args.out_dir / args.renders_name
    if refusal := in_use_refusal(LOCAL_DIR, renders, args.overwrite_in_use):
        print(refusal)
        return IN_USE
    cache_root: Path = args.cache_dir or renders
    try:
        root = claim_scratch(args, renders)
        versions = require_gen("ooz", "texture2ddecoder", "PIL.Image", "zstandard")
        started = _render(args, layers, root, cache_root, versions)
    except Refusal as refusal:
        print(refusal.message)
        return refusal.code
    if not (args.kernel_only or args.keep_direct or args.restyle):
        gc.collect()
        for directory in remove_run_caches(cache_root):
            print(f"could not remove {directory}: a file in it is still open")
    print(f"done in {time.time() - started:.0f}s")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


def _render(
    args: argparse.Namespace,
    layers: tuple[str, ...],
    root: Path | None,
    cache_root: Path,
    versions: dict[str, str],
) -> float:
    """Prepare the run, then draw and cut every layer; when the drawing started.

    ``root`` is the light's scratch, None without it; ``cache_root`` the raster caches' root,
    and ``versions`` the ``gen`` extra's.
    """
    import texture2ddecoder as decoder

    light_workers, cut_workers = pool_sizes(args)
    setup = Setup(cache_root, decoder, load_imaging(), versions, cut_workers)
    run = prepare(args, layers, setup)
    crowns = None if root is None else crown_tops(args.paint_dir, run.painted)
    with (
        light_run(root, args.size, crowns, light_workers, setup.cache_root) as light,
        TileStream(setup.image_mod, cut_workers) as cutter,
    ):
        started = time.time()
        _draw_layers(args, layers, run, light, cutter)
    return started


def _draw_layers(
    args: argparse.Namespace,
    layers: tuple[str, ...],
    run: Prepared,
    light: LightingRun | None,
    cutter: TileStream,
) -> None:
    """Draw every layer in one pass, each band cut as it settles; then install each layer's
    trees with its sidecar beside them."""
    two_regime = run.direct is not None
    seam = SeamTrace() if two_regime else None
    regimes = RegimeCoverage() if two_regime else None
    threads = draw_threads(args.draw_threads, layers, args.size, columns=args.draw_columns)
    print(f"drawing {', '.join(layers)} at {args.size}x{args.size} on {threads} thread(s)")
    print(encode_stage(DRAW_STAGE, 0.0), flush=True)
    started = time.time()
    arches = None if run.top is None else run.top.arch_coverage
    out = RenderOut(args.out_dir, args.renders_name)
    stream = RenderStream(cutter, layers, out, args.size, run.record.recipe, light, arches)
    ground = GroundInputs(
        height_dm=run.lattice.heights,
        kernel=taps_cubic if args.kernel_only else taps_pchip,
        direct=run.direct,
        overlay=run.top,
        meshes=run.extras.meshes,
        measured_plane_u8=run.lattice.measured_plane,
        reach=run.water.reach,
        rivers=run.extras.rivers,
        water_level=run.water.level,
        sea=run.sea,
        seam=seam,
        regimes=regimes,
        surface=light.surface if light else None,
    )
    render_layers(
        layers,
        run.field,
        run.biome.rgb,
        run.biome.width,
        run.borrow,
        args.size,
        not args.quiet,
        ground,
        falls=run.extras.falls,
        painted=run.painted,
        relief=run.relief,
        unlit=light is not None,
        threads=threads,
        bands=stream.put,
        columns=args.draw_columns,
    )
    seconds = time.time() - started
    measured: JsonObject = {}
    if seam is not None and regimes is not None:
        trace, table = seam.result(), regimes.result()
        measured = {"seam_trace": trace, "regimes": table}
        print("\n".join(measured_lines(trace, table)))
    stream.finish()
    done = _Pass(measured, seconds, threads)
    for layer in layers:
        print(encode_stage(f"cut:{layer}", 0.0), flush=True)
        _install(args, layer, stream, run, light, done)


class _Pass(NamedTuple):
    """What every layer's sidecar says alike of the pass: its measurements, and the seconds
    and threads it drew in."""

    measured: JsonObject
    seconds: float
    threads: int


def _install(
    args: argparse.Namespace,
    layer: str,
    stream: RenderStream,
    run: Prepared,
    light: LightingRun | None,
    done: _Pass,
) -> None:
    """Install one layer's trees and write its sidecar."""
    try:
        trees = stream.install(layer)
    except PyramidError as exc:
        raise Refusal(CUT_FAILED, str(exc)) from exc
    stats, dense, cut = trees.tiles, trees.dense, trees.seconds
    stats["game_version_pinned"] = dense["game_version_pinned"] = run.field.build
    draw = LayerDraw(
        layer=layer,
        style_id=LAYER_STYLES[layer],
        style_digest=run.style_digests[layer],
        biome=layer in BIOME_LAYERS,
        measured=done.measured,
        shore_optics=cast(JsonValue, SHORE_OPTICS[layer]),
        seconds_to_draw=done.seconds,
        draw_threads=done.threads,
        seconds_to_cut=cut,
    )
    sidecar = layer_sidecar(run.record, draw, stats, dense)
    if light is not None:
        light.decorate(sidecar, layer, trees.unlit)
    directory = layer_dir(args.out_dir, layer, args.renders_name)
    (directory / RENDER_SIDECAR_NAME).write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    trees = f"{tree_text(stats, 'tiles')} plus {tree_text(dense, '@2x')}"
    print(f"wrote {directory}  {trees}  (cut {cut:.0f}s)")
    print(encode_stage(f"cut:{layer}", 1.0), flush=True)


def build_parser() -> argparse.ArgumentParser:
    """The command's flags: what to draw, where, and which stages to keep or skip."""
    parser = base_parser((__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--field",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="the heightfield directory tools/gen_world_heightmap.py wrote",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR,
        help="destination for renders/<layer>/ (gitignored)",
    )
    parser.add_argument(
        "--layer",
        action="append",
        choices=LAYERS,
        help=f"only this layer (repeatable; default all of {', '.join(LAYERS)})",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=RENDER_PX,
        choices=[RENDER_PX >> shift for shift in range(6)],
        help=(
            f"square edge of each render (default {RENDER_PX}, which is 0.229 m to the "
            "pixel; docs/map/renders.md section 20 says what that is and is not a claim "
            f"about). {RENDER_PX >> 4} and {RENDER_PX >> 5} are previews: minutes, not half an "
            "hour"
        ),
    )
    parser.add_argument(
        "--direct-subsamples",
        type=int,
        default=DIRECT_SUBSAMPLES,
        choices=[1, 2, 4],
        help=(
            f"sub-samples per output texel per axis in the direct pass (default "
            f"{DIRECT_SUBSAMPLES}; each doubling costs 4x the rasterising and is the only "
            "antialiasing a silhouette gets)"
        ),
    )
    parser.add_argument(
        "--kernel-only",
        action="store_true",
        help=(
            f"draw recipe {RECIPE_KERNEL_ONLY} instead: the Catmull-Rom kernel everywhere, "
            "no geometry opened, no cross-fade and no de-terracing. The picture this file "
            "drew before, at whatever --size is asked for, and recorded as that recipe"
        ),
    )
    parser.add_argument(
        "--keep-direct",
        action="store_true",
        help="leave the raster caches and the baked light behind so the next run reuses them",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help=(
            "where direct.cache/ and top.cache/ live (default: beside the renders). A cache "
            "here is reused by any run whose size, sub-samples and build match it"
        ),
    )
    parser.add_argument(
        "--restyle",
        action="store_true",
        help="palette only: draw from the kept raster cache and refuse if it is missing",
    )
    parser.add_argument(
        "--no-top",
        action="store_true",
        help="leave out the arches and foliage boulders the field keeps in top.i16.z",
    )
    parser.add_argument(
        "--no-meshes",
        action="store_true",
        help="leave out the render-only meshes and the waterfalls",
    )
    parser.add_argument(
        "--no-titan-trees",
        action="store_true",
        help="leave the Titan forest's trees off the painted layer (a style variant)",
    )
    parser.add_argument(
        "--paint-dir",
        type=Path,
        default=PAINT_DIR,
        help="the paint layers tools/gen_paint_layers.py wrote, for the painted layer",
    )
    parser.add_argument(
        "--renders-name",
        default=RENDERS_DIR_NAME,
        help=(
            f"directory under --out-dir to write into (default {RENDERS_DIR_NAME}, the one the "
            "server reads); a new name keeps the current renders untouched"
        ),
    )
    parser.add_argument(
        "--check-parallel",
        action="store_true",
        help=(
            "cut the artwork's pyramid both serially and in parallel and compare every "
            "tile's SHA-256, then carry on"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace layers this run cannot show were drawn from the field now on disk",
    )
    add_light_flags(parser)
    add_worker_flags(parser)
    add_draw_flags(parser)
    parser.add_argument("--quiet", action="store_true", help="no per-band progress lines")
    add_in_use_flag(parser)
    return parser
