"""The light's occluder: the trees as the colour draw stamps them, over the whole sheet.

The paint store's crowns and the Titan canopy's placements, each from its crown sprite at full
resolution (``terrain/crown_stamp.py``, on the device under ``--gpu``), at opacity 1 whatever
the palette: the higher top of the two, their cover laid together, and where the Titan
canopy's top is the higher. docs/map/light-and-crowns.md section 29, "A porous canopy".
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.spans.trees import TITAN_BYTE
from mapgen.palette.painted.albedo import load_paint_meta
from mapgen.render.draw.painting import crown_band
from mapgen.sprites.store import SpriteAtlas
from mapgen.terrain.crown_atlas import crown_atlas
from mapgen.terrain.crown_stamp import CrownSet, load_crowns
from mapgen.terrain.sample import frame_coordinates
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = ["OCCLUDER_ROWS", "TreeSets", "stamp_occluder", "tree_sets"]

#: Sheet rows stamped at a time.
OCCLUDER_ROWS = 256


class TreeSets(NamedTuple):
    """The trees the light casts: the paint store's crowns and the Titan canopy, either None
    where there are none."""

    crowns: CrownSet | None
    titans: CrownSet | None


def tree_sets(sprites: SpriteAtlas, paint_dir: Path, titans: bool) -> TreeSets:
    """The crowns of the paint store at ``paint_dir`` and, with ``titans``, the Titan canopy,
    over one atlas of ``sprites``; the coral trees the render-only meshes draw are left out."""
    atlas = crown_atlas(sprites)
    meta = load_paint_meta(paint_dir)
    crowns = None
    if meta is not None:
        crowns = load_crowns(paint_dir, meta.get("crowns"), meta["files"], atlas)
    canopy = CrownSet(sprites.titan, atlas) if titans and len(sprites.titan) else None
    return TreeSets(crowns, canopy)


def stamp_occluder(sets: TreeSets, size: int, planes: tuple[F32Grid, U8Grid, U8Grid]) -> None:
    """The trees' top in metres (NaN where none), cover and Titan byte on a ``size`` sheet,
    into ``planes``, ``OCCLUDER_ROWS`` rows at a time."""
    top, cover, titan = planes
    x_cm, y_cm = frame_coordinates(size)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    nan = np.float32(np.nan)
    for start in range(0, size, OCCLUDER_ROWS):
        rows = slice(start, min(start + OCCLUDER_ROWS, size))
        centres = (x_cm, y_cm[rows])
        shape = (rows.stop - rows.start, size)
        tops: list[F32Grid] = []
        alphas: list[F32Grid] = []
        for found in sets:
            if found is None:
                tops.append(np.full(shape, nan, np.float32))
                alphas.append(np.zeros(shape, np.float32))
                continue
            band = crown_band(found, centres, spacing_m)
            tops.append((band["top_cm"] / np.float32(100.0)).astype(np.float32))
            alphas.append(np.clip(band["cover"], np.float32(0.0), np.float32(1.0)))
        (crowns, titans), (a_crowns, a_titans) = tops, alphas
        wins = np.isfinite(titans) & ~(crowns >= titans)
        highest = np.where(wins, titans, crowns)
        share = np.float32(1.0) - (np.float32(1.0) - a_crowns) * (np.float32(1.0) - a_titans)
        top[rows] = highest
        cover[rows] = np.where(np.isfinite(highest), np.round(share * np.float32(255.0)), 0)
        titan[rows] = np.where(wins, TITAN_BYTE, 0)
