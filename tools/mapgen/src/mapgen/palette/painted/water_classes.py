"""The water-class plane: a class per texel, and the swamp-to-ocean blends at mouths."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.water.bodies import (
    BODY_STEP_M,
    OCEAN,
    SWAMP,
    WATER_BODIES_NAME,
    WATER_CLASSES,
    classify,
    level_bodies,
)
from mapgen.palette.painted.shapes import FloatGrid
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.core.arrays import BoolMask, I16Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "MOUTH_BLEND",
    "MOUTH_FEATHER_M",
    "MOUTH_STEPS",
    "class_shares",
    "feather_mouths",
    "water_classes",
]

#: Texels as their row and column indices.
Texels: TypeAlias = tuple[NDArray[np.intp], NDArray[np.intp]]


def water_classes(
    water: I16Grid, grades: U8Grid, bodies: JsonObject, areas: tuple[U8Grid, Sequence[str]]
) -> tuple[U8Grid, JsonObject]:
    """The class plane of the water as drawn, swamp feathered into the ocean where they meet
    (``feather_mouths``), and what the sidecar records. ``water`` is the level plane in dm,
    ``grades`` its quality, ``areas`` the area index grid and the names it indexes."""
    level = np.where(water == hf.NODATA, np.nan, water / np.float32(hf.DM_PER_M))
    wet = grades != hf.WATER_DRY
    level = level.astype(np.float32)
    plane, counts = classify(level, wet, bodies, areas, OCEAN_LEVEL_M)
    counts["mouth_blend_texels"] = feather_mouths(plane, level)
    return plane, {"source": f"paint/{WATER_BODIES_NAME}", **counts}


#: Plane values from here on blend swamp into ocean: ``MOUTH_BLEND + k`` is swamp share
#: ``(k + 0.5) / MOUTH_STEPS``, the rest ocean (``class_shares``).
MOUTH_BLEND = len(WATER_CLASSES)
MOUTH_STEPS = 64

#: Swamp meeting the ocean inside one body blends into it over this far either side.
MOUTH_FEATHER_M = 30.0


def class_shares() -> FloatGrid:
    """Each plane value's share of each class: a class is all its own, a mouth blend is
    part swamp and the rest ocean."""
    table = np.eye(MOUTH_BLEND + MOUTH_STEPS, len(WATER_CLASSES), dtype=np.float32)
    swamp = (np.arange(MOUTH_STEPS, dtype=np.float32) + 0.5) / MOUTH_STEPS
    table[MOUTH_BLEND:, SWAMP] = swamp
    table[MOUTH_BLEND:, OCEAN] = 1.0 - swamp
    return table


def feather_mouths(plane: U8Grid, level_m: FloatGrid) -> int:
    """Swamp blended into ocean in place, within ``MOUTH_FEATHER_M`` of where the two meet
    inside one body, along a smoothstep; returns the texels that turned to a blend."""
    found = _meeting(plane, level_m)
    if found is None:
        return 0
    reach = int(np.ceil(MOUTH_FEATHER_M)) + 1
    marks = np.zeros(plane.shape, np.int8)
    marks[found[0]], marks[found[1]] = 1, 2
    changed = 0
    for window in _mouth_windows(marks != 0, reach):
        part, mark = plane[window], marks[window]
        sheet = level_bodies((part == SWAMP) | (part == OCEAN), level_m[window])
        line = np.unique(sheet[mark != 0])
        kept = np.isin(sheet, line[line > 0])
        into_swamp = _steps(mark == 2, (part == SWAMP) & kept, reach)
        into_ocean = _steps(mark == 1, (part == OCEAN) & kept, reach)
        side = np.where(part == SWAMP, 1.0, -1.0) * (np.minimum(into_swamp, into_ocean) - 0.5)
        t = np.clip((side / MOUTH_FEATHER_M + 1.0) / 2.0, 0.0, 1.0)
        share = t * t * (3.0 - 2.0 * t)
        blend = kept & (share > 0.0) & (share < 1.0)
        step = np.minimum((share[blend] * MOUTH_STEPS).astype(np.int64), MOUTH_STEPS - 1)
        part[blend] = MOUTH_BLEND + step
        changed += int(blend.sum())
    return changed


def _meeting(plane: U8Grid, level_m: FloatGrid) -> tuple[Texels, Texels] | None:
    """``(swamp, ocean)`` texels where the two classes are 8-neighbours at levels within
    ``BODY_STEP_M``, or None."""
    rows, cols = np.nonzero(plane == SWAMP)
    swamp: list[Texels] = []
    ocean: list[Texels] = []
    for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
        r, c = rows + dr, cols + dc
        ok = (r >= 0) & (r < plane.shape[0]) & (c >= 0) & (c < plane.shape[1])
        r, c, r0, c0 = r[ok], c[ok], rows[ok], cols[ok]
        hit = plane[r, c] == OCEAN
        hit[hit] = np.abs(level_m[r[hit], c[hit]] - level_m[r0[hit], c0[hit]]) <= BODY_STEP_M
        swamp.append((r0[hit], c0[hit]))
        ocean.append((r[hit], c[hit]))
    if not sum(len(r) for r, _c in swamp):
        return None
    return _joined(swamp), _joined(ocean)


def _joined(parts: Sequence[Texels]) -> Texels:
    return np.concatenate([r for r, _c in parts]), np.concatenate([c for _r, c in parts])


def _mouth_windows(line: BoolMask, reach: int, cell: int = 64) -> Iterator[tuple[slice, slice]]:
    """Disjoint windows holding every texel within ``reach`` of ``line``."""
    rows, cols = line.shape
    coarse = np.pad(line, ((0, -rows % cell), (0, -cols % cell)))
    coarse = coarse.reshape(coarse.shape[0] // cell, cell, -1, cell).any(axis=(1, 3))
    grown = ndimage.binary_dilation(coarse, np.ones((3, 3), bool), int(np.ceil(reach / cell)))
    while True:
        labels, _count = ndimage.label(grown, structure=np.ones((3, 3), bool))
        boxes = [box for box in ndimage.find_objects(labels) if box is not None]
        filled = np.zeros_like(grown)
        for box in boxes:
            filled[box] = True
        if (filled == grown).all():
            break
        grown = filled
    for r, c in boxes:
        yield (
            slice(r.start * cell, min(r.stop * cell, rows)),
            slice(c.start * cell, min(c.stop * cell, cols)),
        )


def _steps(seed: BoolMask, inside: BoolMask, reach: int) -> FloatGrid:
    """Steps from ``seed`` to each texel of ``inside`` through ``inside``, the 8- and the
    4-neighbourhood in turn (an octagon close to the circle); ``reach + 1`` past ``reach``."""
    out = np.full(seed.shape, np.float32(reach + 1))
    front, reached = seed, seed.copy()
    for k in range(1, reach + 1):
        shape = ndimage.generate_binary_structure(2, 2 if k % 2 else 1)
        front = ndimage.binary_dilation(front, shape) & inside & ~reached
        if not front.any():
            break
        out[front] = k
        reached |= front
    return out
