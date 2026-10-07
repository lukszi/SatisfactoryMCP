"""The water-class plane: a class per texel, the swamp-to-ocean blends at mouths, the
hot-spring terraces' tint in their lakes, and the dry texels beside water, which take its
class."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.water.bodies import (
    BODY_STEP_M,
    DRY,
    HOT_SPRING,
    LAKE,
    OCEAN,
    SWAMP,
    WATER_BODIES_NAME,
    WATER_CLASSES,
    classify,
    level_bodies,
    spring_terraces,
)
from mapgen.palette.painted.shapes import FloatGrid
from mapgen.palette.water.geodesic import geodesic_steps
from mapgen.palette.water.seams import windows_around
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.core.arrays import BoolMask, F64Grid, I16Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "DRY_FILL_M",
    "MOUTH_BLEND",
    "MOUTH_FEATHER_M",
    "MOUTH_STEPS",
    "SPRING_BLEND",
    "SPRING_REACH_M",
    "SPRING_STEPS",
    "class_shares",
    "feather_mouths",
    "fill_dry",
    "tint_springs",
    "water_classes",
]

#: Texels as their row and column indices.
Texels: TypeAlias = tuple[NDArray[np.intp], NDArray[np.intp]]

#: Plane values from here on blend swamp into ocean: ``MOUTH_BLEND + k`` is swamp share
#: ``(k + 0.5) / MOUTH_STEPS``, the rest ocean (``class_shares``).
MOUTH_BLEND = len(WATER_CLASSES)
MOUTH_STEPS = 64

#: Swamp meeting the ocean inside one body blends into it over this far either side.
MOUTH_FEATHER_M = 30.0

#: Plane values from here on blend a hot-spring terrace's tint into its lake:
#: ``SPRING_BLEND + k`` is hot-spring share ``(k + 0.5) / SPRING_STEPS``, the rest lake.
SPRING_BLEND = MOUTH_BLEND + MOUTH_STEPS
SPRING_STEPS = 32

#: A terrace tints its lake fully within the first distance of its root, and not at all
#: past the second, metres.
SPRING_REACH_M = (8.0, 20.0)

#: Dry texels this close to classed water take the class of the nearest: water drawn over
#: them by the sea's crossing rule is that water's.
DRY_FILL_M = 8


def water_classes(
    water: I16Grid,
    grades: U8Grid,
    bodies: JsonObject,
    areas: tuple[U8Grid, Sequence[str]],
    void: BoolMask | None = None,
) -> tuple[U8Grid, JsonObject]:
    """The class plane of the water as drawn, swamp feathered into the ocean where they meet
    (``feather_mouths``), the terraces' tint (``tint_springs``), dry texels beside water
    filled (``fill_dry``), and what the sidecar records. ``water`` is the level plane in dm,
    ``grades`` its quality, ``areas`` the area index grid and the names it indexes, ``void``
    where the field has no ground."""
    level = np.where(water == hf.NODATA, np.nan, water / np.float32(hf.DM_PER_M))
    wet = grades != hf.WATER_DRY
    level = level.astype(np.float32)
    plane, counts = classify(level, wet, bodies, areas, OCEAN_LEVEL_M)
    counts["mouth_blend_texels"] = feather_mouths(plane, level, void)
    counts["spring_tinted_texels"] = tint_springs(plane, level, spring_terraces(bodies))
    counts["dry_texels_filled"] = fill_dry(plane, DRY_FILL_M)
    return plane, {"source": f"paint/{WATER_BODIES_NAME}", **counts}


def tint_springs(plane: U8Grid, level_m: FloatGrid, terraces: F64Grid) -> int:
    """Lake texels around each of ``terraces`` (``spring_terraces``' rows) take its tint, in
    place: all of it within ``SPRING_REACH_M[0]`` of its root, along a smoothstep to none at
    ``SPRING_REACH_M[1]``, where their level lies in its box's range. Overlapping terraces
    take the larger share. Returns how many texels are tinted."""
    near, far = SPRING_REACH_M
    reach = int(np.ceil(far * 100.0 / SPACING_CM)) + 1
    tinted = np.zeros(plane.shape, bool)
    for x, y, z0, z1 in terraces:
        col, row = (x - ORIGIN_X_CM) / SPACING_CM, (y - ORIGIN_Y_CM) / SPACING_CM
        rows = slice(max(int(row) - reach, 0), min(int(row) + reach + 2, plane.shape[0]))
        cols = slice(max(int(col) - reach, 0), min(int(col) + reach + 2, plane.shape[1]))
        if rows.start >= rows.stop or cols.start >= cols.stop:
            continue
        r, c = np.ogrid[rows, cols]
        metres = np.hypot(r - row, c - col) * (SPACING_CM / 100.0)
        t = np.clip((far - metres) / (far - near), 0.0, 1.0)
        share = t * t * (3.0 - 2.0 * t)
        part = plane[rows, cols]
        held = np.where(
            part == HOT_SPRING, 1.0, (part.astype(np.float64) - SPRING_BLEND + 0.5) / SPRING_STEPS
        )
        mine = (part == LAKE) | (part == HOT_SPRING) | (part >= SPRING_BLEND)
        with np.errstate(invalid="ignore"):
            level = level_m[rows, cols]
            mine &= (level >= z0) & (level <= z1) & (share > 0.0)
        share = np.where(part == LAKE, share, np.maximum(share, held))
        step = np.minimum((share * SPRING_STEPS).astype(np.int64), SPRING_STEPS - 1)
        part[mine] = np.where(share[mine] >= 1.0, HOT_SPRING, SPRING_BLEND + step[mine])
        tinted[rows, cols] |= mine
    return int(tinted.sum())


def fill_dry(plane: U8Grid, reach: int) -> int:
    """Dry texels within ``reach`` steps of a classed one take the class of the nearest, in
    place, a step at a time through the 4-neighbours in a fixed order; returns how many."""
    filled = 0
    for _step in range(reach):
        dry = plane == DRY
        if not dry.any():
            break
        grown = plane.copy()
        for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
            near = np.roll(plane, shift, axis)
            edge = slice(0, 1) if shift == 1 else slice(-1, None)
            if axis == 0:
                near[edge] = DRY
            else:
                near[:, edge] = DRY
            take = (grown == DRY) & (near != DRY)
            grown[take] = near[take]
        taken = dry & (grown != DRY)
        if not taken.any():
            break
        plane[taken] = grown[taken]
        filled += int(taken.sum())
    return filled


def class_shares() -> FloatGrid:
    """Each plane value's share of each class: a class is all its own, a mouth blend is
    part swamp and the rest ocean, a spring blend part hot spring and the rest lake."""
    table = np.eye(SPRING_BLEND + SPRING_STEPS, len(WATER_CLASSES), dtype=np.float32)
    mouths = slice(MOUTH_BLEND, SPRING_BLEND)
    swamp = (np.arange(MOUTH_STEPS, dtype=np.float32) + 0.5) / MOUTH_STEPS
    table[mouths, SWAMP] = swamp
    table[mouths, OCEAN] = 1.0 - swamp
    spring = (np.arange(SPRING_STEPS, dtype=np.float32) + 0.5) / SPRING_STEPS
    table[SPRING_BLEND:, HOT_SPRING] = spring
    table[SPRING_BLEND:, LAKE] = 1.0 - spring
    return table


def feather_mouths(plane: U8Grid, level_m: FloatGrid, void: BoolMask | None = None) -> int:
    """Swamp blended into ocean in place, within ``MOUTH_FEATHER_M`` of where the two meet
    inside one body, along a smoothstep; returns the texels that turned to a blend.

    Ocean over ``void``, the artwork's sea past the landscape, takes no swamp: where swamp
    meets it the blend lies on the swamp's side alone, from none at the line.
    """
    found = _meeting(plane, level_m)
    if found is None:
        return 0
    reach = int(np.ceil(MOUTH_FEATHER_M)) + 1
    marks = np.zeros(plane.shape, np.int8)
    marks[found[0]], marks[found[1]] = 1, 2
    off = np.zeros(plane.shape, bool) if void is None else void
    marks[(marks == 2) & off] = 3
    changed = 0
    for window in windows_around(marks != 0, reach):
        part, mark = plane[window], marks[window]
        swamp, ocean = part == SWAMP, (part == OCEAN) & ~off[window]
        sheet = level_bodies(swamp | (part == OCEAN), level_m[window])
        line = np.unique(sheet[mark != 0])
        kept = np.isin(sheet, line[line > 0])
        into_swamp = _steps(mark == 2, swamp & kept, reach)
        into_ocean = _steps(mark == 1, ocean & kept, reach)
        side = np.where(swamp, 1.0, -1.0) * (np.minimum(into_swamp, into_ocean) - 0.5)
        t = np.clip((side / MOUTH_FEATHER_M + 1.0) / 2.0, 0.0, 1.0)
        share = t * t * (3.0 - 2.0 * t)
        if (mark == 3).any():
            past = np.clip((_steps(mark == 3, swamp & kept, reach) - 0.5) / MOUTH_FEATHER_M, 0, 1)
            share = np.minimum(share, past * past * (3.0 - 2.0 * past))
        blend = kept & (swamp | ocean) & (share > 0.0) & (share < 1.0)
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


def _steps(seed: BoolMask, inside: BoolMask, reach: int) -> FloatGrid:
    """Steps from ``seed`` to each texel of ``inside`` through ``inside``, an octagon close to
    the circle; ``reach + 1`` past ``reach``."""
    return geodesic_steps(seed, inside, reach, octagon=True).astype(np.float32)
