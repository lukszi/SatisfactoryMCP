"""A small synthetic paint store with the layers' own textures, for the ground detail tests.

Every texture is seeded noise from its asset's name, 32 px at most; the cell noise is 8 x 8
cells of 16 px, each with its own shift, scale and turn, its mask 255 inside and falling to
0 over its border's 4 px. No install, no data/local.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from mapgen.gamedata.ground.layer_textures import (
    GROUND_TEXTURES_NAME,
    NOISE,
    encode_ground_textures,
)
from mapgen.gamedata.ground.paint_store import META_NAME
from satisfactory_mcp.domain.spatial import heightfield as hf

NOISE_ASSET = "MasterMaterials/Resources/" + NOISE
TEXTURE_PX = 32
CELL_PX = 16
CELLS = 8


def noise_rgba(seed: int = 3) -> np.ndarray:
    """The cell noise: per cell a random R, G and B, the mask a ramp inside its border."""
    rng = np.random.default_rng(seed)
    side = CELL_PX * CELLS
    rgba = np.repeat(np.repeat(rng.integers(0, 256, (CELLS, CELLS, 4)), CELL_PX, 0), CELL_PX, 1)
    inside = np.arange(side) % CELL_PX
    edge = np.minimum(inside, CELL_PX - 1 - inside)
    rgba[..., 3] = np.clip(np.minimum(edge[:, None], edge[None, :]) * 64, 0, 255)
    return rgba.astype(np.uint8)


def texture_rgba(asset: str, side: int) -> np.ndarray:
    """A texture of the store: seeded noise from the asset's name, or the cell noise."""
    if asset == NOISE_ASSET:
        return noise_rgba()
    rng = np.random.default_rng(zlib.crc32(asset.encode()))
    n = min(side, TEXTURE_PX)
    return rng.integers(30, 226, (n, n, 4)).astype(np.uint8)


def write_textured_store(
    directory: Path, weights: Mapping[str, np.ndarray], spacing_cm: float = 100.0
) -> Path:
    """A store of ``weights`` (layer -> uint8 plane on the grid) and the ground textures."""
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    shape = next(iter(weights.values())).shape
    for layer, plane in weights.items():
        name = f"w.{layer}.u8.z"
        (directory / name).write_bytes(hf.encode_u8(np.ascontiguousarray(plane, np.uint8)))
        files[name] = {"shape": list(shape), "kind": "u8", "layer": layer}
    blob, index = encode_ground_textures(texture_rgba)
    (directory / GROUND_TEXTURES_NAME).write_bytes(blob)
    files[GROUND_TEXTURES_NAME] = {"kind": "textures"}
    grid = {"width": shape[1], "height": shape[0], "x0_cm": 0.0, "y0_cm": 0.0,
            "spacing_cm": spacing_cm}  # fmt: skip
    meta = {"generator_version": 5, "grid": grid, "files": files, "ground_textures": index}
    (directory / META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return directory
