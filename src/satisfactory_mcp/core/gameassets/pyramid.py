"""The tile pyramid: one sheet at one resolution per zoom, and renamed into place whole.

Every base layer is cut into ``{z}/{x}_{y}.png``, and level ``z`` (``2**z`` tiles a side) is
**one Lanczos downscale of the whole sheet**, never of the level above, so no level
accumulates the softening of successive halvings. ``tiles@2x/`` is the same grid at 512 px a
tile for hi-DPI displays, and therefore one level shallower. A pyramid is only ever renamed
into place, so a reader meets a whole tree or no tree.

This cutter is serial and the reference. The renders cut through ``mapgen.tiles.cutter``,
which stages and commits through ``stage_tree`` and ``commit_tree`` here and writes the same
bytes (docs/spatial-and-map.md section 17, "Cutting in parallel").
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from typing_extensions import TypedDict

from ..jsontypes import JsonArray, JsonObject, as_int, require_list, require_object
from .imaging import LanczosFilter, PngOptions, TileImage
from .provenance import RETIRED_SUFFIX, STAGING_SUFFIX

#: The directory a pyramid lives in, and the square a browser fetches. Deliberately not
#: called ``TILE_PX``: to ``tools/gen_map_image.py`` a "tile" is one of the four 4096 px
#: slices the game ships, and the two meanings must not collide in a file that holds both.
TILES_DIR_NAME = "tiles"
PYRAMID_TILE_PX = 256

#: A separate directory rather than a filename suffix, because it is a whole tree with its
#: own depth and the endpoint picks between the two the way it picks between layers.
TILES_2X_DIR_NAME = "tiles@2x"
PYRAMID_TILE_2X_PX = PYRAMID_TILE_PX * 2

#: The staging and retirement names, off the suffixes ``provenance`` already spells.
TILES_STAGING = TILES_DIR_NAME + STAGING_SUFFIX
TILES_RETIRED = TILES_DIR_NAME + RETIRED_SUFFIX

#: A directory just written can be held for a moment by a scanner or the indexer: how often a
#: fresh rename is retried, and the pause between tries.
RENAME_TRIES = 30
RENAME_PAUSE_S = 2.0

#: What a level says it was cut from when the caller does not say. Every caller that is not
#: cutting the game's artwork passes its own, so a level record cannot name the artwork under
#: a hillshade.
DEFAULT_LEVEL_SOURCE = "the game's own 8192 px artwork, Lanczos"

#: How much an upscaled top level multiplies the sheet by when the caller does not say.
DEFAULT_UPSCALE = 4

#: How a tile is deflated: zlib level 6, the same pixels as ``optimize=True`` for an eighth of
#: its CPU and a few percent more bytes (docs/spatial-and-map.md section 17, "Deflate level").
TILE_PNG: PngOptions = {"format": "PNG", "compress_level": 6}

#: One level of a cut, as the pyramid record lists it. The functional form: "from" is a keyword.
LevelRecord = TypedDict(
    "LevelRecord", {"z": int, "sheet_px": int, "tiles": int, "bytes": int, "from": str}
)


class PyramidError(Exception):
    """A pyramid cannot be cut, or must not be installed."""


def pyramid_top_z(sheet_px: int, tile_px: int = PYRAMID_TILE_PX) -> int:
    """The deepest level of a pyramid over a ``sheet_px`` square: 8192 -> 5. Derived rather
    than typed in, because ``--size`` can halve the sheet and a pyramid one level too deep is
    a level of tiles upscaled from nothing."""
    levels = sheet_px // tile_px
    if levels < 1 or levels & (levels - 1):
        raise PyramidError(
            f"a {sheet_px} px sheet is not a power-of-two multiple of {tile_px} px tiles, "
            "so no pyramid divides it evenly"
        )
    return levels.bit_length() - 1


def enhanced_top_z(
    sheet_px: int, scale: int = DEFAULT_UPSCALE, tile_px: int = PYRAMID_TILE_PX
) -> int:
    """The deepest level once the sheet has been upscaled ``scale`` times: 8192, 4x -> 7. An
    upscale is worth exactly log2(scale) levels, and one that is not a power of two would not
    divide the tile grid at all."""
    if scale < 1 or scale & (scale - 1):
        raise PyramidError(f"an upscale of {scale}x is not a power of two, so it adds no levels")
    return pyramid_top_z(sheet_px, tile_px) + (scale.bit_length() - 1)


def tile_relpath(z: int, x: int, y: int) -> str:
    """``{z}/{x}_{y}.png`` -- the one place the layout is written down.

    The web API has the same function, and a test asserts the two agree: the tool that
    writes the tree and the endpoint that serves it must not hold two opinions about
    where a tile lives.
    """
    return f"{z}/{x}_{y}.png"


def cut_square(piece: TileImage, dest: Path, z: int, ox: int, oy: int, tile_px: int) -> int:
    """Slice one square image into ``dest/{z}/{x}_{y}.png``, starting at tile ``(ox, oy)``.

    Returns the bytes written, which the caller sums into the level record a reader checks the
    tree against. ``width`` is asked for twice rather than ``height`` once so that a test's
    stand-in sheet need only carry the attributes really used.
    """
    (dest / str(z)).mkdir(parents=True, exist_ok=True)
    written = 0
    for y in range(piece.width // tile_px):
        for x in range(piece.width // tile_px):
            box = (x * tile_px, y * tile_px, (x + 1) * tile_px, (y + 1) * tile_px)
            path = dest / tile_relpath(z, ox + x, oy + y)
            piece.crop(box).save(path, **TILE_PNG)
            written += path.stat().st_size
    return written


def encode_tile_row(job: tuple[str, int, int, int, str, int]) -> int:
    """One row of RGB tiles out of a level in a ``shared_memory`` block, deflated to PNG.

    The parallel cutter's encoder, run in a spawned process: argument-shaped so it pickles,
    and free of numpy, whose thread pool would commit most of a gigabyte in every encoder.
    A module cannot pickle either, so this body imports Pillow itself (DESIGN.md).
    """
    from multiprocessing.shared_memory import SharedMemory

    from PIL import Image

    name, width, z, row, dest, tile_px = job
    block = SharedMemory(name=name)
    try:
        if block.buf is None:
            raise PyramidError(f"the shared level {name} is closed")
        start, length = row * tile_px * width * 3, tile_px * width * 3
        with block.buf[start : start + length] as raw:
            # Pillow reads any buffer, but its stub admits only bytes and array interfaces.
            data = cast("bytes", raw)
            strip = Image.frombuffer("RGB", (width, tile_px), data, "raw", "RGB", 0, 1)
        written = 0
        for x in range(width // tile_px):
            path = Path(dest) / tile_relpath(z, x, row)
            box = (x * tile_px, 0, (x + 1) * tile_px, tile_px)
            strip.crop(box).save(path, **TILE_PNG)
            written += path.stat().st_size
        return written
    finally:
        block.close()


def level_record(z: int, written: int, source: str, tile_px: int = PYRAMID_TILE_PX) -> LevelRecord:
    """One level's entry in the record, printed as it is made."""
    side, tiles = tile_px << z, (1 << z) ** 2
    print(f"  pyramid z{z}: {side}x{side}, {tiles} tiles, {written / 1e6:.2f} MB")
    return {"z": z, "sheet_px": side, "tiles": tiles, "bytes": written, "from": source}


def cut_pyramid(
    sheet: TileImage,
    image_mod: LanczosFilter,
    dest: Path,
    tile_px: int = PYRAMID_TILE_PX,
    source: str = DEFAULT_LEVEL_SOURCE,
    dir_name: str = TILES_DIR_NAME,
) -> JsonObject:
    """Cut ``sheet`` into ``dest/{z}/{x}_{y}.png`` for every level, and say what it wrote.

    ``--enhance`` adds levels ABOVE this top out of upscaled pixels and does not touch these:
    a level with real pixels behind it has no business being drawn from invented ones.
    """
    top = pyramid_top_z(sheet.width, tile_px)
    levels: list[LevelRecord] = []
    for z in range(top + 1):
        side = tile_px << z
        level = sheet if side == sheet.width else sheet.resize((side, side), image_mod.LANCZOS)
        levels.append(level_record(z, cut_square(level, dest, z, 0, 0, tile_px), source, tile_px))
    return pyramid_record(levels, tile_px, 1, dir_name)


def _level_json(level: LevelRecord) -> JsonObject:
    return {key: level[key] for key in ("z", "sheet_px", "tiles", "bytes", "from")}


def pyramid_record(
    levels: list[LevelRecord], tile_px: int, workers: int, dir_name: str
) -> JsonObject:
    """What a cut wrote, ``levels`` in z order: the block a sidecar carries."""
    return {
        "layout": f"{dir_name}/{{z}}/{{x}}_{{y}}.png",
        "tile_px": tile_px,
        "max_z": levels[-1]["z"],
        "enhanced": False,
        "count": sum(level["tiles"] for level in levels),
        "bytes": sum(level["bytes"] for level in levels),
        "levels": [_level_json(level) for level in levels],
        "workers": workers,
        "role": (
            "the same sheet at one resolution per zoom, so the page fetches the pixels it "
            "can actually show. map.png is still written beside it: it is what a page "
            "falls back to when there is no pyramid, and the one file a reader can open."
        ),
        "completeness": (
            "written to " + dir_name + STAGING_SUFFIX + " and renamed into place, so this "
            "directory is either a whole pyramid or absent -- an interrupted run cannot "
            "leave a partial one for a reader to trust. count is what a doubter can check "
            "it against."
        ),
    }


def merge_enhanced(stats: JsonObject, extra: JsonObject) -> JsonObject:
    """Fold the enhanced levels into the pyramid record the sidecar carries. ``count`` and
    ``bytes`` are re-summed rather than added to, so the number ``install_pyramid`` checks the
    tree against stays derived from the list a reader would count themselves."""
    levels: JsonArray = require_list(stats["levels"]) + require_list(extra["levels"])

    def total(key: str) -> int:
        return sum(as_int(require_object(level)[key]) for level in levels)

    return {
        **stats,
        "max_z": max(as_int(require_object(level)["z"]) for level in levels),
        "enhanced": True,
        "count": total("tiles"),
        "bytes": total("bytes"),
        "levels": levels,
        "enhancement": extra["enhancement"],
    }


def install_pyramid(
    sheet: TileImage,
    image_mod: LanczosFilter,
    out_dir: Path,
    tile_px: int = PYRAMID_TILE_PX,
    enhance: Callable[[Path], JsonObject] | None = None,
    source: str = DEFAULT_LEVEL_SOURCE,
    dir_name: str = TILES_DIR_NAME,
) -> JsonObject:
    """Cut the pyramid into staging, then rename it over any older one.

    ``enhance`` runs INSIDE the staging window: the GPU stage is the part most likely to fail,
    and a failure there must leave the installed pyramid untouched. ``dir_name`` picks which
    tree of this layer is being installed -- ``tiles/`` or the @2x grid -- and carries its own
    staging names, so cutting one cannot disturb the other.
    """
    staging = stage_tree(out_dir, dir_name)
    stats = cut_pyramid(sheet, image_mod, staging, tile_px, source, dir_name)
    if enhance is not None:
        stats = merge_enhanced(stats, enhance(staging))
    return commit_tree(stats, out_dir, dir_name)


def stage_tree(out_dir: Path, dir_name: str) -> Path:
    """An empty staging directory for ``dir_name``, leftovers of a run that died cleared first."""
    staging = out_dir / (dir_name + STAGING_SUFFIX)
    for stale in (staging, out_dir / (dir_name + RETIRED_SUFFIX)):
        if stale.exists():
            shutil.rmtree(stale)
    staging.mkdir(parents=True)
    return staging


def commit_tree(stats: JsonObject, out_dir: Path, dir_name: str) -> JsonObject:
    """Check the staged tree against its own count, then swap it in; ``stats`` says how.

    A previous tree is moved aside first (Windows will not rename onto a non-empty directory)
    and deleted afterwards.
    """
    staging = out_dir / (dir_name + STAGING_SUFFIX)
    on_disk = sum(1 for _ in staging.rglob("*.png"))
    if on_disk != stats["count"]:
        raise PyramidError(
            f"the pyramid was cut with {stats['count']} tiles but {on_disk} PNGs are in "
            f"{staging} -- refusing to install a tree that does not match its own count"
        )
    retired = out_dir / (dir_name + RETIRED_SUFFIX)
    stats["installed_by"] = swap_into_place(staging, out_dir / dir_name, retired)
    return stats


def rename_retrying(source: Path, target: Path) -> None:
    """``source.rename(target)``, retried while Windows briefly refuses it."""
    for attempt in range(RENAME_TRIES):
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_PAUSE_S)


def swap_into_place(staging: Path, final: Path, retired: Path) -> str:
    """Put the finished tree where it is served from, and say how it managed it.

    The whole tree at once is the intent and is tried first, so a reader meets the old pyramid
    or the new one and never a mixture. **Windows will not rename a directory anything has
    open** -- an Explorer window, the search indexer, a backup agent -- so the fallback swaps
    one level at a time, each renamed atomically over its predecessor. A reader who catches
    the middle of that sees every level present and some of them still the old cut, rather
    than a level missing; levels only the old tree had are removed after, so a shallower new
    pyramid leaves no deep level behind pretending to belong to it.

    Which of the two happened is returned and recorded, because the weaker guarantee is worth
    seeing from the outside.
    """
    if not final.exists():
        rename_retrying(staging, final)
        return "the whole tree renamed into place"
    try:
        final.rename(retired)
    except OSError:
        levels = sorted(child.name for child in staging.iterdir())
        for name in levels:
            target = final / name
            if target.exists():
                spent = final / (name + RETIRED_SUFFIX)
                if spent.exists():
                    shutil.rmtree(spent)
                target.rename(spent)
                (staging / name).rename(target)
                shutil.rmtree(spent)
            else:
                (staging / name).rename(target)
        for leftover in final.iterdir():
            if leftover.name in levels:
                continue
            if leftover.is_dir():
                shutil.rmtree(leftover)
            else:
                leftover.unlink()
        shutil.rmtree(staging)
        return (
            "level by level: something has the served directory open, which on Windows "
            "refuses a whole-tree rename"
        )
    staging.rename(final)
    shutil.rmtree(retired)
    return "the whole tree renamed into place"
