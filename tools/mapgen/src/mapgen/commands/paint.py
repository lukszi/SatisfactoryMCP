"""Extract the landscape's paint layers once, into ``data/local/paint/``.

    uv run --extra gen python tools/gen_paint_layers.py

The game-painted satellite style colours the ground from these planes: one uint8 weight plane
per paint layer on the heightfield's 1 m grid, the tree canopy cover, the PigmentMap tint
texture, and the albedo of every layer as the game's own textures and material parameters
state it. A palette change never re-reads the install; a new game build does.
docs/map/painted.md section 27 describes the planes and how they are drawn.
"""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast

import numpy as np

from mapgen.common import base_parser, require_gen
from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.ground.bake import BAKE_NAME, fit_layer_table, read_bake
from mapgen.gamedata.ground.landscape_albedo import (
    CANOPY_TEXTURE,
    GAME_ROOT,
    LAYERS,
    MATERIAL,
    OVERLAYS,
    PIGMENT,
    PIGMENT_MAX_PX,
    ROCK_TEXTURES,
    TEXTURES,
    decode_texture,
    layer_albedo,
    material_vectors,
    rock_family_colours,
    srgb_to_linear,
)
from mapgen.gamedata.ground.paint_store import (
    CANOPY_NAME,
    GRID,
    META_NAME,
    PAINT_DIR,
    PIGMENT_NAME,
    WEIGHT_PREFIX,
    WEIGHT_SUFFIX,
)
from mapgen.gamedata.ground.weightmaps import (
    WEIGHTMAP_PX,
    component_layers,
    component_origin,
    place_component_layers,
)
from mapgen.gamedata.install import GameReader, open_game
from mapgen.gamedata.level.lighting import (
    VOLUME_CLASS,
    AtmosphereVolume,
    level_volumes,
    persistent_lighting,
)
from mapgen.gamedata.level.sweep import FOLIAGE_CLASSES, LEVEL_DIR, LEVEL_SUFFIX, foliage_instances
from mapgen.gamedata.meshes import MeshBounds
from mapgen.gamedata.vegetation import crown_sprites
from mapgen.gamedata.vegetation.carpet import is_carpet, write_carpet
from mapgen.gamedata.vegetation.trees import canopy_cover, is_tree
from mapgen.gamedata.water.bodies import WATER_BODIES_NAME, WaterBodies, collect_water_bodies
from satisfactory_mcp.core.arrays import F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import PackageView, class_name_of
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    changelist,
    files_digest,
    install_directory,
    installed_build,
    sha256_hex,
)
from satisfactory_mcp.core.gameassets.versions import PAINT_GENERATOR_VERSION
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue, to_json
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "GENERATOR_VERSION",
    "PaintSweep",
    "SatelliteInputs",
    "TextureInputs",
    "crown_payload",
    "main",
    "satellite_inputs",
    "sweep_paint_levels",
]


GENERATOR_VERSION = PAINT_GENERATOR_VERSION

#: Packages walked between progress lines.
_PROGRESS_EVERY = 500

#: The shell meshes' materials, whose mean colours calibrate reads.
SHELL_MATERIALS = (
    "/Game/FactoryGame/World/Environment/Foliage/Coral/BigShell/Materials/MI_Bigshell_01",
    "/Game/FactoryGame/World/Environment/Foliage/Coral/PlateauShell/Materials/PlateauShell_Inst",
    "/Game/FactoryGame/World/Environment/Foliage/Coral/SmallShell/Materials/SmallShell_Inst",
)


@dataclass
class PaintSweep:
    """One walk of every level: weight planes, component origins, trees and water bodies."""

    planes: dict[str, U8Grid] = field(default_factory=dict[str, U8Grid])
    origins: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    trees: dict[str, F32Grid] = field(default_factory=dict[str, F32Grid])
    water_bodies: WaterBodies = field(
        default_factory=lambda: WaterBodies(actors=[], hot_springs=[])
    )
    carpet: dict[str, F32Grid] = field(default_factory=dict[str, F32Grid])
    volumes: list[AtmosphereVolume] = field(default_factory=list[AtmosphereVolume])
    unreadable: int = 0
    failed_packages: int = 0
    seconds: float = 0.0


@dataclass(frozen=True)
class TextureInputs:
    """The landscape material's vectors, each texture's mean linear albedo, and the pigment."""

    vectors: dict[str, tuple[float, ...]]
    means: dict[str, list[float]]
    pigment: U8Grid


@dataclass(frozen=True)
class SatelliteInputs:
    """The baked ground colour's store files, its meta, the refitted table, rock families."""

    payload: dict[str, bytes]
    files: dict[str, JsonObject]
    bake: JsonObject
    layers_bake_fit: dict[str, list[float]]
    rock_families: dict[str, JsonObject]


def satellite_inputs(
    game: GameReader,
    decoder: ModuleType,
    image_mod: ModuleType,
    planes: Mapping[str, U8Grid],
    table: Mapping[str, list[float]],
) -> SatelliteInputs:
    """The baked ground colour, its refitted layer table and the rock families.

    The crown tops come from ``crown_payload``.
    """
    started = time.time()
    bake, bake_stats = read_bake(game, decoder, image_mod, GRID)
    blended = {name: plane for name, plane in planes.items() if name in table}
    fit, fit_stats = fit_layer_table(blended, table, bake)
    families = rock_family_colours(game, decoder)
    return SatelliteInputs(
        payload={BAKE_NAME: hf.encode_u8(bake.reshape(GRID, -1))},
        files={BAKE_NAME: {"shape": [GRID, GRID, 3], "kind": "u8", "srgb": True, "role": "bake"}},
        bake={**bake_stats, "fit": fit_stats, "seconds": round(time.time() - started, 1)},
        layers_bake_fit=fit,
        rock_families=families,
    )


def _place_components(found: PaintSweep, view: PackageView, bulk: bytes) -> None:
    """One level's landscape components: their weights placed on the grid, their origins kept."""
    for base_x, base_y, layers in component_layers(view, bulk):
        row, col = component_origin(base_x, base_y)
        place_component_layers(found.planes, row, col, layers, GRID)
        found.origins.append((row, col))


def sweep_paint_levels(
    game: GameReader, progress: bool, meshes: MeshBounds | None = None
) -> PaintSweep:
    """One walk of every level: weight planes, component origins, trees and water bodies."""
    found = PaintSweep()
    trees: dict[str, list[F32Grid]] = {}
    carpet: dict[str, list[F32Grid]] = {}
    started = time.time()

    def skip(_path: str, _exc: Exception) -> None:
        found.unreadable += 1

    def wanted(mesh: str) -> bool:
        return is_tree(mesh) or is_carpet(mesh)

    paths = level_paths(game.store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)
    walk = walk_levels(game.store, game.scripts, paths=paths, on_unreadable=skip)
    for index, total, path, view in walk:
        classes_here = {class_name_of(c) for c in view.class_of.values()}
        if "LandscapeComponent" in classes_here:
            try:
                bulk = game.store.read_path(path[: -len(LEVEL_SUFFIX)] + ".ubulk")
                _place_components(found, view, bulk)
            except (KeyError, ValueError, struct.error):
                found.failed_packages += 1
        if classes_here & FOLIAGE_CLASSES:
            for slot, class_path in view.class_of.items():
                if class_name_of(class_path) not in FOLIAGE_CLASSES:
                    continue
                instances = foliage_instances(view, slot, game.classes, wanted=wanted)
                if instances is not None:
                    into = carpet if is_carpet(instances[0]) else trees
                    into.setdefault(instances[0], []).append(instances[1].astype(np.float32))
        if meshes is not None:
            collect_water_bodies(view, game.classes, meshes, found.water_bodies)
        if VOLUME_CLASS in classes_here:
            found.volumes += level_volumes(view, path.rsplit("/", 1)[-1], game.classes)
        if progress and index % _PROGRESS_EVERY == 0:
            print(
                f"  {index}/{total} packages, {len(found.origins)} components, "
                f"{time.time() - started:.0f}s",
                flush=True,
            )
    found.trees = {mesh: np.concatenate(parts) for mesh, parts in trees.items()}
    found.carpet = {mesh: np.concatenate(parts) for mesh, parts in carpet.items()}
    found.seconds = round(time.time() - started, 1)
    return found


def crown_payload(
    game: GameReader, decoder: ModuleType, trees: Mapping[str, F32Grid]
) -> tuple[dict[str, tuple[bytes, JsonObject]], JsonObject, dict[str, float]]:
    """The crown files ``{name: (bytes, files entry)}``, their meta block, measured radii."""
    started = time.time()

    def texture_rgba(path: str) -> U8Grid:
        asset = path.split(".")[0].removeprefix("/Game/FactoryGame/")
        return decode_texture(game, decoder, asset, 256, channels=4)

    built = crown_sprites.build_crowns(game, trees, texture_rgba)
    sprite_blob, sprite_index = crown_sprites.encode_sprites(built.sprites)
    half = SPACING_CM / 2
    top_cm = crown_sprites.stamp_tops(
        built.records, built.sprites, GRID, ORIGIN_X_CM - half, ORIGIN_Y_CM - half, SPACING_CM
    )
    top_dm = np.where(np.isfinite(top_cm), np.round(top_cm / 10.0), hf.NODATA).astype(np.int16)
    record_fields: list[JsonValue] = [
        [str(part) for part in field] for field in crown_sprites.CROWN_RECORD.descr
    ]
    files: dict[str, tuple[bytes, JsonObject]] = {
        crown_sprites.CROWNS_NAME: (
            crown_sprites.encode_records(built.records),
            {"kind": "records", "count": len(built.records), "dtype": record_fields},
        ),
        crown_sprites.SPRITES_NAME: (
            sprite_blob,
            {"kind": "sprites", "texel_m": crown_sprites.SPRITE_M},
        ),
        crown_sprites.CROWN_TOP_NAME: (
            hf.encode_i16(top_dm),
            {"shape": [GRID, GRID], "kind": "i16", "unit": "dm", "role": "crown top"},
        ),
    }
    for entry, sprite in zip(built.species, sprite_index, strict=True):
        entry["sprite"] = sprite
    radii = {e["mesh"]: e["radius_m"] for e in built.species}
    meta: JsonObject = {
        # A CrownSpecies holds JSON values only.
        "species": cast(list[JsonValue], built.species),
        "skipped": dict(built.skipped),
        "instances": built.stats["instances"],
        "tilt_max_deg": built.stats["tilt_max_deg"],
        "top_texels": int(np.isfinite(top_cm).sum()),
        "seconds": round(time.time() - started, 1),
    }
    print(
        f"  {len(built.species)} crown sprites, {built.stats['instances']} trees, "
        f"{len(sprite_blob) / 1e6:.1f} MB of sprites in {meta['seconds']}s"
    )
    return files, meta, radii


def _texture_inputs(game: GameReader, decoder: ModuleType) -> TextureInputs:
    """The landscape material's vectors, every listed texture's mean, and the pigment map."""
    material = PackageView(game.store.read_path(GAME_ROOT + MATERIAL + ".uasset"), game.scripts)
    means = {
        name: srgb_to_linear(decode_texture(game, decoder, asset, 512))
        .reshape(-1, 3)
        .mean(0)
        .tolist()
        for name, asset in TEXTURES.items()
    }
    pigment = decode_texture(game, decoder, PIGMENT, PIGMENT_MAX_PX)
    return TextureInputs(material_vectors(material), means, pigment)


def _plane_files(
    found: PaintSweep, canopy: F32Grid
) -> tuple[dict[str, bytes], dict[str, JsonObject]]:
    """The weight planes, sorted by layer, and the canopy cover, encoded with their entries."""
    payload: dict[str, bytes] = {}
    files: dict[str, JsonObject] = {}
    for name, plane in sorted(found.planes.items()):
        payload[WEIGHT_PREFIX + name + WEIGHT_SUFFIX] = hf.encode_u8(plane)
        files[WEIGHT_PREFIX + name + WEIGHT_SUFFIX] = {
            "shape": [GRID, GRID],
            "kind": "u8",
            "layer": name,
        }
    payload[CANOPY_NAME] = hf.encode_u8(np.round(canopy * 255).astype(np.uint8))
    files[CANOPY_NAME] = {"shape": [GRID, GRID], "kind": "u8", "scale": 255}
    return payload, files


def _store_payload(
    parts: list[tuple[dict[str, bytes], dict[str, JsonObject]]],
) -> tuple[dict[str, bytes], dict[str, JsonObject]]:
    """Every file of the store in the order given, each entry with its hash and size."""
    payload: dict[str, bytes] = {}
    files: dict[str, JsonObject] = {}
    for blobs, entries in parts:
        payload.update(blobs)
        files.update(entries)
    for name, blob in payload.items():
        files[name]["sha256"] = sha256_hex(blob)
        files[name]["bytes"] = len(blob)
    return payload, files


def _albedo_block(
    textures: TextureInputs, layers: dict[str, list[float]], satellite: SatelliteInputs
) -> JsonObject:
    """The linear albedo of every layer, as named and as refitted, the overlays, rock, canopy."""
    means = textures.means
    rock: list[JsonValue] = np.mean([means[t] for t in ROCK_TEXTURES], axis=0).round(5).tolist()
    canopy: list[JsonValue] = [round(v, 5) for v in means[CANOPY_TEXTURE]]
    overlays = layer_albedo(OVERLAYS, means, textures.vectors)
    return {
        "layers": _json_rows(layers),
        "layers_bake_fit": _json_rows(satellite.layers_bake_fit),
        "overlays": _json_rows(overlays),
        "rock": rock,
        "canopy": canopy,
    }


def _json_rows(table: Mapping[str, Sequence[float]]) -> JsonObject:
    """A ``{name: albedo}`` table as JSON values."""
    rows: JsonObject = {}
    for name, row in table.items():
        values: list[JsonValue] = list(row)
        rows[name] = values
    return rows


@dataclass(frozen=True)
class _StoreParts:
    """What ``_paint_meta`` writes beside the files, gathered from the run's stages."""

    found: PaintSweep
    textures: TextureInputs
    satellite: SatelliteInputs
    layers: dict[str, list[float]]
    crowns: JsonObject
    tree_counts: dict[str, int]
    carpet: JsonObject
    daylight: JsonObject


def _paint_meta(
    build: tuple[str, JsonObject, str | None],
    files: dict[str, JsonObject],
    parts: _StoreParts,
    started: float,
) -> JsonObject:
    """``meta.json``: provenance, the grid, every file, the albedo tables and the counts."""
    pin, raw, pyooz = build
    found, textures = parts.found, parts.textures
    sha256s = {name: str(entry["sha256"]) for name, entry in files.items()}
    unknown: list[JsonValue] = [
        name for name in sorted(set(found.planes) - set(LAYERS) - set(OVERLAYS))
    ]
    return {
        "generator": "tools/gen_paint_layers.py",
        "generator_version": GENERATOR_VERSION,
        "transcribed": datetime.now(UTC).date().isoformat(),
        "cl": changelist(raw),
        "sources": {"game": {"game_version_pinned": pin, "game_version_raw": raw, "pyooz": pyooz}},
        "grid": {
            "width": GRID,
            "height": GRID,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "spacing_cm": SPACING_CM,
        },
        "files": dict(files),
        "digest": files_digest(sha256s),
        "albedo_linear": _albedo_block(textures, parts.layers, parts.satellite),
        "texture_means_linear": {k: [round(v, 5) for v in m] for k, m in textures.means.items()},
        "material_vectors": {k: list(v) for k, v in textures.vectors.items()},
        "unknown_layers": unknown,
        "components": [list(origin) for origin in sorted(found.origins)],
        "component_px": WEIGHTMAP_PX,
        "trees": dict(parts.tree_counts),
        "crowns": parts.crowns,
        "bake": parts.satellite.bake,
        "rock_families": dict(parts.satellite.rock_families),
        "carpet": parts.carpet,
        **parts.daylight,
        "counts": {"unreadable": found.unreadable, "failed_packages": found.failed_packages},
        "seconds": round(time.time() - started, 1),
    }


def _daylight(game: GameReader, decoder: ModuleType, volumes: list[AtmosphereVolume]) -> JsonObject:
    """The level's noon light, the atmosphere volumes and the shell materials' colours."""
    lighting, missing = persistent_lighting(game)
    if lighting is None:
        print(f"  no daylight in the persistent level: {', '.join(missing)} unread")

    def texture_rgba(path: str) -> U8Grid:
        asset = path.split(".")[0].removeprefix("/Game/FactoryGame/")
        return decode_texture(game, decoder, asset, 256, channels=4)

    shells = {p: crown_sprites.material_colour(game, p, texture_rgba) for p in SHELL_MATERIALS}
    print(f"  daylight {'read' if lighting else 'missing'}, {len(volumes)} atmosphere volumes")
    return {
        "lighting": to_json(lighting),
        "atmosphere_volumes": to_json(volumes),
        "mesh_materials": to_json(shells),
    }


def _parse_args() -> tuple[Path, Path, bool]:
    parser = base_parser((__doc__ or "").splitlines()[0])
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=PAINT_DIR,
        help="destination directory (gitignored)",
    )
    parser.add_argument("--quiet", action="store_true", help="no progress lines")
    args = parser.parse_args()
    return args.game, args.out_dir, args.quiet


def main() -> int:
    game_dir, out_dir, quiet = _parse_args()
    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder
    from PIL import Image as image_mod

    try:
        pin, raw = installed_build(game_dir)
    except (InstallNotFound, OSError, ValueError) as exc:
        print(f"not a game install: {exc}")
        return 1
    game = open_game(game_dir)
    started = time.time()
    textures = _texture_inputs(game, decoder)
    print(
        f"  {len(textures.means)} textures, {len(textures.vectors)} material vectors, "
        f"pigment {textures.pigment.shape[0]} px"
    )

    found = sweep_paint_levels(game, not quiet, MeshBounds(game.store, game.scripts, game.index))
    if not found.origins:
        print("no LandscapeComponent was read; the landscape moved or the format changed")
        return 1
    crown_files, crown_meta, radii = crown_payload(game, decoder, found.trees)
    canopy, tree_counts = canopy_cover(found.trees, GRID, radii)
    print(
        f"  {len(found.origins)} components, layers {sorted(found.planes)}, "
        f"{sum(tree_counts.values())} trees, swept in {found.seconds}s"
    )
    layers = layer_albedo(LAYERS, textures.means, textures.vectors)
    satellite = satellite_inputs(game, decoder, image_mod, found.planes, layers)
    bodies = found.water_bodies
    carpet_blobs, carpet_files, carpet_meta = write_carpet(found.carpet, game, GRID)
    payload, files = _store_payload(
        [
            (
                {n: blob for n, (blob, _e) in crown_files.items()},
                {n: entry for n, (_b, entry) in crown_files.items()},
            ),
            _plane_files(found, canopy),
            (satellite.payload, satellite.files),
            (
                {
                    PIGMENT_NAME: hf.encode_u8(
                        textures.pigment.reshape(textures.pigment.shape[0], -1)
                    )
                },
                {
                    PIGMENT_NAME: {
                        "shape": list(textures.pigment.shape),
                        "kind": "u8",
                        "srgb": True,
                        "placement": "the render frame, texel centres",
                    }
                },
            ),
            (
                {WATER_BODIES_NAME: json.dumps(bodies, separators=(",", ":")).encode("utf-8")},
                {
                    WATER_BODIES_NAME: {
                        "kind": "json",
                        "actors": len(bodies["actors"]),
                        "hot_springs": len(bodies["hot_springs"]),
                    }
                },
            ),
            (carpet_blobs, carpet_files),
        ]
    )
    daylight = _daylight(game, decoder, found.volumes)
    parts = _StoreParts(
        found, textures, satellite, layers, crown_meta, tree_counts, carpet_meta, daylight
    )
    meta = _paint_meta((pin, raw, versions.get("pyooz")), files, parts, started)
    payload[META_NAME] = json.dumps(meta, indent=1).encode("utf-8")
    written = install_directory(out_dir, payload)
    unknown = meta["unknown_layers"]
    print(
        f"wrote {out_dir}: {len(written)} files, {sum(written.values()) / 1e6:.1f} MB "
        f"in {time.time() - started:.0f}s; unknown layers {unknown or 'none'}"
    )
    return 0
