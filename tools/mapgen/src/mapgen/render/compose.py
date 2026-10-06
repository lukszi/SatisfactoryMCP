"""The band loop that composes a layer's sheet: two height regimes, water and colour.

Moved from ``tools/gen_map_renders.py``; the bands run on threads (``render/drawpool.py``).
"""

from __future__ import annotations

import time
from contextlib import ExitStack, closing
from functools import partial
from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.borrow import BORROW_CLAMP, BORROW_GAIN
from mapgen.lighting.hillshade import (
    SUN_ALTITUDE_DEG,
    flat_shade,
    hillshade,
    slope_degrees,
    sun_dot,
)
from mapgen.palette.painted.ground import ROCK_GRID_M, painted_colours, painted_ndl
from mapgen.palette.relief import relief_colours
from mapgen.palette.styles import (
    LAYER_PAINTERS,
    NOISE_SEED,
    biome_index,
    noise_fields,
    ramp_range,
    with_sea,
    with_void,
)
from mapgen.palette.water.falls import draw_falls
from mapgen.palette.water.rivers import water_sources
from mapgen.palette.water.shore import (
    MESH_FULL_LIFT_M,
    OCEAN_LEVEL_M,
    blend_water,
    composite_meshes,
    shore_terms,
)
from mapgen.palette.water.surface import (
    WATER_DEPTH_FULL_M,
    WATER_EDGE_BLUR_M,
    water_alpha,
    water_depth_fraction,
)
from mapgen.render.drawpool import bands_held, in_order
from mapgen.terrain.crown_stamp import crown_band
from mapgen.terrain.measure import SEAM_MID
from mapgen.terrain.rasters import pixel_coverage
from mapgen.terrain.sample import (
    frame_coordinates,
    grid_position,
    reads_nothing,
    sample_coverage,
    sample_noise,
    sample_plain,
    sample_surface,
    taps_footprint,
    taps_linear,
    taps_pchip,
)
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.core.mapprogress import encode_stage
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BAND_HALO",
    "BAND_ROWS",
    "DIRECT_LIFT_KNEE_M",
    "band_water",
    "blend_regimes",
    "composite_top",
    "crowns_in_band",
    "render_layer",
]

#: The knee of the smoothed positive part that lets a rock raise the ground and never lower
#: it, in metres: the field's own hard ``max`` with its corner rounded. A hard max puts a
#: first-derivative discontinuity where the rock meets the ground, and the hillshade would
#: draw it as a line around the base of every formation on the map. A quarter of a metre
#: sits at most an eighth of one above the hard answer and never below it, so the rule holds
#: exactly rather than nearly.
DIRECT_LIFT_KNEE_M = 0.25

#: Rows of the output drawn at a time. 256 rows of 16384 costs about 17 MB of float32 per
#: intermediate; the whole sheet at once would be over a gigabyte apiece.
BAND_ROWS = 256

#: The halo each band is computed with, so the hillshade's gradient at a band edge sees the
#: rows on the other side of it -- and so does the water edge's blur, which reaches further
#: than the gradient does. Cropped off before the band is stored, so no output pixel was
#: computed from a one-sided difference or a truncated kernel. Size it against the widest
#: kernel in the band: too small and the render draws a seam every 256 rows.
BAND_HALO = 8


def composite_top(z_m, top_z_cm, top_coverage, subsamples: int = 1) -> np.ndarray:
    """``z_m`` raised by the top raster through the same coverage and smoothed lift as rocks."""
    z_cm, fraction = top_z_cm, pixel_coverage(top_coverage, subsamples)
    w = np.clip(fraction, 0.0, 1.0)
    delta = z_cm / np.float32(100.0) - z_m
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    return (z_m + w * 0.5 * (delta + np.sqrt(delta * delta + knee * knee))).astype(np.float32)


def blend_regimes(base_m, missing, direct, linear, subsamples, keep=None):
    """The two-regime height and what it was made of: ``(z_m, missing, w, switched)``.

    This is the field's **own composition rule**, performed at the render's spacing instead
    of read back from the 1 m fold that rule already produced: ``base_m`` is the
    landscape-and-fill lattice interpolated here, and the same rocks go on top of it,
    rasterised at 0.229 m rather than folded onto a metre first.

    ``z = base + w * lift(z_direct - base)``. ``w`` is the direct raster's **coverage** of
    the pixel: the share of its sub-samples a triangle covered, so at one sub-sample a pixel
    is rock at the triangle's own height or ground, and no rock height is ever spread to a
    neighbour across a silhouette. ``lift`` is a **smoothed positive
    part**, which is the other half of the field's rule -- a rock may raise the ground and
    may never lower it -- without the first-derivative discontinuity a hard ``max`` would put
    exactly where the rock meets the ground. It is never negative and sits at most
    ``DIRECT_LIFT_KNEE_M / 2`` above the hard answer.

    Where the lattice knows nothing -- inside a formation big enough that no landscape texel
    survives under it -- the caller passes the whole field's own fold as ``base_m``. There
    the coverage is 1 and the rock is the answer either way. ``keep`` scales the coverage:
    the share the void leaves a rock under the sea's level (``_rock_kept``).
    """
    z_cm, coverage = direct
    fraction = pixel_coverage(coverage, subsamples)
    if keep is not None:
        fraction = fraction * keep
    w = np.clip(fraction, 0.0, 1.0).astype(np.float32)
    z_direct_m = z_cm / np.float32(100.0)
    delta = z_direct_m - base_m
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    lift = 0.5 * (delta + np.sqrt(delta * delta + knee * knee))
    z_m = base_m + w * lift
    # Where the field has nothing at all and the geometry does -- a rock standing off the
    # edge of the landscape -- the geometry is the whole answer and the pixel stops being
    # no-data. A switch rather than a fade, and allowed to be one: the no-data boundary is
    # already a hard edge the render paints the page's own sea against.
    only_rock = missing & (fraction > 0.0)
    z_m = np.where(only_rock, z_direct_m, z_m)
    w = np.where(only_rock, np.float32(1.0), w)
    # The counterfactual the seam trace measures the blend against: the same two surfaces
    # joined by a switch. Computed here so the trace is never handed two arrays to pair up.
    switched = np.where(w >= SEAM_MID, np.maximum(z_direct_m, base_m), base_m)
    return (
        z_m.astype(np.float32),
        missing & (fraction <= 0.0),
        w,
        switched.astype(np.float32),
    )


def band_water(z_m, water_m, wet, measured, blur_px, reach, linear, spacing_m) -> dict:
    """Recipe 5's water, and within ``reach`` of the sea the ocean's crossing rule. ``wet``
    rides along for the rivers: past the last wet texel, the edge's blur is no water."""
    old_cover = water_alpha(z_m, water_m, wet, measured, blur_px)
    old_depth = water_depth_fraction(z_m, water_m, measured)
    if reach is None:
        terms = blend_water(None, old_cover, old_depth, None, WATER_DEPTH_FULL_M)
    else:
        terms = blend_water(
            sample_coverage(reach, linear),
            old_cover,
            old_depth,
            shore_terms(z_m, spacing_m),
            WATER_DEPTH_FULL_M,
        )
    terms["wet"] = wet
    return terms


def crowns_in_band(painted, x_cm, y_cm, spacing_m, unlit=False) -> dict | None:
    """The crowns over these pixel centres, with their domes lit by the shared sun."""
    if painted.crowns is None:
        return None
    step_cm = spacing_m * 100.0
    band = crown_band(
        painted.crowns, x_cm[0] - step_cm / 2, y_cm[0] - step_cm / 2, step_cm, len(y_cm), len(x_cm)
    )
    gain = np.float32(painted.palette["crowns"]["dome_gain"])
    flat = np.float32(np.sin(np.deg2rad(SUN_ALTITUDE_DEG)))
    dome = band["dome_m"] * gain
    band["ndl"] = np.full(dome.shape, flat) if unlit else sun_dot(dome, spacing_m)
    return band


def _band_family(meshes, band):
    """The render-only meshes' rock family on this band; None for a cache without the plane."""
    return None if meshes is None or len(meshes) < 3 else np.asarray(meshes[2][band])


def _capture(surface, rows, z_m, missing, cover) -> None:
    """Hand one band's drawn heights and land weight, halo cropped, to the lighting stage."""
    top, lo, bottom, c0, c1 = rows
    keep = slice(top - lo, bottom - lo)
    dry = np.where(missing, 0.0, 1.0 - cover)
    surface.put(top, z_m[keep], dry[keep], slice(c0, c1))


def _band_water(z_m, planes, smooth, linear):
    """One band's water surface, level (NaN where none), wet cover and measured share.

    ``planes`` ends with the run's ``OpenSea`` or None. With it, the wet cover counts only
    the share of a pixel that is not void, so the void's edge is never drawn as land.
    """
    water, wet_plane, measured_plane, sea = planes
    if wet_plane is None:
        wet = measured = np.zeros(z_m.shape, np.float32)
        return z_m, np.full(z_m.shape, np.nan, np.float32), wet, measured
    water_dm, water_missing = sample_surface(water, smooth, linear, hf.NODATA)
    water_m = water_dm / np.float32(hf.DM_PER_M)
    level_m = np.where(water_missing, np.nan, water_m)
    wet = sample_coverage(wet_plane, linear)
    measured = sample_coverage(measured_plane, linear) / np.where(wet <= 0.0, 1.0, wet)
    if sea is not None and not reads_nothing(sea.void.cover, linear):
        land = 1.0 - sample_plain(sea.void.cover, linear) / np.float32(255.0)
        wet = np.clip(wet / np.maximum(land, np.float32(1e-3)), 0.0, 1.0)
    return water_m, level_m, wet, np.clip(measured, 0.0, 1.0)


def _rock_kept(z_rock_cm, missing, planes, linear):
    """The share of its coverage a rock keeps under the void; None without the open sea.

    ``planes`` is ``(wet plane, OpenSea or None)``. Out of the sea a rock keeps all of it.
    Under the sea's level it keeps none on no data, and where the open sea runs on under the
    void only what the void's cover leaves, so the sea fades into the void with no rock in it.
    """
    wet_plane, sea = planes
    if sea is None:
        return None
    above = np.clip(z_rock_cm / np.float32(100.0) - np.float32(OCEAN_LEVEL_M) + 0.5, 0.0, 1.0)
    if reads_nothing(sea.void.cover, linear):
        under = np.where(missing, np.float32(1.0), np.float32(0.0))
    else:
        cover = np.clip(sample_plain(sea.void.cover, linear) / np.float32(255.0), 0.0, 1.0)
        under = np.where(missing, np.float32(1.0), cover * sample_coverage(wet_plane, linear))
    return (1.0 - (1.0 - above) * under).astype(np.float32)


def _void(rgb, missing, sea, linear, rock, z_m):
    """A finished band under the void: no data at all, and the open sea's void planes with
    the cover and rim kept off the rocks a pixel's ``rock`` coverage holds where they stand
    out of the sea; without the open sea, no data only, in the page's sea."""
    if sea is None:
        return with_sea(rgb, missing)
    if not missing.any() and all(reads_nothing(p, linear) for p in (sea.void.cover, sea.void.rim)):
        return rgb.astype(np.result_type(rgb, np.float32), copy=False)
    cover, falloff, pit, rim = (sample_plain(p, linear) / np.float32(255.0) for p in sea.void)
    if rock is not None:
        # A rock deep in the void, under the sea's level, is the void's, as the artwork has it.
        rock = rock * np.clip(z_m - np.float32(OCEAN_LEVEL_M) + 0.5, 0.0, 1.0)
        cover, rim = cover * (1.0 - rock), rim * (1.0 - rock)
    cover = np.where(missing, np.float32(1.0), np.clip(cover, 0.0, 1.0))
    return with_void(rgb, cover, falloff, pit, rim)


def render_layer(
    layer,
    field,
    biome_rgb,
    biome,
    borrow,
    size,
    progress,
    height_dm=None,
    direct=None,
    seam=None,
    regimes=None,
    measured_plane_u8=None,
    overlay=None,
    kernel=None,
    meshes=None,
    falls=None,
    reach=None,
    painted=None,
    window=None,
    rivers=None,
    relief=None,
    unlit=False,
    surface=None,
    water_level=None,
    sea=None,
    threads=1,
) -> np.ndarray:
    """One whole layer, drawn a band of rows at a time. Returns ``(size, size, 3)`` uint8.

    Each band carries BAND_HALO extra rows, cropped after, so no stencil sees a band edge.
    ``window`` is ``(r0, r1, c0, c1)``, with every raster passed in cut to it. ``unlit`` draws
    the sun term flat; ``surface`` receives the drawn heights and land weight. ``sea`` is the
    run's ``OpenSea``, whose water planes replace ``water_level``'s. ``threads`` bands are
    drawn at once, to the same bytes; ``seam`` and ``regimes`` take the bands in order. The
    other arguments: tools/mapgen/README.md, "Design notes", "The band loop".
    """
    job = _layer_job(dict(locals()))
    r0, r1, c0, c1 = job.window
    out = np.empty((r1 - r0, c1 - c0, 3), np.uint8)
    tops = range(r0, r1, BAND_ROWS)
    threads = max(1, min(threads, len(tops)))
    started = time.time()
    with ExitStack() as stores:
        for plane in _band_planes(job) if threads > 1 else ():
            stores.enter_context(plane.holding(bands_held(threads)))
        owed = stores.enter_context(closing(in_order(partial(_draw_band, job, out), tops, threads)))
        for top, measured in zip(tops, owed, strict=True):
            for merge, value in measured:
                merge(value)
            if progress and (top // BAND_ROWS) % 16 == 0:
                done = (min(top + BAND_ROWS, r1) - r0) / (r1 - r0)
                print(
                    f"  {layer}: {done:5.1%} of {size}x{size} in {time.time() - started:5.1f}s",
                    flush=True,
                )
                print(encode_stage(f"draw:{layer}", done), flush=True)
    return out


def _layer_job(params: dict) -> SimpleNamespace:
    """``render_layer``'s arguments and what every band of the layer shares, built once.

    The bands only read it, so any number of threads may share it.
    """
    job = SimpleNamespace(**params)
    field, size = job.field, job.size
    job.kernel = taps_pchip if job.kernel is None else job.kernel
    job.painter = LAYER_PAINTERS.get(job.layer)
    x_cm, job.y_cm = frame_coordinates(size)
    job.window = job.window or (0, size, 0, size)
    _r0, _r1, c0, c1 = job.window
    job.x_cm = x_cm = x_cm[c0:c1]
    job.spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    job.blur_px = WATER_EDGE_BLUR_M / job.spacing_m
    job.heights = field.height_dm if job.height_dm is None else job.height_dm
    job.ramp = ramp_range(field)
    job.noise = noise_fields(NOISE_SEED) if job.layer == "satellite" else None
    planes = (job.water_level, None) if job.sea is None else job.sea.planes
    job.water, job.wet_plane, job.measured_plane = water_sources(field, job.rivers, *planes)
    job.column_index = np.arange(c0, c1)

    # The column taps are the same for every band, on both grids the bands sample: the
    # field's 1 m lattice and the artwork's 8192 sheet. Built once.
    field_x = grid_position(x_cm, field.x0_cm, field.spacing_cm, field.width)
    job.cols_smooth = job.kernel(field_x, field.width)
    job.cols_linear = taps_linear(field_x, field.width)
    job.art_step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SHEET_PX
    art_x0_cm = BOUNDS_M["x_min_m"] * 100 + job.art_step_cm / 2
    job.art_cols = taps_linear(grid_position(x_cm, art_x0_cm, job.art_step_cm, SHEET_PX), SHEET_PX)
    job.art_y0_cm = BOUNDS_M["y_min_m"] * 100 + job.art_step_cm / 2
    job.biome_cols = biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], job.biome["width"])
    # Nearest, never in between: a province is a name.
    job.prov_cols = np.clip(
        np.round((x_cm - field.x0_cm) / field.spacing_cm).astype(np.int64), 0, field.width - 1
    )
    if job.painted is not None:
        job.rock_step = field.spacing_cm * ROCK_GRID_M
        job.rock_h, rock_w = job.painted.rock[0].shape
        job.rock_cols = taps_linear(grid_position(x_cm, field.x0_cm, job.rock_step, rock_w), rock_w)
        job.footprint = job.spacing_m * 100.0 / field.spacing_cm
        job.paint_cols = taps_footprint(field_x, job.footprint, field.width)
    return job


def _band_planes(job) -> list:
    """The band stores among the rasters the layer reads a band at a time."""
    painted = job.painted
    planes = [*(job.direct or ())[:2], *(job.overlay or ())[:2], *(job.meshes or ())]
    if painted is not None:
        planes += [
            getattr(painted, "rock_family", None),
            *(getattr(painted, "titan", None) or ())[:2],
        ]
    return [plane for plane in planes if hasattr(plane, "holding")]


def _draw_band(job, out, top) -> list:
    """Rows ``[top, top + BAND_ROWS)`` into ``out``, and the ``(merge, measured)`` pairs the
    caller merges in band order. Nothing shared is written but those rows and the surface's."""
    r0, r1, _c0, _c1 = job.window
    bottom = min(top + BAND_ROWS, r1)
    lo = max(top - BAND_HALO, r0)
    hi = min(bottom + BAND_HALO, r1)
    rows = (top, bottom, lo, hi, slice(lo - r0, hi - r0))
    ground, owed = _band_ground(job, rows)
    rgb = _band_colour(job, rows, ground)
    out[top - r0 : bottom - r0] = np.clip(rgb[top - lo : bottom - lo], 0, 255).astype(np.uint8)
    return owed


def _band_ground(job, rows) -> tuple[dict, list]:
    """One band's surface: the two height regimes, the top overlay, the water and the meshes.

    Also the seam and regime measurements it owes, for the caller to merge in band order.
    """
    top, bottom, lo, hi, band = rows
    field = job.field
    field_y = grid_position(job.y_cm[lo:hi], field.y0_cm, field.spacing_cm, field.height)
    smooth = (job.kernel(field_y, field.height), job.cols_smooth)
    linear = (taps_linear(field_y, field.height), job.cols_linear)

    z_dm, missing = sample_surface(job.heights, smooth, linear, hf.NODATA)
    z_m = z_dm / np.float32(hf.DM_PER_M)
    weight = rock_seen = None
    owed: list = []
    if job.direct is not None:
        direct_z, direct_coverage, lattice, subsamples = job.direct
        # The base the rocks are composited onto is the lattice UNDERNEATH them, not the
        # field's own fold -- see ``ground_lattice``. Where that lattice knows nothing
        # the fold stands in, which is inside a formation the rock covers anyway.
        ground_dm, ground_missing = sample_surface(lattice, smooth, linear, hf.NODATA)
        base_m = np.where(ground_missing, z_m, ground_dm / np.float32(hf.DM_PER_M))
        rock = (np.asarray(direct_z[band], np.float32), np.asarray(direct_coverage[band]))
        kept = _rock_kept(rock[0], missing, (job.wet_plane, job.sea), linear)
        z_m, missing, weight, switched = blend_regimes(
            base_m, missing, rock, linear, subsamples, kept
        )
        rock_lift = np.clip((z_m - base_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
        rock_seen = np.where(ground_missing, weight, np.minimum(weight, rock_lift))
        keep = slice(top - lo, bottom - lo)
        if job.seam is not None:
            delta = (rock[0] / 100.0 - base_m)[keep]
            seam = job.seam.measure(z_m[keep], switched[keep], weight[keep], job.spacing_m, delta)
            owed.append((job.seam.merge, seam))
        if job.regimes is not None:
            prov_rows = np.clip(
                np.round((job.y_cm[top:bottom] - field.y0_cm) / field.spacing_cm).astype(np.int64),
                0,
                field.height - 1,
            )
            picked = np.ix_(prov_rows, job.prov_cols)
            regimes = job.regimes.measure(
                field.provenance_plane[picked], weight[keep], job.measured_plane_u8[picked] > 0
            )
            owed.append((job.regimes.merge, regimes))
    top_weight = None
    if job.overlay is not None:
        top_z, top_coverage, top_subsamples = job.overlay
        below = z_m
        z_m = composite_top(
            z_m,
            np.asarray(top_z[band], np.float32),
            np.asarray(top_coverage[band]),
            top_subsamples,
        )
        top_weight = np.clip((z_m - below) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    water_m, level_m, wet, measured = _band_water(
        z_m, (job.water, job.wet_plane, job.measured_plane, job.sea), smooth, linear
    )
    mesh_weight = mesh_class = None
    if job.meshes is not None:
        z_m, mesh_weight, mesh_class = composite_meshes(
            z_m,
            np.asarray(job.meshes[0][band], np.float32),
            np.asarray(job.meshes[1][band]),
            level_m,
            composite_top,
            seabed=job.layer != "painted",
        )
    return {
        "field_y": field_y, "linear": linear, "z_m": z_m, "missing": missing, "weight": weight,
        "rock_seen": rock_seen, "top_weight": top_weight, "water_m": water_m, "level_m": level_m,
        "wet": wet, "measured": measured, "mesh_weight": mesh_weight, "mesh_class": mesh_class,
    }, owed  # fmt: skip


def _band_colour(job, rows, ground: dict) -> np.ndarray:
    """One band in the layer's style over its surface, then the void and the falls."""
    top, bottom, lo, hi, _band = rows
    _r0, _r1, c0, c1 = job.window
    linear, z_m, y_cm = ground["linear"], ground["z_m"], job.y_cm[lo:hi]
    detail, province = job.borrow
    art_rows = taps_linear(grid_position(y_cm, job.art_y0_cm, job.art_step_cm, SHEET_PX), SHEET_PX)
    strength = sample_plain(province, linear) / 255.0
    lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, (art_rows, job.art_cols)) / 127.0)

    water_terms = band_water(
        z_m, ground["water_m"], ground["wet"], ground["measured"], job.blur_px, job.reach,
        linear, job.spacing_m,
    )  # fmt: skip
    if job.rivers is not None:
        water_terms = job.rivers.over(water_terms, z_m, linear, job.spacing_m)
    if job.surface is not None:
        _capture(
            job.surface, (top, lo, bottom, c0, c1), z_m, ground["missing"], water_terms["cover"]
        )
    scene: dict = {
        "z_m": z_m,
        "borrow": np.clip(lift, *BORROW_CLAMP),
        "ramp_lo": job.ramp[0],
        "ramp_hi": job.ramp[1],
        "water": water_terms,
    }
    if job.layer == "painted":
        rgb = _painted_band(job, rows, ground, scene)
    elif job.relief is not None:
        scene.update(spacing_m=job.spacing_m, unlit=job.unlit)
        biome_rows = biome_index(y_cm, BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], job.biome["width"])
        rgb = relief_colours(
            scene,
            job.relief,
            lambda plane, taps=linear: sample_plain(plane, taps),
            lambda plane, rows=biome_rows: plane[np.ix_(rows, job.biome_cols)],
        )
    else:
        scene["shade"] = flat_shade(z_m.shape) if job.unlit else hillshade(z_m, job.spacing_m)
        if job.layer == "satellite":
            scene["slope"] = slope_degrees(z_m, job.spacing_m)
            biome_rows = biome_index(
                y_cm, BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], job.biome["width"]
            )
            scene["biome_rgb"] = job.biome_rgb[np.ix_(biome_rows, job.biome_cols)].astype(
                np.float32
            )
            scene["noise"] = sample_noise(job.noise, np.arange(lo, hi), job.column_index, job.size)
        rgb = job.painter(scene)
    rgb = _void(rgb, ground["missing"], job.sea, linear, ground["weight"], z_m)
    return draw_falls(rgb, job.falls, job.layer, job.x_cm, y_cm, z_m, job.spacing_m)


def _painted_band(job, rows, ground: dict, scene: dict) -> np.ndarray:
    """The game-painted style's band: its rock weight, crowns, sun term and water optics."""
    _top, _bottom, lo, hi, band = rows
    _r0, _r1, c0, c1 = job.window
    painted, z_m, field = job.painted, ground["z_m"], job.field
    rock_rows = taps_linear(
        grid_position(job.y_cm[lo:hi], field.y0_cm, job.rock_step, job.rock_h), job.rock_h
    )
    rock_weight = (
        np.zeros(z_m.shape, np.float32) if ground["weight"] is None else ground["rock_seen"]
    )
    if ground["top_weight"] is not None:
        rock_weight = np.maximum(rock_weight, ground["top_weight"])
    flat = np.float32(np.sin(np.deg2rad(SUN_ALTITUDE_DEG)))
    scene["crowns"] = crowns_in_band(painted, job.x_cm, job.y_cm[lo:hi], job.spacing_m, job.unlit)
    meshes = (ground["mesh_weight"], ground["mesh_class"], ground["level_m"])
    scene.update(
        ndl=painted_ndl(z_m, job.spacing_m, job.unlit, job.surface, meshes),
        ndl_flat=flat,
        rock_weight=rock_weight,
        mesh_weight=ground["mesh_weight"],
        mesh_class=ground["mesh_class"],
        mesh_family=_band_family(job.meshes, band),
        water_optics=painted.water_optics(ground["linear"], scene["water"].get("river")),
        grid=(band, lo, hi, c0, c1, job.spacing_m),
    )
    paint = (taps_footprint(ground["field_y"], job.footprint, field.height), job.paint_cols)
    return painted_colours(
        scene,
        painted,
        lambda plane, taps=paint: sample_plain(plane, taps),
        lambda plane, taps=(rock_rows, job.rock_cols): sample_plain(plane, taps),
    )
