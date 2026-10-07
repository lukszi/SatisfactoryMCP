"""River water: the spline ribbons on the 1 m grid, reconciled with the field's own water.

The field levels a river's water on the river actor's box, one AABB around the whole river,
so it stands metres too high and as wide as the artwork drew it. Here that water gives way
to the spline's own sloped plane, whose banks are where it meets the ground. Why each
constant is what it is: docs/spatial-and-map.md section 34.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias, TypedDict, cast

import numpy as np
from scipy import ndimage

from mapgen.cache import RIVER_CACHE_DIR_NAME, cached_rivers, river_stamp, write_rivers
from mapgen.gamedata.level.sweep import Sweep
from mapgen.gamedata.water.channel import lower_bodies
from mapgen.gamedata.water.rivers import (
    RiverActor,
    RiverSamples,
    box_tops,
    ribbon_planes,
    river_volumes,
    sample_rivers,
)
from mapgen.palette.scene import BandTaps, FloatGrid, WaterTerms, field_heights, field_water
from mapgen.palette.water.seams import SeamFeather, feather_steps
from mapgen.palette.water.shore import OCEAN_LEVEL_M, shore_terms
from mapgen.palette.water.surface import WATER_DEPTH_FULL_M, water_planes
from mapgen.terrain.sample import sample_plain, sample_surface
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I16Grid, U8Grid
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "RIVER_EDGE_FADE_M",
    "RIVER_HANDOVER_M",
    "RIVER_JOINTS",
    "RIVER_LEVEL_MATCH_M",
    "RIVER_MAX_DEPTH_M",
    "RIVER_MOUTH_BLEND_M",
    "RIVER_MOUTH_FADE_M",
    "RIVER_OVER_WATER_M",
    "RIVER_STEP_M",
    "RIVER_TIE_M",
    "RiverCache",
    "RiverWater",
    "load_rivers",
    "river_terms",
    "water_sources",
]

#: A wet texel's level came from a river box when it equals that box's top within this.
RIVER_LEVEL_MATCH_M = 0.05

#: Deeper than this over the 1 m ground, the plane is not over its own channel.
RIVER_MAX_DEPTH_M = 8.0

#: The plane's own edge fades out over this many metres inside it.
RIVER_EDGE_FADE_M = 1.5

#: A plane hanging more than this over other water is not drawn there, gone at twice it.
RIVER_OVER_WATER_M = 1.0

#: A jump in plane height between neighbouring texels larger than this is two planes.
RIVER_STEP_M = 0.5

#: Where a river meets other water, it is drawn unless that surface stands this much higher.
RIVER_TIE_M = 0.05

#: Over this much difference in level a river hands over to the other water it meets,
#: centred on the tie, so a joint or a mouth changes tone along a ramp, not a line.
RIVER_HANDOVER_M = 1.0

#: Over other water a river's plane fades in over this many metres from its edges and open
#: ends, where on land it takes ``RIVER_EDGE_FADE_M``; the other water's edge is blended over
#: this many metres.
RIVER_MOUTH_FADE_M = 12.0
RIVER_MOUTH_BLEND_M = 2.0

#: The joints between a river's sections: steps of 0.5 to 2 m feathered over 8 m either side,
#: through a surface sloping at most 0.25 m from texel to texel.
RIVER_JOINTS = SeamFeather(low_m=0.5, high_m=2.0, reach=8, run_m=0.25)

#: The water boxes by actor name: the river boxes, and every other surface box.
Boxes: TypeAlias = list[tuple[str, tuple[float, ...]]]


class RiverCache(TypedDict):
    """``rivers.cache``: its stamp, the river splines as swept, and every water box."""

    stamp: JsonObject
    rivers: list[RiverActor]
    boxes: list[tuple[str, list[float]]]


class RiverWater:
    """The ribbons and the field's water with the river boxes' share taken back out."""

    def __init__(self, cached: RiverCache, field: hf.Field) -> None:
        samples = sample_rivers(cached["rivers"])
        heights = field_heights(field)
        shape = heights.shape
        ground = np.where(
            heights == hf.NODATA, np.float32(np.nan), heights / np.float32(hf.DM_PER_M)
        )
        planes = ribbon_planes(samples, shape=shape, hang=(ground, RIVER_MAX_DEPTH_M))
        boxes: Boxes = [(name, tuple(box)) for name, box in river_volumes(cached["boxes"])]
        river_top, other_top = box_tops(boxes, True, shape), box_tops(boxes, False, shape)
        # ``across`` is the distance from the centreline in half widths, 1 at the plane's edge.
        level, across, half_m = planes["level_m"], planes["u"], planes["half_m"]
        zone = np.isfinite(across)
        with np.errstate(invalid="ignore"):
            deep = (level - ground > RIVER_MAX_DEPTH_M) | ~np.isfinite(ground)
            fade = np.clip((1.0 - across) * half_m / RIVER_EDGE_FADE_M, 0.0, 1.0)
            fade *= np.clip((RIVER_MAX_DEPTH_M - (level - ground)) / RIVER_EDGE_FADE_M, 0, 1)
        speaks = zone & ~deep
        steps = _beside_a_step(level)
        with np.errstate(invalid="ignore"):
            valley = speaks & (ground - level <= RIVER_MAX_DEPTH_M)
            draws = speaks & (across <= 1.0) & ~steps & (level > ground)
        drawn, joints = feather_steps(level, zone & np.isfinite(level), RIVER_JOINTS)
        lips = _beside_a_step(drawn)
        tops = (river_top, other_top, boxes)
        self.water_dm: I16Grid
        self.grades: U8Grid
        self.stats: JsonObject
        self.water_dm, self.grades, self.stats = _reconcile(
            field, ground, (speaks, valley, draws, lips), (level, drawn), tops
        )
        del valley, draws
        fade *= _below_other(drawn, self.water_dm, self.grades) * ~lips
        on_plane, other = zone & (across <= 1.0), self.grades != hf.WATER_DRY
        fade *= _mouth_fade(on_plane, other)
        fade = np.where(speaks, fade, 0.0)
        self.presence: U8Grid = np.round(fade * 255).astype(np.uint8)
        self.yields: U8Grid = np.round(_other_yields(other, on_plane & speaks) * 255).astype(
            np.uint8
        )
        self.level_dm: I16Grid = np.where(zone, np.round(drawn * hf.DM_PER_M), hf.NODATA).astype(
            np.int16
        )
        self.stats["joint_texels_feathered"] = joints
        self.stats.update(
            rivers=len(cached["rivers"]),
            sections=sum(len(river["sections"]) for river in cached["rivers"]),
            centreline_km=round(_length_m(samples) / 1000, 2),
            ribbon_km2=round(float((self.presence > 0).sum()) / 1e6, 4),
        )

    def over(
        self, terms: WaterTerms, z_m: FloatGrid, linear: BandTaps, spacing_m: float
    ) -> WaterTerms:
        """The band's water with the ribbons laid over it."""
        level_dm, missing = sample_surface(self.level_dm, linear, linear, hf.NODATA)
        river_m = np.where(missing, np.nan, level_dm / np.float32(hf.DM_PER_M))
        presence = sample_plain(self.presence, linear) / np.float32(255.0)
        yields = sample_plain(self.yields, linear) / np.float32(255.0)
        return river_terms(terms, z_m, river_m, presence, spacing_m, yields)


def _mouth_fade(plane: BoolMask, other: BoolMask) -> FloatGrid:
    """1 on the plane but over other water, where it fades in over ``RIVER_MOUTH_FADE_M`` from
    the plane's edge and its open ends, blended in over the other water's edge."""
    inside = ndimage.distance_transform_cdt(plane, metric="chessboard").astype(np.float32)
    wide = np.clip(inside / np.float32(RIVER_MOUTH_FADE_M), 0.0, 1.0)
    over = ndimage.gaussian_filter(other.astype(np.float32), RIVER_MOUTH_BLEND_M)
    return 1.0 - over * (1.0 - wide)


def _other_yields(other: BoolMask, plane: BoolMask) -> FloatGrid:
    """On the plane, how far other water under it gives way to the river whatever their
    levels: all of it at that water's edge, none ``RIVER_MOUTH_FADE_M`` in, so where a box
    leaves water standing in a river's channel it ends in a ramp, not a line."""
    inside = ndimage.distance_transform_cdt(other, metric="chessboard").astype(np.float32)
    ramp = np.clip(1.0 - (inside - 1.0) / np.float32(RIVER_MOUTH_FADE_M), 0.0, 1.0)
    return np.where(plane & other, ramp, np.float32(0.0))


def _beside_a_step(level: F32Grid) -> BoolMask:
    """Texels beside a step between two overlapping planes, where no sampler can be right."""
    step = np.zeros(level.shape, bool)
    with np.errstate(invalid="ignore"):
        rows = np.abs(np.diff(level, axis=0)) > RIVER_STEP_M
        cols = np.abs(np.diff(level, axis=1)) > RIVER_STEP_M
    step[1:] |= rows
    step[:-1] |= rows
    step[:, 1:] |= cols
    step[:, :-1] |= cols
    return step


def _below_other(level: F32Grid, water_dm: I16Grid, grades: U8Grid) -> FloatGrid:
    """1 where the plane is not hanging over other water, fading to 0 by twice the margin."""
    other = np.where(grades != hf.WATER_DRY, water_dm / np.float32(hf.DM_PER_M), np.nan)
    with np.errstate(invalid="ignore"):
        excess = np.nan_to_num(level - other, nan=0.0)
    return np.clip(2.0 - excess / RIVER_OVER_WATER_M, 0.0, 1.0)


def _length_m(samples: RiverSamples) -> float:
    step = np.hypot(np.diff(samples["x"]), np.diff(samples["y"]))
    return float(step[samples["section"][1:] == samples["section"][:-1]].sum())


def _reconcile(
    field: hf.Field,
    ground: FloatGrid,
    ribbon: tuple[BoolMask, BoolMask, BoolMask, BoolMask],
    planes: tuple[F32Grid, F32Grid],
    tops: tuple[F32Grid, F32Grid, Boxes],
) -> tuple[I16Grid, U8Grid, JsonObject]:
    """The field's water planes with every texel a river box levelled taken back.

    A texel under another water box (a lake the river's AABB overhangs) takes that box's
    level, or goes dry where the ground stands above it, unless the ribbon speaks there and
    runs above that level: then the box is a lake's AABB reaching over the river's valley.
    Any other where the ribbon speaks (its reach, minus where the plane hangs too far over the
    ground) is dropped: the ribbon draws the river there; but beside a fall in the drawn
    plane, where the ribbon draws nothing, the water stays at the plane's level above the
    lip. Then a lower body takes back the box tops over it
    (``gamedata.water.channel.lower_bodies``). ``ribbon`` is ``(speaks, valley, draws,
    lips)``: ``valley`` where the ground stands at most ``RIVER_MAX_DEPTH_M`` over the plane,
    ``draws`` where the ribbon covers the texel, ``lips`` beside a step of the drawn plane.
    ``planes`` is ``(plane, drawn)``, the plane as read and as drawn. Water in the valley more
    than that above the plane is a higher body's box over the river (``_over_the_river``).
    """
    speaks, valley, draws, lips = ribbon
    plane_m, drawn = planes
    river_top, other_top, boxes = tops
    level_dm, quality = field_water(field)
    water, grades = level_dm.copy(), quality.copy()
    level = water / np.float32(hf.DM_PER_M)
    wet = grades != hf.WATER_DRY
    with np.errstate(invalid="ignore"):
        from_river = (
            wet
            & (np.abs(level - river_top) <= RIVER_LEVEL_MATCH_M)
            & ~(other_top >= level - RIVER_LEVEL_MATCH_M)
        )
        over = speaks & (plane_m > other_top + RIVER_OVER_WATER_M)
        relevel = from_river & np.isfinite(other_top) & ~over
        water[relevel] = np.round(other_top[relevel] * hf.DM_PER_M).astype(np.int16)
        above = (grades == hf.WATER_MEASURED) & (ground >= other_top)
        drop = from_river & ((speaks & ~relevel) | (relevel & above))
    del level
    lip = drop & lips & ~relevel
    water[lip] = np.round(_upper_plane(drawn, lip) * hf.DM_PER_M).astype(np.int16)
    drop &= ~lip
    water[drop] = hf.NODATA
    grades[drop] = hf.WATER_DRY
    water, lowered = lower_bodies(water, grades, field_heights(field), boxes, OCEAN_LEVEL_M)
    hung = _over_the_river(water, grades != hf.WATER_DRY, plane_m, valley)
    water[hung] = np.round(plane_m[hung] * hf.DM_PER_M).astype(np.int16)
    with np.errstate(invalid="ignore"):
        gone = hung & (draws | (ground >= plane_m))
    water[gone] = hf.NODATA
    grades[gone] = hf.WATER_DRY
    return (
        water,
        grades,
        {
            "from_river_boxes_km2": round(float(from_river.sum()) / 1e6, 4),
            "dropped_km2": round(float((drop | gone).sum()) / 1e6, 4),
            "relevelled_km2": round(float((relevel & ~drop).sum()) / 1e6, 4),
            "lower_bodies_km2": round(lowered / 1e6, 4),
            "over_the_river_km2": round(float(hung.sum()) / 1e6, 4),
        },
    )


def _upper_plane(plane: F32Grid, where: BoolMask) -> F32Grid:
    """At each texel of ``where``, the highest plane level among it and its 8-neighbours."""
    rows, cols = np.nonzero(where)
    best = np.full(len(rows), -np.inf, np.float32)
    height, width = plane.shape
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            r, c = np.clip(rows + dr, 0, height - 1), np.clip(cols + dc, 0, width - 1)
            best = np.fmax(best, plane[r, c])
    return best


def _over_the_river(water: I16Grid, wet: BoolMask, plane_m: F32Grid, valley: BoolMask) -> BoolMask:
    """Wet texels in ``valley`` whose level stands more than ``RIVER_MAX_DEPTH_M`` above the
    ribbon's plane: a higher body's box reaching over the river, as over a fall's basin."""
    with np.errstate(invalid="ignore"):
        return valley & wet & (water / np.float32(hf.DM_PER_M) - plane_m > RIVER_MAX_DEPTH_M)


def river_terms(
    terms: WaterTerms,
    z_m: FloatGrid,
    river_m: FloatGrid,
    presence: FloatGrid,
    spacing_m: float,
    yields: FloatGrid | None = None,
) -> WaterTerms:
    """Lay a river surface over the band's other water: whichever surface is higher shows,
    handed over along ``RIVER_HANDOVER_M`` of level.

    The river's coverage is its plane crossing the drawn ground, one pixel wide, so the
    banks are where the game's plane meets the terrain and not where a mask ends. Past the
    other water's last wet texel (``terms["wet"]``), away from the sea, its edge's blur is no
    water the river gives way to; ``yields`` is how far the other water gives way to it
    whatever the levels (``RiverWater.yields``).
    """
    level = np.where(np.isfinite(river_m), river_m, z_m - np.float32(1e3))
    cross = shore_terms(z_m, spacing_m, level)
    other = z_m + terms["depth_m"]
    blur = (terms.get("wet", 1.0) <= 0.0) & (terms["ocean"] <= 0.0)
    handover = (level - other + np.float32(RIVER_TIE_M)) / np.float32(RIVER_HANDOVER_M) + 0.5
    wins = np.clip(handover, 0.0, 1.0)
    if yields is not None:
        wins = np.maximum(wins, yields)
    rules = presence * np.maximum((terms["cover"] <= 0.0) | blur, wins)
    mine = cross["cover"] * rules
    cover = mine + (1.0 - mine) * terms["cover"]
    share = mine / np.maximum(cover, np.float32(1e-6))
    depth_m = cross["depth_m"]
    out = terms.copy()
    out.update(
        cover=cover,
        depth_m=share * depth_m + (1.0 - share) * terms["depth_m"],
        depth=share * np.clip(depth_m / WATER_DEPTH_FULL_M, 0.0, 1.0)
        + (1.0 - share) * terms["depth"],
        river=share,
        river_below_m=cross["below_m"],
        banks=np.maximum(terms["banks"], rules),
        above_m=np.where(
            rules > 0, np.minimum(terms["above_m"], cross["above_m"]), terms["above_m"]
        ),
    )
    return out


def water_sources(
    field: hf.Field,
    rivers: RiverWater | None,
    level: I16Grid | None = None,
    grades: U8Grid | None = None,
) -> tuple[I16Grid | None, U8Grid | None, U8Grid | None]:
    """``(water level plane, wet, measured)``: the field's, or the reconciled ones.

    ``level`` replaces the level plane: the same water, re-levelled where it was perched.
    ``grades`` replaces the quality plane the wet and measured planes are read from, so
    water the run re-wet or added (``palette.water.perched``, ``open_sea``) is drawn.
    """
    if grades is None and rivers is not None:
        grades = rivers.grades
    if grades is None:
        wet, measured, _source = water_planes(field)
    else:
        wet = (grades != hf.WATER_DRY).astype(np.uint8)
        measured = (grades == hf.WATER_MEASURED).astype(np.uint8)
    if level is None:
        level = field.water_raster() if rivers is None else rivers.water_dm
    return level, wet, measured


def load_rivers(
    cache_root: Path, build: str | None, sweep_once: Callable[[], Sweep], field: hf.Field
) -> tuple[RiverWater, JsonObject]:
    """The rivers from the cache, or from the shared sweep into it; and what to record."""
    cache_dir = cache_root / RIVER_CACHE_DIR_NAME
    started = time.time()
    stamp = river_stamp(build, READER_VERSIONS["river_splines"])
    cached = cached_rivers(cache_dir, stamp)
    reused = cached is not None
    if cached is None:
        sweep = sweep_once()
        if "rivers" not in sweep or "water" not in sweep:
            raise ValueError("the level sweep came back without its rivers and water boxes")
        cached = write_rivers(cache_dir, stamp, sweep["rivers"], sweep["water"])
    # The stamp matched, or the sweep was just written: the cache has its shape.
    rivers = RiverWater(cast(RiverCache, cached), field)
    source: JsonObject = {**rivers.stats, "cache": "reused" if reused else "swept",
                          "seconds": round(time.time() - started, 1)}  # fmt: skip
    print(f"  rivers: {source}")
    return rivers, source
