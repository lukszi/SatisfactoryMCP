"""The tile formats a render's trees are cut in besides the PNG pyramid: the ground under the
live light as lossless WebP, and the trees as lossless RGBA WebP, sparse.

``encode_tiles`` is the cutter's encoder for them, run in a spawned process as the PNG one is
(``core/gameassets/pyramid.py`` ``encode_tile_row``): argument-shaped so it pickles, and free
of numpy. docs/map/light-and-crowns.md section 36, "Trees apart".
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from satisfactory_mcp.core.gameassets.provenance import RETIRED_SUFFIX, STAGING_SUFFIX
from satisfactory_mcp.core.gameassets.pyramid import PyramidError, swap_into_place
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "GROUND_TILES",
    "PNG_TILES",
    "TREES_TILES",
    "TileFormat",
    "commit_tiles",
    "encode_tiles",
    "tile_name",
]


class TileFormat(NamedTuple):
    """How a tree's tiles are written: the file suffix, the image mode, whether a tile with
    nothing in it (alpha all 0) is left out, and the WebP effort (0 for PNG)."""

    suffix: str
    mode: str
    sparse: bool = False
    method: int = 0

    @property
    def channels(self) -> int:
        return len(self.mode)

    @property
    def record(self) -> JsonObject:
        """What a tree's record says of its format."""
        if self.suffix == ".png":
            return {"format": "png"}
        out: JsonObject = {"format": "webp", "lossless": True, "method": self.method}
        if self.mode == "RGBA":
            out["alpha"] = "straight"
        if self.sparse:
            out["sparse"] = True
        return out


#: The shipped pyramids: ``tiles/`` and ``tiles@2x/``.
PNG_TILES = TileFormat(".png", "RGB")
#: ``unlit/``, the ground the page relights: 24% under its PNG at the normal tiles' effort.
GROUND_TILES = TileFormat(".webp", "RGB", method=2)
#: ``trees/``: RGB under alpha 0 is free to compress (Pillow's ``exact=False``).
TREES_TILES = TileFormat(".webp", "RGBA", sparse=True, method=4)


def tile_name(z: int | str, x: int | str, y: int | str, suffix: str) -> str:
    """``{z}/{x}_{y}<suffix>``: ``core/gameassets/pyramid.py`` ``tile_relpath``'s layout."""
    return f"{z}/{x}_{y}{suffix}"


def encode_tiles(job: tuple[str, int, int, int, str, int, TileFormat]) -> int:
    """Row ``row`` of level ``z``'s tiles, out of a ``shared_memory`` block holding just that
    row, as WebP in ``fmt``; a sparse format leaves out a tile whose alpha is all 0. Returns
    the bytes written."""
    from multiprocessing.shared_memory import SharedMemory

    from PIL import Image

    name, width, z, row, dest, tile_px, fmt = job
    block = SharedMemory(name=name)
    try:
        if block.buf is None:
            raise PyramidError(f"the shared row {name} is closed")
        with block.buf[: tile_px * width * fmt.channels] as raw:
            # A copy: an RGBA strip would map the block, which must close after.
            strip = Image.frombytes(fmt.mode, (width, tile_px), bytes(raw))
    finally:
        block.close()
    written = 0
    for x in range(width // tile_px):
        tile = strip.crop((x * tile_px, 0, (x + 1) * tile_px, tile_px))
        if fmt.sparse and tile.getchannel("A").getbbox() is None:
            continue
        path = Path(dest) / tile_name(z, x, row, fmt.suffix)
        tile.save(path, "WEBP", lossless=True, exact=False, method=fmt.method)
        written += path.stat().st_size
    return written


def commit_tiles(stats: JsonObject, out_dir: Path, dir_name: str, fmt: TileFormat) -> JsonObject:
    """``core/gameassets/pyramid.py`` ``commit_tree`` for a tree in ``fmt``: its staged tiles
    checked against its own count, then swapped into place."""
    staging = out_dir / (dir_name + STAGING_SUFFIX)
    on_disk = sum(1 for _ in staging.rglob("*" + fmt.suffix))
    if on_disk != stats["count"]:
        raise PyramidError(
            f"the tree was cut with {stats['count']} tiles but {on_disk} are in {staging} -- "
            "refusing to install a tree that does not match its own count"
        )
    retired = out_dir / (dir_name + RETIRED_SUFFIX)
    stats["installed_by"] = swap_into_place(staging, out_dir / dir_name, retired)
    return stats
