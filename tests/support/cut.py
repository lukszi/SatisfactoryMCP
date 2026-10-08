"""A sheet cut whole and serially in one of mapgen's tile formats: the reference the parallel
cutter's WebP trees are held to, as ``install_pyramid`` is the PNG trees' reference."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from mapgen.tiles.formats import TileFormat, tile_name
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX, pyramid_top_z


def cut_whole(
    sheet: np.ndarray, dest: Path, fmt: TileFormat, tile_px: int = PYRAMID_TILE_PX
) -> int:
    """Each level one Lanczos resize of the whole sheet (premultiplied for RGBA), cut into
    ``dest`` in ``fmt``, a sparse format's empty tiles left out. Returns the tiles written."""
    from PIL import Image

    whole = Image.fromarray(sheet)
    written = 0
    for z in range(pyramid_top_z(sheet.shape[0], tile_px) + 1):
        side = tile_px << z
        level = whole if side == whole.width else whole.resize((side, side), Image.LANCZOS)
        (dest / str(z)).mkdir(parents=True, exist_ok=True)
        for y in range(1 << z):
            for x in range(1 << z):
                tile = level.crop((x * tile_px, y * tile_px, (x + 1) * tile_px, (y + 1) * tile_px))
                if fmt.sparse and tile.getchannel("A").getbbox() is None:
                    continue
                path = dest / tile_name(z, x, y, fmt.suffix)
                tile.save(path, "WEBP", lossless=True, exact=False, method=fmt.method)
                written += 1
    return written
