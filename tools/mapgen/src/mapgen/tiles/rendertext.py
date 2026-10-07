"""The render sidecar's words for how a layer's pixels were sampled and composed."""

from __future__ import annotations

__all__ = [
    "COMPOSITION_TEXT",
    "LEVEL_ONLY_TEXT",
    "Z7_TEXT",
    "sampling_text",
]


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
        "rebuilt by mapgen.terrain.fill -- the cliff province taken out, because "
        "interpolating the composed field reconstructs its own 1 m fold and a rim "
        "reconstructed from a fold is a 1 m staircase at any output resolution -- falling back to "
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
