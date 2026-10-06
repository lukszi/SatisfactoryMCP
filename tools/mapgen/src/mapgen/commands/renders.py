"""Draw the rendered base-map layers of this world from the 1 m heightfield and the game's data.

    python -m mapgen renders [--layer painted] [--size 2048] [--restyle] ...

What each layer is, and why: docs/spatial-and-map.md sections 17, 20, 25 to 29 and 40.
"""

from __future__ import annotations

import argparse
import gc
import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import numpy as np

from mapgen.cache import (
    DIRECT_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    DirectPlanes,
    TopPlanes,
    cached_family,
)
from mapgen.common import LOCAL_DIR, RENDERS_DIR_NAME, Refusal, base_parser, require_gen
from mapgen.gamedata.frame import BOUNDS_M, RENDER_PX
from mapgen.gamedata.ground.paint_store import PAINT_DIR
from mapgen.gamedata.water.channel import artwork_planes
from mapgen.palette.painted.ground import PaintedGround
from mapgen.palette.relief import ReliefGround
from mapgen.palette.styles import LAYER_STYLES, RELIEF_PALETTES, SHORE_OPTICS, STYLE_DIGESTS
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.perched import WaterSurfaces
from mapgen.palette.water.surface import (
    WATER_DEPTH_FULL_M,
    WATER_EDGE_BLUR_M,
    WATER_EDGE_M,
    drawn_water,
)
from mapgen.render.biome_inputs import BiomeInputs, read_biome_inputs
from mapgen.render.cached_rasters import LevelSweep, RasterGrid, direct_raster, top_raster
from mapgen.render.compose import render_layer
from mapgen.render.drawpool import add_draw_flags, draw_threads
from mapgen.render.extras import RenderExtras, load_extras, remove_run_caches
from mapgen.render.inputs import (
    Lattice,
    PaintInputs,
    artwork_borrow,
    check_parallel_cutter,
    field_input,
    field_lattice,
    field_water_source,
    load_field,
    open_game_inputs,
    prepare_paint,
    rebuilt_lattice,
    refuse_restyle_gaps,
    refuse_stale_layers,
)
from mapgen.render.inuse import IN_USE, add_in_use_flag, in_use_refusal
from mapgen.render.light import LightingRun, add_light_flags, claim_scratch, light_run
from mapgen.render.surface import DIRECT_LIFT_KNEE_M
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from mapgen.terrain.rasters import DIRECT_SUBSAMPLES
from mapgen.terrain.sample import taps_cubic, taps_pchip
from mapgen.tiles.layer_meta import LayerDraw, RenderFacts, RunRecord, layer_sidecar
from mapgen.tiles.pyramid import add_worker_flags, install_layer, layer_dir, pool_sizes
from mapgen.tiles.recipes import RECIPE_KERNEL_ONLY
from mapgen.tiles.rendertext import LEVEL_ONLY_TEXT
from mapgen.tiles.sidecar import RENDER_SIDECAR_NAME
from satisfactory_mcp.core.gameassets.provenance import changelist
from satisfactory_mcp.core.gameassets.pyramid import PyramidError
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The layers this command draws, in the order they are cut; ``--layer`` restricts it.
LAYERS = ("terrain", "satellite", "painted", "relief", "relief-dark")

#: The layers coloured from the biome raster, which is read only when one of them is drawn.
BIOME_LAYERS = ("satellite", "painted", "relief")

#: Exit code of a run whose tiles could not be cut into place.
CUT_FAILED = 1


@dataclass(frozen=True)
class Setup:
    """How a run reads the game and cuts its tiles, and where its raster caches go."""

    cache_root: Path
    decoder: ModuleType
    image_mod: ModuleType
    versions: dict[str, str]
    cut_workers: int


@dataclass(frozen=True)
class Prepared:
    """What every layer of a run is drawn from, and what its sidecars say alike.

    It holds the raster caches' memory maps: the run lets it go before removing them.
    """

    field: hf.Field
    lattice: Lattice
    borrow: tuple[np.ndarray, np.ndarray]
    biome: BiomeInputs
    paint: PaintInputs | None
    direct: DirectPlanes | None
    top: TopPlanes | None
    extras: RenderExtras
    water: WaterSurfaces
    sea: OpenSea | None
    relief: dict[str, ReliefGround]
    style_digests: dict[str, str]
    record: RunRecord

    @property
    def painted(self) -> PaintedGround | None:
        return None if self.paint is None else self.paint.ground


def load_imaging() -> ModuleType:
    """Pillow, once ``require_gen`` has shown it is there, with its size limit off.

    The limit is a decompression-bomb rule for images off the internet; an 8192 px sheet is
    the point here.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    return Image


def main() -> int:
    args = build_parser().parse_args()
    layers: tuple[str, ...] = tuple(dict.fromkeys(args.layer)) if args.layer else LAYERS
    renders: Path = args.out_dir / args.renders_name
    if refusal := in_use_refusal(LOCAL_DIR, renders, args.overwrite_in_use):
        print(refusal)
        return IN_USE
    cache_root: Path = args.cache_dir or renders
    try:
        scratch = claim_scratch(args, renders)
        versions = require_gen("ooz", "texture2ddecoder", "PIL.Image", "zstandard")
        started = _render(args, layers, scratch, (cache_root, versions))
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
    scratch: Path | None,
    caching: tuple[Path, dict[str, str]],
) -> float:
    """Prepare the run, then draw and cut every layer; when the drawing started.

    ``scratch`` is the light's, None without it; ``caching`` the raster caches' root and the
    ``gen`` extra's versions.
    """
    import texture2ddecoder as decoder

    light_workers, cut_workers = pool_sizes(args)
    cache_root, versions = caching
    setup = Setup(cache_root, decoder, load_imaging(), versions, cut_workers)
    run = _prepare(args, layers, setup)
    with light_run(scratch, args.size, run.painted, light_workers) as light:
        started = time.time()
        _draw_layers(args, layers, run, light, setup)
    return started


def _prepare(args: argparse.Namespace, layers: tuple[str, ...], setup: Setup) -> Prepared:
    """Every stage before the first layer is drawn, in the order the run reports them."""
    cache_root, image_mod, versions = setup.cache_root, setup.image_mod, setup.versions
    field = load_field(args.field)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
    lattice = field_lattice(field, spacing_m, args.kernel_only)
    if not args.force:
        refuse_stale_layers(args.out_dir, args.renders_name, layers, field.build)
    grid = RasterGrid(args.size, args.direct_subsamples, field.build, not args.quiet)
    if args.restyle and lattice.measured_plane is not None:
        titan = "painted" in layers and not args.no_titan_trees
        refuse_restyle_gaps(
            cache_root, grid, top=not args.no_top, meshes=not args.no_meshes, titan=titan
        )
    game = open_game_inputs(args.game, setup.decoder, image_mod, versions["pyooz"])
    inputs = {
        "heightfield": field_input(field, args.kernel_only),
        "artwork_sheet": game.artwork_input(),
    }
    borrow = artwork_borrow(game.artwork, field)
    water_source = field_water_source(field)
    art_water, art_void = (None, None) if args.kernel_only else artwork_planes(game.artwork)
    lattice = rebuilt_lattice(lattice, field, game.store, art_void)
    parallel_check = None
    if args.check_parallel:
        scratch = args.out_dir / "parallel.check"
        workers = setup.cut_workers
        parallel_check = check_parallel_cutter(game.artwork, image_mod, scratch, workers)
    biome = BiomeInputs()
    if any(layer in BIOME_LAYERS for layer in layers):
        biome = read_biome_inputs(
            game.store, game.scripts, game.artwork, image_mod, game.build_cl, versions["pyooz"]
        )
        inputs["biome_raster"] = biome.provenance or {}
    style_digests = dict(STYLE_DIGESTS)
    paint = None
    if "painted" in layers:
        paint = prepare_paint(args.paint_dir, args.no_titan_trees, field, biome.raster or {},
                              biome.drawn)  # fmt: skip
        inputs["paint"], style_digests["painted"] = paint.provenance, paint.digest
    level = LevelSweep(game.store, game.scripts, not args.quiet)
    direct, top, raster_sources = _rasters(args, setup, lattice, (level, grid), paint, inputs)
    two_regime = lattice.measured_plane is not None
    extras = load_extras(
        cache_root, args.size, field.build, level, field,
        meshes=two_regime and not args.no_meshes,
        titan=two_regime and paint is not None and not args.no_titan_trees,
        rivers=not args.kernel_only, quiet=args.quiet,
    )  # fmt: skip
    if extras.titan is not None and paint is not None:
        paint.ground.titan = extras.titan  # pyright: ignore[reportAttributeAccessIssue]
    for name in extras.readers:
        inputs[name] = {"cl": changelist(field.build), "reader_version": READER_VERSIONS[name]}
    water, sea, planes = drawn_water(
        field, args.kernel_only, extras.rivers, (lattice.heights, lattice.ground), art_water
    )
    if paint is not None:
        paint.block["water_classes"] = paint.ground.classify_water(field, planes)
    relief = {
        layer: ReliefGround(
            RELIEF_PALETTES[layer][0], field, biome.raster, list(biome.drawn), planes,
            lattice.heights,
        )
        for layer in layers
        if layer in RELIEF_PALETTES
    }  # fmt: skip
    facts = RenderFacts(
        size=args.size,
        spacing_m=spacing_m,
        subsamples=args.direct_subsamples,
        two_regime=direct is not None,
        composition=_composition_record(lattice, top is not None),
        water=_water_record(water, sea, extras.river_meta, spacing_m, water_source),
        cut_workers=setup.cut_workers,
        parallel_check=parallel_check,
        pillow_version=versions["pillow"],
    )
    record = RunRecord(
        render=facts,
        recipe=lattice.recipe,
        kernel_only=args.kernel_only,
        field_meta=field.meta,
        build_raw=game.build_raw,
        inputs=inputs,
        sources={**borrow.source, **raster_sources, **extras.mesh_source},
        biome_source=biome.source,
        paint_source={} if paint is None else {"paint": paint.block, **extras.titan_source},
    )
    return Prepared(
        field, lattice, borrow.planes, biome, paint, direct, top, extras, water, sea, relief,
        style_digests, record,
    )  # fmt: skip


def _rasters(
    args: argparse.Namespace,
    setup: Setup,
    lattice: Lattice,
    rasters: tuple[LevelSweep, RasterGrid],
    paint: PaintInputs | None,
    inputs: dict[str, JsonObject],
) -> tuple[DirectPlanes | None, TopPlanes | None, JsonObject]:
    """The rocks and the top overlay at the render's own spacing, and their sidecar blocks.

    ``rasters`` is the level sweep and the grid. None for ``--kernel-only``, which opens no
    geometry.
    """
    if lattice.measured_plane is None or lattice.ground is None:
        return None, None, {}
    level, grid = rasters
    cache = setup.cache_root / DIRECT_CACHE_DIR_NAME
    (rock_z, rock_coverage), sources = direct_raster(level, cache, grid, setup.versions["pyooz"])
    direct = DirectPlanes(rock_z, rock_coverage, lattice.ground, args.direct_subsamples)
    inputs["cliff_geometry"] = {
        "cl": changelist(grid.build),
        "reader_version": READER_VERSIONS["cliff_geometry"],
    }
    if paint is not None:
        paint.ground.attach_families(cached_family(cache, grid.stamp))
        for name in ("rock_families",) + (() if args.no_titan_trees else ("titan_trees",)):
            inputs[name] = {**inputs["cliff_geometry"], "reader_version": READER_VERSIONS[name]}
    top = None
    if not args.no_top:
        top_cache = setup.cache_root / TOP_CACHE_DIR_NAME
        (top_z, top_coverage), top_source = top_raster(level, top_cache, grid)
        top = TopPlanes(top_z, top_coverage, args.direct_subsamples)
        sources = {**sources, **top_source}
    return direct, top, sources


def _composition_record(lattice: Lattice, top_overlay: bool) -> JsonObject:
    """``_meta.render.two_regime``'s record of the run's lattices and its lift rule."""
    return {
        "ground_lattice": lattice.ground_meta,
        "terrain_lattice": lattice.terrain_meta,
        "top_overlay": top_overlay,
        "measurement_rule": lattice.measurement_rule,
        "lift_knee_m": DIRECT_LIFT_KNEE_M,
        "fill_rebuild": lattice.fill_meta,
    }


def _water_record(
    water: WaterSurfaces,
    sea: OpenSea | None,
    river_meta: JsonObject,
    spacing_m: float,
    source: str,
) -> JsonObject:
    """``_meta.render.water`` as every layer says it; the shore's optics are each style's."""
    shore = None
    if water.reach is not None:
        shore = {
            **water.reach_meta,
            "rule": (
                "within reach_m of measured ocean water, coverage is the drawn "
                "surface crossing level_m, antialiased to one pixel; elsewhere "
                "recipe 5's rule"
            ),
        }
    return {
        "source": source,
        "depth_ramp_m": WATER_DEPTH_FULL_M,
        "edge_feather_m": WATER_EDGE_M,
        "edge_blur_m": WATER_EDGE_BLUR_M,
        "edge_blur_px": round(WATER_EDGE_BLUR_M / spacing_m, 3),
        "shore": shore,
        "perched": water.perched,
        "level_only": LEVEL_ONLY_TEXT if sea is None else sea.meta,
        "rivers": river_meta or None,
    }


def _draw_layers(
    args: argparse.Namespace,
    layers: tuple[str, ...],
    run: Prepared,
    light: LightingRun | None,
    setup: Setup,
) -> None:
    """Draw each layer, cut it into place, and write its sidecar beside it."""
    two_regime = run.direct is not None
    seam = SeamTrace() if two_regime else None
    regimes = RegimeCoverage() if two_regime else None
    measured: JsonObject = {}
    for layer in layers:
        threads = draw_threads(args.draw_threads, layer, args.size)
        print(f"drawing {layer} at {args.size}x{args.size} on {threads} thread(s)")
        print(encode_stage(f"draw:{layer}", 0.0), flush=True)
        started = time.time()
        sheet = render_layer(
            layer, run.field, run.biome.rgb, run.biome.width, run.borrow, args.size,
            not args.quiet,
            height_dm=run.lattice.heights, direct=run.direct,
            measured_plane_u8=run.lattice.measured_plane, overlay=run.top,
            kernel=taps_cubic if args.kernel_only else taps_pchip, meshes=run.extras.meshes,
            falls=run.extras.falls, reach=run.water.reach, water_level=run.water.level,
            sea=run.sea, painted=run.painted if layer == "painted" else None,
            rivers=run.extras.rivers, relief=run.relief.get(layer),
            # Every layer draws the same surface: measured on the first, quoted for all.
            seam=seam if not measured else None, regimes=regimes if not measured else None,
            unlit=light is not None, surface=light.surface_for() if light else None,
            threads=threads,
        )  # fmt: skip
        drew = time.time() - started
        if seam is not None and regimes is not None and not measured:
            measured = {"seam_trace": seam.result(), "regimes": regimes.result()}
            _report_measured(measured)
        install = light.install if light else install_layer
        try:
            stats, dense, cut = install(
                sheet, setup.image_mod, args.out_dir, layer, setup.cut_workers, run.record.recipe,
                args.renders_name,
            )  # fmt: skip
        except PyramidError as exc:
            raise Refusal(CUT_FAILED, str(exc)) from exc
        del sheet
        stats["game_version_pinned"] = dense["game_version_pinned"] = run.field.build
        draw = LayerDraw(
            layer=layer,
            style_id=LAYER_STYLES[layer],
            style_digest=run.style_digests[layer],
            biome=layer in BIOME_LAYERS,
            measured=measured,
            shore_optics=SHORE_OPTICS[layer],
            seconds_to_draw=drew,
            draw_threads=threads,
            seconds_to_cut=cut,
        )
        sidecar = layer_sidecar(run.record, draw, stats, dense)
        if light is not None:
            light.decorate(sidecar, layer)
        directory = layer_dir(args.out_dir, layer, args.renders_name)
        (directory / RENDER_SIDECAR_NAME).write_text(
            json.dumps(sidecar, indent=1), encoding="utf-8"
        )
        print(
            f"wrote {directory}  {stats['count']} tiles over "
            f"z0..z{stats['max_z']} ({stats['bytes'] / 1e6:.1f} MB) plus {dense['count']} "
            f"@2x over z0..z{dense['max_z']} ({dense['bytes'] / 1e6:.1f} MB)  "
            f"(drew {drew:.0f}s, cut {cut:.0f}s)"
        )
        print(encode_stage(f"cut:{layer}", 1.0), flush=True)


def _report_measured(measured: JsonObject) -> None:
    """The seam trace and the regime table, as the first layer measured them."""
    trace = measured["seam_trace"]
    if isinstance(trace, dict) and trace.get("measured"):
        curvature = trace["p99_curvature"]
        assert isinstance(curvature, dict)
        print(
            f"  seam trace: p99 |d2z/dx2| {curvature['seam']} over the "
            f"blend against {curvature['switch']} for the hard max on "
            f"the same texels -- the fade spends "
            f"{trace['share_of_a_hard_switch']} of that ceiling; against the terrain "
            f"beside the join it reads {trace['against_the_pure_regimes']}, which is "
            "the design's own reference and is measuring the silhouette"
        )
    regimes = measured["regimes"]
    if isinstance(regimes, dict):
        print(f"  regimes: {regimes['sheet_pct']}")


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
            "pixel; docs/spatial-and-map.md section 20 says what that is and is not a claim "
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
        help="leave the direct.cache/ and top.cache/ rasters behind so the next run reuses them",
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
