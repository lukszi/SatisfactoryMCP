"""River water: the spline ribbons on the 1 m grid, reconciled with the field's own water.

The field levels a river's water on the river actor's box, one AABB around the whole river,
so it stands metres too high and as wide as the artwork drew it. Here that water gives way
to the spline's own sloped plane, whose banks are where it meets the ground. Why each
constant is what it is: docs/spatial-and-map.md section 34.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from mapgen.cache import RIVER_CACHE_DIR_NAME, cached_rivers, river_stamp, write_rivers
from mapgen.gamedata.water.channel import lower_bodies
from mapgen.gamedata.water.rivers import box_tops, ribbon_planes, sample_rivers
from mapgen.palette.water.shore import OCEAN_LEVEL_M, shore_terms
from mapgen.palette.water.surface import WATER_DEPTH_FULL_M, water_planes
from mapgen.terrain.sample import sample_plain, sample_surface
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "RIVER_EDGE_FADE_M",
    "RIVER_LEVEL_MATCH_M",
    "RIVER_MAX_DEPTH_M",
    "RIVER_OVER_WATER_M",
    "RIVER_STEP_M",
    "RIVER_TIE_M",
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


class RiverWater:
    """The ribbons and the field's water with the river boxes' share taken back out."""

    def __init__(self, cached: dict, field):
        samples = sample_rivers(cached["rivers"])
        shape = field.height_dm.shape
        heights = field.height_dm
        ground = np.where(
            heights == hf.NODATA, np.float32(np.nan), heights / np.float32(hf.DM_PER_M)
        )
        planes = ribbon_planes(samples, shape=shape, hang=(ground, RIVER_MAX_DEPTH_M))
        boxes = [(name, tuple(box)) for name, box in cached["boxes"]]
        river_top, other_top = box_tops(boxes, True, shape), box_tops(boxes, False, shape)
        level, u, half = planes["level_m"], planes["u"], planes["half_m"]
        zone = np.isfinite(u)
        with np.errstate(invalid="ignore"):
            deep = (level - ground > RIVER_MAX_DEPTH_M) | ~np.isfinite(ground)
            fade = np.clip((1.0 - u) * half / RIVER_EDGE_FADE_M, 0.0, 1.0)
            fade *= np.clip((RIVER_MAX_DEPTH_M - (level - ground)) / RIVER_EDGE_FADE_M, 0, 1)
        speaks = zone & ~deep
        steps = _steps(level)
        with np.errstate(invalid="ignore"):
            valley = speaks & (ground - level <= RIVER_MAX_DEPTH_M)
            draws = speaks & (u <= 1.0) & ~steps & (level > ground)
        tops = (river_top, other_top, boxes)
        self.water_dm, self.grades, self.stats = _reconcile(
            field, ground, (speaks, valley, draws), level, tops
        )
        del valley, draws
        fade *= _below_other(level, self.water_dm, self.grades) * ~steps
        fade = np.where(speaks, fade, 0.0)
        self.presence = np.round(fade * 255).astype(np.uint8)
        self.level_dm = np.where(zone, np.round(level * hf.DM_PER_M), hf.NODATA).astype(np.int16)
        self.stats.update(
            rivers=len(cached["rivers"]),
            sections=sum(len(r["sections"]) for r in cached["rivers"]),
            centreline_km=round(_length_m(samples) / 1000, 2),
            ribbon_km2=round(float((self.presence > 0).sum()) / 1e6, 4),
        )

    def over(self, terms: dict, z_m, linear, spacing_m: float) -> dict:
        """The band's water with the ribbons laid over it."""
        level_dm, missing = sample_surface(self.level_dm, linear, linear, hf.NODATA)
        river_m = np.where(missing, np.nan, level_dm / np.float32(hf.DM_PER_M))
        presence = sample_plain(self.presence, linear) / np.float32(255.0)
        return river_terms(terms, z_m, river_m, presence, spacing_m)


def _steps(level) -> np.ndarray:
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


def _below_other(level, water_dm, grades):
    """1 where the plane is not hanging over other water, fading to 0 by twice the margin."""
    other = np.where(grades != hf.WATER_DRY, water_dm / np.float32(hf.DM_PER_M), np.nan)
    with np.errstate(invalid="ignore"):
        excess = np.nan_to_num(level - other, nan=0.0)
    return np.clip(2.0 - excess / RIVER_OVER_WATER_M, 0.0, 1.0)


def _length_m(samples: dict) -> float:
    step = np.hypot(np.diff(samples["x"]), np.diff(samples["y"]))
    return float(step[samples["section"][1:] == samples["section"][:-1]].sum())


def _reconcile(field, ground, ribbon, plane_m, tops):
    """The field's water planes with every texel a river box levelled taken back.

    A texel under another water box (a lake the river's AABB overhangs) takes that box's
    level, or goes dry where the ground stands above it, unless the ribbon speaks there and
    runs above that level: then the box is a lake's AABB reaching over the river's valley. Any other where the ribbon speaks
    (its reach, minus where the plane hangs too far over the ground) is dropped: the ribbon
    draws the river there. Then a lower body takes back the box tops over it
    (``gamedata.water.channel.lower_bodies``). ``ribbon`` is ``(speaks, valley, draws)``: ``valley``
    where the ground stands at most ``RIVER_MAX_DEPTH_M`` over the plane, ``draws`` where the
    ribbon covers the texel. Water in the valley more than that above the plane is a higher
    body's box over the river (``_over_the_river``).
    """
    speaks, valley, draws = ribbon
    river_top, other_top, boxes = tops
    water = field.water_raster().copy()
    grades = field.water_quality_raster().copy()
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
    water[drop] = hf.NODATA
    grades[drop] = hf.WATER_DRY
    water, lowered = lower_bodies(water, grades, field.height_dm, boxes, OCEAN_LEVEL_M)
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


def _over_the_river(water, wet, plane_m, valley) -> np.ndarray:
    """Wet texels in ``valley`` whose level stands more than ``RIVER_MAX_DEPTH_M`` above the
    ribbon's plane: a higher body's box reaching over the river, as over a fall's basin."""
    with np.errstate(invalid="ignore"):
        return valley & wet & (water / np.float32(hf.DM_PER_M) - plane_m > RIVER_MAX_DEPTH_M)


def river_terms(terms: dict, z_m, river_m, presence, spacing_m: float) -> dict:
    """Lay a river surface over the band's other water: whichever surface is higher shows.

    The river's coverage is its plane crossing the drawn ground, one pixel wide, so the
    banks are where the game's plane meets the terrain and not where a mask ends. Past the
    other water's last wet texel (``terms["wet"]``), away from the sea, its edge's blur is no
    water the river gives way to.
    """
    level = np.where(np.isfinite(river_m), river_m, z_m - np.float32(1e3))
    cross = shore_terms(z_m, spacing_m, level)
    other = z_m + terms["depth_m"]
    blur = (terms.get("wet", 1.0) <= 0.0) & (terms["ocean"] <= 0.0)
    rules = presence * ((terms["cover"] <= 0.0) | blur | (level >= other - RIVER_TIE_M))
    mine = cross["cover"] * rules
    cover = mine + (1.0 - mine) * terms["cover"]
    share = mine / np.maximum(cover, np.float32(1e-6))
    depth_m = cross["depth_m"]
    out = dict(terms)
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


def water_sources(field, rivers: RiverWater | None, level=None, grades=None):
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


def load_rivers(cache_root: Path, build, sweep_once, field) -> tuple[RiverWater, dict]:
    """The rivers from the cache, or from the shared sweep into it; and what to record."""
    cache_dir = cache_root / RIVER_CACHE_DIR_NAME
    started = time.time()
    stamp = river_stamp(build, READER_VERSIONS["river_splines"])
    cached = cached_rivers(cache_dir, stamp)
    reused = cached is not None
    if cached is None:
        sweep = sweep_once()
        cached = write_rivers(cache_dir, stamp, sweep["rivers"], sweep["water"])
    rivers = RiverWater(cached, field)
    source = {**rivers.stats, "cache": "reused" if reused else "swept",
              "seconds": round(time.time() - started, 1)}  # fmt: skip
    print(f"  rivers: {source}")
    return rivers, source
