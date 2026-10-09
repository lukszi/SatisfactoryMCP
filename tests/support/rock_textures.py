"""Synthetic rock textures in the shape the install gives them: RGBA bytes per texture and a
sand top layer, random unless a test asks for a constant."""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.gamedata.rocks.looks import LOOK_TEXTURES, RockTextures, TopRule

SIDE = 64


def _rgba(seed: int, rgb: tuple[int, ...] | None, side: int = SIDE) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = rng.integers(0, 256, (side, side, 4), dtype=np.uint8)
    if rgb is not None:
        out[..., : len(rgb)] = rgb
    out[..., 3] = 255
    return out


def rock_textures(
    *,
    albedo: tuple[int, int, int] | None = None,
    normals: tuple[int, int] | None = None,
    cells_rgb: tuple[int, int, int] | None = None,
    cells_alpha: int | None = None,
) -> RockTextures:
    """Every texture of ``LOOK_TEXTURES`` and a sand top: ``albedo`` and ``normals`` fix the
    colour and normal maps' bytes, ``cells_rgb`` and ``cells_alpha`` the anti-tiling cells'."""
    textures: dict[str, np.ndarray] = {}
    for seed, name in enumerate(LOOK_TEXTURES):
        fixed = normals if name.endswith(("normal", "detail")) else albedo
        textures[name] = _rgba(seed, None if name == "cells" else fixed)
    cells = textures["cells"]
    if cells_rgb is not None:
        cells[..., :3] = cells_rgb
    cells[..., 3] = np.random.default_rng(99).integers(0, 256, cells.shape[:2])
    if cells_alpha is not None:
        cells[..., 3] = cells_alpha
    top = TopRule("Tiles/Sand/TX_Sand_BC", 50.0, 1.5, 1.2)
    tops = {FAMILIES.index("sand"): (top, _rgba(50, albedo, 32))}
    return RockTextures(textures, tops, dict(LOOK_TEXTURES))
