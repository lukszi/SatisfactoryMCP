"""The sea's physics row: the game's ocean absorption over the bed, the sky term at depth, and
what is seen behind the surface scaled as the ocean material scales it.

docs/map/water.md section 33, "The sea's physics row".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.colour import srgb_to_linear
from mapgen.gamedata.water.ocean import OceanAbsorption
from mapgen.palette.schema import PaintedWaterStyle
from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["SeaRow", "sea_row", "surface_sky"]


class SeaRow(NamedTuple):
    """The ocean's optics, linear: absorption per metre, body, deep colour, deep tau, and the
    scale on everything seen behind the surface."""

    k: F32Grid
    body: F32Grid
    deep: F32Grid
    deep_tau_m: np.float32
    behind: F32Grid


def surface_sky(water: PaintedWaterStyle) -> F32Grid:
    """The sky the surface reflects over every water, linear."""
    return srgb_to_linear(water["sky"]) * np.float32(water["surface_r"])


def sea_row(water: PaintedWaterStyle, absorption: OceanAbsorption | None = None) -> SeaRow:
    """The sea's row: ``absorption_scale`` times the material's absorption (the palette's
    ``k_per_m`` without it), and a body that, with the surface's reflection, is the sky term,
    which is also the deep colour, so the open sea draws the sky term exactly. What is seen
    behind the surface takes the material's scale (``behind_scale`` without it) in the
    fitted ``behind_tint``."""
    k, scale = water["k_per_m"], water["behind_scale"]
    if absorption is not None:
        k = [water["absorption_scale"] * a for a in absorption.per_m]
        scale = absorption.behind
    deep = srgb_to_linear(water["sky_term"])
    body = np.maximum(deep - surface_sky(water), np.float32(0.0))
    behind = np.float32(scale) * np.asarray(water["behind_tint"], np.float32)
    return SeaRow(np.asarray(k, np.float32), body, deep, np.float32(water["deep_tau_m"]), behind)
