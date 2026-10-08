"""Rock colour on the coarse rock grid: the game's rock albedo, tinted by the ground around it,
then set on its area's or the default target. docs/map/painted.md section 27 and
docs/map/calibration.md section 31, "Area targets".
"""

from __future__ import annotations

from collections.abc import Callable, Collection

import numpy as np
from scipy import ndimage

from mapgen.colour import linear_from_oklab, oklab
from mapgen.palette.painted.calibration import display_to_ground
from mapgen.palette.painted.shapes import FloatGrid, PaintedPalette, PaintMeta

__all__ = ["ROCK_GRID_M", "rock_planes", "tinted_rock"]

#: The rock colour's grid, coarser than the paint: it is a 25 m blur of it.
ROCK_GRID_M = 4


def rock_planes(
    lab: FloatGrid, palette: PaintedPalette, area_weight: Callable[[Collection[str]], FloatGrid]
) -> list[FloatGrid]:
    """Rock colour on the rock grid from ``lab``, ``tinted_rock``'s.

    Each area entry's rock target, then the default ``rock`` target everywhere else, sets
    the chroma and hue and moves the lightness by the median offset, keeping its variation.
    """
    cal = palette["calibration"]
    groups = [
        (area_weight(e["areas"])[: lab.shape[0], : lab.shape[1]], e["rock"])
        for e in cal.get("areas", [])
        if "rock" in e
    ]
    mask = np.zeros(lab.shape[:2], np.float32)
    for weight, _ in groups:
        mask += weight
    if "rock" in cal:
        groups.append((np.clip(1.0 - mask, 0.0, 1.0), cal["rock"]))
        mask = mask + groups[-1][0]
    norm = np.maximum(mask, 1.0)
    shift = np.zeros_like(lab)
    for weight, hex_colour in groups:
        if not (weight > 0.5).any():
            continue
        target = display_to_ground(palette, hex_colour)
        moved = np.empty_like(lab)
        moved[..., 0] = lab[..., 0] + (target[0] - np.median(lab[weight > 0.5][:, 0]))
        moved[..., 1:] = target[1:]
        shift += (moved - lab) * (weight / norm)[..., None]
    lab = lab + shift
    mask = np.minimum(mask, 1.0)
    rock = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    if cal.get("rock_keeps_exposure"):
        rock *= (mask + (1.0 - mask) / np.float32(palette["tone"]["gain"]))[..., None]
    return [rock[..., k].astype(np.float32) for k in range(3)]


def tinted_rock(albedo: FloatGrid, meta: PaintMeta, palette: PaintedPalette) -> FloatGrid:
    """The game's rock albedo in OKLab on the rock grid, moved toward the ground around."""
    step = ROCK_GRID_M
    rock_lab = oklab(np.asarray(meta["albedo_linear"]["rock"], np.float32))
    blur = palette["rock_tint_blur_m"]
    near = np.stack(
        [ndimage.gaussian_filter(albedo[..., k], blur)[::step, ::step] for k in range(3)], -1
    )
    lab = oklab(np.clip(near, 1e-7, None))
    lightness, chroma = palette["rock_tint_lightness"], palette["rock_tint_chroma"]
    lab[..., 0] = rock_lab[0] * (1 - lightness) + lab[..., 0] * lightness
    lab[..., 0] += np.float32(palette["rock_lightness_add"])
    lab[..., 1:] = rock_lab[1:] * (1 - chroma) + lab[..., 1:] * chroma
    return lab
