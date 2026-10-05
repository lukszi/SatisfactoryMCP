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
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    property_tags,
)
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
from tools import gen_world_heightmap as gen
from tools._common import base_parser, require_gen

GENERATOR_VERSION = PAINT_GENERATOR_VERSION
PAINT_DIR_NAME = "paint"
META_NAME = "meta.json"
CANOPY_NAME = "canopy.u8.z"
PIGMENT_NAME = "pigment.rgb.u8.z"
WEIGHT_PREFIX = "w."
WEIGHT_SUFFIX = ".u8.z"

#: The output grid: the heightfield's, 1 m, vertex-aligned on the render frame.
GRID = 7500

GAME_ROOT = "../../../FactoryGame/Content/FactoryGame/"
MATERIAL = "World/Environment/Landscape/Material/FG_Landscape_Inst"
TILES = "World/Environment/Landscape/Texture/Tiles/"
PIGMENT = "-Shared/Texture/PigmentMap"

#: Every texture whose mean albedo the table below reads, by short name.
TEXTURES = {
    "TX_Grass_Far_01_Alb": TILES + "Grass/TX_Grass_Far_01_Alb",
    "TX_Forest_Far_01_Alb": TILES + "Forest/TX_Forest_Far_01_Alb",
    "TX_GrassRed_01_Alb": TILES + "GrassRed/TX_GrassRed_01_Alb",
    "TX_Grass_RedJungle_01_Alb": TILES + "RedJungle/TX_Grass_RedJungle_01_Alb",
    "TX_SandRock_Alb_01": TILES + "SandRock/TX_SandRock_Alb_01",
    "TX_SandPebbles_01_Alb": TILES + "Pebbels/TX_SandPebbles_01_Alb",
    "Sand_Dry_02_Alb": TILES + "Sand/Sand_Dry_02_Alb",
    "Gravel_Alb": TILES + "Stones/Gravel_Alb",
    "TX_Soil_01_Alb": TILES + "Soil/TX_Soil_01_Alb",
    "TX_Puddles_01_Alb": TILES + "Soil/TX_Puddles_01_Alb",
    "TX_SeaRocks_01_Alb": TILES + "SeaRocks/TX_SeaRocks_01_Alb",
    "Cliff_Macro_Alb_02": TILES + "Cliff/Cliff_Macro_Alb_02",
    "Cliff_Detail_Alb": TILES + "Cliff/Cliff_Detail_Alb",
    "Cliff_Sediment_Alb": "World/Environment/Rock/Cliff/Textures/CliffSediment/Cliff_Sediment_Alb",
}

#: Paint layer -> (texture or None, material vector parameter or None); the albedo is their
#: product. Matched by name: the cooked layer functions that wire them are stripped.
LAYERS = {
    "Grass_LayerInfo": ("TX_Grass_Far_01_Alb", None),
    "Forest_LayerInfo": ("TX_Forest_Far_01_Alb", None),
    "PurpleForest_LayerInfo": ("TX_Forest_Far_01_Alb", None),
    "GrassRed_LayerInfo": ("TX_GrassRed_01_Alb", None),
    "RedJungle_LayerInfo": ("TX_Grass_RedJungle_01_Alb", None),
    "Sand_LayerInfo": (None, "Sand Far Color"),
    "SandRipples_LayerInfo": (None, "SandRipples Far Color"),
    "WetSand_LayerInfo": (None, "WetSand_Color"),
    "SandRock_LayerInfo": ("TX_SandRock_Alb_01", "Sand Rock BaseColor"),
    "DesertRock_LayerInfo": ("TX_SandRock_Alb_01", "Sand Rock BaseColor"),
    "SandPebbles_LayerInfo": ("TX_SandPebbles_01_Alb", None),
    "SandCracks_LayerInfo": ("Sand_Dry_02_Alb", None),
    "Gravel_WeightLayerInfo": ("Gravel_Alb", None),
    "Soil_LayerInfo": ("TX_Soil_01_Alb", None),
    "Cliff_LayerInfo": ("Cliff_Sediment_Alb", None),
    "CoralRock_LayerInfo": ("TX_SeaRocks_01_Alb", None),
}

#: Not weight-blended: lerped over the blend by its own weight.
OVERLAYS = {"Puddles_LayerInfo": ("TX_Puddles_01_Alb", None)}
#: Present on components and carrying no colour.
IGNORED = frozenset({"LandscapeVisibilityLayerInfo", "Foliage_Eraser_LayerInfo"})

ROCK_TEXTURES = ("Cliff_Macro_Alb_02", "Cliff_Detail_Alb")
CANOPY_TEXTURE = "TX_Forest_Far_01_Alb"

#: Tree foliage, and a crown radius in metres by name fragment (first match wins).
TREE_MARKS = (
    "/Foliage/Trees/",
    "/Coral/CoralTree",
    "Bamboo",
    "Palm",
    "palm",
    "Kapok",
    "Mangrove",
    "SM_Trunk_01",
    "CraterTree",
)
CROWN_M = (
    ("Kapok", 14.0),
    ("DioTree", 10.0),
    ("Diospyros", 9.0),
    ("AncientPine", 9.0),
    ("GreenTree", 8.0),
    ("PollenTree", 7.0),
    ("PurpleTree", 7.0),
    ("AmberTree", 7.0),
    ("BalloonTree", 6.0),
    ("FunnelTree", 6.0),
    ("SnakeLegs", 6.0),
    ("CraterTree", 6.0),
    ("SnailBottom", 6.0),
    ("Mangrove", 6.0),
    ("SwampTree", 5.0),
    ("Uppochner", 5.0),
    ("BananaTree", 4.0),
    ("SM_Trunk_01", 4.0),
    ("Palm", 3.5),
    ("palm", 3.5),
    ("Yucca", 3.0),
    ("CoralTree", 3.0),
    ("Stump", 1.5),
    ("Bamboo", 1.5),
)
CROWN_DEFAULT_M = 4.0
PIGMENT_MAX_PX = 2048

WEIGHTMAP_PX = 128
WEIGHTMAP_BYTES = WEIGHTMAP_PX * WEIGHTMAP_PX * 4


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    c = np.asarray(values, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def is_tree(mesh: str) -> bool:
    return any(mark in mesh for mark in TREE_MARKS) and "Fallen" not in mesh


def crown_radius(mesh: str) -> float:
    return next((r for key, r in CROWN_M if key in mesh), CROWN_DEFAULT_M)


# ----------------------------------------------------------------------- weightmaps


def weightmap_channels(bgra: bytes) -> np.ndarray:
    """A 128x128 BGRA8 weightmap mip as (128, 128, 4) in R, G, B, A channel order."""
    pixels = np.frombuffer(bgra, np.uint8, count=WEIGHTMAP_BYTES).reshape(
        WEIGHTMAP_PX, WEIGHTMAP_PX, 4
    )
    return pixels[..., [2, 1, 0, 3]]


def allocations(view, payload: bytes) -> list[dict]:
    """``WeightmapLayerAllocations``: layer name, texture index and channel per entry."""
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos, out = 4, []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        entry: dict = {}
        for name, kind, raw, _value in tags:
            if kind == "ObjectProperty":
                entry[name] = view.import_path(raw)
            elif kind == "ByteProperty":
                entry[name] = raw[0]
        out.append(entry)
    return out


def weightmap_textures(view, ubulk: bytes) -> dict[int, np.ndarray]:
    """``{export slot: (128,128,4)}`` for every Weightmap texture in a level package.

    Bulk entries are grouped per owning export (an inline entry continues the group before
    it); owners and groups pair up in export offset order, which is checked by count.
    """
    groups: list[list[dict]] = []
    for entry in view.pkg.bulk_entries():
        if not entry["flags"] & 0x40:
            groups.append([entry])
        elif groups:
            groups[-1].append(entry)
    owners = sorted(
        (
            e
            for e in view.exports
            if class_name_of(view.class_of[e["slot"]])
            in ("Texture2D", "LandscapeTextureStorageProviderFactory")
            and e["name"].split("_")[0] in ("Weightmap", "LandscapeTextureStorageProviderFactory")
        ),
        key=lambda e: e["offset"],
    )
    if len(owners) != len(groups):
        raise ValueError(f"{len(owners)} weightmap owners against {len(groups)} bulk groups")
    out = {}
    for export, group in zip(owners, groups, strict=True):
        if not export["name"].startswith("Weightmap"):
            continue
        if group[0]["size"] != WEIGHTMAP_BYTES:
            raise ValueError(f"{export['name']}: top mip is {group[0]['size']} bytes")
        start = group[0]["offset"]
        out[export["slot"]] = weightmap_channels(ubulk[start : start + WEIGHTMAP_BYTES])
    return out


def component_layers(view, ubulk: bytes) -> list[tuple[int, int, dict[str, np.ndarray]]]:
    """``(SectionBaseX, SectionBaseY, {layer: 128x128 uint8})`` per LandscapeComponent."""
    textures = weightmap_textures(view, ubulk)
    found = []
    for slot, class_path in view.class_of.items():
        if class_name_of(class_path) != "LandscapeComponent":
            continue
        props = view.props(slot)
        base_x = struct.unpack("<i", props.get("SectionBaseX", b"\0\0\0\0"))[0]
        base_y = struct.unpack("<i", props.get("SectionBaseY", b"\0\0\0\0"))[0]
        refs_raw = props.get("WeightmapTextures", b"\0\0\0\0")
        refs = [
            struct.unpack_from("<i", refs_raw, 4 + 4 * i)[0] - 1
            for i in range(struct.unpack_from("<I", refs_raw)[0])
        ]
        layers = {}
        for entry in allocations(view, props.get("WeightmapLayerAllocations", b"")):
            name = (entry.get("LayerInfo") or "None").rsplit("/", 1)[-1].split(".")[0]
            index = entry.get("WeightmapTextureIndex", 0)
            channel = entry.get("WeightmapTextureChannel", 0)
            if index < len(refs) and refs[index] in textures:
                layers[name] = textures[refs[index]][..., channel].copy()
        found.append((base_x, base_y, layers))
    return found


def component_origin(base_x: int, base_y: int) -> tuple[int, int]:
    """Where a component's first sample lands on the 1 m grid, as (row, col)."""
    origin = int(gen.LANDSCAPE_SECTION_ORIGIN)
    col = base_x - origin + round(-gen.ORIGIN_X_CM / gen.SPACING_CM)
    row = base_y - origin + round(-gen.ORIGIN_Y_CM / gen.SPACING_CM)
    return row, col


def place(planes: dict[str, np.ndarray], row: int, col: int, layers: dict, grid: int) -> None:
    """Write one component's layers into the grid planes, clipped to the grid."""
    r0, c0 = max(row, 0), max(col, 0)
    r1, c1 = min(row + WEIGHTMAP_PX, grid), min(col + WEIGHTMAP_PX, grid)
    if r0 >= r1 or c0 >= c1:
        return
    source = (slice(r0 - row, r1 - row), slice(c0 - col, c1 - col))
    for name, weight in layers.items():
        if name in IGNORED:
            continue
        plane = planes.get(name)
        if plane is None:
            plane = planes[name] = np.zeros((grid, grid), np.uint8)
        plane[r0:r1, c0:c1] = weight[source]


# ----------------------------------------------------------------------- textures


def material_vectors(view) -> dict[str, tuple[float, float, float]]:
    """The material instance's ``VectorParameterValues`` as ``{name: (r, g, b)}``."""
    payload = view.props(0).get("VectorParameterValues", b"")
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos, out = 4, {}
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        name, value = None, None
        for tag, kind, raw, _v in tags:
            if tag == "ParameterInfo" and kind == "StructProperty":
                inner, _end = property_tags(raw, view.pkg.names, 0)
                for key, inner_kind, inner_raw, _iv in inner:
                    if key == "Name" and inner_kind == "NameProperty":
                        name = view._fname(inner_raw)
            elif tag == "ParameterValue" and len(raw) == 16:
                value = struct.unpack("<4f", raw)[:3]
        if name and value:
            out[name] = tuple(float(v) for v in value)
    return out


def decode_texture(store, scripts, decoder, asset: str, want_max: int) -> np.ndarray:
    """The largest mip no wider than ``want_max`` as (H, W, 3) uint8 RGB. Square only."""
    blocks = {
        "PF_DXT1": (8, decoder.decode_bc1),
        "PF_DXT5": (16, decoder.decode_bc3),
        "PF_BC7": (16, decoder.decode_bc7),
        "PF_BC5": (16, decoder.decode_bc5),
        "PF_BC4": (8, decoder.decode_bc4),
    }
    path = GAME_ROOT + asset
    view = PackageView(store.read_path(path + ".uasset"), scripts)
    fmt = next(n for n in view.pkg.names if n.startswith("PF_"))
    export = next(
        e for e in view.exports if class_name_of(view.class_of[e["slot"]]).startswith("Texture")
    )
    ubulk = store.read_path(path + ".ubulk") if path + ".ubulk" in store.by_path else b""
    best = None
    for entry in view.pkg.bulk_entries():
        if fmt in blocks:
            side = round((entry["size"] / blocks[fmt][0]) ** 0.5) * 4
        else:
            side = round((entry["size"] / (4 if fmt == "PF_B8G8R8A8" else 1)) ** 0.5)
        if side > want_max or (best and best[0] >= side):
            continue
        if entry["flags"] & 0x40:
            raw = view.pkg.body(export)[entry["offset"] : entry["offset"] + entry["size"]]
        else:
            raw = ubulk[entry["offset"] : entry["offset"] + entry["size"]]
        if len(raw) == entry["size"]:
            best = (side, raw)
    if best is None:
        raise ValueError(f"{asset}: no mip at or under {want_max} px")
    side, raw = best
    if fmt == "PF_B8G8R8A8":
        rgba = np.frombuffer(raw, np.uint8).reshape(side, side, 4)[..., [2, 1, 0, 3]]
    elif fmt == "PF_G8":
        grey = np.frombuffer(raw, np.uint8).reshape(side, side)
        rgba = np.dstack([grey, grey, grey, grey])
    else:
        out = blocks[fmt][1](raw, side, side)
        rgba = np.frombuffer(out, np.uint8).reshape(side, side, 4)[..., [2, 1, 0, 3]]
    return np.ascontiguousarray(rgba[..., :3])


def layer_albedo(layers: dict, means: dict, vectors: dict) -> dict[str, list[float]]:
    """Linear albedo per layer: texture mean times material vector, either alone."""
    table = {}
    for layer, (texture, vector) in layers.items():
        value = np.ones(3)
        if texture is not None:
            value = value * np.asarray(means[texture])
        if vector is not None:
            value = value * np.asarray(vectors[vector])
        table[layer] = [round(float(v), 5) for v in value]
    return table


# ----------------------------------------------------------------------- canopy


def canopy_cover(trees: dict[str, np.ndarray], grid: int) -> tuple[np.ndarray, dict]:
    """Crown cover in [0, 1]: ``1 - exp(-crown area per m^2)``, crowns blurred by radius."""
    by_radius: dict[float, list[np.ndarray]] = {}
    for mesh, points in trees.items():
        by_radius.setdefault(crown_radius(mesh), []).append(points)
    area = np.zeros((grid, grid), np.float32)
    counts = {}
    for radius, parts in sorted(by_radius.items()):
        points = np.concatenate(parts)
        col = np.floor((points[:, 0] - gen.ORIGIN_X_CM) / gen.SPACING_CM).astype(np.int64)
        row = np.floor((points[:, 1] - gen.ORIGIN_Y_CM) / gen.SPACING_CM).astype(np.int64)
        ok = (col >= 0) & (col < grid) & (row >= 0) & (row < grid)
        hits = np.zeros((grid, grid), np.float32)
        np.add.at(hits, (row[ok], col[ok]), np.float32(np.pi * radius * radius))
        area += ndimage.gaussian_filter(hits, radius / 1.5)
        counts[str(radius)] = int(ok.sum())
    return 1.0 - np.exp(-area), counts


# ----------------------------------------------------------------------- the pass


def sweep(store, scripts, classes, progress: bool) -> dict:
    """One walk of every level: weight planes, component origins and tree positions."""
    planes: dict[str, np.ndarray] = {}
    origins: list[tuple[int, int]] = []
    trees: dict[str, list[np.ndarray]] = {}
    unreadable, failed = 0, 0
    started = time.time()

    def skip(_path: str, _exc: Exception) -> None:
        nonlocal unreadable
        unreadable += 1

    paths = level_paths(store, contains=gen.LEVEL_DIR, suffix=gen.LEVEL_SUFFIX)
    for index, total, path, view in walk_levels(store, scripts, paths=paths, on_unreadable=skip):
        classes_here = {class_name_of(c) for c in view.class_of.values()}
        if "LandscapeComponent" in classes_here:
            try:
                bulk = store.read_path(path[: -len(gen.LEVEL_SUFFIX)] + ".ubulk")
                for base_x, base_y, layers in component_layers(view, bulk):
                    row, col = component_origin(base_x, base_y)
                    place(planes, row, col, layers, GRID)
                    origins.append((row, col))
            except (KeyError, ValueError, struct.error):
                failed += 1
        if classes_here & gen.FOLIAGE_CLASSES:
            for slot, class_path in view.class_of.items():
                if class_name_of(class_path) not in gen.FOLIAGE_CLASSES:
                    continue
                found = gen.foliage_instances(view, slot, classes, wanted=is_tree)
                if found is not None:
                    trees.setdefault(found[0], []).append(found[1][:, 3, :3].astype(np.float32))
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
        "unreadable": unreadable,
        "failed_packages": failed,
        "seconds": round(time.time() - started, 1),
    }


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
    versions = require_gen("ooz", "texture2ddecoder")
    import texture2ddecoder as decoder

    try:
        pin, raw = installed_build(args.game)
    except (InstallNotFound, OSError, ValueError) as exc:
        print(f"not a game install: {exc}")
        return 1
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    classes = ClassFacts(store, AssetIndex(store))
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

    found = sweep(store, scripts, classes, not args.quiet)
    planes = found["planes"]
    if not found["origins"]:
        print("no LandscapeComponent was read; the landscape moved or the format changed")
        return 1
    unknown = sorted(set(planes) - set(LAYERS) - set(OVERLAYS))
    canopy, tree_counts = canopy_cover(found["trees"], GRID)
    print(
        f"  {len(found['origins'])} components, layers {sorted(planes)}, "
        f"{sum(tree_counts.values())} trees, swept in {found['seconds']}s"
    )

    payload: dict[str, bytes] = {}
    files: dict[str, dict] = {}
    for name, plane in sorted(planes.items()):
        payload[WEIGHT_PREFIX + name + WEIGHT_SUFFIX] = hf.encode_u8(plane)
        files[WEIGHT_PREFIX + name + WEIGHT_SUFFIX] = {
            "shape": [GRID, GRID],
            "kind": "u8",
            "layer": name,
        }
    payload[CANOPY_NAME] = hf.encode_u8(np.round(canopy * 255).astype(np.uint8))
    files[CANOPY_NAME] = {"shape": [GRID, GRID], "kind": "u8", "scale": 255}
    payload[PIGMENT_NAME] = hf.encode_u8(pigment.reshape(pigment.shape[0], -1))
    files[PIGMENT_NAME] = {
        "shape": list(pigment.shape),
        "kind": "u8",
        "srgb": True,
        "placement": "the render frame, texel centres",
    }
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
            "x0_cm": gen.ORIGIN_X_CM,
            "y0_cm": gen.ORIGIN_Y_CM,
            "spacing_cm": gen.SPACING_CM,
        },
        "files": files,
        "digest": files_digest({name: entry["sha256"] for name, entry in files.items()}),
        "albedo_linear": {
            "layers": layer_albedo(LAYERS, means, vectors),
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


if __name__ == "__main__":
    raise SystemExit(main())
