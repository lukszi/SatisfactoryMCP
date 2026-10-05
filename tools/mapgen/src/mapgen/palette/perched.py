"""Water whose box top is not its surface: the level re-read from its own shoreline.

The field levels each wet texel at the highest water-box top over it. For a sloped river the
box's top is the river's upstream end, and where boxes of two bodies overlap in plan the
higher body's top lands on the lower one. Either way the level stands metres above the dry
banks around it, which still water cannot do, and the renderer would draw tens of metres of
depth. docs/spatial-and-map.md section 38.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from scipy import ndimage

from mapgen.palette.shore import OCEAN_LEVEL_BAND_M, OCEAN_LEVEL_M, OCEAN_REACH_M, ocean_reach
from mapgen.terrain.fill import solve
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "PERCHED_EXCESS_M",
    "PERCHED_LIST_MAX",
    "SPILL_RING_M",
    "SPILL_SHARE",
    "WaterSurfaces",
    "perched_levels",
    "relevel",
    "spill_share",
    "water_surfaces",
]

#: The box level is kept while it stands at most this far above the shoreline's own
#: surface, and given up entirely at twice this. About what a 3.66 m mask block on a steep
#: bank can account for.
PERCHED_EXCESS_M = 2.0

#: The ring of banks the spill test reads, metres from the body: past the artwork
#: mask's own block error, and near enough to be this body's banks.
SPILL_RING_M = (6.0, 24.0)

#: A body is perched when more than this share of that ring stands below its level.
SPILL_SHARE = 0.25

#: How many of the largest perched bodies the sidecar lists by place.
PERCHED_LIST_MAX = 12

_FOUR = ((0, 1), (0, -1), (1, 0), (-1, 0))
_EIGHT = np.ones((3, 3), bool)


def _shifted(plane: np.ndarray, dr: int, dc: int, fill) -> np.ndarray:
    """``plane`` moved by ``(dr, dc)``, with ``fill`` where nothing moved in."""
    out = np.full_like(plane, fill)
    h, w = plane.shape
    out[max(dr, 0) : h + min(dr, 0), max(dc, 0) : w + min(dc, 0)] = plane[
        max(-dr, 0) : h + min(-dr, 0), max(-dc, 0) : w + min(-dc, 0)
    ]
    return out


def _padded(box, origin, pad: int, shape) -> tuple[slice, slice]:
    return tuple(
        slice(max(o + s.start - pad, 0), min(o + s.stop + pad, n))
        for s, o, n in zip(box, origin, shape)
    )


def _bodies(measured: np.ndarray, level: np.ndarray, skip, pad: int):
    """Each body as ``(window, mask, level_dm)``: connected texels of one level, in a
    window ``pad`` texels wider than the body all round."""
    labels, _count = ndimage.label(measured, structure=_EIGHT)
    for index, box in enumerate(ndimage.find_objects(labels), 1):
        part = labels[box] == index
        values = level[box]
        for value in np.unique(values[part]):
            if skip(int(value)):
                continue
            same, _n = ndimage.label(part & (values == value), structure=_EIGHT)
            for body, inner in enumerate(ndimage.find_objects(same), 1):
                window = _padded(inner, (box[0].start, box[1].start), pad, level.shape)
                mask = np.zeros(
                    (window[0].stop - window[0].start, window[1].stop - window[1].start), bool
                )
                r0 = box[0].start + inner[0].start - window[0].start
                c0 = box[1].start + inner[1].start - window[1].start
                cut = same[inner] == body
                mask[r0 : r0 + cut.shape[0], c0 : c0 + cut.shape[1]] = cut
                yield window, mask, int(value)


def _shore(body: np.ndarray, ground: np.ndarray, bank: np.ndarray, level: float):
    """``(shore, waterline)``: the body's shoreline texels, and on each the highest surface
    its neighbours allow, ``level`` where they all stand above it (decimetres)."""
    shore = np.zeros_like(body)
    total = np.zeros(body.shape, np.float32)
    count = np.zeros(body.shape, np.float32)
    for dr, dc in _FOUR:
        beside_bank = _shifted(bank, dr, dc, np.nan)
        beside = np.isfinite(beside_bank) & body
        shore |= beside
        bound = np.maximum(np.minimum(beside_bank, np.float32(level)), ground)
        total += np.where(beside, bound, 0.0)
        count += beside
    return shore, total / np.maximum(count, 1.0)


def spill_share(body, bank, level: float, step_m: float) -> float:
    """The share of the ring around ``body`` whose ground or other water stands more than
    ``PERCHED_EXCESS_M`` below ``level``: where still water at that level would run to."""
    distance = ndimage.distance_transform_edt(~body) * step_m
    ring = np.isfinite(bank) & (distance > SPILL_RING_M[0]) & (distance <= SPILL_RING_M[1])
    if not ring.any():
        return 0.0
    below = ring & (bank < np.float32(level - PERCHED_EXCESS_M * hf.DM_PER_M))
    return float(below.sum() / ring.sum())


def relevel(level_dm: float, surface_dm: np.ndarray) -> np.ndarray:
    """The level kept where it stands within ``PERCHED_EXCESS_M`` of ``surface_dm``, the
    surface where it stands twice that above, and a linear hand-over between."""
    band = np.float32(PERCHED_EXCESS_M * hf.DM_PER_M)
    excess = np.float32(level_dm) - surface_dm
    handed = surface_dm + np.maximum(2 * band - excess, 0.0)
    return np.where(excess <= band, np.float32(level_dm), handed)


def perched_levels(field, planes=None) -> tuple[np.ndarray | None, dict]:
    """The water raster with every perched texel re-levelled, and what was changed.

    ``planes`` is ``(level_dm, grades)`` to re-level instead of the field's own.

    A body is perched when its level stands above its banks and still water at that level
    would run off across the ring around it. Its surface is then the harmonic membrane
    spanning its shoreline, so a river's follows it downhill. Every other body, and the
    ocean, is returned byte for byte.
    """
    water, grades = planes or (field._water_raster(), field._water_quality_raster())
    if water is None or grades is None:
        return water, {"absent": "no water planes"}
    heights = field._height_dm
    known = heights != hf.NODATA
    measured = (grades == hf.WATER_MEASURED) & (water != hf.NODATA) & known
    # Ground the water encloses is no bank: the artwork draws deep water too dark for its
    # blue test, so a lake's deep middle reads dry and below the level.
    wet = grades != hf.WATER_DRY
    dry = known & ~ndimage.binary_fill_holes(wet)
    # What bounds a body: dry ground at its height, other water at its level.
    banks = np.where(dry, heights, np.where(wet & (water != hf.NODATA), water, np.nan))
    banks = banks.astype(np.float32)
    band_dm = PERCHED_EXCESS_M * hf.DM_PER_M
    ocean_dm = OCEAN_LEVEL_M * hf.DM_PER_M

    def is_ocean(value: int) -> bool:
        return abs(value - ocean_dm) <= OCEAN_LEVEL_BAND_M * hf.DM_PER_M

    out = water.copy()
    found = []
    candidates = 0
    step_m = field.spacing_cm / 100.0
    pad = int(np.ceil(SPILL_RING_M[1] / step_m)) + 1
    for window, body, value in _bodies(measured, water, is_ocean, pad):
        g = heights[window].astype(np.float32)
        bank = np.where(body, np.float32(np.nan), banks[window])
        shore, waterline = _shore(body, g, bank, value)
        if not shore.any() or value - float(waterline[shore].min()) <= band_dm:
            continue
        share = spill_share(body, bank, value, step_m)
        candidates += 1
        if share <= SPILL_SHARE:
            continue
        surface = solve(waterline, shore, body & ~shore, 1).astype(np.float32)
        # A part joined to the rest only by a corner has no shoreline of its own to span.
        parts, count = ndimage.label(body)
        ashore = np.zeros(count + 1, bool)
        ashore[parts[shore]] = True
        surface[body & ~ashore[parts]] = value
        new = np.rint(relevel(value, surface[body])).astype(np.int16)
        changed = new < value
        if not changed.any():
            continue
        out[window][body] = np.minimum(new, value)
        rows, cols = np.nonzero(body)
        x_m = field.x0_cm / 100 + (window[1].start + cols.mean()) * step_m
        y_m = field.y0_cm / 100 + (window[0].start + rows.mean()) * step_m
        found.append(
            {
                "texels": int(changed.sum()),
                "x_m": round(float(x_m), 1),
                "y_m": round(float(y_m), 1),
                "box_level_m": value / hf.DM_PER_M,
                "spill_share": round(share, 3),
                "surface_m": [
                    round(float(new[changed].min()) / hf.DM_PER_M, 1),
                    round(float(new[changed].max()) / hf.DM_PER_M, 1),
                ],
            }
        )
    found.sort(key=lambda body: -body["texels"])
    return out, {
        "rule": (
            "a measured body is perched when its box level stands more than excess_m above "
            "a neighbour (dry ground at its height, other water at its level) and more than "
            "spill_share of the ring spill_ring_m around it stands that far below too. Its "
            "surface is then the harmonic membrane spanning its shoreline, each shoreline "
            "texel at the highest level its neighbours allow; a texel keeps the box level "
            "within excess_m of that surface, takes the surface at twice excess_m, and is "
            "handed over linearly between. The ocean level is exempt"
        ),
        "excess_m": PERCHED_EXCESS_M,
        "spill_ring_m": list(SPILL_RING_M),
        "spill_share": SPILL_SHARE,
        "bodies_standing_above_a_bank": candidates,
        "bodies": len(found),
        "texels": int(sum(body["texels"] for body in found)),
        "largest": found[:PERCHED_LIST_MAX],
    }


class WaterSurfaces(NamedTuple):
    """The water a render draws: the ocean reach, and the level and grade planes after the
    rivers took their boxes' water back and perched water was re-levelled."""

    reach: np.ndarray | None
    reach_meta: dict
    level: np.ndarray | None
    grades: np.ndarray | None
    perched: dict | None

    @property
    def planes(self) -> tuple | None:
        return None if self.level is None else (self.level, self.grades)


def water_surfaces(field, kernel_only: bool, rivers=None) -> WaterSurfaces:
    """Recipe 6's ocean reach and the re-levelled water, both announced. ``rivers`` is the
    run's ``RiverWater``: its reconciled planes are re-levelled, so a spline's ribbon stands
    in for its box everywhere it speaks. ``--kernel-only`` draws neither."""
    if kernel_only:
        return WaterSurfaces(None, {}, None, None, None)
    reach, reach_meta = ocean_reach(field)
    print(
        f"  ocean shore at {OCEAN_LEVEL_M} m: {reach_meta.get('ocean_texels')} ocean "
        f"texels, {reach_meta.get('reach_texels')} within {OCEAN_REACH_M:g} m"
    )
    planes = None if rivers is None else (rivers.water_dm, rivers.grades)
    level, meta = perched_levels(field, planes)
    if "bodies" in meta:
        print(f"  perched water: {meta['bodies']} bodies, {meta['texels']} texels re-levelled")
    grades = field._water_quality_raster() if rivers is None else rivers.grades
    return WaterSurfaces(reach, reach_meta, level, grades, meta)
