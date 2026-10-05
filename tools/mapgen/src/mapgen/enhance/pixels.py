"""The three passes around the model: pre-sharpen the input, repair faint marks, fix colour."""

from __future__ import annotations

from mapgen.enhance.upscaler import ENHANCE_SCALE

__all__ = [
    "COLOUR_FIX_SIGMA",
    "FAINT_FEATHER",
    "FAINT_GROW",
    "FAINT_HI",
    "FAINT_LO",
    "FAINT_WINDOW",
    "PRESHARPEN_AMOUNT",
    "PRESHARPEN_EDGE",
    "PRESHARPEN_HI",
    "PRESHARPEN_NEIGHBOURS",
    "PRESHARPEN_ON",
    "PRESHARPEN_ROUNDS",
    "PRESHARPEN_SIGMA",
    "colour_fix",
    "colour_fix_pixels",
    "faint_band",
    "faint_depth",
    "faint_mask",
    "hybrid_upscale",
    "presharpen",
    "presharpen_mask",
    "presharpen_pixels",
]

#: The faint-detail mask, and the whole of it. ``depth`` is how far a pixel sits below the
#: local mean of a FAINT_WINDOW box -- the map's marks are darker than what they are drawn
#: on -- grown by FAINT_GROW so a mark's halo is covered too. Between FAINT_LO and FAINT_HI
#: is the band the AI drops; below it there is nothing to protect and above it the AI is
#: better than Lanczos. FAINT_FEATHER then blurs the mask so the blend has no edge.
FAINT_WINDOW = 9


FAINT_GROW = 3


FAINT_LO = 3.0


FAINT_HI = 14.0


FAINT_FEATHER = 5


#: The pre-sharpen, on the INPUT square before the model sees it: three rounds of unsharp
#: masking blended in where the same depth statistic says there is a faint mark. The amount
#: is a nudge that carries the weak band over the model's floor, not a sharpening pass.
#:
#: PRESHARPEN_HI is 10 against the repair's 14 because amplifying a mid stroke hands the
#: model more contrast to expand: decoupling the two bands costs 0.03 of weak-stroke
#: retention and buys back a mid retention of 1.13 against 1.07.
#:
#: The mask is hard rather than a ramp, then grown by one PASSIVE round -- a pixel joins
#: only if more than three of its eight neighbours are already in, so a mark thickens and a
#: lone speck of noise does not spread -- and PRESHARPEN_EDGE feathers the blend.
PRESHARPEN_ROUNDS = 3


PRESHARPEN_SIGMA = 1.0


PRESHARPEN_AMOUNT = 0.14


PRESHARPEN_HI = 10.0


PRESHARPEN_ON = 0.15


PRESHARPEN_NEIGHBOURS = 4


PRESHARPEN_EDGE = 0.6


#: In pixels of the 4x output, and it must exceed a stroke's width there -- strokes are 4
#: to 8 px at 4x -- or the fix blurs back the sharpening it exists to protect.
COLOUR_FIX_SIGMA = 6.0


def faint_depth(luma):
    """How far each pixel sits below its own neighbourhood, grown to cover a mark's halo.

    The one statistic both masks are built on. Measured on the SOURCE luma alone, so
    everything downstream is reproducible from the input without reference to any
    candidate's output. The map's marks are all darker than what they are drawn on, so
    depth is positive on a mark and near zero on flat fill.
    """
    import numpy as np
    from scipy.ndimage import maximum_filter, uniform_filter

    value = luma.astype(np.float32)
    return maximum_filter(uniform_filter(value, FAINT_WINDOW) - value, FAINT_GROW)


def faint_band(depth, hi: float):
    """The band from FAINT_LO to ``hi``, as feathered weights in [0, 1].

    A product of two clipped ramps, box-blurred, which cannot leave the interval -- so a
    caller can blend with it without clamping again.
    """
    import numpy as np
    from scipy.ndimage import uniform_filter

    weight = np.clip((hi - depth) / (hi - FAINT_LO), 0.0, 1.0)
    weight *= np.clip((depth - FAINT_LO) / FAINT_LO, 0.0, 1.0)
    return uniform_filter(weight, FAINT_FEATHER)


def faint_mask(luma):
    """Where the AI must not be trusted: 1 on faint marks, 0 on flat fill and strong ones."""
    return faint_band(faint_depth(luma), FAINT_HI)


def presharpen_mask(luma):
    """Where the input is nudged before the model sees it: a hard mask, grown passively.

    Stops at PRESHARPEN_HI where the repair's band stops at FAINT_HI: the repair covers
    every stroke the model weakens, mid ones included, while this one must cover only the
    weak ones, because a mid stroke handed more contrast is one the model expands harder.
    """
    import numpy as np
    from scipy.ndimage import convolve

    inside = faint_band(faint_depth(luma), PRESHARPEN_HI) > PRESHARPEN_ON
    neighbours = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8)
    grown = convolve(inside.astype(np.uint8), neighbours, mode="nearest")
    return inside | (grown >= PRESHARPEN_NEIGHBOURS)


def presharpen_pixels(rgb):
    """Unsharp the faint marks of one source square, and nothing else. Returns (rgb, mask).

    A mark 4 to 10 luma below its surroundings sits under the model's response floor, and
    no amount of repairing the output puts back a stroke that was never drawn. Amplifying
    it by a third on the way IN carries it over, and the model then keeps about 85% of what
    it was handed instead of 79% of a mark it half-missed. Off the mask it is the identity.
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    flat = np.asarray(rgb, np.float32)
    sharp = flat.copy()
    for _ in range(PRESHARPEN_ROUNDS):
        blurred = gaussian_filter(sharp, (PRESHARPEN_SIGMA, PRESHARPEN_SIGMA, 0))
        sharp += PRESHARPEN_AMOUNT * (sharp - blurred)
    mask = presharpen_mask(flat.mean(2))
    weight = gaussian_filter(mask.astype(np.float32), PRESHARPEN_EDGE)[..., None]
    blend = flat * (1.0 - weight) + np.clip(sharp, 0.0, 255.0) * weight
    return np.clip(blend, 0.0, 255.0), mask


def presharpen(source, image_mod):
    """``presharpen_pixels`` on one source square. Returns (image, mask coverage)."""
    import numpy as np

    blend, mask = presharpen_pixels(np.asarray(source.convert("RGB"), np.float32))
    return image_mod.fromarray(blend.astype(np.uint8)), float(mask.mean())


def colour_fix_pixels(out_rgb, source_rgb, sigma: float = COLOUR_FIX_SIGMA):
    """Put the source's low frequencies back into the output, and leave the detail alone.

        fixed = out - blur(out, sigma) + blur(source, sigma)

    An upscaler is allowed an opinion about detail the source does not resolve, not about
    what colour a flat fill is, and this model drifts the largest fill in a square by up to
    a whole level of the map's own palette. Both arrays and ``sigma`` are at the OUTPUT's
    resolution: blurring the source small and stretching it drifts half again as much.
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    out = np.array(out_rgb, np.float32)
    out -= gaussian_filter(out, (sigma, sigma, 0))
    out += gaussian_filter(np.asarray(source_rgb, np.float32), (sigma, sigma, 0))
    return np.clip(out, 0.0, 255.0, out=out)


def colour_fix(upscaled, source, image_mod, sigma: float = COLOUR_FIX_SIGMA):
    """``colour_fix_pixels`` on one upscaled square, against the source Lanczos'd to meet it."""
    import numpy as np

    fixed = colour_fix_pixels(
        np.asarray(upscaled.convert("RGB"), np.float32),
        np.asarray(source.resize(upscaled.size, image_mod.LANCZOS), np.float32),
        sigma,
    )
    return image_mod.fromarray(fixed.astype(np.uint8))


def hybrid_upscale(source, upscaled, image_mod, scale: int = ENHANCE_SCALE):
    """The AI everywhere, Lanczos where the AI drops detail. Returns (image, coverage).

    Both sides are computed from the same source square, so the only thing the mask picks
    between is two renderings of identical pixels. It is upsampled bilinearly: a blocky
    blend weight would print the mask's own 4 px grid into the output.
    """
    import numpy as np

    side = source.width * scale
    weight = faint_mask(np.asarray(source, np.float32).mean(2))
    grid = image_mod.fromarray((weight * 255.0).astype(np.uint8))
    big = np.asarray(grid.resize((side, side), image_mod.BILINEAR), np.float32)[..., None] / 255.0
    anime = np.asarray(upscaled, np.float32)
    lanczos = np.asarray(source.resize((side, side), image_mod.LANCZOS), np.float32)
    blend = anime * (1.0 - big) + lanczos * big
    return image_mod.fromarray(blend.astype(np.uint8)), float(weight.mean())
