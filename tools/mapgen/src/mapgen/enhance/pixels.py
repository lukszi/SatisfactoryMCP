"""The three passes around the model: pre-sharpen the input, repair faint marks, fix colour."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import convolve, gaussian_filter, maximum_filter, uniform_filter

from mapgen.enhance.upscaler import ENHANCE_SCALE
from mapgen.tiles.cutter import TileImaging
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

if TYPE_CHECKING:
    from PIL import Image

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
    "ImageModule",
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

#: The faint-detail mask: a pixel's depth below its FAINT_WINDOW box mean, grown by
#: FAINT_GROW. FAINT_LO to FAINT_HI is the band the model drops; FAINT_FEATHER blurs the edge.
FAINT_WINDOW = 9
FAINT_GROW = 3
FAINT_LO = 3.0
FAINT_HI = 14.0
FAINT_FEATHER = 5

#: The pre-sharpen of the model's input: PRESHARPEN_ROUNDS of unsharp masking where the band
#: up to PRESHARPEN_HI is over PRESHARPEN_ON, grown where PRESHARPEN_NEIGHBOURS of the eight
#: neighbours are in, feathered by PRESHARPEN_EDGE.
PRESHARPEN_ROUNDS = 3
PRESHARPEN_SIGMA = 1.0
PRESHARPEN_AMOUNT = 0.14
PRESHARPEN_HI = 10.0
PRESHARPEN_ON = 0.15
PRESHARPEN_NEIGHBOURS = 4
PRESHARPEN_EDGE = 0.6

#: In pixels of the 4x output, wider than a stroke there, or the fix blurs the sharpening.
COLOUR_FIX_SIGMA = 6.0


class ImageModule(TileImaging, Protocol):
    """The parts of ``PIL.Image`` the artwork and its enhancement use; passed in, not imported."""

    def open(self, fp: Path, /) -> Image.Image: ...


def faint_depth(luma: NDArray[np.floating]) -> F32Grid:
    """How far each pixel sits below its neighbourhood, grown over a mark's halo.

    Both masks read it, from the source luma alone: positive on a mark, near zero on fill.
    """
    value = luma.astype(np.float32)
    return maximum_filter(uniform_filter(value, FAINT_WINDOW) - value, FAINT_GROW)


def faint_band(depth: F32Grid, hi: float) -> F32Grid:
    """The band from FAINT_LO to ``hi`` as feathered weights, never outside [0, 1]."""
    weight = np.clip((hi - depth) / (hi - FAINT_LO), 0.0, 1.0)
    weight *= np.clip((depth - FAINT_LO) / FAINT_LO, 0.0, 1.0)
    return uniform_filter(weight, FAINT_FEATHER)


def faint_mask(luma: NDArray[np.floating]) -> F32Grid:
    """Where the AI must not be trusted: 1 on faint marks, 0 on flat fill and strong ones."""
    return faint_band(faint_depth(luma), FAINT_HI)


def presharpen_mask(luma: NDArray[np.floating]) -> BoolMask:
    """Where the input is nudged before the model sees it: the weak strokes only, grown once.

    The band stops at PRESHARPEN_HI, below the repair's FAINT_HI: the model expands a mid
    stroke handed more contrast.
    """
    inside = faint_band(faint_depth(luma), PRESHARPEN_HI) > PRESHARPEN_ON
    neighbours = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8)
    grown = convolve(inside.astype(np.uint8), neighbours, mode="nearest")
    return inside | (grown >= PRESHARPEN_NEIGHBOURS)


def presharpen_pixels(rgb: NDArray[np.number]) -> tuple[NDArray[np.floating], BoolMask]:
    """Unsharp the faint marks of one source square, and nothing else. Returns (rgb, mask).

    It lifts a mark over the model's response floor; off the mask it is the identity.
    """
    flat = np.asarray(rgb, np.float32)
    sharp = flat.copy()
    for _ in range(PRESHARPEN_ROUNDS):
        blurred = gaussian_filter(sharp, (PRESHARPEN_SIGMA, PRESHARPEN_SIGMA, 0))
        sharp += PRESHARPEN_AMOUNT * (sharp - blurred)
    mask = presharpen_mask(flat.mean(2))
    weight = gaussian_filter(mask.astype(np.float32), PRESHARPEN_EDGE)[..., None]
    blend = flat * (1.0 - weight) + np.clip(sharp, 0.0, 255.0) * weight
    return np.clip(blend, 0.0, 255.0), mask


def presharpen(source: Image.Image, image_mod: ImageModule) -> tuple[Image.Image, float]:
    """``presharpen_pixels`` on one source square. Returns (image, mask coverage)."""
    blend, mask = presharpen_pixels(np.asarray(source.convert("RGB"), np.float32))
    return image_mod.fromarray(blend.astype(np.uint8)), float(mask.mean())


def colour_fix_pixels(
    out_rgb: NDArray[np.number], source_rgb: NDArray[np.number], sigma: float = COLOUR_FIX_SIGMA
) -> F32Grid:
    """``out - blur(out, sigma) + blur(source, sigma)``: the source's colour, the model's detail.

    Both arrays and ``sigma`` are at the output's resolution.
    """
    out = np.array(out_rgb, np.float32)
    out -= gaussian_filter(out, (sigma, sigma, 0))
    out += gaussian_filter(np.asarray(source_rgb, np.float32), (sigma, sigma, 0))
    return np.clip(out, 0.0, 255.0, out=out)


def colour_fix(
    upscaled: Image.Image,
    source: Image.Image,
    image_mod: ImageModule,
    sigma: float = COLOUR_FIX_SIGMA,
) -> Image.Image:
    """``colour_fix_pixels`` on one upscaled square, against the source Lanczos'd to meet it."""
    fixed = colour_fix_pixels(
        np.asarray(upscaled.convert("RGB"), np.float32),
        np.asarray(source.resize(upscaled.size, image_mod.Resampling.LANCZOS), np.float32),
        sigma,
    )
    return image_mod.fromarray(fixed.astype(np.uint8))


def hybrid_upscale(
    source: Image.Image, upscaled: Image.Image, image_mod: ImageModule, scale: int = ENHANCE_SCALE
) -> tuple[Image.Image, float]:
    """The AI everywhere, Lanczos where the AI drops detail. Returns (image, coverage).

    The mask is upsampled bilinearly: a blocky weight would print its 4 px grid.
    """
    side = source.width * scale
    weight = faint_mask(np.asarray(source, np.float32).mean(2))
    grid = image_mod.fromarray((weight * 255.0).astype(np.uint8))
    resized = grid.resize((side, side), image_mod.Resampling.BILINEAR)
    big = np.asarray(resized, np.float32)[..., None] / 255.0
    anime = np.asarray(upscaled, np.float32)
    lanczos = np.asarray(source.resize((side, side), image_mod.Resampling.LANCZOS), np.float32)
    blend = anime * (1.0 - big) + lanczos * big
    return image_mod.fromarray(blend.astype(np.uint8)), float(weight.mean())
