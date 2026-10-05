"""Draw two base-map layers of this world out of the 1 m heightfield and the game's biomes.

    uv run --extra gen python tools/gen_map_renders.py

``tools/gen_map_image.py`` cuts the game's own drawn map into ``data/local/tiles/``; this
file adds two layers drawn rather than found. **terrain** is a hypsometric ramp under a
north-west hillshade with water tinted by its own depth; **satellite** is the same relief
coloured from the game's own per-pixel biome raster through a palette designed to look like
imagery. Both are 32768x32768 on the **same frame as the artwork sheet** -- x [-3247, 4253]
m, y [-3750, 3750] m -- and cut into the same 256 px pyramid, so the page's tile grid, CRS
and bounds are untouched and a layer is a change of picture and nothing else.

The height under a pixel comes from two regimes and a cross-fade between them. Where
``density.u8.z`` says at least one source vertex landed in the ground under an output texel,
the cliff geometry is rasterised into this grid at 0.229 m -- by ``gen_world_heightmap.py``'s
own sweep, mesh decode, cull rules and ``MaxZRaster``, imported and called here so the only
thing that differs is the grid they are pointed at. Everywhere else, which is the great
majority of the sheet, a PCHIP kernel over the 1 m lattice answers. The join is a blend
and never a switch: the hillshade is a function of the derivative, so a hard switch between
a rasterised surface and a C1 interpolant would draw the density plane's own boundaries into
the relief as ridges. ``SeamTrace`` measures that along the seam on every run. The lattice
under the kernel takes its landscape from ``terrain.u16.z`` at 7.8 mm and its fill and holes
from ``tools/map_fill.py``, and the arches and foliage boulders of ``top.i16.z`` are
rasterised the same way and composited last (docs/spatial-and-map.md sections 25 and 26).

The frame and the artwork slices come from ``tools/gen_map_image.py``, which measured them;
the codec from ``domain.spatial.heightfield``, the pyramid cutter from
``core.gameassets.pyramid``, and the biome decode from ``core.gameassets.maparea``, which
``tools/gen_region_names.py`` reads too. Nothing about any of them is re-decided here,
because three pyramids that retype each other's corners are three pyramids that disagree
about where the world is.

Everything is written under gitignored ``data/local/renders/<layer>/``:
``tiles/{z}/{x}_{y}.png`` for z0..z7, ``tiles@2x/{z}/{x}_{y}.png`` for z0..z5, and a
``meta.json`` the web API reads. Four pins are refused on rather than overwritten -- a field
whose sidecar names another build, a field that is not there, a field with no density plane,
and a direct cache rasterised for another size or build. ``ooz``, ``texture2ddecoder`` and
Pillow are the project's ``gen`` extra and are imported inside the functions that need them,
so a machine without the extra still imports every module and runs the test suite.

The heightfield and the biome raster are derived from Coffee Stain's cooked assets, read out
of the reader's own install. The colours are this file's, and nothing here is committed,
uploaded or redistributed.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import numpy as np

from mapgen.cache import (
    DIRECT_CACHE_DIR_NAME,
    DIRECT_CACHE_SIDECAR,
    MESH_CACHE_DIR_NAME,
    MESH_CACHE_SIDECAR,
    RIVER_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    cached_direct,
    cached_meshes,
    direct_cache_dir,
    direct_cache_stamp,
    top_cache_dir,
)
from mapgen.cache import mesh_stamp as cache_mesh_stamp
from mapgen.common import LOCAL_DIR, RENDERS_DIR_NAME, base_parser, require_gen
from mapgen.gamedata.biome import calibrate_biome, read_biome, region_table_is_current
from mapgen.gamedata.frame import BOUNDS_M, RENDER_PX
from mapgen.gamedata.paint import PAINT_DIR
from mapgen.gamedata.waterfalls import FALLS_CACHE_DIR_NAME, falls_input
from mapgen.lighting.hillshade import (
    BORROW_CLAMP,
    BORROW_DETAIL_SIGMA_PX,
    BORROW_FEATHER_M,
    BORROW_GAIN,
    SHADE_FLOOR,
    SHADE_RANGE,
    SUN_ALTITUDE_DEG,
    SUN_AZIMUTH_DEG,
    artwork_detail,
    coarse_province,
)
from mapgen.palette.falls import prepare_falls
from mapgen.palette.painted import PaintedGround, load_paint_meta
from mapgen.palette.rivers import load_rivers
from mapgen.palette.shore import OCEAN_LEVEL_M, OCEAN_REACH_M, ocean_reach
from mapgen.palette.styles import (
    BIOME_BLEND_TEXELS,
    BIOME_COLOURS,
    LAYER_STYLES,
    NO_MANS_LAND_RGB,
    PAINTED_PALETTE,
    SHORE_OPTICS,
    STYLE_DIGESTS,
    UNKNOWN_BIOME_RGB,
    biome_colour_field,
    biome_lookup,
)
from mapgen.palette.water import WATER_DEPTH_FULL_M, WATER_EDGE_BLUR_M, WATER_EDGE_M, water_planes
from mapgen.terrain.fill import ground_lattice, rebuild_lattice, terrain_lattice
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from mapgen.terrain.rasters import (
    DIRECT_BAND_ROWS,
    DIRECT_SUBSAMPLES,
    direct_placements,
    mesh_items,
    rasterise_direct,
    rasterise_direct_band,
    rasterise_meshes,
    rasterise_top_band,
    read_cliff_geometry,
    sweep_world,
    top_items,
)
from mapgen.terrain.sample import direct_weight, taps_cubic, taps_pchip
from mapgen.terrain.sidecar import GENERATOR_VERSION
from mapgen.tiles.compose import DIRECT_LIFT_KNEE_M, render_layer
from mapgen.tiles.pyramid import (
    CHECK_PARALLEL_Z,
    DEFAULT_WORKERS,
    check_parallel,
    install_layer,
    layer_dir,
)
from mapgen.tiles.recipes import (
    COMPOSITION_NOTE,
    LEVEL_ONLY_NOTE,
    RECIPE,
    RECIPE_KERNEL_ONLY,
    Z7_NOTE,
)
from mapgen.tiles.sidecar import RENDER_SIDECAR_NAME, build_sidecar, pinned_field_build
from satisfactory_mcp.core.gameassets.container import (
    SHEET_PX,
    SLICES,
    open_container,
    read_artwork_sheet,
)
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.maparea import (
    MAP_AREA_CLASS,
    MAP_AREA_PATH,
    NO_MANS_LAND,
)
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    changelist,
    installed_build,
    provenance_block,
    sha256_hex,
)
from satisfactory_mcp.core.gameassets.pyramid import (
    TILES_DIR_NAME,
    PyramidError,
)
from satisfactory_mcp.core.gameassets.versions import (
    READER_VERSIONS,
    RENDER_RECIPES,
    STYLES,
)
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf


class Refusal(Exception):
    """A run the generator will not do, with the exit code ``main`` returns for it."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Step:
    id: str
    cached: bool
    est_s: float


@dataclass(frozen=True)
class Plan:
    layers: tuple[str, ...]
    size: int
    steps: tuple[Step, ...]


#: The layers this file draws, in the order they are cut; ``--layer`` restricts it.
LAYERS = ("terrain", "satellite", "painted")


def load_imaging():
    """Pillow, once ``require_gen`` has shown it is there.

    The size limit goes off because Pillow's default guard is a decompression-bomb rule for
    images off the internet, and here an 8192 px sheet is the point.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    return Image


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
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
            "pixel -- see the module docstring for what that is and is not a claim about). "
            f"{RENDER_PX >> 4} and {RENDER_PX >> 5} are previews: minutes, not half an hour"
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
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"processes deflating tiles (default {DEFAULT_WORKERS}; 1 cuts serially). The "
            "resampling is single-threaded either way, so the tiles are the same bytes"
        ),
    )
    parser.add_argument(
        "--check-parallel",
        action="store_true",
        help=(
            f"cut z{CHECK_PARALLEL_Z} both serially and in parallel and compare every "
            "tile's SHA-256, then carry on"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace layers this run cannot show were drawn from the field now on disk",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-band progress lines")
    args = parser.parse_args()

    layers = tuple(dict.fromkeys(args.layer)) if args.layer else LAYERS
    workers = max(1, args.workers)

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    pillow_version, pyooz_version = versions["pillow"], versions["pyooz"]
    import texture2ddecoder as decoder

    image_mod = load_imaging()

    field = hf.load_field(args.field)
    if field is None:
        print(
            f"no heightfield at {args.field}. That field is the one input this file cannot "
            "invent -- every pixel of both layers is a height off it -- so there is nothing "
            "to draw. Write it first:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py\n"
            "It reads your own installed game and writes to the same gitignored directory."
        )
        return 4
    field_meta = field.meta
    field_build = field.build
    print(
        f"field: {field.width}x{field.height} at {field.spacing_cm / 100:g} m, build {field_build}"
    )

    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
    weight_plane, weight_meta = (None, {}) if args.kernel_only else direct_weight(field, spacing_m)
    if weight_plane is None and not args.kernel_only:
        print(
            f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its texels "
            "are measurements and which are the cliff rasteriser interpolating across a "
            "triangle wider than a texel. That plane is the only thing the two-regime "
            f"sampler switches on, so recipe {RECIPE} has nothing to draw. Cut a field with "
            f"generator version {GENERATOR_VERSION} or later:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py --force\n"
            "or pass --kernel-only to draw the single-regime picture and say so in the "
            "sidecar."
        )
        return 6
    if weight_plane is not None:
        print(
            f"  a measurement where {weight_meta['rule']} -- "
            f"{weight_meta['qualifying_share_of_the_field']}% of the field, "
            f"{weight_meta['qualifying_share_of_the_cliff_province']}% of its cliff "
            "province. Provenance, not a gate: the rocks are drawn wherever they cover a "
            "pixel"
        )
    recipe = RECIPE_KERNEL_ONLY if args.kernel_only else RECIPE
    heights = None if args.kernel_only else field._height_dm.astype(np.float32)
    if heights is None:
        print(f"  --kernel-only: drawing recipe {recipe}, the picture before the two regimes")
    ground, ground_meta = (None, {}) if heights is None else ground_lattice(field, heights)
    terrain_meta: dict = {}
    if ground is not None:
        ground, terrain_meta = terrain_lattice(field, ground)
        if "absent" in terrain_meta:
            print(f"  {terrain_meta['absent']}")
        else:
            print(
                f"  landscape from {hf.TERRAIN_NAME}: {terrain_meta['landscape_texels']} texels "
                f"plus {terrain_meta['under_cliff_texels']} under the cliff province"
            )
    if ground is not None:
        print(
            f"  the lattice under the rocks: {ground_meta['lattice_share_of_the_field']}% of "
            f"the field, with {ground_meta['removed_share_of_the_field']}% of it -- the cliff "
            "province -- taken out so the rocks are composited over the ground rather than "
            "over their own 1 m fold"
        )

    out_dir: Path = args.out_dir
    if not args.force:
        for layer in layers:
            sidecar_path = layer_dir(out_dir, layer, args.renders_name) / RENDER_SIDECAR_NAME
            if not (layer_dir(out_dir, layer, args.renders_name) / TILES_DIR_NAME).is_dir():
                continue
            try:
                existing = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                existing = {}
            pinned = pinned_field_build(existing if isinstance(existing, dict) else {})
            if pinned != field_build:
                print(
                    f"{layer_dir(out_dir, layer, args.renders_name)} already holds a {layer} pyramid and this "
                    "run cannot show it was drawn from the field now on disk.\n"
                    f"  field on disk: {field_build}\n"
                    f"  those tiles:   {pinned or 'no meta.json, or no build recorded in it'}\n"
                    "A render from another build is a picture of another world's terrain, "
                    "and drift is announced rather than overwritten. Pass --force to "
                    "replace it anyway."
                )
                return 3

    # ---- the artwork sheet, which every layer now needs ------------------------------
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the game's own assets from {paks} with pyooz {pyooz_version}")
    store = open_container(args.game)
    scripts = ScriptObjects(paks, oodle_decompress)
    artwork = read_artwork_sheet(store, decoder, image_mod)
    try:
        _pin, game_raw = installed_build(args.game)
    except (InstallNotFound, OSError, ValueError):
        game_raw = {}
    game_cl = changelist(game_raw)
    inputs = {
        "heightfield": {
            "cl": changelist(field_build),
            "generator_version": field_meta.get("generator_version"),
            "planes": ["height", "prov", "water", "waterq"]
            + ([] if args.kernel_only else ["density", "terrain"]),
            "digest": field_meta.get("digest"),
        },
        "artwork_sheet": {
            "cl": game_cl,
            "reader_version": READER_VERSIONS["artwork_sheet"],
            "digest": sha256_hex(np.ascontiguousarray(np.asarray(artwork, np.uint8)).data),
        },
    }
    detail, detail_meta = artwork_detail(artwork)
    print(
        f"  artwork sheet {SHEET_PX}x{SHEET_PX} from {len(SLICES)} BC1 slices; luminance "
        f"high pass at sigma {BORROW_DETAIL_SIGMA_PX} px, std {detail_meta['measured_std']}"
    )
    province, province_meta = coarse_province(field)
    print(
        f"  coarse provenance ({', '.join(province_meta['provinces'])}) is "
        f"{province_meta['share_of_the_field']}% of the field, feathered "
        f"{BORROW_FEATHER_M:g} m"
    )
    borrow = (detail, province)
    _wet_plane, _measured_plane, water_source = water_planes(field)
    print(f"  water: {water_source}")
    fill_meta: dict = {}
    if ground is not None:
        heights, ground, fill_meta = rebuild_lattice(field, ground, store)
        print(
            f"  lattice rebuilt in {sum(fill_meta['seconds'].values()):.0f}s: "
            f"{fill_meta['share_of_the_field_pct']}; {fill_meta['holes']['holes']} holes "
            f"filled ({fill_meta['holes']['harmonic_fallback']} harmonic)"
        )

    # The check cuts the ARTWORK: a real picture with real entropy, so the PNGs are real
    # PNGs rather than a run-length of one colour that compares equal whatever happened.
    parallel_check = None
    if args.check_parallel:
        parallel_check = check_parallel(
            np.asarray(artwork, np.uint8), image_mod, out_dir / "parallel.check", workers
        )
        print(
            f"  parallel cutter: z{parallel_check['level']}, {parallel_check['tiles']} tiles, "
            f"{parallel_check['seconds_serial']}s serial vs "
            f"{parallel_check['seconds_parallel']}s on {workers} workers "
            f"({parallel_check['speedup']}x) -- byte-identical: "
            f"{parallel_check['byte_identical']}"
        )
        if not parallel_check["byte_identical"]:
            print(
                "  the parallel cutter does not reproduce the serial one's bytes. That is "
                "the one thing it promises, so nothing is written: "
                + ", ".join(parallel_check["differing_tiles"])
            )
            return 5

    # ---- the biome raster ------------------------------------------------------------
    biome = None
    drawn: list[str] = []
    if "satellite" in layers or "painted" in layers:
        biome = read_biome(store, scripts)
        print(
            f"  {biome['width']}x{biome['width']} palette indices, "
            f"{len(biome['palette'])} entries, {len(biome['distinct_areas'])} named areas"
        )
        calibration = calibrate_biome(biome, artwork, image_mod)
        print(
            f"  calibration: edge ratio {calibration['edge_ratio_at_the_pin']} at the pin "
            f"against {calibration['edge_ratio_at_the_best_rival_shift']} for the best "
            f"shift and {calibration['edge_ratio_at_other_scales']} at other scales -- "
            f"margin {calibration['margin_over_the_best_rival']}x over "
            f"{calibration['sweep']}"
        )
        if not calibration["pin_holds"]:
            print(
                "  WARNING: the pin no longer beats its rivals by the required margin. "
                "The biome texture moved, or the artwork sheet did. The layer is still "
                "drawn -- it is the corners that are in question -- and _meta says so."
            )
        agreement = region_table_is_current(biome)
        if "skipped" in agreement:
            print(f"  region table: {agreement['skipped']}")
        else:
            print(
                f"  region table: {agreement['cells_agreeing']} of "
                f"{agreement['cells_compared']} committed cells match this raster "
                f"({agreement['agreement_pct']}%)"
            )
            if not agreement["table_is_current"]:
                print(
                    "  WARNING: data/region_names.json is no longer this asset's own "
                    "downsample, so it was cut from a different build. Re-run:\n"
                    "      uv run --extra gen python tools/gen_region_names.py"
                )
        table, drawn = biome_lookup(biome)
        inputs["biome_raster"] = {
            "cl": game_cl,
            "reader_version": READER_VERSIONS["biome_raster"],
            "digest": sha256_hex(
                np.ascontiguousarray(biome["area"]).data,
                json.dumps(biome["assets_by_index"]).encode("utf-8"),
            ),
        }
        biome_rgb = biome_colour_field(biome, table)
        biome_source = {
            "biome_raster": {
                "name": "/Game/"
                + MAP_AREA_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0],
                "class": MAP_AREA_CLASS,
                "licence": (
                    "Coffee Stain Studios' own asset, read out of the reader's installed "
                    "copy of the game. Not committed, not redistributed, and served to "
                    "localhost only."
                ),
                "derivation": (
                    f"mAreaData, {biome['width']}x{biome['width']} palette indices; "
                    "mColorToArea resolves each index to a UFGMapArea object"
                ),
                "areas": biome["distinct_areas"],
                "shipped_palette_rgba": [list(entry) for entry in biome["palette"]],
                "shipped_palette_role": (
                    "the game's own UI legend -- flat primaries, cyan, magenta, white. "
                    "Decoded for the record and NOT drawn: see palette below, which is this "
                    "file's own and was written to look like imagery."
                ),
                "palette": {name: list(BIOME_COLOURS[name]) for name in sorted(BIOME_COLOURS)},
                "palette_blend_texels": BIOME_BLEND_TEXELS,
                "palette_fallback": {
                    NO_MANS_LAND: list(NO_MANS_LAND_RGB),
                    "an area this file has no colour for": list(UNKNOWN_BIOME_RGB),
                },
                "index_to_area": {str(i): name for i, name in enumerate(drawn)},
                "index_to_asset": {str(i): name for i, name in enumerate(biome["assets_by_index"])},
                "calibration": calibration,
                "region_table_check": agreement,
                "pyooz_version": pyooz_version,
            }
        }
    else:
        biome_rgb, biome_source = None, {}

    painted = None
    paint_source: dict = {}
    if "painted" in layers:
        paint_meta = load_paint_meta(args.paint_dir)
        if paint_meta is None:
            print(
                f"no paint layers at {args.paint_dir}, which the painted layer is coloured "
                "from. Extract them once from the installed game:\n"
                "    uv run --extra gen python tools/gen_paint_layers.py"
            )
            return 8
        started = time.time()
        painted = PaintedGround(args.paint_dir, PAINTED_PALETTE, field, biome, list(drawn))
        inputs["paint"] = {
            "cl": paint_meta.get("cl"),
            "generator_version": paint_meta.get("generator_version"),
            "digest": paint_meta.get("digest"),
        }
        paint_source = {
            "paint": {
                "name": f"data/local/{args.paint_dir.name}/",
                "generator": paint_meta.get("generator"),
                "generator_version": paint_meta.get("generator_version"),
                "digest": paint_meta.get("digest"),
                **painted.source,
                "seconds_to_prepare": round(time.time() - started, 1),
            }
        }
        print(f"  paint layers prepared in {time.time() - started:.0f}s")

    reach, reach_meta = (None, {}) if args.kernel_only else ocean_reach(field)
    if reach is not None:
        print(
            f"  ocean shore at {OCEAN_LEVEL_M} m: {reach_meta['ocean_texels']} ocean "
            f"texels, {reach_meta['reach_texels']} within {OCEAN_REACH_M:g} m"
        )

    # ---- the cliff geometry and the top overlay, rasterised into this render's own grid
    direct = None
    top = None
    direct_source: dict = {}
    top_source: dict = {}
    loaded: dict = {}

    def sweep_once() -> dict:
        if "sweep" not in loaded:
            index = AssetIndex(store)
            loaded["index"] = index
            loaded["sweep"] = sweep_world(
                store, scripts, index, ClassFacts(store, index), not args.quiet
            )
        return loaded["sweep"]

    def geometry_once() -> dict:
        if "geometry" not in loaded:
            sweep = sweep_once()
            index = loaded["index"]
            loaded["geometry"] = read_cliff_geometry(
                store, scripts, index, ClassFacts(store, index), not args.quiet, sweep
            )
            got = loaded["geometry"]
            print(
                f"  {got['meshes']} rock meshes, {got['tris'] / 1e6:.2f} M triangles "
                f"{got['by_source']}, swept in {got['seconds_sweep']}s and decoded "
                f"in {got['seconds_decode']}s"
            )
        return loaded["geometry"]

    if weight_plane is not None:
        cache = (
            args.cache_dir / DIRECT_CACHE_DIR_NAME
            if args.cache_dir
            else direct_cache_dir(out_dir, args.renders_name)
        )
        stamp = direct_cache_stamp(args.size, args.direct_subsamples, field_build)
        maps = cached_direct(cache, stamp)
        if maps is None:
            print(
                f"decoding the cliff geometry and rasterising it at {spacing_m:.4f} m"
                + (
                    f" with {args.direct_subsamples}x{args.direct_subsamples} sub-samples"
                    if args.direct_subsamples > 1
                    else ""
                )
            )
            geometry = geometry_once()
            prepared, dropped = direct_placements(geometry["sweep"], geometry["geometry"])
            print(f"  {len(prepared)} placements rasterised, dropped {dropped}")
            cache_stats = rasterise_direct(
                partial(rasterise_direct_band, prepared, geometry["geometry"]),
                cache,
                args.size,
                args.direct_subsamples,
                stamp,
                not args.quiet,
            )
            print(
                f"  direct raster: {cache_stats['texels_with_geometry'] / 1e6:.1f} M texels "
                f"({cache_stats['share_of_the_sheet']}% of the sheet) in "
                f"{cache_stats['seconds']}s"
            )
            direct_source = {
                "cliff_geometry": {
                    "name": "the same placed rock meshes tools/gen_world_heightmap.py folds "
                    "into the 1 m field, decoded here a second time",
                    "licence": (
                        "Coffee Stain Studios' own cooked assets, read out of the reader's "
                        "installed copy of the game. Nothing is committed, redistributed or "
                        "served past localhost."
                    ),
                    "decoder": (
                        "tools/gen_world_heightmap.py's own sweep_levels, read_mesh_geometry, "
                        "rotation_matrix, winding_sign and MaxZRaster, imported and called. "
                        "The grid they are pointed at is the only thing this file changes."
                    ),
                    "meshes": geometry["meshes"],
                    "by_source": geometry["by_source"],
                    "source_triangles": geometry["tris"],
                    "triangles_out_of_bounds": geometry["triangles_out_of_bounds"],
                    "placements_rasterised": len(prepared),
                    "placements_dropped": dropped,
                    "raster": cache_stats,
                    "pyooz_version": pyooz_version,
                }
            }
            maps = cached_direct(cache, stamp)
            del prepared
        else:
            print(f"reusing the direct raster already in {cache}")
            direct_source = {
                "cliff_geometry": {
                    "reused": json.loads((cache / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8"))
                }
            }
        if maps is None:
            print(f"the direct raster in {cache} could not be read back after writing it")
            return 7
        direct = (maps[0], maps[1], ground, args.direct_subsamples)
        inputs["cliff_geometry"] = {
            "cl": changelist(stamp["game_version_pinned"]),
            "reader_version": READER_VERSIONS["cliff_geometry"],
        }

        if not args.no_top:
            top_cache = (
                args.cache_dir / TOP_CACHE_DIR_NAME
                if args.cache_dir
                else top_cache_dir(out_dir, args.renders_name)
            )
            top_maps = cached_direct(top_cache, stamp)
            if top_maps is None:
                print(f"rasterising the arches and foliage boulders at {spacing_m:.4f} m")
                geometry = geometry_once()
                items, top_meta = top_items(
                    store, scripts, loaded["index"], geometry["sweep"], geometry["geometry"]
                )
                print(
                    f"  {top_meta['arch_placements']} arches, "
                    f"{top_meta['foliage_instances']} boulders {top_meta['foliage_sources']}"
                )
                top_stats = rasterise_direct(
                    partial(rasterise_top_band, items),
                    top_cache,
                    args.size,
                    args.direct_subsamples,
                    stamp,
                    not args.quiet,
                )
                print(
                    f"  top raster: {top_stats['texels_with_geometry'] / 1e6:.1f} M texels in "
                    f"{top_stats['seconds']}s"
                )
                top_source = {"top_overlay": {**top_meta, "raster": top_stats}}
                del items
                top_maps = cached_direct(top_cache, stamp)
            else:
                print(f"reusing the top raster already in {top_cache}")
                top_source = {
                    "top_overlay": {
                        "reused": json.loads(
                            (top_cache / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8")
                        )
                    }
                }
            if top_maps is None:
                print(f"the top raster in {top_cache} could not be read back after writing it")
                return 7
            top = (top_maps[0], top_maps[1], args.direct_subsamples)

    meshes = falls = None
    mesh_source: dict = {}
    if weight_plane is not None and not args.no_meshes:
        cache_root = args.cache_dir or out_dir / args.renders_name
        mesh_cache = cache_root / MESH_CACHE_DIR_NAME
        mesh_stamp = cache_mesh_stamp(args.size, field_build, READER_VERSIONS["render_meshes"])
        meshes = cached_meshes(mesh_cache, mesh_stamp)
        if meshes is None:
            print(f"rasterising the render-only meshes at {spacing_m:.4f} m")
            sweep = sweep_once()
            prepared, mesh_meta = mesh_items(store, scripts, loaded["index"], sweep)
            print(f"  {mesh_meta['meshes']} meshes, {mesh_meta['instances']}")
            mesh_stats = rasterise_meshes(
                prepared, mesh_cache, mesh_stamp, BOUNDS_M, DIRECT_BAND_ROWS, not args.quiet
            )
            print(f"  mesh raster: {mesh_stats['texels'] / 1e6:.2f} M texels in "
                  f"{mesh_stats['seconds']}s")  # fmt: skip
            mesh_source = {"render_meshes": {**mesh_meta, "raster": mesh_stats}}
            del prepared
            meshes = cached_meshes(mesh_cache, mesh_stamp)
        else:
            print(f"reusing the render-only mesh raster already in {mesh_cache}")
            mesh_source = {
                "render_meshes": {
                    "reused": json.loads(
                        (mesh_cache / MESH_CACHE_SIDECAR).read_text(encoding="utf-8")
                    )
                }
            }
        records, falls_source = falls_input(cache_root, field_build, sweep_once)
        falls, mesh_source = prepare_falls(records, field), {**mesh_source, **falls_source}
        falls_source["waterfalls"]["drawable"] = len(falls)
        for name in ("render_meshes", "waterfalls"):
            inputs[name] = {"cl": changelist(field_build), "reader_version": READER_VERSIONS[name]}
    rivers, river_meta = (None, {}) if args.kernel_only else load_rivers(
        (args.cache_dir or out_dir / args.renders_name) / RIVER_CACHE_DIR_NAME,
        field_build, sweep_once, field,
    )  # fmt: skip
    if rivers is not None:
        inputs["river_splines"] = {"cl": changelist(field_build),
                                   "reader_version": READER_VERSIONS["river_splines"]}  # fmt: skip
    loaded.clear()

    # ---- draw and cut ----------------------------------------------------------------
    borrow_source = {
        "artwork_detail": {
            "name": f"the game's own {SHEET_PX} px map sheet, from its four BC1 slices",
            "licence": (
                "Coffee Stain Studios' own artwork, read out of the reader's installed copy "
                "of the game. Its LUMINANCE only, high-passed, and multiplied into shading "
                "-- no pixel of it is drawn and no colour of it crosses. Not committed, not "
                "redistributed, and served to localhost only."
            ),
            **detail_meta,
            "applied_where": province_meta,
            "gain": BORROW_GAIN,
            "clamp": list(BORROW_CLAMP),
            "reading": (
                "the field is one resolution but not one accuracy. Over the landscape "
                "province -- 45.3% of it -- the geometry is continuous and its own shading "
                "is the best there is, so nothing is borrowed. Over cliff and fill it is "
                "rasterised hulls and 3.9 m blocks, which is why those provinces read as "
                "melted wax when drawn from the field alone, and the artwork drew the same "
                "ground at 0.92 m."
            ),
        }
    }
    total_started = time.time()
    seam = SeamTrace() if direct is not None else None
    regimes = RegimeCoverage() if direct is not None else None
    measured: dict = {}
    for layer in layers:
        print(f"drawing {layer} at {args.size}x{args.size}")
        print(encode_stage(f"draw:{layer}", 0.0), flush=True)
        started = time.time()
        sheet = render_layer(
            layer,
            field,
            biome_rgb,
            biome or {"width": 1, "area": np.zeros((1, 1), np.uint8)},
            borrow,
            args.size,
            not args.quiet,
            height_dm=heights,
            direct=direct,
            measured_plane_u8=weight_plane,
            overlay=top,
            kernel=taps_cubic if args.kernel_only else taps_pchip,
            meshes=meshes,
            falls=falls,
            reach=reach,
            painted=painted if layer == "painted" else None,
            rivers=rivers,
            # Both layers draw the identical surface, so the seam and the regime table are
            # measured on the first one and quoted for both.
            seam=seam if not measured else None,
            regimes=regimes if not measured else None,
        )
        drew = time.time() - started
        if seam is not None and not measured:
            measured = {"seam_trace": seam.result(), "regimes": regimes.result()}
            trace = measured["seam_trace"]
            if trace.get("measured"):
                print(
                    f"  seam trace: p99 |d2z/dx2| {trace['p99_curvature']['seam']} over the "
                    f"blend against {trace['p99_curvature']['switch']} for the hard max on "
                    f"the same texels -- the fade spends "
                    f"{trace['share_of_a_hard_switch']} of that ceiling; against the terrain "
                    f"beside the join it reads {trace['against_the_pure_regimes']}, which is "
                    "the design's own reference and is measuring the silhouette"
                )
            print(f"  regimes: {measured['regimes']['sheet_pct']}")
        try:
            stats, dense, cut = install_layer(
                sheet, image_mod, out_dir, layer, workers, recipe, args.renders_name
            )
        except PyramidError as exc:
            print(exc)
            return 1
        del sheet
        stats["game_version_pinned"] = field_build
        dense["game_version_pinned"] = field_build
        spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
        render = {
            "width_px": args.size,
            "height_px": args.size,
            "metres_per_pixel": round(spacing_m, 4),
            "sampling": (
                "the field's own composition rule, at this render's spacing. KERNEL: "
                "tensor-product PCHIP (Fritsch-Butland slopes: exact at the 1 m vertices, "
                "never outside a cell's own range) over the LANDSCAPE AND FILL lattices, "
                "rebuilt by tools/map_fill.py -- the cliff province taken out, because "
                "interpolating the composed field reconstructs its own 1 m fold and a rim reconstructed from "
                "a fold is a 1 m staircase at any output resolution -- falling back to "
                "bilinear where the 4x4 stencil straddles no data and to nothing where no "
                "texel under it has a value. DIRECT: the cliff geometry rasterised into "
                f"this grid at {spacing_m:.4f} m and composited on top of that lattice by "
                "its own coverage of the pixel, raising the ground and never lowering it, "
                "through a smoothed positive part so the line where a rock meets the ground "
                "is not a derivative discontinuity the hillshade would draw. density.u8.z "
                "does not gate any of this: it says which of the drawn texels are "
                "measurements and which are the plane of a triangle wider than a texel, and "
                "_meta.render.two_regime.regimes counts both"
            )
            if direct is not None
            else (
                "Catmull-Rom (cubic convolution, a = -1/2) over the 1 m field per output "
                "pixel wherever the 4x4 stencil is whole, bilinear where it straddles the "
                "edge of the data, and nothing at all where no texel under it has a value. "
                "--kernel-only: no geometry was opened and no direct regime was drawn"
            ),
            "two_regime": {
                "enabled": direct is not None,
                "subsamples_per_axis": args.direct_subsamples if direct is not None else None,
                "silhouette_antialiasing": (
                    "none: a pixel is rock where a triangle covers its centre, at the "
                    "triangle's own height, and ground where none does. Rock heights are "
                    "never blurred across a silhouette"
                )
                if args.direct_subsamples == 1
                else (
                    f"{args.direct_subsamples}x{args.direct_subsamples} sub-samples per output "
                    "texel, box-folded"
                ),
                "composition": COMPOSITION_NOTE,
                "ground_lattice": ground_meta,
                "terrain_lattice": terrain_meta,
                "top_overlay": top is not None,
                "measurement_rule": weight_meta,
                "lift_knee_m": DIRECT_LIFT_KNEE_M,
                "fill_rebuild": fill_meta,
                **measured,
            },
            "z7": Z7_NOTE if args.size >= RENDER_PX else None,
            "hillshade": (
                f"sun at azimuth {SUN_AZIMUTH_DEG} deg, altitude {SUN_ALTITUDE_DEG} deg, "
                f"shade in [{SHADE_FLOOR}, {SHADE_FLOOR + SHADE_RANGE}], computed at the "
                "output's own spacing"
            ),
            "water": {
                "source": water_source,
                "depth_ramp_m": WATER_DEPTH_FULL_M,
                "edge_feather_m": WATER_EDGE_M,
                "edge_blur_m": WATER_EDGE_BLUR_M,
                "edge_blur_px": round(WATER_EDGE_BLUR_M / spacing_m, 3),
                "shore": (
                    {
                        **reach_meta,
                        "rule": (
                            "within reach_m of measured ocean water, coverage is the drawn "
                            "surface crossing level_m, antialiased to one pixel; elsewhere "
                            "recipe 5's rule"
                        ),
                        "optics": SHORE_OPTICS[layer],
                    }
                    if reach is not None
                    else None
                ),
                "level_only": LEVEL_ONLY_NOTE,
                "rivers": river_meta or None,
            },
            "seconds_to_draw": round(drew, 1),
            "seconds_to_cut": round(cut, 1),
            "cut_workers": workers,
            **({"parallel_cutter_check": parallel_check} if parallel_check else {}),
            "imaging": {"name": "pillow", "version": pillow_version},
        }
        style_id = LAYER_STYLES[layer]
        recipe_row = RENDER_RECIPES[recipe]
        sidecar = build_sidecar(
            layer=layer,
            recipe=recipe,
            field_meta=field_meta,
            tiles=stats,
            tiles_2x=dense,
            render=render,
            provenance=provenance_block(
                game_raw,
                {
                    key: value
                    for key, value in inputs.items()
                    if (key != "biome_raster" or layer in ("satellite", "painted"))
                    and (key != "paint" or layer == "painted")
                },
                {
                    "family": "render",
                    "recipe": recipe,
                    "version": recipe_row["version"],
                    "label": recipe_row["label"],
                    "sampler": "catmull-rom" if args.kernel_only else recipe_row["sampler"],
                    "two_regime": direct is not None,
                    "size_px": args.size,
                    "subsamples": args.direct_subsamples,
                },
                {
                    "id": style_id,
                    "version": STYLES[style_id]["version"],
                    "label": STYLES[style_id]["label"],
                    "digest": STYLE_DIGESTS[layer],
                },
            ),
            extra={
                **borrow_source,
                **direct_source,
                **top_source,
                **mesh_source,
                **(biome_source if layer in ("satellite", "painted") else {}),
                **(paint_source if layer == "painted" else {}),
            },
        )
        path = layer_dir(out_dir, layer, args.renders_name) / RENDER_SIDECAR_NAME
        path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
        print(
            f"wrote {layer_dir(out_dir, layer, args.renders_name)}  {stats['count']} tiles over "
            f"z0..z{stats['max_z']} ({stats['bytes'] / 1e6:.1f} MB) plus {dense['count']} "
            f"@2x over z0..z{dense['max_z']} ({dense['bytes'] / 1e6:.1f} MB)  "
            f"(drew {drew:.0f}s, cut {cut:.0f}s)"
        )
        print(encode_stage(f"cut:{layer}", 1.0), flush=True)
    if direct is not None:
        # Let the memory maps go before removing the files under them: on Windows an open
        # mapping refuses the unlink outright.
        direct = maps = top = top_maps = meshes = None
        if not args.keep_direct:
            kept_dirs = (DIRECT_CACHE_DIR_NAME, TOP_CACHE_DIR_NAME, MESH_CACHE_DIR_NAME)
            for kept in (*kept_dirs, RIVER_CACHE_DIR_NAME, FALLS_CACHE_DIR_NAME):
                root = args.cache_dir or out_dir / args.renders_name
                shutil.rmtree(root / kept, ignore_errors=True)
    print(f"done in {time.time() - total_started:.0f}s")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0
