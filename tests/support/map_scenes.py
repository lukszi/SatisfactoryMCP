"""Stand-in grounds and scenes for the map-style tests: no install, no field on disk."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.water.bodies import CLASSES
from mapgen.palette.painted import WATER_TABLE_COLUMNS, srgb_to_linear, water_table
from mapgen.palette.relief import ReliefGround
from mapgen.palette.styles import PAINTED_PALETTE, RELIEF_PALETTES
from satisfactory_mcp.domain.spatial import heightfield as hf

#: Water class name to its row in the class table.
CLASS_ID = {name: i for i, name in enumerate(CLASSES)}


def stub_field(height_m: np.ndarray, water_m: np.ndarray | None = None, grades=None):
    """The parts of a heightfield the relief painter reads, at 1 m spacing."""
    height = (height_m * hf.DM_PER_M).astype(np.int16)
    water = None if water_m is None else (water_m * hf.DM_PER_M).astype(np.int16)
    rows, cols = height.shape
    return SimpleNamespace(
        _height_dm=height,
        _water_raster=lambda: water,
        _water_quality_raster=lambda: grades,
        spacing_cm=100.0,
        width=cols,
        height=rows,
    )


def relief_ground(layer: str, biome=None, names=()) -> ReliefGround:
    """A relief layer's ground over a 64x64 ramp from 0 to 100 m."""
    ramp = np.linspace(0.0, 100.0, 64, dtype=np.float32)
    return ReliefGround(
        RELIEF_PALETTES[layer][0], stub_field(np.tile(ramp, (64, 1))), biome, list(names)
    )


def painted_ground_stub(n: int) -> SimpleNamespace:
    """A one-row painted ground of ``n`` texels: flat albedo, grey rock, the palette's water."""
    palette = PAINTED_PALETTE
    water = palette["water"]
    rock = [np.full((1, n), 0.2, np.float32) for _ in range(3)]
    return SimpleNamespace(
        palette=palette,
        albedo=[np.full((1, n), v, np.float32) for v in (0.35, 0.3, 0.2)],
        canopy=np.zeros((1, n), np.float32),
        canopy_rgb=np.zeros(3, np.float32),
        rock=rock,
        rock_family=None,
        crown=None,
        titan=None,
        carpet=None,
        mesh_rgb={},
        seabed_coral=np.zeros(3, np.float32),
        water={
            "k": np.asarray(water["k_per_m"], np.float32),
            "body": srgb_to_linear(water["body"]),
            "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
            "deep": srgb_to_linear(water["deep"]),
            "deep_tau_m": np.float32(water["deep_tau_m"]),
            "bed": np.float32(water["bed_wet"]),
            "inland_floor": np.float32(water.get("inland_floor", 0.0)),
        },
        ramp=(0.0, 100.0, np.linspace(0.0, 100.0, 101, dtype=np.float32)),
        opaque_water=[],
    )


def water_scene(n: int, optics=None) -> dict:
    """A one-row scene under water deepening from 0 to 6 m, with optional class optics."""
    depth = np.linspace(0.0, 6.0, n, dtype=np.float32)[None, :]
    zero = np.zeros((1, n), np.float32)
    water = {
        "depth_m": depth,
        "cover": np.ones((1, n), np.float32),
        "edge": zero,
        "ocean": zero,
        "above_m": zero + 10,
        "below_m": zero + 10,
    }
    return {
        "z_m": zero + 5,
        "borrow": np.ones((1, n), np.float32),
        "ndl": np.ones((1, n), np.float32),
        "ndl_flat": np.float32(1.0),
        "rock_weight": zero,
        "mesh_weight": None,
        "water": water,
        "water_optics": optics,
    }


def class_optics(ground, cls: str, n: int) -> dict:
    """The optics of water class ``cls`` across ``n`` texels, over ``ground``'s water."""
    row = water_table(PAINTED_PALETTE)[CLASS_ID[cls]]
    k, body, deep, tau, turbidity, tint = np.split(
        np.tile(row, (1, n, 1)), np.cumsum(WATER_TABLE_COLUMNS)[:-1], axis=-1
    )
    return {**ground.water, "k": k, "body": body, "deep": deep, "deep_tau_m": tau,
            "turbidity": turbidity, "tint": tint}  # fmt: skip
