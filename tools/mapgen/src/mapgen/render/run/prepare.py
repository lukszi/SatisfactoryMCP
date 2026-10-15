"""A renders run's preparation: every stage before the first band is drawn.

The field and its lattices, the game and the artwork's borrow, the biome raster and the paint,
the rasters at the run's spacing, the extras, the water, the relief layer's ground, and the
record every layer's sidecar shares; ``mapgen.commands.renders`` then draws them.
"""

from __future__ import annotations

import argparse
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from mapgen.cache import (
    DIRECT_CACHE_DIR_NAME,
    TOP_CACHE_DIR_NAME,
    DirectPlanes,
    TopPlanes,
    cached_family,
    cached_top_rules,
)
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.install import GameReader
from mapgen.gamedata.rocks.looks import read_rock_textures
from mapgen.gamedata.vegetation.crown_sprites import CROWNS_NAME
from mapgen.gamedata.water.channel import artwork_planes
from mapgen.gamedata.water.ocean import read_ocean_absorption
from mapgen.gamedata.water.river_material import read_river_absorption
from mapgen.palette.painted.albedo import load_paint_meta
from mapgen.palette.painted.ground import PaintedGround
from mapgen.palette.painted.rock_look.atlas import rock_look
from mapgen.palette.painted.rock_look.tilt import RockTilt
from mapgen.palette.relief import ReliefGround
from mapgen.palette.styles import PAINTED_PALETTE, RELIEF_PALETTES, STYLE_DIGESTS
from mapgen.palette.water.footprints.plane import TexelPlanes, mesh_land
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.rivers import water_sources
from mapgen.palette.water.surface import WaterSurfaces, drawn_water
from mapgen.render.run.biome_inputs import BiomeInputs, read_biome_inputs
from mapgen.render.run.cached_rasters import LevelSweep, RasterGrid, direct_raster, top_raster
from mapgen.render.run.extras import RenderExtras, load_extras
from mapgen.render.run.inputs import (
    GameInputs,
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
    require_paint,
    water_record,
)
from mapgen.render.run.sprites import crown_sprites
from mapgen.sprites.store import SpriteAtlas
from mapgen.terrain.ground_detail.textures import (
    DETAIL_MAX_SPACING_M,
    DetailTextures,
    load_detail_textures,
)
from mapgen.tiles.imaging import TileImaging
from mapgen.tiles.layer_meta import RenderFacts, RunRecord
from satisfactory_mcp.core.arrays import I8Grid, U8Grid
from satisfactory_mcp.core.gameassets.imaging import BlockDecoder
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts
from satisfactory_mcp.core.gameassets.provenance import changelist
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject, require_object
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["BIOME_LAYERS", "Prepared", "Setup", "prepare"]

#: The layers drawn from the biome raster, which is read only when one of them is drawn.
BIOME_LAYERS = ("painted",)


@dataclass(frozen=True)
class Setup:
    """How a run reads the game and cuts its tiles, and where its raster caches go."""

    cache_root: Path
    decoder: BlockDecoder
    image_mod: TileImaging
    versions: dict[str, str]
    cut_workers: int


@dataclass(frozen=True)
class Prepared:
    """What every layer of a run is drawn from, and what its sidecars say alike.

    It holds the raster caches' memory maps: the run lets it go before removing them.
    """

    field: hf.Field
    lattice: Lattice
    borrow: tuple[I8Grid, U8Grid]
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
    textures: DetailTextures | None = None
    #: The crown sprites, read where the painted layer is drawn or the light casts the trees.
    sprites: SpriteAtlas | None = None
    #: The rock look's normal maps, where the light carries the detail normal.
    rock_tilt: RockTilt | None = None

    @property
    def painted(self) -> PaintedGround | None:
        return None if self.paint is None else self.paint.ground


@dataclass
class _Gathered:
    """What the stages have gathered so far: the sidecars' inputs and parallel check."""

    inputs: dict[str, JsonObject]
    parallel_check: JsonObject | None = None


def prepare(args: argparse.Namespace, layers: tuple[str, ...], setup: Setup) -> Prepared:
    """Every stage before the first layer is drawn, in the order the run reports them."""
    field = load_field(args.field)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
    lattice = field_lattice(field, spacing_m, args.kernel_only)
    if not args.force:
        refuse_stale_layers(args.out_dir, args.renders_name, layers, field.build)
    grid = RasterGrid(args.size, args.direct_subsamples, field.build, not args.quiet)
    if args.restyle and lattice.measured_plane is not None:
        titan = "painted" in layers and not args.no_titan_trees
        refuse_restyle_gaps(
            setup.cache_root, grid, top=not args.no_top, meshes=not args.no_meshes, titan=titan
        )
    game = open_game_inputs(args.game, setup.decoder, setup.image_mod, setup.versions["pyooz"])
    gathered = _Gathered(
        {"heightfield": field_input(field, args.kernel_only), "artwork_sheet": game.artwork_input()}
    )
    borrow = artwork_borrow(game.artwork, field)
    water_source = field_water_source(field)
    art_water, art_void = (None, None) if args.kernel_only else artwork_planes(game.artwork)
    lattice = rebuilt_lattice(lattice, field, game.store, art_void)
    if args.check_parallel:
        scratch = args.out_dir / "parallel.check"
        gathered.parallel_check = check_parallel_cutter(
            game.artwork, setup.image_mod, scratch, setup.cut_workers
        )
    level = LevelSweep(game.store, game.scripts, not args.quiet)
    found = _biome_and_paint(args, layers, (setup, game, level), field, gathered)
    biome, paint, style_digests, sprites = found
    if paint is not None:
        _attach_look(paint, game, spacing_m, gathered)
        _attach_sea(paint, game, level.index, gathered)
        _attach_river(paint, game, level.index, gathered)
    textures = _detail_textures(args, layers, spacing_m, paint)
    direct, top, raster_sources = _rasters(args, setup, lattice, level, grid, paint, gathered)
    tilt = _rock_tilt(args, (setup, game, gathered), grid, paint, textures is not None)
    extras = _extras(args, setup, lattice, level, field, paint, gathered)
    water, sea, planes = drawn_water(
        field,
        args.kernel_only,
        extras.rivers,
        (lattice.heights, lattice.ground),
        art_water,
        extras.lips,
    )
    footprints = _mesh_footprints(args.size, field, lattice, (water, sea), extras)
    if paint is not None:
        paint.block["water_classes"] = paint.ground.classify_water(field, planes)
    relief = {
        layer: ReliefGround(
            RELIEF_PALETTES[layer][0],
            field,
            biome.raster,
            list(biome.drawn),
            planes,
            lattice.heights,
        )
        for layer in layers
        if layer in RELIEF_PALETTES
    }
    water_facts = water_record(water, sea, extras.river_meta, spacing_m, water_source)
    if footprints is not None:
        water_facts["mesh_footprints"] = footprints
    facts = RenderFacts(
        size=args.size,
        spacing_m=spacing_m,
        subsamples=args.direct_subsamples,
        two_regime=direct is not None,
        composition=lattice.composition(top_overlay=top is not None),
        water=water_facts,
        cut_workers=setup.cut_workers,
        parallel_check=gathered.parallel_check,
        pillow_version=setup.versions["pillow"],
    )
    record = RunRecord(
        render=facts,
        recipe=lattice.recipe,
        kernel_only=args.kernel_only,
        field_meta=field.meta,
        build_raw=game.build_raw,
        inputs=gathered.inputs,
        sources={**borrow.source, **raster_sources, **extras.mesh_source},
        biome_source=biome.source,
        paint_source={} if paint is None else {"paint": paint.block, **extras.titan_source},
    )
    return Prepared(
        field,
        lattice,
        borrow.planes,
        biome,
        paint,
        direct,
        top,
        extras,
        water,
        sea,
        relief,
        style_digests,
        record,
        textures,
        sprites,
        tilt if direct is not None else None,
    )


def _detail_textures(
    args: argparse.Namespace, layers: tuple[str, ...], spacing_m: float, paint: PaintInputs | None
) -> DetailTextures | None:
    """The landscape layers' textures for the ground's detail, where the painted layer is drawn
    or the light captures the ground, and the pixel is finer than the bake's metre; what the
    painted sidecar says of them."""
    if spacing_m >= DETAIL_MAX_SPACING_M or not ("painted" in layers or args.light):
        return None
    started = time.time()
    textures = load_detail_textures(args.paint_dir, spacing_m)
    if textures is None:
        print(f"  no layer textures in {args.paint_dir} (paint generator 5): no ground detail")
        return None
    seconds = time.time() - started
    print(f"  ground detail: {len(textures.layers)} layers' textures in {seconds:.0f}s")
    if paint is not None:
        paint.block["ground_detail"] = textures.provenance(seconds)
    return textures


def _rock_tilt(
    args: argparse.Namespace,
    readers: tuple[Setup, GameInputs, _Gathered],
    grid: RasterGrid,
    paint: PaintInputs | None,
    detail: bool,
) -> RockTilt | None:
    """The rock look's normal maps for a light that carries the ``detail`` normal, whatever
    layers the run draws: the painted ground's look, else the install's (its normal maps need
    no paint store), and the direct pass's family plane. None where the look did not read."""
    if not (args.light and detail):
        return None
    setup, game, gathered = readers
    strength = float(PAINTED_PALETTE["rock_look"]["shade"])
    if paint is not None:
        look = paint.ground.rock_look
        strength = float(paint.ground.palette["rock_look"]["shade"])
    else:
        try:
            look = rock_look(read_rock_textures(game.store, game.scripts, {}), grid.spacing_m)
        except (KeyError, ValueError, StopIteration):
            return None
        gathered.inputs["rock_textures"] = {
            "cl": game.build_cl,
            "reader_version": READER_VERSIONS["rock_textures"],
        }
    if look is None:
        return None
    family = cached_family(setup.cache_root / DIRECT_CACHE_DIR_NAME, grid.stamp)
    return RockTilt(look, family, strength)


def _biome_and_paint(
    args: argparse.Namespace,
    layers: tuple[str, ...],
    readers: tuple[Setup, GameInputs, LevelSweep],
    field: hf.Field,
    gathered: _Gathered,
) -> tuple[BiomeInputs, PaintInputs | None, dict[str, str], SpriteAtlas | None]:
    """The biome raster when a layer is drawn from it, the paint and the crown sprites when
    the painted layer is drawn, each style's digest with the paint's in the painted layer's,
    and the crown sprites, which a lit run reads for the light whatever it draws."""
    setup, game, level = readers
    biome = BiomeInputs()
    if any(layer in BIOME_LAYERS for layer in layers):
        biome = read_biome_inputs(
            game.store,
            game.scripts,
            game.artwork,
            setup.image_mod,
            game.build_cl,
            setup.versions["pyooz"],
        )
        gathered.inputs["biome_raster"] = biome.provenance or {}
    style_digests = dict(STYLE_DIGESTS)
    paint = None

    def reader() -> GameReader:
        index = level.index
        return GameReader(game.store, game.scripts, index, ClassFacts(game.store, index))

    def sprites() -> tuple[SpriteAtlas, JsonObject]:
        return crown_sprites(args.sprites_dir, args.paint_dir, args.game, reader, lambda: level.sweep)

    if "painted" in layers and biome.raster is not None:
        require_paint(args.paint_dir)
        found = sprites()
        drawn = (biome.raster, list(biome.drawn))
        paint = prepare_paint(args.paint_dir, args.no_titan_trees, field, drawn, found)
        gathered.inputs["paint"], style_digests["painted"] = paint.provenance, paint.digest
        return biome, paint, style_digests, found[0]
    meta = load_paint_meta(args.paint_dir)
    if args.light and meta is not None and CROWNS_NAME in meta["files"]:
        return biome, paint, style_digests, sprites()[0]
    return biome, paint, style_digests, None


def _attach_look(
    paint: PaintInputs, game: GameInputs, spacing_m: float, gathered: _Gathered
) -> None:
    """The rocks' textures from the install on the painted ground, and the reader among the
    inputs; a build whose textures cannot be read draws its rock flat and says why."""
    families = paint.ground.meta.get("rock_families") or {}
    tops = {name: entry.get("top_texture") for name, entry in families.items()}
    try:
        textures = read_rock_textures(game.store, game.scripts, tops)
    except (KeyError, ValueError, StopIteration) as exc:
        paint.block["rock_look"] = f"not drawn: the rock textures did not read ({exc!r})"
        return
    paint.ground.attach_look(textures, spacing_m)
    paint.block["rock_look"] = paint.ground.source["rock_look"]
    gathered.inputs["rock_textures"] = {
        "cl": game.build_cl,
        "reader_version": READER_VERSIONS["rock_textures"],
    }


def _attach_sea(
    paint: PaintInputs, game: GameInputs, index: AssetIndex, gathered: _Gathered
) -> None:
    """The ocean material's absorption from the install on the painted ground, and the reader
    among the inputs; where it cannot be read the sea takes the palette's ``k_per_m``."""
    try:
        absorption = read_ocean_absorption(game.store, game.scripts, index)
    except (KeyError, ValueError, IndexError, struct.error) as exc:
        paint.block["sea_absorption"] = f"the palette's k_per_m: the ocean did not read ({exc!r})"
        return
    paint.ground.attach_sea(absorption)
    paint.block["sea_absorption"] = paint.ground.source["sea_absorption"]
    gathered.inputs["ocean_absorption"] = {
        "cl": game.build_cl,
        "reader_version": READER_VERSIONS["ocean_absorption"],
    }


def _attach_river(
    paint: PaintInputs, game: GameInputs, index: AssetIndex, gathered: _Gathered
) -> None:
    """The river material's absorption from the install on the painted ground, and the reader
    among the inputs; where it cannot be read the river takes the palette's ``k_per_m``."""
    try:
        absorption = read_river_absorption(game.store, game.scripts, index)
    except (KeyError, ValueError, IndexError, struct.error) as exc:
        paint.block["river_absorption"] = f"the palette's k_per_m: the river did not read ({exc!r})"
        return
    paint.ground.attach_river(absorption)
    paint.block["river_absorption"] = paint.ground.source["river_absorption"]
    gathered.inputs["river_absorption"] = {
        "cl": game.build_cl,
        "reader_version": READER_VERSIONS["river_absorption"],
    }


def _rasters(
    args: argparse.Namespace,
    setup: Setup,
    lattice: Lattice,
    level: LevelSweep,
    grid: RasterGrid,
    paint: PaintInputs | None,
    gathered: _Gathered,
) -> tuple[DirectPlanes | None, TopPlanes | None, JsonObject]:
    """The rocks and the top overlay on ``grid``, swept from ``level``, and their sidecar
    blocks. None for ``--kernel-only``, which opens no geometry.
    """
    if lattice.measured_plane is None or lattice.ground is None:
        return None, None, {}
    inputs = gathered.inputs
    cache = setup.cache_root / DIRECT_CACHE_DIR_NAME
    rock, sources = direct_raster(level, cache, grid, setup.versions["pyooz"])
    direct = DirectPlanes(
        rock.z, rock.coverage, lattice.ground, args.direct_subsamples, rock.under, rock.floor
    )
    inputs["cliff_geometry"] = {
        "cl": changelist(grid.build),
        "reader_version": READER_VERSIONS["cliff_geometry"],
    }
    if paint is not None:
        paint.ground.attach_families(cached_family(cache, grid.stamp))
        paint.ground.attach_top_rules(cached_top_rules(cache, grid.stamp))
        for name in ("rock_families",) + (() if args.no_titan_trees else ("titan_trees",)):
            inputs[name] = {**inputs["cliff_geometry"], "reader_version": READER_VERSIONS[name]}
    top = None
    if not args.no_top:
        top, top_source = top_raster(level, setup.cache_root / TOP_CACHE_DIR_NAME, grid)
        sources = {**sources, **top_source}
    return direct, top, sources


def _mesh_footprints(
    size: int,
    field: hf.Field,
    lattice: Lattice,
    drawn: tuple[WaterSurfaces, OpenSea | None],
    extras: RenderExtras,
) -> JsonObject | None:
    """The meshes' land plane put on ``extras.meshes``, from the water and ground the bands
    draw, where the run draws the meshes and the ocean's reach; its record, else None."""
    (water, sea), meshes, ground = drawn, extras.meshes, lattice.heights
    if meshes is None or water.reach is None or ground is None:
        return None
    level, grades = (water.level, None) if sea is None else sea.planes
    level, wet, _measured = water_sources(field, extras.rivers, level, grades)
    if level is None or wet is None:
        return None
    land, record = mesh_land(meshes, field, size, TexelPlanes(wet, water.reach, ground, level))
    extras.meshes = meshes._replace(land=land)
    coral, terraces = (require_object(record[name]) for name in ("coral_and_shells", "terraces"))
    print(
        f"  mesh footprints: {coral['on_land_and_in_the_sea']} coral and shell footprints "
        f"standing in the sea kept whole on land, {coral['in_the_sea']} left whole to the "
        f"seabed; terraces {terraces['on_land_and_in_the_sea']} and {terraces['in_the_sea']}"
    )
    return record


def _extras(
    args: argparse.Namespace,
    setup: Setup,
    lattice: Lattice,
    level: LevelSweep,
    field: hf.Field,
    paint: PaintInputs | None,
    gathered: _Gathered,
) -> RenderExtras:
    """What the run draws beside the field, the Titan trees attached to the paint, and each
    reader's version among the inputs."""
    two_regime = lattice.measured_plane is not None
    extras = load_extras(
        setup.cache_root,
        args.size,
        field.build,
        level,
        field,
        meshes=two_regime and not args.no_meshes,
        titan=two_regime and paint is not None and not args.no_titan_trees,
        rivers=not args.kernel_only,
        quiet=args.quiet,
    )
    if extras.titan is not None and paint is not None:
        paint.ground.attach_titan(extras.titan)
    for name in extras.readers:
        gathered.inputs[name] = {
            "cl": changelist(field.build),
            "reader_version": READER_VERSIONS[name],
        }
    return extras
