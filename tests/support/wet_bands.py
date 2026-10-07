"""Random bands of water for the wet painters' tests: no install, no field on disk."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.water.bodies import OCEAN, SWAMP
from mapgen.palette.painted.optics import class_optics, water_table
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.terrain.sample import taps_linear
from tests.support.map_scenes import painted_ground_stub


def band_water(rng: np.random.Generator, shape: tuple[int, int], dry: float,
               full: float = 0.3) -> dict:  # fmt: skip
    """A band's water terms: ``dry`` of it without water; of the rest about ``full`` wholly
    covered and the others partly."""
    cover = rng.random(shape).astype(np.float32)
    cover[rng.random(shape) < full] = 1.0
    cover[rng.random(shape) < dry] = 0.0
    river = np.where(rng.random(shape) < 0.3, rng.random(shape), 0.0).astype(np.float32)
    planes = {name: rng.uniform(0.0, 6.0, shape).astype(np.float32)
              for name in ("depth_m", "above_m", "below_m", "river_below_m")}  # fmt: skip
    planes["river_below_m"][river == 0] = np.inf
    ocean = (rng.random(shape) < 0.5).astype(np.float32)
    return {**planes, "cover": cover, "depth": rng.random(shape).astype(np.float32),
            "ocean": ocean, "banks": ocean, "edge": rng.random(shape).astype(np.float32),
            "river": river}  # fmt: skip


def _optics(rng: np.random.Generator, shape: tuple[int, int], ground: SimpleNamespace):
    """Class optics over a class plane of ocean, swamp and lake, as a band reads them."""
    plane = rng.choice(np.array([OCEAN, SWAMP, 3], np.uint8), (shape[0] + 1, shape[1] + 1))
    taps = tuple(taps_linear(np.linspace(0.0, n - 0.01, n), n + 1) for n in shape)
    rows = water_table(PAINTED_PALETTE)
    river = (rng.random(shape) < 0.2).astype(np.float32)
    return class_optics(plane, rows, ground.water, taps, river, shares=(SWAMP,))


def painted_band(rng: np.random.Generator, shape: tuple[int, int], dry: float, *, optics: bool,
                 crowns: bool, carpet: bool, opaque: bool) -> tuple[dict, SimpleNamespace, dict | None]:  # fmt: skip
    """A painted band's scene, ground and crowns, every plane the band's shape."""
    ground = painted_ground_stub(shape[1])
    ground.water["opaque_tau_m"] = np.float32(0.3)
    if carpet:
        ground.carpet = (rng.integers(0, 256, shape).astype(np.uint8),
                         rng.uniform(-20.0, -14.0, shape).astype(np.float16))  # fmt: skip
    if opaque:
        mud = np.array([0.4, 0.2, 0.2], np.float32)
        ground.opaque_water = [(rng.random(shape).astype(np.float32), mud, SWAMP)]
    if optics:
        ground.water_class = np.zeros((1, 1), np.uint8)
    scene = {
        "z_m": rng.uniform(-20.0, -15.0, shape).astype(np.float32),
        "water": band_water(rng, shape, dry),
        "water_optics": _optics(rng, shape, ground) if optics else None,
    }
    layer = None
    if crowns:
        layer = {"alpha": rng.random(shape).astype(np.float32),
                 "colour": rng.random((*shape, 3)).astype(np.float32),
                 "top_m": rng.uniform(-20.0, -12.0, shape).astype(np.float32),
                 "sunk": rng.random(shape).astype(np.float32)}  # fmt: skip
    return scene, ground, layer
