"""Water whose box top is not its surface: the level re-read from its own shoreline.

The field levels each wet texel at the highest water-box top over it. For a sloped river the
box's top is the river's upstream end, and where boxes of two bodies overlap in plan the
higher body's top lands on the lower one, as where a lake's box reaches past its fall over the
basin below. Either way the level stands metres above the dry banks around it, which still
water cannot do, and the renderer would draw tens of metres of depth. The artwork's mask also
leaves dry holes inside a lake, which ``wet_holes`` fills. docs/spatial-and-map.md section 38.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import NamedTuple, NotRequired, TypeAlias, TypedDict, TypeVar, final, overload

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage
from scipy import sparse as sp
from scipy.sparse import csgraph

from mapgen.palette.scene import (
    FloatGrid,
    ReconciledWater,
    WaterPlanes,
    field_heights,
    field_water,
)
from mapgen.palette.water.shore import OCEAN_LEVEL_BAND_M, OCEAN_LEVEL_M, OCEAN_REACH_M, ocean_reach
from mapgen.terrain.fill import harmonic_fill
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I16Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "HOLE_BRIDGE_M",
    "HOLE_DEPTH_MAX_M",
    "LIP_DROP_M",
    "PERCHED_EXCESS_M",
    "PERCHED_LIST_MAX",
    "SPILL_RING_M",
    "SPILL_SHARE",
    "HolesMeta",
    "PerchedMeta",
    "WaterSurfaces",
    "perched_levels",
    "relevel",
    "spill_share",
    "water_surfaces",
    "wet_holes",
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

#: Ground falling more than this between neighbouring texels of a body is a fall's lip or a
#: cliff. A part beyond such drops that stands this far under the level all over is water
#: below the drop, re-levelled on its own.
LIP_DROP_M = 8.0

#: How many of the largest perched bodies the sidecar lists by place.
PERCHED_LIST_MAX = 12

#: Dry ground inside a lake is re-wetted across gaps up to twice this wide: the arches and
#: bridges the artwork draws over the water.
HOLE_BRIDGE_M = 12.0

#: A hole deeper than this anywhere under its lake is a cliff foot or a box over lower water,
#: not a lake middle too dark for the artwork's blue test.
HOLE_DEPTH_MAX_M = 15.0

_FOUR = ((0, 1), (0, -1), (1, 0), (-1, 0))
_EIGHT = np.ones((3, 3), bool)

#: Rows and columns of a part of the field.
Window: TypeAlias = tuple[slice, slice]

_Scalar = TypeVar("_Scalar", bound=np.generic)


class Place(TypedDict):
    """Where a body or a hole sits: the world position of its centre, metres."""

    x_m: float
    y_m: float


class PerchedBody(TypedDict):
    """One re-levelled body, as the sidecar lists it."""

    texels: int
    x_m: float
    y_m: float
    box_level_m: float
    spill_share: float
    below_a_drop: bool
    surface_m: list[float]


class HoleBody(TypedDict):
    """One filled hole, as the sidecar lists it."""

    texels: int
    x_m: float
    y_m: float
    level_m: float


class HolesMeta(TypedDict):
    """``wet_holes``'s sidecar block; only the counts when no hole was found."""

    bodies: int
    texels: int
    rule: NotRequired[str]
    bridge_m: NotRequired[float]
    depth_max_m: NotRequired[float]
    excess_m: NotRequired[float]
    largest: NotRequired[list[HoleBody]]


class PerchedMeta(TypedDict):
    """``perched_levels``'s sidecar block, with the holes ``water_surfaces`` filled after."""

    rule: str
    excess_m: float
    spill_ring_m: list[float]
    spill_share: float
    lip_drop_m: float
    bodies_standing_above_a_bank: int
    bodies_cut_at_a_drop: int
    bodies: int
    texels: int
    largest: list[PerchedBody]
    holes: NotRequired[HolesMeta]


@final
class NoWaterPlanes(TypedDict):
    """The sidecar block of a field without water planes."""

    absent: str


class PerchedShore(NamedTuple):
    """A body standing above a bank: its shoreline, the waterline on it, and its spill share."""

    shore: BoolMask
    waterline: FloatGrid
    spill_share: float


@overload
def _is_ocean(level_dm: int) -> bool: ...
@overload
def _is_ocean(level_dm: I16Grid) -> BoolMask: ...
def _is_ocean(level_dm: int | I16Grid) -> bool | BoolMask:
    return abs(level_dm - OCEAN_LEVEL_M * hf.DM_PER_M) <= OCEAN_LEVEL_BAND_M * hf.DM_PER_M


def _shifted(plane: NDArray[_Scalar], dr: int, dc: int, fill: float) -> NDArray[_Scalar]:
    """``plane`` moved by ``(dr, dc)``, with ``fill`` where nothing moved in."""
    out = np.full_like(plane, fill)
    h, w = plane.shape
    out[max(dr, 0) : h + min(dr, 0), max(dc, 0) : w + min(dc, 0)] = plane[
        max(-dr, 0) : h + min(-dr, 0), max(-dc, 0) : w + min(-dc, 0)
    ]
    return out


def _padded(
    box: tuple[slice, ...], origin: tuple[int, int], pad: int, shape: tuple[int, ...]
) -> Window:
    rows, cols = (
        slice(max(o + s.start - pad, 0), min(o + s.stop + pad, n))
        for s, o, n in zip(box, origin, shape)
    )
    return rows, cols


def _bodies(
    measured: BoolMask, level: I16Grid, skip: Callable[[int], bool], pad: int
) -> Iterator[tuple[Window, BoolMask, int]]:
    """Each body as ``(window, mask, level_dm)``: connected texels of one level, in a
    window ``pad`` texels wider than the body all round."""
    labels, _count = ndimage.label(measured, structure=_EIGHT)
    for index, box in enumerate(ndimage.find_objects(labels), 1):
        if box is None:
            continue
        part = labels[box] == index
        values = level[box]
        for value in np.unique(values[part]):
            if skip(int(value)):
                continue
            same, _n = ndimage.label(part & (values == value), structure=_EIGHT)
            for body, inner in enumerate(ndimage.find_objects(same), 1):
                if inner is None:
                    continue
                window = _padded(inner, (box[0].start, box[1].start), pad, level.shape)
                mask = np.zeros(
                    (window[0].stop - window[0].start, window[1].stop - window[1].start), bool
                )
                r0 = box[0].start + inner[0].start - window[0].start
                c0 = box[1].start + inner[1].start - window[1].start
                cut = same[inner] == body
                mask[r0 : r0 + cut.shape[0], c0 : c0 + cut.shape[1]] = cut
                yield window, mask, int(value)


def _below_drops(
    body: BoolMask, ground: I16Grid, value: int, pad: int
) -> list[tuple[Window, BoolMask]]:
    """The water below a drop in ``body``, as ``(window, mask)`` inside its window, lowest
    first. Cut wherever the ground falls more than ``LIP_DROP_M`` between neighbours, it is
    every part with no ground within ``LIP_DROP_M`` of ``value``, beside one that has."""
    h, w = body.shape
    drop = LIP_DROP_M * hf.DM_PER_M
    index = np.arange(h * w, dtype=np.int64).reshape(h, w)
    pairs: list[NDArray[np.int64]] = []
    cut = False
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = (slice(0, h - dr), slice(max(-dc, 0), w + min(-dc, 0)))
        b = (slice(dr, h), slice(max(dc, 0), w + min(dc, 0)))
        both = body[a] & body[b]
        steep = np.abs(ground[a][both].astype(np.int32) - ground[b][both]) > drop
        pairs.append(np.stack([index[a][both], index[b][both]])[:, ~steep])
        cut |= bool(steep.any())
    if not cut:
        return []
    kept = np.concatenate(pairs, axis=1)
    graph = sp.coo_matrix((np.ones(kept.shape[1], np.int8), (kept[0], kept[1])), (h * w,) * 2)
    labels = csgraph.connected_components(graph, directed=False)[1].reshape(h, w)
    level_held = np.unique(labels[body & (ground >= value - drop)])
    below = body & ~np.isin(labels, level_held)
    if not len(level_held) or not below.any():
        return []
    parts, count = ndimage.label(below, structure=_EIGHT)
    order = np.argsort(ndimage.mean(ground, parts, np.arange(1, count + 1)))
    found = ndimage.find_objects(parts)
    drops: list[tuple[Window, BoolMask]] = []
    for k in order:
        box = found[int(k)]
        if box is not None:
            sub = _padded(box, (0, 0), pad, body.shape)
            drops.append((sub, parts[sub] == k + 1))
    return drops


def _shore(
    body: BoolMask, ground: F32Grid, bank: F32Grid, level: float
) -> tuple[BoolMask, FloatGrid]:
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


def spill_share(body: BoolMask, bank: F32Grid, level: float, step_m: float) -> float:
    """The share of the ring around ``body`` whose ground or other water stands more than
    ``PERCHED_EXCESS_M`` below ``level``: where still water at that level would run to."""
    distance = ndimage.distance_transform_edt(~body) * step_m
    ring = np.isfinite(bank) & (distance > SPILL_RING_M[0]) & (distance <= SPILL_RING_M[1])
    if not ring.any():
        return 0.0
    below = ring & (bank < np.float32(level - PERCHED_EXCESS_M * hf.DM_PER_M))
    return float(below.sum() / ring.sum())


def relevel(level_dm: float, surface_dm: FloatGrid) -> FloatGrid:
    """The level kept where it stands within ``PERCHED_EXCESS_M`` of ``surface_dm``, the
    surface where it stands twice that above, and a linear hand-over between."""
    band = np.float32(PERCHED_EXCESS_M * hf.DM_PER_M)
    excess = np.float32(level_dm) - surface_dm
    handed = surface_dm + np.maximum(2 * band - excess, 0.0)
    return np.where(excess <= band, np.float32(level_dm), handed)


def _perched_shoreline(
    body: BoolMask, ground_window: F32Grid, bank: F32Grid, value: int, step_m: float
) -> PerchedShore | None:
    """``body``'s shoreline when its level stands more than ``PERCHED_EXCESS_M`` above a
    bank, else ``None``."""
    shore, waterline = _shore(body, ground_window, bank, value)
    if not shore.any() or value - float(waterline[shore].min()) <= PERCHED_EXCESS_M * hf.DM_PER_M:
        return None
    return PerchedShore(shore, waterline, spill_share(body, bank, value, step_m))


def _surface(
    body: BoolMask,
    ground_window: F32Grid,
    shoreline: PerchedShore,
    value: int,
    wet: BoolMask | None = None,
) -> I16Grid | None:
    """The new levels on a perched ``body``: the membrane over its shoreline, handed over.

    Below a drop (``wet``, the window's water, given), the drop and the cliffs standing more
    than ``LIP_DROP_M`` over the water's ground hold nothing up. The water it joins below
    the level holds its surface, or failing that its low shoreline. ``None`` for neither.
    """
    held, waterline = shoreline.shore, shoreline.waterline
    if wet is not None:
        low = waterline < value - PERCHED_EXCESS_M * hf.DM_PER_M
        held = held & low & (waterline - ground_window <= LIP_DROP_M * hf.DM_PER_M)
        beside = np.logical_or.reduce([_shifted(wet & ~body, dr, dc, False) for dr, dc in _FOUR])
        held = held & beside if (held & beside).any() else held
        if not held.any():
            return None
    surface = harmonic_fill(waterline, held, body & ~held, 1).astype(np.float32)
    # A part joined to the rest only by a corner has no shoreline of its own to span.
    parts, count = ndimage.label(body)
    ashore = np.zeros(count + 1, bool)
    ashore[parts[held]] = True
    surface[body & ~ashore[parts]] = value
    return np.rint(relevel(value, surface[body])).astype(np.int16)


def _place(field: hf.Field, window: Window, sub: Window, body: BoolMask, step_m: float) -> Place:
    rows, cols = np.nonzero(body)
    x_m = field.x0_cm / 100 + (window[1].start + sub[1].start + cols.mean()) * step_m
    y_m = field.y0_cm / 100 + (window[0].start + sub[0].start + rows.mean()) * step_m
    return {"x_m": round(float(x_m), 1), "y_m": round(float(y_m), 1)}


def perched_levels(
    field: hf.Field, planes: WaterPlanes | None = None
) -> tuple[I16Grid | None, PerchedMeta | NoWaterPlanes]:
    """The water raster with every perched texel re-levelled, and what was changed.

    ``planes`` is ``(level_dm, grades)`` to re-level instead of the field's own.

    A body is perched when its level stands above its banks and still water at that level
    would run off across the ring around it. Its surface is then the harmonic membrane
    spanning its shoreline, so a river's follows it downhill. Water below a drop in a body
    (``_below_drops``) is judged first, by its own ring or the body's, and once re-levelled
    is a bank of the rest. Every other body, and the ocean, is returned byte for byte.
    """
    water, grades = planes or (field.water_raster(), field.water_quality_raster())
    if water is None or grades is None:
        return water, {"absent": "no water planes"}
    heights = field_heights(field)
    known = heights != hf.NODATA
    measured = (grades == hf.WATER_MEASURED) & (water != hf.NODATA) & known
    # Ground the water encloses is no bank: the artwork draws deep water too dark for its
    # blue test, so a lake's deep middle reads dry and below the level.
    wet = grades != hf.WATER_DRY
    dry = known & ~ndimage.binary_fill_holes(wet)
    # What bounds a body: dry ground at its height, other water at its level.
    banks = np.where(dry, heights, np.where(wet & (water != hf.NODATA), water, np.nan))
    banks = banks.astype(np.float32)
    out = water.copy()
    found: list[PerchedBody] = []
    candidates = cut = 0
    step_m = field.spacing_cm / 100.0
    pad = int(np.ceil(SPILL_RING_M[1] / step_m)) + 1
    for window, whole, value in _bodies(measured, water, _is_ocean, pad):
        ground_window = heights[window].astype(np.float32)
        drops = _below_drops(whole, heights[window], value, pad)
        work, rest, spills = banks[window], whole, False
        if drops:
            cut += 1
            # Water below a drop, once re-levelled, leaves the body and bounds the rest at
            # its new level; any part spills when the whole body would.
            work, rest = work.copy(), whole.copy()
            bank = np.where(whole, np.float32(np.nan), work)
            judged = _perched_shoreline(whole, ground_window, bank, value, step_m)
            spills = judged is not None and judged.spill_share > SPILL_SHARE
        parts = [(sub, mask, True) for sub, mask in drops]
        parts.append(((slice(0, whole.shape[0]), slice(0, whole.shape[1])), rest, False))
        for sub, body, below in parts:
            bank = np.where(body, np.float32(np.nan), work[sub])
            judged = _perched_shoreline(body, ground_window[sub], bank, value, step_m)
            candidates += judged is not None
            if judged is None or (judged.spill_share <= SPILL_SHARE and not spills):
                continue
            lakes = (wet[window][sub] & (water[window][sub] != hf.NODATA)) if below else None
            new = _surface(body, ground_window[sub], judged, value, lakes)
            if new is None or not (changed := new < value).any():
                continue
            out[window][sub][body] = np.minimum(new, value)
            if below:
                work[sub][body] = np.minimum(new, value)
                rest[sub][body] &= ~changed
            found.append(
                {
                    "texels": int(changed.sum()),
                    **_place(field, window, sub, body, step_m),
                    "box_level_m": value / hf.DM_PER_M,
                    "spill_share": round(judged.spill_share, 3),
                    "below_a_drop": below,
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
            "handed over linearly between. A body is cut where its ground falls more than "
            "lip_drop_m between neighbours; a part below such a cut and more than lip_drop_m "
            "under the level all over is judged first, perched when its own ring or the "
            "body's spills. Its membrane spans only the shoreline beside the water it joins "
            "more than excess_m below the level, or failing that the shoreline that far below "
            "and within lip_drop_m of its ground, never the drop or the cliffs. Re-levelled, "
            "it leaves the body and bounds the rest at its new level. The ocean level is exempt"
        ),
        "excess_m": PERCHED_EXCESS_M,
        "spill_ring_m": list(SPILL_RING_M),
        "spill_share": SPILL_SHARE,
        "lip_drop_m": LIP_DROP_M,
        "bodies_standing_above_a_bank": candidates,
        "bodies_cut_at_a_drop": cut,
        "bodies": len(found),
        "texels": int(sum(body["texels"] for body in found)),
        "largest": found[:PERCHED_LIST_MAX],
    }


def _holes(
    body: BoolMask,
    near: BoolMask,
    ground: I16Grid,
    surface: I16Grid,
    dry: BoolMask,
    step_m: float,
) -> BoolMask:
    """Which of a window's ``dry`` texels are holes in ``body``: inside the closed shape of
    the water at its level, below its ``surface``, away from where it would spill, and in a
    part that reaches the body, more than ``PERCHED_EXCESS_M`` deep somewhere and nowhere
    deeper than ``HOLE_DEPTH_MAX_M``.

    It would spill over ground outside that shape standing that far below it which runs on
    past the bridge, not over the rounded ends of a bridged gap."""
    bridge = HOLE_BRIDGE_M / step_m
    grown = ndimage.distance_transform_edt(~near) <= bridge
    hull = ndimage.binary_fill_holes(ndimage.distance_transform_edt(grown) > bridge)
    depth = surface.astype(np.int32) - ground
    band = PERCHED_EXCESS_M * hf.DM_PER_M
    hole = hull & dry & (depth > 0)
    low, _count = ndimage.label(dry & ~hull & (depth > band), structure=_EIGHT)
    away = np.unique(low[(low > 0) & ~grown])
    if len(away):
        hole &= ndimage.distance_transform_edt(~np.isin(low, away)) > bridge
    parts, _count = ndimage.label(hole, structure=_EIGHT)
    beside = np.unique(parts[ndimage.binary_dilation(body, structure=_EIGHT) & hole])
    beside = beside[beside > 0]
    if not len(beside):
        return np.zeros_like(hole)
    deepest = np.asarray(ndimage.maximum(depth, parts, beside))
    return np.isin(parts, beside[(deepest > band) & (deepest <= HOLE_DEPTH_MAX_M * hf.DM_PER_M)])


def wet_holes(
    field: hf.Field,
    before: I16Grid,
    level: I16Grid,
    grades: U8Grid,
    dropped: BoolMask | None = None,
) -> tuple[I16Grid, U8Grid, HolesMeta]:
    """``(level, grades, meta)`` with the dry holes the artwork left in measured water filled.

    The artwork's blue test reads a lake's deep middle, and water under an arch it draws
    over the lake, as dry. A hole takes the surface of the nearest texel of its body and the
    measured grade. Bodies are read off ``before``'s box levels, as ``perched_levels`` reads
    them, and ``level`` is its result. ``dropped`` is water the river reconcile took out,
    which stays out. New arrays where anything changed; the ocean is never touched.
    """
    heights = field_heights(field)
    known = heights != hf.NODATA
    wet = grades != hf.WATER_DRY
    if dropped is not None:
        wet = wet | dropped
    measured = (grades == hf.WATER_MEASURED) & (before != hf.NODATA) & known
    inland = measured & ~_is_ocean(before)
    step_m = field.spacing_cm / 100.0
    band = PERCHED_EXCESS_M * hf.DM_PER_M
    found: list[tuple[Window, BoolMask, I16Grid]] = []
    pad = 2 * int(np.ceil(HOLE_BRIDGE_M / step_m)) + 2
    for window, body, value in _bodies(measured, before, _is_ocean, pad):
        level_of = np.abs(before[window].astype(np.int32) - value) <= band
        nearest = ndimage.distance_transform_edt(~body, return_distances=False, return_indices=True)
        surface = level[window][tuple(nearest)]
        near = body | (inland[window] & level_of)
        hole = _holes(body, near, heights[window], surface, known[window] & ~wet[window], step_m)
        if hole.any():
            found.append((window, hole, surface))
    if not found:
        return level, grades, {"bodies": 0, "texels": 0}
    level, grades, was = level.copy(), grades.copy(), grades
    whole = (slice(0, None), slice(0, None))
    places: list[HoleBody] = []
    for window, hole, surface in found:
        target = level[window]
        # Two bodies that close over one gap leave it at the lower surface.
        first = grades[window][hole] == hf.WATER_DRY
        target[hole] = np.where(first, surface[hole], np.minimum(target[hole], surface[hole]))
        grades[window][hole] = hf.WATER_MEASURED
        places.append(
            {
                "texels": int(hole.sum()),
                **_place(field, window, whole, hole, step_m),
                "level_m": float(surface[hole].max()) / hf.DM_PER_M,
            }
        )
    places.sort(key=lambda place: -place["texels"])
    meta: HolesMeta = {
        "rule": (
            "dry ground inside the shape the measured water at a body's level closes over "
            "gaps up to twice bridge_m, standing below the surface, farther than bridge_m "
            "from dry ground outside that shape standing excess_m below and running on past "
            "bridge_m, in a part reaching the body, deeper than excess_m somewhere and "
            "nowhere deeper than depth_max_m"
        ),
        "bridge_m": HOLE_BRIDGE_M,
        "depth_max_m": HOLE_DEPTH_MAX_M,
        "excess_m": PERCHED_EXCESS_M,
        "bodies": len(found),
        "texels": int(np.count_nonzero(grades != was)),
        "largest": places[:PERCHED_LIST_MAX],
    }
    return level, grades, meta


class WaterSurfaces(NamedTuple):
    """The water a render draws: the ocean reach, and the level and grade planes after the
    rivers took their boxes' water back, perched water was re-levelled and holes filled."""

    reach: U8Grid | None
    reach_meta: JsonObject
    level: I16Grid | None
    grades: U8Grid | None
    perched: PerchedMeta | NoWaterPlanes | None

    @property
    def planes(self) -> WaterPlanes | None:
        return None if self.level is None else (self.level, self.grades)


def water_surfaces(
    field: hf.Field, kernel_only: bool, rivers: ReconciledWater | None = None
) -> WaterSurfaces:
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
    own = field.water_quality_raster()
    grades = own if rivers is None else rivers.grades
    if "bodies" in meta and level is not None and grades is not None:
        print(f"  perched water: {meta['bodies']} bodies, {meta['texels']} texels re-levelled")
        before = field_water(field)[0] if rivers is None else rivers.water_dm
        dropped = None if rivers is None else (own != hf.WATER_DRY) & (grades == hf.WATER_DRY)
        level, grades, holes = wet_holes(field, before, level, grades, dropped)
        meta["holes"] = holes
        print(f"  holes in lakes: {holes['bodies']} bodies, {holes['texels']} texels re-wetted")
    return WaterSurfaces(reach, reach_meta, level, grades, meta)
