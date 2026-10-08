"""Synthetic crown sprites for the draw's tests, packed by the sprite cache's own encoder.

A paint store sprite (cover, top, material slot) becomes a crown sprite of its slots' flat
colours and an upright normal; a random one has every channel varied. No install.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from mapgen.gamedata.vegetation.crown_sprites import CrownSprite
from mapgen.sprites.raster import SpritePlanes
from mapgen.sprites.store import SpriteAtlas, encode_atlas
from mapgen.terrain.crown_atlas import CrownAtlas, crown_atlas


def flat_sprite(sprite: CrownSprite, colours: Sequence[Sequence[float]]) -> SpritePlanes:
    """A paint store sprite as a crown sprite: each slot's colour, upright, its own top."""
    alpha = sprite["cover"].astype(np.float32) / np.float32(255.0)
    colour = np.zeros((*alpha.shape, 3), np.float32)
    for k, rgb in enumerate(colours):
        colour[sprite["slot"] == k] = rgb
    normal = np.zeros((*alpha.shape, 3), np.float32)
    normal[..., 2] = 1.0
    return SpritePlanes(
        sprite["x0_cm"], sprite["y0_cm"], alpha, colour, normal, sprite["top_cm"].astype(np.float32)
    )


def random_sprite(rng: np.random.Generator, rows: int, cols: int) -> SpritePlanes:
    """A sprite of holes, colours, tilted normals and tops, its corner off the pivot."""
    alpha = rng.uniform(0.0, 1.0, (rows, cols)).astype(np.float32)
    alpha[rng.random(alpha.shape) < 0.3] = 0.0
    colour = rng.uniform(0.02, 0.4, (rows, cols, 3)).astype(np.float32)
    normal = rng.normal(0.0, 0.5, (rows, cols, 3)).astype(np.float32)
    normal[..., 2] = np.abs(normal[..., 2]) + 0.5
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    top = rng.uniform(300.0, 2500.0, (rows, cols)).astype(np.float32)
    x0, y0 = -0.5 * 12.5 * cols, -0.4 * 12.5 * rows
    return SpritePlanes(x0, y0, alpha, colour, normal.astype(np.float32), top)


def sprite_atlas(
    sprites: Sequence[SpritePlanes],
    titan: np.ndarray | None = None,
    names: Sequence[str] | None = None,
) -> SpriteAtlas:
    """The sprites as the cache stores them, named ``S0``, ``S1``, ... unless ``names``."""
    named = names or [f"S{k}" for k in range(len(sprites))]
    return encode_atlas(list(zip(named, sprites, strict=True)), titan)


def draw_atlas(sprites: Sequence[SpritePlanes], names: Sequence[str] | None = None) -> CrownAtlas:
    """The sprites as the stamps read them."""
    return crown_atlas(sprite_atlas(sprites, names=names))
