"""The renders command's inputs: the field and its lattices, the game, the borrow, the paint.

Each stage prints what it found, and raises ``Refusal`` with the command's exit code when the
run cannot go on (docs/map/renders.md section 20, "Refusals"). The lattices and the water
also say what every layer's sidecar records of them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np

from mapgen.cache import restyle_gaps
from mapgen.common import Refusal
from mapgen.gamedata.install import missing_container
from mapgen.gamedata.nodes import oil_nodes
from mapgen.lighting.borrow import (
    BORROW_DETAIL_SIGMA_PX,
    BORROW_FEATHER_M,
    artwork_detail,
    borrow_metadata,
    coarse_province,
)
from mapgen.palette.painted.albedo import load_paint_meta
from mapgen.palette.painted.derive.palette import calibrated_palette
from mapgen.palette.painted.ground import PaintedGround
from mapgen.palette.painted.shapes import BiomeGrid
from mapgen.palette.styles import painted_style
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.perched import WaterSurfaces
from mapgen.palette.water.surface import (
    WATER_DEPTH_FULL_M,
    WATER_EDGE_BLUR_M,
    WATER_EDGE_M,
    water_planes,
)
from mapgen.render.ground.lift import DIRECT_LIFT_KNEE_M
from mapgen.render.run.cached_rasters import RasterGrid
from mapgen.terrain.fill import ground_lattice, rebuild_lattice, terrain_lattice
from mapgen.terrain.heightfield.sidecar import GENERATOR_VERSION
from mapgen.terrain.sample import direct_mask
from mapgen.tiles.imaging import TileImaging
from mapgen.tiles.pyramid import check_parallel, layer_dir
from mapgen.tiles.recipes import RECIPE, RECIPE_KERNEL_ONLY
from mapgen.tiles.rendertext import LEVEL_ONLY_TEXT
from mapgen.tiles.sidecar import RENDER_SIDECAR_NAME, pinned_field_build
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I8Grid, U8Grid
from satisfactory_mcp.core.gameassets.container import (
    SHEET_PX,
    SLICES,
    open_container,
    paks_dir,
    read_artwork_sheet,
)
from satisfactory_mcp.core.gameassets.imaging import BlockDecoder
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    changelist,
    installed_build,
    sha256_hex,
)
from satisfactory_mcp.core.gameassets.pyramid import TILES_DIR_NAME
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.domain.spatial import heightfield as hf

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = [
    "NO_CONTAINER",
    "NO_DENSITY",
    "NO_FIELD",
    "NO_PAINT",
    "PARALLEL_MISMATCH",
    "RESTYLE_MISS",
    "STALE_LAYER",
    "ArtworkBorrow",
    "GameInputs",
    "Lattice",
    "PaintInputs",
    "artwork_borrow",
    "check_parallel_cutter",
    "field_input",
    "field_lattice",
    "field_water_source",
    "load_field",
    "open_game_inputs",
    "prepare_paint",
    "rebuilt_lattice",
    "refuse_restyle_gaps",
    "refuse_stale_layers",
    "water_record",
]

#: The renders command's exit codes for the runs these stages refuse.
NO_CONTAINER = 1
STALE_LAYER = 3
NO_FIELD = 4
PARALLEL_MISMATCH = 5
NO_DENSITY = 6
NO_PAINT = 8
RESTYLE_MISS = 9


@dataclass(frozen=True)
class Lattice:
    """The heights the band loop samples, and the lattice under the rocks it composes over.

    Both None for ``--kernel-only``, which samples the field's own heights and has no
    ``measured_plane``. The ``*_meta`` blocks are the sidecar's record of each.
    """

    recipe: int
    measured_plane: U8Grid | None
    measurement_rule: JsonObject
    heights: F32Grid | None
    ground: F32Grid | None
    ground_meta: JsonObject
    terrain_meta: JsonObject
    fill_meta: JsonObject

    def composition(self, *, top_overlay: bool) -> JsonObject:
        """``_meta.render.two_regime``'s record of these lattices and the lift rule."""
        return {
            "ground_lattice": self.ground_meta,
            "terrain_lattice": self.terrain_meta,
            "top_overlay": top_overlay,
            "measurement_rule": self.measurement_rule,
            "lift_knee_m": DIRECT_LIFT_KNEE_M,
            "fill_rebuild": self.fill_meta,
        }


@dataclass(frozen=True)
class GameInputs:
    """The installed game opened for a run: its container, the artwork sheet and its build."""

    store: IoStore
    scripts: ScriptObjects
    artwork: Image
    build_raw: JsonObject
    build_cl: int | None

    def artwork_input(self) -> JsonObject:
        """The provenance input naming the artwork sheet this run decoded."""
        pixels = np.ascontiguousarray(np.asarray(self.artwork, np.uint8))
        return {
            "cl": self.build_cl,
            "reader_version": READER_VERSIONS["artwork_sheet"],
            "digest": sha256_hex(pixels.data),
        }


@dataclass(frozen=True)
class ArtworkBorrow:
    """The artwork's shading the coarse provinces borrow: ``(detail, province)`` and its record."""

    detail: I8Grid
    province: U8Grid
    source: JsonObject

    @property
    def planes(self) -> tuple[I8Grid, U8Grid]:
        return self.detail, self.province


@dataclass(frozen=True)
class PaintInputs:
    """The painted layer's ground, its style digest, and what its sidecar records of it.

    ``block`` is the sidecar's ``sources.paint``; the run adds the water classes to it.
    """

    ground: PaintedGround
    digest: str
    provenance: JsonObject
    block: JsonObject


def load_field(path: Path) -> hf.Field:
    """The heightfield the render is drawn from; a run without one is refused."""
    field = hf.load_field(path)
    if field is None:
        raise Refusal(
            NO_FIELD,
            f"no heightfield at {path}. That field is the one input this file cannot "
            "invent -- every pixel of both layers is a height off it -- so there is nothing "
            "to draw. Write it first:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py\n"
            "It reads your own installed game and writes to the same gitignored directory.",
        )
    print(
        f"field: {field.width}x{field.height} at {field.spacing_cm / 100:g} m, build {field.build}"
    )
    return field


def field_input(field: hf.Field, kernel_only: bool) -> JsonObject:
    """The provenance input naming the heightfield and the planes this run reads off it."""
    planes: list[JsonValue] = ["height", "prov", "water", "waterq"]
    return {
        "cl": changelist(field.build),
        "generator_version": field.meta.get("generator_version"),
        "planes": planes + ([] if kernel_only else ["density", "terrain"]),
        "digest": field.meta.get("digest"),
    }


def field_lattice(field: hf.Field, spacing_m: float, kernel_only: bool) -> Lattice:
    """The two regimes' inputs at this spacing; ``--kernel-only`` samples the field alone."""
    measured_plane, rule = (None, {}) if kernel_only else direct_mask(field, spacing_m)
    if measured_plane is None and not kernel_only:
        raise Refusal(
            NO_DENSITY,
            f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its texels "
            "are measurements and which are the cliff rasteriser interpolating across a "
            "triangle wider than a texel. That plane is the only thing the two-regime "
            f"sampler switches on, so recipe {RECIPE} has nothing to draw. Cut a field with "
            f"generator version {GENERATOR_VERSION} or later:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py --force\n"
            "or pass --kernel-only to draw the single-regime picture and say so in the "
            "sidecar.",
        )
    if measured_plane is not None:
        print(
            f"  a measurement where {rule['rule']} -- "
            f"{rule['qualifying_share_of_the_field']}% of the field, "
            f"{rule['qualifying_share_of_the_cliff_province']}% of its cliff "
            "province. Provenance, not a gate: the rocks are drawn wherever they cover a "
            "pixel"
        )
    recipe = RECIPE_KERNEL_ONLY if kernel_only else RECIPE
    if kernel_only:
        print(f"  --kernel-only: drawing recipe {recipe}, the picture before the two regimes")
        return Lattice(recipe, measured_plane, rule, None, None, {}, {}, {})
    heights = field.height_dm.astype(np.float32)
    ground, ground_meta = ground_lattice(field, heights)
    ground, terrain_meta = terrain_lattice(field, ground)
    if "absent" in terrain_meta:
        print(f"  {terrain_meta['absent']}")
    else:
        print(
            f"  landscape from {hf.TERRAIN_NAME}: {terrain_meta['landscape_texels']} texels "
            f"plus {terrain_meta['under_cliff_texels']} under the cliff province"
        )
    print(
        f"  the lattice under the rocks: {ground_meta['lattice_share_of_the_field']}% of "
        f"the field, with {ground_meta['removed_share_of_the_field']}% of it -- the cliff "
        "province -- taken out so the rocks are composited over the ground rather than "
        "over their own 1 m fold"
    )
    return Lattice(recipe, measured_plane, rule, heights, ground, ground_meta, terrain_meta, {})


def rebuilt_lattice(
    lattice: Lattice, field: hf.Field, store: IoStore, art_void: BoolMask | None
) -> Lattice:
    """The lattice with its fill re-read and its holes filled; ``--kernel-only`` as it was."""
    if lattice.ground is None:
        return lattice
    heights, ground, fill_meta = rebuild_lattice(field, lattice.ground, store, art_void)
    seconds = cast(dict[str, float], fill_meta["seconds"])
    holes, pits = cast(JsonObject, fill_meta["holes"]), cast(JsonObject, fill_meta["pits"])
    print(
        f"  lattice rebuilt in {sum(seconds.values()):.0f}s: "
        f"{fill_meta['share_of_the_field_pct']}; {holes['holes']} holes "
        f"filled ({holes['harmonic_fallback']} harmonic), "
        f"{pits['holes']} pits left empty"
    )
    return replace(lattice, heights=heights, ground=ground, fill_meta=fill_meta)


def refuse_stale_layers(
    out_dir: Path, renders_name: str, layers: tuple[str, ...], field_build: str | None
) -> None:
    """Refuse to replace a layer this run cannot show was drawn from the field on disk."""
    for layer in layers:
        directory = layer_dir(out_dir, layer, renders_name)
        if not (directory / TILES_DIR_NAME).is_dir():
            continue
        try:
            existing: JsonValue = json.loads(
                (directory / RENDER_SIDECAR_NAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            existing = {}
        pinned = pinned_field_build(existing if isinstance(existing, dict) else {})
        if pinned != field_build:
            raise Refusal(
                STALE_LAYER,
                f"{directory} already holds a {layer} pyramid and this "
                "run cannot show it was drawn from the field now on disk.\n"
                f"  field on disk: {field_build}\n"
                f"  those tiles:   {pinned or 'no meta.json, or no build recorded in it'}\n"
                "A render from another build is a picture of another world's terrain, "
                "and drift is announced rather than overwritten. Pass --force to "
                "replace it anyway.",
            )


def refuse_restyle_gaps(
    cache_root: Path, grid: RasterGrid, *, top: bool, meshes: bool, titan: bool
) -> None:
    """Refuse a palette-only run whose kept raster caches do not cover it."""
    gaps = restyle_gaps(
        cache_root, grid.size, grid.subsamples, grid.build, top=top, meshes=meshes, titan=titan
    )
    if gaps:
        raise Refusal(
            RESTYLE_MISS,
            f"--restyle: {', '.join(gaps)} under {cache_root} is missing or for another size "
            "or build. Draw once with --cache-dir and --keep-direct to keep it.",
        )


def open_game_inputs(
    game: Path, decoder: BlockDecoder, image_mod: TileImaging, pyooz_version: str
) -> GameInputs:
    """The installed game's container, its script objects, the artwork sheet and the build."""
    if missing := missing_container(game):
        raise Refusal(NO_CONTAINER, missing)
    paks = paks_dir(game)
    print(f"reading the game's own assets from {paks} with pyooz {pyooz_version}")
    store = open_container(game)
    scripts = ScriptObjects(paks, oodle_decompress)
    artwork = read_artwork_sheet(store, decoder, image_mod)
    try:
        _pin, build_raw = installed_build(game)
    except (InstallNotFound, OSError, ValueError):
        build_raw = {}
    return GameInputs(store, scripts, artwork, build_raw, changelist(build_raw))


def artwork_borrow(artwork: Image, field: hf.Field) -> ArtworkBorrow:
    """The artwork's high pass and the coarse provinces it is borrowed into."""
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
    return ArtworkBorrow(detail, province, borrow_metadata(detail_meta, province_meta))


def field_water_source(field: hf.Field) -> str:
    """Where the field's water planes come from, as the sidecar says it."""
    _wet, _measured, source = water_planes(field)
    print(f"  water: {source}")
    return source


def water_record(
    water: WaterSurfaces,
    sea: OpenSea | None,
    river_meta: JsonObject,
    spacing_m: float,
    source: str,
) -> JsonObject:
    """``_meta.render.water`` as every layer says it; the shore's optics are each style's."""
    shore: JsonObject | None = None
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
        "perched": cast(JsonValue, water.perched),
        "level_only": LEVEL_ONLY_TEXT if sea is None else sea.meta,
        "rivers": river_meta or None,
    }


def check_parallel_cutter(
    artwork: Image, image_mod: TileImaging, scratch: Path, workers: int
) -> JsonObject:
    """Cut the artwork serially and in parallel, and refuse the run if the bytes differ.

    The artwork because it is a real picture: a sheet of one colour compares equal however
    the cutter went wrong. Returns the check as the sidecar records it.
    """
    check = check_parallel(np.asarray(artwork, np.uint8), image_mod, scratch, workers)
    print(
        f"  parallel cutter: z0..z{check.levels[-1]}, {check.tiles} tiles, "
        f"{check.seconds_serial}s serial vs {check.seconds_parallel}s on {workers} workers "
        f"({check.speedup}x) -- byte-identical: {check.byte_identical}"
    )
    if not check.byte_identical:
        raise Refusal(
            PARALLEL_MISMATCH,
            "  the parallel cutter does not reproduce the serial one's bytes. That is "
            "the one thing it promises, so nothing is written: " + ", ".join(check.differing_tiles),
        )
    return check.record()


def prepare_paint(
    paint_dir: Path, no_titan_trees: bool, field: hf.Field, biome: BiomeGrid, drawn: list[str]
) -> PaintInputs:
    """The painted layer's ground from the paint store; a run without the store is refused."""
    paint_meta = load_paint_meta(paint_dir)
    if paint_meta is None:
        raise Refusal(
            NO_PAINT,
            f"no paint layers at {paint_dir}, which the painted layer is coloured "
            "from. Extract them once from the installed game:\n"
            "    uv run --extra gen python tools/gen_paint_layers.py",
        )
    started = time.time()
    palette, digest = painted_style(no_titan_trees)
    palette, digest, derived = calibrated_palette(palette, digest, paint_dir, (biome, list(drawn)),
                                                  field)  # fmt: skip
    ground = PaintedGround(paint_dir, palette, field, biome, list(drawn), oil_nodes())
    provenance: JsonObject = {
        "cl": paint_meta.get("cl"),
        "generator_version": paint_meta.get("generator_version"),
        "digest": paint_meta.get("digest"),
    }
    block: JsonObject = {
        "name": f"data/local/{paint_dir.name}/",
        "generator": paint_meta.get("generator"),
        "generator_version": paint_meta.get("generator_version"),
        "digest": paint_meta.get("digest"),
        **ground.provenance(),
        "derived_targets": derived,
        "seconds_to_prepare": round(time.time() - started, 1),
    }
    print(f"  paint layers prepared in {time.time() - started:.0f}s")
    return PaintInputs(ground, digest, provenance, block)
