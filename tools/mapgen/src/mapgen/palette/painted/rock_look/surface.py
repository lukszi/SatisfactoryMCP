"""The look on a band: the pixels a rock or the Cliff layer covers read through the texel
kernel, and turned into albedo detail, the top layer's mask and the normal maps' light.

The colour targets stay the calibrated ones: a texture moves a pixel by its texel over the
texture's mean, so a rock's median keeps its target. The normal maps' light is a ratio of the
style's sky and sun on the mapped normal to the same light on the drawn surface's own, so it
rides under the run's light. docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.gamedata.rocks.looks import FALLOFF_CONTRAST, FALLOFF_POWER
from mapgen.jit import gpu_on
from mapgen.lighting.sun import DEFAULT_SUN, sun_vector
from mapgen.palette.painted.rock_look.atlas import RockLook
from mapgen.palette.painted.rock_look.reference import LookPixels, LookTexels
from mapgen.palette.painted.shapes import PaintedPalette, PaintedScene
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, I32Grid, U8Grid

__all__ = [
    "BandLook",
    "band_look",
    "mixed",
    "read_texels",
    "surface_normals",
    "top_mask",
    "top_rules",
]

_F = np.float32


class BandLook(NamedTuple):
    """A band's look at its picked pixels, neutral elsewhere: the body's and the top layer's
    albedo over their means, the normal-mapped normal's up component, and the light's ratio."""

    body: F32Grid
    top: F32Grid
    up: F32Grid
    shade: F32Grid


def surface_normals(z_m: FloatGrid, spacing_m: float) -> F32Grid:
    """The drawn surface's unit normal per pixel, ``(rows, cols, 3)`` east, south, up, from
    central differences."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    inv = 1.0 / np.sqrt(1.0 + d_east * d_east + d_south * d_south)
    return np.stack([-d_east * inv, -d_south * inv, inv], -1).astype(np.float32)


def read_texels(look: RockLook, px: LookPixels) -> LookTexels:
    """The texel kernel: on the device with ``--gpu``, else the reference, the same bits."""
    if gpu_on():
        from mapgen.palette.painted.rock_look import gpu

        return gpu.look_texels(look, px)
    from mapgen.palette.painted.rock_look import reference

    return reference.look_texels(look, px)


def top_rules(look: RockLook | None, codes: U8Grid) -> tuple[F32Grid, F32Grid]:
    """Each pixel's top layer mask power and contrast: its family's, else the cliff master's."""
    power = np.full(codes.shape, FALLOFF_POWER, np.float32)
    contrast = np.full(codes.shape, FALLOFF_CONTRAST, np.float32)
    for code, rule in ({} if look is None else look.rules).items():
        hit = codes == code
        power[hit], contrast[hit] = rule.power, rule.contrast
    return power, contrast


def top_mask(up: FloatGrid, power: FloatGrid, contrast: FloatGrid) -> F32Grid:
    """Where the top layer lies: the cliff master's ``CheapContrast(SlopeMask ** power,
    contrast)``, the slope mask being the normal's up component."""
    lifted = np.power(np.maximum(up, _F(0.0)), power)
    return np.clip(lifted * (_F(1.0) + contrast * _F(2.0)) - contrast, 0.0, 1.0).astype(np.float32)


def _light(normal: F32Grid, palette: PaintedPalette) -> F32Grid:
    """The style's sky and sun on ``normal``, one on flat ground, under the default sun."""
    sun = np.asarray(sun_vector(*DEFAULT_SUN), np.float32)
    ambient = _F(palette["ambient"])
    toward = normal[..., 0] * sun[0] + normal[..., 1] * sun[1] + normal[..., 2] * sun[2]
    lit = np.maximum(toward, _F(0.0)) / sun[2]
    return ambient + (_F(1.0) - ambient) * lit


def band_look(
    look: RockLook,
    scene: PaintedScene,
    palette: PaintedPalette,
    pick: BoolMask,
    reads: tuple[U8Grid, I32Grid],
) -> BandLook:
    """The look at the band's ``pick``ed pixels, each read as its kind with its family's top
    layer's albedo tile (-1 for none): ``reads``."""
    kind, top_tile = reads
    shape = pick.shape
    ones = np.ones((*shape, 3), np.float32)
    out = BandLook(ones, ones.copy(), np.zeros(shape, np.float32), np.ones(shape, np.float32))
    rows, cols = np.nonzero(pick)
    if not rows.size:
        return out
    _band, lo, _hi, c0, _c1, spacing_m = scene["grid"]
    normals = surface_normals(scene["z_m"], spacing_m)[rows, cols]
    px = LookPixels(
        x_m=np.asarray((c0 + cols + 0.5) * spacing_m, np.float64),
        y_m=np.asarray((lo + rows + 0.5) * spacing_m, np.float64),
        normal=normals,
        kind=kind[rows, cols].astype(np.uint8),
        top=top_tile[rows, cols].astype(np.int32),
    )
    texels = read_texels(look, px)
    out.body[rows, cols], out.top[rows, cols] = texels.body, texels.top
    out.up[rows, cols] = texels.normal[:, 2]
    out.shade[rows, cols] = _light(texels.normal, palette) / _light(normals, palette)
    return out


def mixed(ratio: F32Grid, strength: float) -> F32Grid:
    """A ratio to one, taken ``strength`` of the way: 0 is none of it, 1 all."""
    return (_F(1.0) + (ratio - _F(1.0)) * _F(strength)).astype(np.float32)
