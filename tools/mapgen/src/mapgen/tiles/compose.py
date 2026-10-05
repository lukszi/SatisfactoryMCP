"""The band loop that composes a layer's sheet: two height regimes, water and colour.

Moved verbatim from ``tools/gen_map_renders.py``.
"""

from __future__ import annotations

import time

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.hillshade import (
    BORROW_CLAMP,
    BORROW_GAIN,
    SUN_ALTITUDE_DEG,
    hillshade,
    slope_degrees,
    sun_dot,
)
from mapgen.palette.painted import ROCK_GRID_M, painted_colours
from mapgen.palette.rivers import water_sources
from mapgen.palette.shore import MESH_FULL_LIFT_M, blend_water, composite_meshes, shore_terms
from mapgen.palette.styles import (
    LAYER_PAINTERS,
    NOISE_SEED,
    biome_index,
    noise_fields,
    ramp_range,
    with_sea,
)
from mapgen.palette.water import (
    WATER_DEPTH_FULL_M,
    WATER_EDGE_BLUR_M,
    water_alpha,
    water_depth_fraction,
)
from mapgen.terrain.measure import SEAM_MID
from mapgen.terrain.rasters import pixel_coverage
from mapgen.terrain.sample import (
    frame_coordinates,
    grid_position,
    sample_coverage,
    sample_noise,
    sample_plain,
    sample_surface,
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


def blend_regimes(base_m, missing, direct, linear, subsamples):
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
    the coverage is 1 and the rock is the answer either way.
    """
    z_cm, coverage = direct
    fraction = pixel_coverage(coverage, subsamples)
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
    """Recipe 5's water, and within ``reach`` of the sea the ocean's crossing rule."""
    old_cover = water_alpha(z_m, water_m, wet, measured, blur_px)
    old_depth = water_depth_fraction(z_m, water_m, measured)
    if reach is None:
        return blend_water(None, old_cover, old_depth, None, WATER_DEPTH_FULL_M)
    return blend_water(
        sample_coverage(reach, linear),
        old_cover,
        old_depth,
        shore_terms(z_m, spacing_m),
        WATER_DEPTH_FULL_M,
    )


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
    reach=None,
    painted=None,
    window=None,
    rivers=None,
) -> np.ndarray:
    """One whole layer, drawn a band of rows at a time. Returns ``(size, size, 3)`` uint8.

    Banded because the sheet is a billion pixels at 32768 and this recipe holds a dozen
    float32 intermediates over it, four gigabytes apiece whole. Each band is computed with
    BAND_HALO extra rows on both sides and cropped afterwards, so neither the hillshade's
    gradient nor the cubic sampler's stencil nor the water blur's kernel ever sees a band
    edge: a one-sided difference at every 256th row would
    draw 127 horizontal lines across the world.

    ``direct`` is the pair of memory maps the direct pass wrote, with the weight plane and
    the sub-sampling beside them; ``None`` draws the single-regime picture. ``seam`` and
    ``regimes`` are accumulators, passed for the first layer only, both layers drawing the
    identical surface. ``overlay`` is the arch-and-boulder pair of maps and its sub-sampling,
    composited last. ``kernel`` builds the smooth taps: ``taps_pchip`` unless told otherwise.
    ``meshes`` is the render-only mesh raster (z, class); ``reach`` the 1 m plane where the
    ocean's crossing rule applies, ``None`` for recipe 5's water everywhere; ``painted`` the
    ``palette.painted.PaintedGround`` the painted layer samples. ``window`` draws only rows
    ``[r0, r1)`` and columns ``[c0, c1)`` of the sheet, with every raster passed in cut to it.
    ``rivers`` is the ``palette.rivers.RiverWater`` whose ribbons and reconciled water are
    drawn in place of the field's river water; ``None`` draws the field's water alone.
    """
    kernel = taps_pchip if kernel is None else kernel
    painter = LAYER_PAINTERS.get(layer)
    x_cm, y_cm = frame_coordinates(size)
    r0, r1, c0, c1 = window or (0, size, 0, size)
    x_cm = x_cm[c0:c1]
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    blur_px = WATER_EDGE_BLUR_M / spacing_m
    detail, province = borrow
    heights = field._height_dm if height_dm is None else height_dm
    ramp_lo, ramp_hi = ramp_range(field)
    noise = noise_fields(NOISE_SEED) if layer == "satellite" else None
    water, wet_plane, measured_plane = water_sources(field, rivers)
    out = np.empty((r1 - r0, c1 - c0, 3), np.uint8)
    column_index = np.arange(c0, c1)

    # The column taps are the same for every band, on both grids the bands sample: the
    # field's 1 m lattice and the artwork's 8192 sheet. Built once.
    field_x = grid_position(x_cm, field.x0_cm, field.spacing_cm, field.width)
    cols_smooth = kernel(field_x, field.width)
    cols_linear = taps_linear(field_x, field.width)
    art_step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SHEET_PX
    art_x0_cm = BOUNDS_M["x_min_m"] * 100 + art_step_cm / 2
    art_cols = taps_linear(grid_position(x_cm, art_x0_cm, art_step_cm, SHEET_PX), SHEET_PX)
    art_y0_cm = BOUNDS_M["y_min_m"] * 100 + art_step_cm / 2
    biome_cols = biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], biome["width"])
    # Nearest, never in between: a province is a name.
    prov_cols = np.clip(
        np.round((x_cm - field.x0_cm) / field.spacing_cm).astype(np.int64), 0, field.width - 1
    )
    if painted is not None:
        rock_step = field.spacing_cm * ROCK_GRID_M
        rock_h, rock_w = painted.rock[0].shape
        rock_cols = taps_linear(grid_position(x_cm, field.x0_cm, rock_step, rock_w), rock_w)

    started = time.time()
    for top in range(r0, r1, BAND_ROWS):
        bottom = min(top + BAND_ROWS, r1)
        lo = max(top - BAND_HALO, r0)
        hi = min(bottom + BAND_HALO, r1)
        band = slice(lo - r0, hi - r0)
        field_y = grid_position(y_cm[lo:hi], field.y0_cm, field.spacing_cm, field.height)
        smooth = (kernel(field_y, field.height), cols_smooth)
        linear = (taps_linear(field_y, field.height), cols_linear)

        z_dm, missing = sample_surface(heights, smooth, linear, hf.NODATA)
        z_m = z_dm / np.float32(hf.DM_PER_M)
        weight = None
        if direct is not None:
            direct_z, direct_coverage, ground, subsamples = direct
            # The base the rocks are composited onto is the lattice UNDERNEATH them, not the
            # field's own fold -- see ``ground_lattice``. Where that lattice knows nothing
            # the fold stands in, which is inside a formation the rock covers anyway.
            ground_dm, ground_missing = sample_surface(ground, smooth, linear, hf.NODATA)
            base_m = np.where(ground_missing, z_m, ground_dm / np.float32(hf.DM_PER_M))
            z_m, missing, weight, switched = blend_regimes(
                base_m,
                missing,
                (np.asarray(direct_z[band], np.float32), np.asarray(direct_coverage[band])),
                linear,
                subsamples,
            )
            rock_lift = np.clip((z_m - base_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
            rock_seen = np.where(ground_missing, weight, np.minimum(weight, rock_lift))
            if seam is not None:
                keep = slice(top - lo, bottom - lo)
                seam.add(
                    z_m[keep],
                    switched[keep],
                    weight[keep],
                    spacing_m,
                    (np.asarray(direct_z[band], np.float32) / 100.0 - base_m)[keep],
                )
            if regimes is not None:
                prov_rows = np.clip(
                    np.round((y_cm[top:bottom] - field.y0_cm) / field.spacing_cm).astype(np.int64),
                    0,
                    field.height - 1,
                )
                picked = np.ix_(prov_rows, prov_cols)
                regimes.add(
                    field._prov[picked],
                    weight[top - lo : bottom - lo],
                    measured_plane_u8[picked] > 0,
                )
        top_weight = None
        if overlay is not None:
            top_z, top_coverage, top_subsamples = overlay
            below = z_m
            z_m = composite_top(
                z_m,
                np.asarray(top_z[band], np.float32),
                np.asarray(top_coverage[band]),
                top_subsamples,
            )
            top_weight = np.clip((z_m - below) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
        if wet_plane is None:
            wet = measured = np.zeros(z_m.shape, np.float32)
            water_m = z_m
            level_m = np.full(z_m.shape, np.nan, np.float32)
        else:
            water_dm, water_missing = sample_surface(water, smooth, linear, hf.NODATA)
            water_m = water_dm / np.float32(hf.DM_PER_M)
            level_m = np.where(water_missing, np.nan, water_m)
            wet = sample_coverage(wet_plane, linear)
            measured = sample_coverage(measured_plane, linear) / np.where(wet <= 0.0, 1.0, wet)
            measured = np.clip(measured, 0.0, 1.0)
        mesh_weight = mesh_class = None
        if meshes is not None:
            z_m, mesh_weight, mesh_class = composite_meshes(
                z_m,
                np.asarray(meshes[0][band], np.float32),
                np.asarray(meshes[1][band]),
                level_m,
                composite_top,
            )

        art_rows = taps_linear(
            grid_position(y_cm[lo:hi], art_y0_cm, art_step_cm, SHEET_PX), SHEET_PX
        )
        strength = sample_plain(province, linear) / 255.0
        lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, (art_rows, art_cols)) / 127.0)

        water_terms = band_water(z_m, water_m, wet, measured, blur_px, reach, linear, spacing_m)
        if rivers is not None:
            water_terms = rivers.over(water_terms, z_m, linear, spacing_m)
        scene: dict = {
            "z_m": z_m,
            "borrow": np.clip(lift, *BORROW_CLAMP),
            "ramp_lo": ramp_lo,
            "ramp_hi": ramp_hi,
            "water": water_terms,
        }
        if layer == "painted":
            rock_rows = taps_linear(
                grid_position(y_cm[lo:hi], field.y0_cm, rock_step, rock_h), rock_h
            )
            rock_weight = np.zeros(z_m.shape, np.float32) if weight is None else rock_seen
            if top_weight is not None:
                rock_weight = np.maximum(rock_weight, top_weight)
            scene.update(
                ndl=sun_dot(z_m, spacing_m),
                ndl_flat=np.float32(np.sin(np.deg2rad(SUN_ALTITUDE_DEG))),
                rock_weight=rock_weight,
                mesh_weight=mesh_weight,
                mesh_class=mesh_class,
                water_optics=painted.water_optics(linear, water_terms.get("river")),
            )
            rgb = painted_colours(
                scene,
                painted,
                lambda plane, taps=linear: sample_plain(plane, taps),
                lambda plane, taps=(rock_rows, rock_cols): sample_plain(plane, taps),
            )
        else:
            scene["shade"] = hillshade(z_m, spacing_m)
            if layer == "satellite":
                scene["slope"] = slope_degrees(z_m, spacing_m)
                biome_rows = biome_index(
                    y_cm[lo:hi], BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], biome["width"]
                )
                scene["biome_rgb"] = biome_rgb[np.ix_(biome_rows, biome_cols)].astype(np.float32)
                scene["noise"] = sample_noise(noise, np.arange(lo, hi), column_index, size)
            rgb = painter(scene)
        rgb = with_sea(rgb, missing)
        out[top - r0 : bottom - r0] = np.clip(rgb[top - lo : bottom - lo], 0, 255).astype(np.uint8)
        if progress and (top // BAND_ROWS) % 16 == 0:
            done = (bottom - r0) / (r1 - r0)
            print(
                f"  {layer}: {done:5.1%} of {size}x{size} in {time.time() - started:5.1f}s",
                flush=True,
            )
            print(encode_stage(f"draw:{layer}", done), flush=True)
    return out
