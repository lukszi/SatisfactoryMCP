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
    flat_shade,
    hillshade,
    slope_degrees,
    sun_dot,
)
from mapgen.palette.falls import draw_falls
from mapgen.palette.painted import ROCK_GRID_M, painted_colours
from mapgen.palette.relief import relief_colours
from mapgen.palette.rivers import water_sources
from mapgen.palette.shore import (
    MESH_FULL_LIFT_M,
    OCEAN_LEVEL_M,
    blend_water,
    composite_meshes,
    shore_terms,
)
from mapgen.palette.styles import (
    LAYER_PAINTERS,
    NOISE_SEED,
    biome_index,
    noise_fields,
    ramp_range,
    with_sea,
    with_void,
)
from mapgen.palette.water import (
    WATER_DEPTH_FULL_M,
    WATER_EDGE_BLUR_M,
    water_alpha,
    water_depth_fraction,
)
from mapgen.terrain.crowns import crown_band
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
    if sea is not None:
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
    cover = np.clip(sample_plain(sea.void.cover, linear) / np.float32(255.0), 0.0, 1.0)
    under = np.where(missing, np.float32(1.0), cover * sample_coverage(wet_plane, linear))
    return (1.0 - (1.0 - above) * under).astype(np.float32)


def _void(rgb, missing, sea, linear, rock, z_m):
    """A finished band under the void: no data at all, and the open sea's void planes with
    the cover and rim kept off the rocks a pixel's ``rock`` coverage holds where they stand
    out of the sea; without the open sea, no data only, in the page's sea."""
    if sea is None:
        return with_sea(rgb, missing)
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
) -> np.ndarray:
    """One whole layer, drawn a band of rows at a time. Returns ``(size, size, 3)`` uint8.

    Each band carries BAND_HALO extra rows, cropped after, so no stencil sees a band edge.
    ``window`` is ``(r0, r1, c0, c1)``, with every raster passed in cut to it. ``unlit`` draws
    the sun term flat; ``surface`` receives the drawn heights and land weight. ``sea`` is the
    run's ``OpenSea``, whose water planes replace ``water_level``'s. The other
    arguments: tools/mapgen/README.md, "Design notes", "The band loop".
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
    planes = (water_level, None) if sea is None else sea.planes
    water, wet_plane, measured_plane = water_sources(field, rivers, *planes)
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
            rock = (np.asarray(direct_z[band], np.float32), np.asarray(direct_coverage[band]))
            kept = _rock_kept(rock[0], missing, (wet_plane, sea), linear)
            z_m, missing, weight, switched = blend_regimes(
                base_m, missing, rock, linear, subsamples, kept
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
                    (rock[0] / 100.0 - base_m)[keep],
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
        water_m, level_m, wet, measured = _band_water(
            z_m, (water, wet_plane, measured_plane, sea), smooth, linear
        )
        mesh_weight = mesh_class = None
        if meshes is not None:
            z_m, mesh_weight, mesh_class = composite_meshes(
                z_m,
                np.asarray(meshes[0][band], np.float32),
                np.asarray(meshes[1][band]),
                level_m,
                composite_top,
                seabed=layer != "painted",
            )

        art_rows = taps_linear(
            grid_position(y_cm[lo:hi], art_y0_cm, art_step_cm, SHEET_PX), SHEET_PX
        )
        strength = sample_plain(province, linear) / 255.0
        lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, (art_rows, art_cols)) / 127.0)

        water_terms = band_water(z_m, water_m, wet, measured, blur_px, reach, linear, spacing_m)
        if rivers is not None:
            water_terms = rivers.over(water_terms, z_m, linear, spacing_m)
        if surface is not None:
            _capture(surface, (top, lo, bottom, c0, c1), z_m, missing, water_terms["cover"])
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
            flat = np.float32(np.sin(np.deg2rad(SUN_ALTITUDE_DEG)))
            scene["crowns"] = crowns_in_band(painted, x_cm, y_cm[lo:hi], spacing_m, unlit)
            scene.update(
                ndl=np.full(z_m.shape, flat) if unlit else sun_dot(z_m, spacing_m),
                ndl_flat=flat,
                rock_weight=rock_weight,
                mesh_weight=mesh_weight,
                mesh_class=mesh_class,
                water_optics=painted.water_optics(linear, water_terms.get("river")),
                grid=(band, lo, hi, c0, c1, spacing_m),
            )
            rgb = painted_colours(
                scene,
                painted,
                lambda plane, taps=linear: sample_plain(plane, taps),
                lambda plane, taps=(rock_rows, rock_cols): sample_plain(plane, taps),
            )
        elif relief is not None:
            scene.update(spacing_m=spacing_m, unlit=unlit)
            rows = biome_index(
                y_cm[lo:hi], BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], biome["width"]
            )
            rgb = relief_colours(
                scene,
                relief,
                lambda plane, taps=linear: sample_plain(plane, taps),
                lambda plane, rows=rows: plane[np.ix_(rows, biome_cols)],
            )
        else:
            scene["shade"] = flat_shade(z_m.shape) if unlit else hillshade(z_m, spacing_m)
            if layer == "satellite":
                scene["slope"] = slope_degrees(z_m, spacing_m)
                biome_rows = biome_index(
                    y_cm[lo:hi], BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], biome["width"]
                )
                scene["biome_rgb"] = biome_rgb[np.ix_(biome_rows, biome_cols)].astype(np.float32)
                scene["noise"] = sample_noise(noise, np.arange(lo, hi), column_index, size)
            rgb = painter(scene)
        rgb = _void(rgb, missing, sea, linear, weight, z_m)
        rgb = draw_falls(rgb, falls, layer, x_cm, y_cm[lo:hi], z_m, spacing_m)
        out[top - r0 : bottom - r0] = np.clip(rgb[top - lo : bottom - lo], 0, 255).astype(np.uint8)
        if progress and (top // BAND_ROWS) % 16 == 0:
            done = (bottom - r0) / (r1 - r0)
            print(
                f"  {layer}: {done:5.1%} of {size}x{size} in {time.time() - started:5.1f}s",
                flush=True,
            )
            print(encode_stage(f"draw:{layer}", done), flush=True)
    return out
