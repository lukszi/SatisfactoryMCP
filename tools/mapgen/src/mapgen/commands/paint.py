"""Extract the landscape's paint layers once, into ``data/local/paint/``.

    uv run --extra gen python tools/gen_paint_layers.py

The game-painted satellite style colours the ground from these planes: one uint8 weight plane
per paint layer on the heightfield's 1 m grid, the tree canopy cover, the PigmentMap tint
texture, and the albedo of every layer as the game's own textures and material parameters
state it. A palette change never re-reads the install; a new game build does.
docs/spatial-and-map.md section 27 describes the planes and how they are drawn.
"""

from __future__ import annotations

import json
import struct
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from mapgen.common import ROOT, base_parser, require_gen
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
    PAINT_DIR_NAME,
    PIGMENT_NAME,
    WEIGHT_PREFIX,
    WEIGHT_SUFFIX,
)
from mapgen.gamedata.ground.weightmaps import (
    WEIGHTMAP_PX,
    component_layers,
    component_origin,
    place,
)
from mapgen.gamedata.install import open_game
from mapgen.gamedata.level.sweep import FOLIAGE_CLASSES, LEVEL_DIR, LEVEL_SUFFIX, foliage_instances
from mapgen.gamedata.meshes import MeshBounds
from mapgen.gamedata.vegetation import crown_sprites as crown_data
from mapgen.gamedata.vegetation.carpet import is_carpet, write_carpet
from mapgen.gamedata.vegetation.trees import canopy_cover, is_tree
from mapgen.gamedata.water.bodies import WATER_BODIES_NAME, harvest
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import AssetIndex, PackageView, class_name_of
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    changelist,
    files_digest,
    install_directory,
    installed_build,
    sha256_hex,
)
from satisfactory_mcp.core.gameassets.versions import PAINT_GENERATOR_VERSION
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "GENERATOR_VERSION",
    "crown_payload",
    "main",
    "satellite_inputs",
    "sweep",
]


GENERATOR_VERSION = PAINT_GENERATOR_VERSION


def satellite_inputs(store, scripts, decoder, image_mod, planes, table) -> tuple:
    """The baked ground colour, its refitted layer table and the rock families.

    Returns ``(payload, files, meta)`` to merge into the store. The crown tops come from
    ``crown_payload``.
    """
    started = time.time()
    bake, bake_stats = read_bake(store, scripts, decoder, image_mod, GRID)
    blended = {name: plane for name, plane in planes.items() if name in table}
    fit, fit_stats = fit_layer_table(blended, table, bake)
    families = rock_family_colours(store, scripts, AssetIndex(store), decoder)
    payload = {BAKE_NAME: hf.encode_u8(bake.reshape(GRID, -1))}
    files = {BAKE_NAME: {"shape": [GRID, GRID, 3], "kind": "u8", "srgb": True, "role": "bake"}}
    meta = {
        "bake": {**bake_stats, "fit": fit_stats, "seconds": round(time.time() - started, 1)},
        "layers_bake_fit": fit,
        "rock_families": families,
    }
    return payload, files, meta


def sweep(store, scripts, classes, progress: bool, meshes=None) -> dict:
    """One walk of every level: weight planes, component origins, trees and water bodies."""
    planes: dict[str, np.ndarray] = {}
    bodies: dict[str, list] = {"actors": [], "hot_springs": []}
    origins: list[tuple[int, int]] = []
    trees: dict[str, list[np.ndarray]] = {}
    carpet: dict[str, list[np.ndarray]] = {}
    unreadable, failed = 0, 0
    started = time.time()

    def skip(_path: str, _exc: Exception) -> None:
        nonlocal unreadable
        unreadable += 1

    paths = level_paths(store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)
    for index, total, path, view in walk_levels(store, scripts, paths=paths, on_unreadable=skip):
        classes_here = {class_name_of(c) for c in view.class_of.values()}
        if "LandscapeComponent" in classes_here:
            try:
                bulk = store.read_path(path[: -len(LEVEL_SUFFIX)] + ".ubulk")
                for base_x, base_y, layers in component_layers(view, bulk):
                    row, col = component_origin(base_x, base_y)
                    place(planes, row, col, layers, GRID)
                    origins.append((row, col))
            except (KeyError, ValueError, struct.error):
                failed += 1
        if classes_here & FOLIAGE_CLASSES:
            for slot, class_path in view.class_of.items():
                if class_name_of(class_path) not in FOLIAGE_CLASSES:
                    continue
                found = foliage_instances(
                    view, slot, classes, wanted=lambda m: is_tree(m) or is_carpet(m)
                )
                if found is not None and is_carpet(found[0]):
                    carpet.setdefault(found[0], []).append(found[1].astype(np.float32))
                elif found is not None:
                    trees.setdefault(found[0], []).append(found[1].astype(np.float32))
        if meshes is not None:
            harvest(view, classes, meshes, bodies)
        if progress and index % 500 == 0:
            print(
                f"  {index}/{total} packages, {len(origins)} components, "
                f"{time.time() - started:.0f}s",
                flush=True,
            )
    return {
        "planes": planes,
        "origins": origins,
        "trees": {mesh: np.concatenate(parts) for mesh, parts in trees.items()},
        "water_bodies": bodies,
        "carpet": {mesh: np.concatenate(parts) for mesh, parts in carpet.items()},
        "unreadable": unreadable,
        "failed_packages": failed,
        "seconds": round(time.time() - started, 1),
    }


def crown_payload(store, scripts, index, decoder, trees: dict) -> tuple[dict, dict, dict]:
    """The crown files ``{name: (bytes, files entry)}``, their meta block, measured radii."""
    started = time.time()

    def texture_rgba(path: str) -> np.ndarray:
        asset = path.split(".")[0].removeprefix("/Game/FactoryGame/")
        return decode_texture(store, scripts, decoder, asset, 256, channels=4)

    built = crown_data.build_crowns(store, scripts, index, trees, texture_rgba)
    sprite_blob, sprite_index = crown_data.encode_sprites(built["sprites"])
    half = SPACING_CM / 2
    top_cm = crown_data.stamp_tops(
        built["records"], built["sprites"], GRID, ORIGIN_X_CM - half, ORIGIN_Y_CM - half, SPACING_CM
    )
    top_dm = np.where(np.isfinite(top_cm), np.round(top_cm / 10.0), hf.NODATA).astype(np.int16)
    files = {
        crown_data.CROWNS_NAME: (
            crown_data.encode_records(built["records"]),
            {"kind": "records", "count": len(built["records"]),
             "dtype": [list(f) for f in crown_data.CROWN_RECORD.descr]},
        ),
        crown_data.SPRITES_NAME: (
            sprite_blob, {"kind": "sprites", "texel_m": crown_data.SPRITE_M}
        ),
        crown_data.CROWN_TOP_NAME: (
            hf.encode_i16(top_dm), {"shape": [GRID, GRID], "kind": "i16", "unit": "dm", "role": "crown top"}
        ),
    }  # fmt: skip
    for entry, sprite in zip(built["species"], sprite_index, strict=True):
        entry["sprite"] = sprite
    radii = {e["mesh"]: e["radius_m"] for e in built["species"]}
    meta = {
        "species": built["species"],
        "skipped": built["skipped"],
        "instances": built["instances"],
        "tilt_max_deg": built["tilt_max_deg"],
        "top_texels": int(np.isfinite(top_cm).sum()),
        "seconds": round(time.time() - started, 1),
    }
    print(
        f"  {len(built['species'])} crown sprites, {built['instances']} trees, "
        f"{len(sprite_blob) / 1e6:.1f} MB of sprites in {meta['seconds']}s"
    )
    return files, meta, radii


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=ROOT / "data" / "local" / PAINT_DIR_NAME,
        help="destination directory (gitignored)",
    )
    parser.add_argument("--quiet", action="store_true", help="no progress lines")
    args = parser.parse_args()
    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder
    from PIL import Image as image_mod

    try:
        pin, raw = installed_build(args.game)
    except (InstallNotFound, OSError, ValueError) as exc:
        print(f"not a game install: {exc}")
        return 1
    reader = open_game(args.game)
    store, scripts, index, classes = reader.store, reader.scripts, reader.index, reader.classes
    started = time.time()

    vectors = material_vectors(
        PackageView(store.read_path(GAME_ROOT + MATERIAL + ".uasset"), scripts)
    )
    means = {
        name: srgb_to_linear(decode_texture(store, scripts, decoder, asset, 512))
        .reshape(-1, 3)
        .mean(0)
        .tolist()
        for name, asset in TEXTURES.items()
    }
    pigment = decode_texture(store, scripts, decoder, PIGMENT, PIGMENT_MAX_PX)
    print(
        f"  {len(means)} textures, {len(vectors)} material vectors, pigment {pigment.shape[0]} px"
    )

    found = sweep(store, scripts, classes, not args.quiet, MeshBounds(store, scripts, index))
    planes = found["planes"]
    if not found["origins"]:
        print("no LandscapeComponent was read; the landscape moved or the format changed")
        return 1
    unknown = sorted(set(planes) - set(LAYERS) - set(OVERLAYS))
    crown_files, crown_meta, radii = crown_payload(store, scripts, index, decoder, found["trees"])
    canopy, tree_counts = canopy_cover(found["trees"], GRID, radii)
    print(
        f"  {len(found['origins'])} components, layers {sorted(planes)}, "
        f"{sum(tree_counts.values())} trees, swept in {found['seconds']}s"
    )

    payload: dict[str, bytes] = {name: blob for name, (blob, _e) in crown_files.items()}
    files: dict[str, dict] = {name: entry for name, (_b, entry) in crown_files.items()}
    for name, plane in sorted(planes.items()):
        payload[WEIGHT_PREFIX + name + WEIGHT_SUFFIX] = hf.encode_u8(plane)
        files[WEIGHT_PREFIX + name + WEIGHT_SUFFIX] = {
            "shape": [GRID, GRID],
            "kind": "u8",
            "layer": name,
        }
    payload[CANOPY_NAME] = hf.encode_u8(np.round(canopy * 255).astype(np.uint8))
    files[CANOPY_NAME] = {"shape": [GRID, GRID], "kind": "u8", "scale": 255}
    layers = layer_albedo(LAYERS, means, vectors)
    extra_payload, extra_files, extra_meta = satellite_inputs(
        store, scripts, decoder, image_mod, planes, layers
    )
    payload.update(extra_payload)
    files.update(extra_files)
    payload[PIGMENT_NAME] = hf.encode_u8(pigment.reshape(pigment.shape[0], -1))
    files[PIGMENT_NAME] = {
        "shape": list(pigment.shape),
        "kind": "u8",
        "srgb": True,
        "placement": "the render frame, texel centres",
    }
    bodies = found["water_bodies"]
    payload[WATER_BODIES_NAME] = json.dumps(bodies, separators=(",", ":")).encode("utf-8")
    files[WATER_BODIES_NAME] = {
        "kind": "json",
        "actors": len(bodies["actors"]),
        "hot_springs": len(bodies["hot_springs"]),
    }
    carpet_blobs, carpet = write_carpet(
        found["carpet"], store, scripts, index, GRID, (ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM)
    )
    payload.update(carpet_blobs)
    files.update(carpet["files"])
    for name, blob in payload.items():
        files[name]["sha256"] = sha256_hex(blob)
        files[name]["bytes"] = len(blob)
    meta = {
        "generator": "tools/gen_paint_layers.py",
        "generator_version": GENERATOR_VERSION,
        "transcribed": datetime.now(UTC).date().isoformat(),
        "cl": changelist(raw),
        "sources": {
            "game": {
                "game_version_pinned": pin,
                "game_version_raw": raw,
                "pyooz": versions.get("pyooz"),
            }
        },
        "grid": {
            "width": GRID,
            "height": GRID,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "spacing_cm": SPACING_CM,
        },
        "files": files,
        "digest": files_digest({name: entry["sha256"] for name, entry in files.items()}),
        "albedo_linear": {
            "layers": layers,
            "layers_bake_fit": extra_meta.pop("layers_bake_fit"),
            "overlays": layer_albedo(OVERLAYS, means, vectors),
            "rock": np.mean([means[t] for t in ROCK_TEXTURES], axis=0).round(5).tolist(),
            "canopy": [round(v, 5) for v in means[CANOPY_TEXTURE]],
        },
        "texture_means_linear": {k: [round(v, 5) for v in m] for k, m in means.items()},
        "material_vectors": vectors,
        "unknown_layers": unknown,
        "components": sorted(found["origins"]),
        "component_px": WEIGHTMAP_PX,
        "trees": tree_counts,
        "crowns": crown_meta,
        **extra_meta,
        "carpet": carpet["meta"],
        "counts": {"unreadable": found["unreadable"], "failed_packages": found["failed_packages"]},
        "seconds": round(time.time() - started, 1),
    }
    payload[META_NAME] = json.dumps(meta, indent=1).encode("utf-8")
    written = install_directory(args.out_dir, payload)
    print(
        f"wrote {args.out_dir}: {len(written)} files, {sum(written.values()) / 1e6:.1f} MB "
        f"in {time.time() - started:.0f}s; unknown layers {unknown or 'none'}"
    )
    return 0
