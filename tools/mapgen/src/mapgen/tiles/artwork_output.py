"""The artwork's output: its two tile trees and the ``map.json`` the web API reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.tiles.imaging import TileImaging
from mapgen.tiles.pyramid import tree_megabytes
from mapgen.tiles.recipes import ENHANCE_RECIPE, UNNUMBERED_RECIPE
from satisfactory_mcp.core.gameassets.container import (
    MIP0_BYTES,
    MIP_SIZES,
    SHEET_PX,
    TILE_PX,
    UBULK_BYTES,
)
from satisfactory_mcp.core.gameassets.provenance import (
    changelist,
    provenance_block,
    read_path,
    read_str_path,
)
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    install_pyramid,
)
from satisfactory_mcp.core.gameassets.versions import ARTWORK_RECIPES, READER_VERSIONS, STYLES
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = [
    "ENHANCED_PATH",
    "IMAGE_NAME",
    "PIN_PATH",
    "RECIPE_PATH",
    "SIDECAR_NAME",
    "artwork_provenance",
    "build_artwork_sidecar",
    "enhancement_downgrades",
    "image_block",
    "install_artwork_trees",
    "integrity_block",
    "pinned_build",
    "pinned_enhanced",
    "pinned_recipe",
]

IMAGE_NAME = "map.png"


SIDECAR_NAME = "map.json"


#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "map_slices", "game_version_pinned")


#: Both are read: sidecars older than the recipe number carry only the boolean.
ENHANCED_PATH = ("tiles", "enhanced")


RECIPE_PATH = ("tiles", "enhancement", "recipe")


def install_artwork_trees(
    sheet: Image,
    image_mod: TileImaging,
    out_dir: Path,
    *,
    enhance: Callable[[Path], JsonObject] | None,
    with_2x: bool,
    build_pin: str,
) -> tuple[JsonObject, JsonObject | None]:
    """Install the 1x tree, then the @2x one on its own. Returns their sidecar blocks.

    A failure in the second leaves the first where it is. ``install_pyramid``'s and
    ``enhance``'s exceptions pass through to the caller.
    """
    tiles: JsonObject = install_pyramid(sheet, image_mod, out_dir, enhance=enhance)
    tiles["game_version_pinned"] = build_pin
    print(f"wrote {out_dir / TILES_DIR_NAME}  {_tree_text(tiles)}")

    # Never enhanced: @2x level z is 1x level z+1, so the upscaled levels stay reachable.
    tiles_2x: JsonObject | None = None
    if with_2x:
        tiles_2x = install_pyramid(
            sheet,
            image_mod,
            out_dir,
            tile_px=PYRAMID_TILE_2X_PX,
            dir_name=TILES_2X_DIR_NAME,
        )
        tiles_2x["game_version_pinned"] = build_pin
        print(f"wrote {out_dir / TILES_2X_DIR_NAME}  {_tree_text(tiles_2x)}")
    return tiles, tiles_2x


def _tree_text(tree: JsonObject) -> str:
    """``N tiles over z0..zM  B B  (S MB)`` for a tree ``install_pyramid`` recorded."""
    return (
        f"{tree['count']} tiles over z0..z{tree['max_z']}  {tree['bytes']} B  "
        f"({tree_megabytes(tree):.1f} MB)"
    )


def pinned_build(sidecar: Mapping[str, JsonValue]) -> str | None:
    """The build an existing sidecar names, or None if it names none."""
    return read_str_path(sidecar.get("_meta"), PIN_PATH)


def pinned_enhanced(sidecar: Mapping[str, JsonValue]) -> bool:
    """Whether an existing sidecar's pyramid was cut with ``--enhance``; only literal true."""
    return read_path(sidecar.get("_meta"), ENHANCED_PATH) is True


def pinned_recipe(sidecar: Mapping[str, JsonValue]) -> int:
    """Which recipe cut an existing sidecar's pyramid: 0 plain, a bare boolean is recipe 1.

    Anything but a positive whole number (``true`` included) falls back to the boolean.
    """
    node = read_path(sidecar.get("_meta"), RECIPE_PATH)
    if isinstance(node, int) and not isinstance(node, bool) and node > 0:
        return node
    return UNNUMBERED_RECIPE if pinned_enhanced(sidecar) else 0


def enhancement_downgrades(
    sidecar: Mapping[str, JsonValue], enhance_now: bool, recipe: int = ENHANCE_RECIPE
) -> bool:
    """Would this run replace a pyramid with one cut by an earlier recipe? Then it must not.

    The whole rule in one place, compared on the number: docs/spatial-and-map.md section 17.
    """
    return pinned_recipe(sidecar) > (recipe if enhance_now else 0)


def image_block(size: int, written: int, mode: str, alpha_note: str) -> JsonObject:
    """``_meta.image``: the PNG as written."""
    return {
        "file": IMAGE_NAME,
        "width_px": size,
        "height_px": size,
        "bytes": written,
        "mode": mode,
        "source_resolution_px": SHEET_PX,
        "downscale": (
            "none; this is the game's own resolution"
            if size == SHEET_PX
            else f"Lanczos, {SHEET_PX} -> {size}"
        ),
        "alpha": alpha_note,
        "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size, 4),
    }


def integrity_block() -> JsonObject:
    """``_meta.integrity``: the slice length every run checks the texture against."""
    return {
        "ubulk_bytes_expected": UBULK_BYTES,
        "mip_chain": [f"{px}x{px}: {size} B" for px, size in MIP_SIZES],
        "mip0_bytes": MIP0_BYTES,
        "role": (
            "every slice's .ubulk is exactly this long, so the length is a free check that "
            "the texture still has the size and mip count this file knows how to read. Mip "
            "0 is then the first mip0_bytes with no offset to guess. A different length "
            "means the game changed and the run stops."
        ),
    }


_DESCRIPTION = (
    "Corners for data/local/map.png and the tiles/ pyramid cut from it, and "
    "where that picture came from. All of it is local: data/local/ is "
    "gitignored and no map imagery is ever committed to this repository."
)

_BOUNDS_NOTE = (
    "metres, game axes -- +X east, +Y south. These are the corners of the "
    "in-game map square, stated here rather than left to the server's own "
    "default so this file says where its picture goes without reference to "
    "anything else. calibration below is the measurement behind them."
)

_STALENESS = (
    "sources.map_slices.game_version_pinned is the build this picture was cut "
    "from, in the same shape data/resource_nodes.json uses, so an image and a "
    "node table from different builds are comparable on sight. "
    "tools/gen_map_image.py refuses to overwrite map.png OR tiles/ unless this "
    "sidecar names the build then installed; --force says it anyway. tiles."
    "enhancement.recipe is the second half of the same posture: a run whose "
    "recipe is BEHIND the one named here refuses to replace these tiles, "
    "because a refresh that quietly costs two zoom levels -- or re-cuts them "
    "with a pipeline that was measured worse -- is drift too. A later recipe "
    "over an earlier one is an upgrade and runs."
)


def build_artwork_sidecar(
    *,
    build_pin: str,
    build_raw: Mapping[str, JsonValue],
    image: JsonObject,
    integrity: JsonObject,
    layout: JsonObject,
    calibration: JsonObject,
    versions: Mapping[str, str],
    tiles: JsonObject | None = None,
    tiles_2x: JsonObject | None = None,
    provenance: JsonObject | None = None,
) -> JsonObject:
    """The file the web API reads: the four corners it copies, and ``_meta`` beside them."""
    today = datetime.now(UTC).date().isoformat()
    return {
        **BOUNDS_M,
        "_meta": {
            "description": _DESCRIPTION,
            "bounds": _BOUNDS_NOTE,
            "generator": "tools/gen_map_image.py",
            "transcribed": today,
            "sources": {"map_slices": _map_slices_source(build_pin, build_raw, today)},
            "image": image,
            "tiles": tiles or {"absent": "this run wrote no pyramid; map.png is the whole map"},
            # Absent, not a record saying "absent": the endpoint reads a block as a tree.
            **({"tiles_2x": tiles_2x} if tiles_2x else {}),
            **({"provenance": provenance} if provenance else {}),
            "integrity": integrity,
            "layout": layout,
            "calibration": calibration,
            "decoders": _decoders_block(versions),
            "staleness": _STALENESS,
        },
    }


def _map_slices_source(
    build_pin: str, build_raw: Mapping[str, JsonValue], today: str
) -> JsonObject:
    """``sources.map_slices``: the four slices, how they were read, and the build pinned."""
    return {
        "name": "/Game/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/Map_{col}-{row}",
        "licence": (
            "Coffee Stain Studios' own artwork, read out of the reader's "
            "installed copy of the game. Not committed, not redistributed, "
            "and served to localhost only."
        ),
        "derivation": (
            f"four {TILE_PX}x{TILE_PX} PF_DXT1 Texture2D; mip 0 of each .ubulk, "
            f"BC1-decoded and stitched 2x2 into a {SHEET_PX}x{SHEET_PX} sheet"
        ),
        "role": "the whole picture",
        "game_version_pinned": build_pin,
        "game_version_raw": {
            key: build_raw.get(key)
            for key in ("Changelist", "BranchName", "BuildId", "GameVersion")
        },
        "transcribed": today,
    }


def _decoders_block(versions: Mapping[str, str]) -> JsonObject:
    """``_meta.decoders``: the ``gen`` extra's three readers, with the versions that ran."""
    return {
        "oodle": {
            "name": "pyooz",
            "version": versions.get("pyooz", "unknown"),
            "import_name": "ooz",
            "licence": "GPL-3.0",
            "role": (
                "container block decompression, offline, at generation time only. "
                "An OPTIONAL dependency: the `gen` extra in pyproject.toml, pinned "
                "exactly because it decides these bytes, and asked for on the "
                "command line -- `uv run --extra gen python tools/gen_map_image.py`. "
                "It is imported at module scope nowhere, and lazily inside one "
                "function of satisfactory_mcp.core.gameassets.iostore, so the "
                "server and the test suite run with it absent. No part of it is in "
                "the output."
            ),
        },
        "block_compression": {
            "name": "texture2ddecoder",
            "version": versions.get("texture2ddecoder", "unknown"),
            "role": "BC1 (DXT1) block decoding",
            "note": (
                "decode_bc1 returns BGRA, not RGBA. Read as RGBA the red and blue "
                "channels swap, which turns the ocean orange and still looks like "
                "a stylised map -- hence the explicit raw/BGRA decode."
            ),
        },
        "imaging": {"name": "pillow", "version": versions.get("pillow", "unknown")},
    }


def artwork_provenance(
    build_raw: JsonObject, sheet_digest: str, enhanced: bool, size: int
) -> JsonObject:
    """``_meta.provenance`` for the artwork: one input, the sheet, and the cutting recipe."""
    recipe = ENHANCE_RECIPE if enhanced else 0
    return provenance_block(
        build_raw,
        {
            "artwork_sheet": {
                "cl": changelist(build_raw),
                "reader_version": READER_VERSIONS["artwork_sheet"],
                "digest": sheet_digest,
            }
        },
        {
            "family": "artwork",
            "recipe": recipe,
            "version": ARTWORK_RECIPES[recipe]["version"],
            "label": ARTWORK_RECIPES[recipe]["label"],
            "size_px": size,
        },
        {"id": "artwork", "version": STYLES["artwork"]["version"], "label": "artwork"},
    )
