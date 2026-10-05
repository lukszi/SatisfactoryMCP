"""Which recipe drew a render layer's pixels: the number and the words the sidecar records."""

from __future__ import annotations

from satisfactory_mcp.core.gameassets.versions import (
    RENDER_RECIPE_CURRENT,
    RENDER_RECIPE_KERNEL_ONLY,
)

__all__ = [
    "COMPOSITION_TEXT",
    "LEVEL_ONLY_TEXT",
    "RECIPE",
    "RECIPES",
    "RECIPE_KERNEL_ONLY",
    "Z7_TEXT",
    "sampling_text",
]

#: Which recipe drew the pixels, recorded per layer so a reader looking at a tile can find
#: out which set of rules made it.
RECIPES = {
    1: (
        "terrain: hypsometric ramp over the 1st..99.5th height percentile, NW hillshade at "
        "45 deg, water tinted by depth. satellite: biome palette, slope-driven rock, "
        "elevation lightening, two octaves of noise, the same hillshade and water"
    ),
    2: (
        "recipe 1 at 16384 px, sampled with a C1 Catmull-Rom kernel so the hillshade has no "
        "cell structure; submersion read from waterq.u8.z rather than inferred from a "
        "comparison, level-only water at full alpha and the deep end of the ramp; and the "
        "artwork sheet's luminance high-pass borrowed into the shading wherever the "
        "provenance byte says cliff or fill, faded out towards landscape"
    ),
    3: (
        "recipe 2 at 32768 px, with a two-regime sampler: the cliff geometry decoded from "
        "the container and rasterised into this grid at 0.229 m wherever density.u8.z says "
        "a source vertex landed under the output texel, the Catmull-Rom kernel over the 1 m "
        "field everywhere else, and a density-weighted cross-fade between them so no "
        "province boundary is ever a derivative discontinuity. Plus the fill province "
        "low-passed at its own 3.66 m cell so its 3.9 m terraces stop being contours"
    ),
    4: (
        "recipe 3 with the landscape under the kernel read from terrain.u16.z at 7.8 mm "
        "instead of the decimetre ground plane, under the cliff province as well, and the "
        "arches and foliage boulders the field keeps in top.i16.z rasterised at 0.229 m and "
        "composited over everything by the same coverage-and-lift rule as the rocks"
    ),
    5: (
        "recipe 4 over a rebuilt lattice, sampled with tensor-product PCHIP instead of "
        "Catmull-Rom: the fill province re-read from the float16 interface raster "
        "(Gaussian, cubic, +1 m), blended into the landscape across a 48 m harmonic seam "
        "band, and interior holes filled biharmonically. Rock texels unchanged; the open "
        "sea past the data stays the page's colour"
    ),
    6: (
        "recipe 5 with a crisp shore: near the sea, water coverage is the drawn surface "
        "crossing the ocean level, antialiased to one pixel, under an exponential "
        "shallow-water fade, where recipe 5 read the artwork's 3.66 m water mask; rivers and "
        "lakes unchanged. Coral, shells, CliffPillar_03 and rubble rasterised for the map "
        "only and composited raise-only where they stand near or above the water"
    ),
}
RECIPE = RENDER_RECIPE_CURRENT

#: What ``--kernel-only`` draws, and it is a whole recipe rather than recipe 3 with a stage
#: switched off: no geometry opened, no direct regime, no cross-fade and no de-terracing.
#: The sidecar records this number, so a layer drawn that way never claims the recipe above.
RECIPE_KERNEL_ONLY = RENDER_RECIPE_KERNEL_ONLY


def sampling_text(spacing_m: float, two_regime: bool) -> str:
    """``_meta.render.sampling``: how a pixel's height was found."""
    if not two_regime:
        return (
            "Catmull-Rom (cubic convolution, a = -1/2) over the 1 m field per output "
            "pixel wherever the 4x4 stencil is whole, bilinear where it straddles the "
            "edge of the data, and nothing at all where no texel under it has a value. "
            "--kernel-only: no geometry was opened and no direct regime was drawn"
        )
    return (
        "the field's own composition rule, at this render's spacing. KERNEL: "
        "tensor-product PCHIP (Fritsch-Butland slopes: exact at the 1 m vertices, "
        "never outside a cell's own range) over the LANDSCAPE AND FILL lattices, "
        "rebuilt by tools/map_fill.py -- the cliff province taken out, because "
        "interpolating the composed field reconstructs its own 1 m fold and a rim reconstructed from "
        "a fold is a 1 m staircase at any output resolution -- falling back to "
        "bilinear where the 4x4 stencil straddles no data and to nothing where no "
        "texel under it has a value. DIRECT: the cliff geometry rasterised into "
        f"this grid at {spacing_m:.4f} m and composited on top of that lattice by "
        "its own coverage of the pixel, raising the ground and never lowering it, "
        "through a smoothed positive part so the line where a rock meets the ground "
        "is not a derivative discontinuity the hillshade would draw. density.u8.z "
        "does not gate any of this: it says which of the drawn texels are "
        "measurements and which are the plane of a triangle wider than a texel, and "
        "_meta.render.two_regime.regimes counts both"
    )


COMPOSITION_TEXT = (
    "the field's own rule at this render's spacing: the landscape and fill "
    "lattices interpolated with the C1 kernel, and the cliff geometry "
    "rasterised at 0.229 m composited over them by its own coverage, raising "
    "the ground and never lowering it. What this replaced was interpolating "
    "the 1 m FOLD of that composition, which reconstructs a rim as the 1 m "
    "staircase the fold put it on however fine the output grid is"
)

Z7_TEXT = (
    "interpolated-smooth. 32768 px is NOT a claim that the field has more to "
    "say -- that was measured twice on this pipeline and refused twice, and the "
    "high-frequency energy per pixel falls at every doubling. What z7 is, is the "
    "same surface evaluated by the same C1 kernel at half the spacing, which a "
    "client cannot produce for itself: a browser shown z6 at twice its scale "
    "upsamples it BILINEARLY, and bilinear is C0, so the relief it draws is "
    "ruled into 0.458 m squares. The exception is the direct regime, where the "
    "pixels are triangles rather than an interpolation and z7 genuinely resolves "
    "geometry the 1 m field folds away -- see two_regime.regimes for how much of "
    "the sheet that is."
)

LEVEL_ONLY_TEXT = (
    "full alpha and the deep end of the ramp. 95.2% of level-only water "
    "stands over the fill province and 98% of its surface levels lie in a "
    "0.7 m band around the ocean's own -16.99 m, so it is the ocean, and a "
    "depth ramp run on a 3.9 m raster's rounding error is what used to draw "
    "3.572 km2 of it as land"
)
