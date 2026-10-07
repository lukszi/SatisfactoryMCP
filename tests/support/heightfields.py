"""Synthetic terrain fields, written to a folder in exactly the format the generator writes.

The real raster is derived from the game's cooked assets and never committed, so every test
that reads a field builds one of these in ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from satisfactory_mcp.core.arrays import F64Grid, U16Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The synthetic field's grid; not square, so a width/height swap cannot pass.
FAKE_W, FAKE_H = 7, 6
FAKE_X0, FAKE_Y0, FAKE_SPACING = -300.0, -200.0, 100.0

#: The terrain plane stores z_m = (raw - ZERO) / UNITS + OFFSET, as the cook does.
T_ZERO, T_UNITS, T_OFFSET = 32768.0, 128.0, 1.0


def build_field(
    tmp_path: Path, *, water: bool = True, quality: bool = True, density: bool = False
) -> Path:
    """Write a whole synthetic field and return its directory.

    One row per kind of reading: landscape, cliff, fill, no data, landscape under measured
    water, and fill under water whose depth was not measured. In row 5 the sea surface
    stands below the recorded ground, as the open ocean does over the fill raster.
    """
    directory = tmp_path / hf.DIR_NAME
    directory.mkdir(parents=True)
    height = np.zeros((FAKE_H, FAKE_W), np.int16)
    prov = np.zeros((FAKE_H, FAKE_W), np.uint8)
    for row, (layer, value) in enumerate(
        [
            (hf.PROV_LANDSCAPE, 123),
            (hf.PROV_CLIFF, 2456),
            (hf.PROV_FILL, -78),
            (hf.PROV_NODATA, hf.NODATA),
            (hf.PROV_LANDSCAPE, -150),
            (hf.PROV_FILL, -150),
        ]
    ):
        height[row, :] = value
        prov[row, :] = layer
    wet = np.full((FAKE_H, FAKE_W), hf.NODATA, np.int16)
    grade = np.full((FAKE_H, FAKE_W), hf.WATER_DRY, np.uint8)
    wet[4, :] = 20  # 2.0 m of water over ground at -15.0 m
    grade[4, :] = hf.WATER_MEASURED
    wet[5, :] = -170  # a sea surface at -17.0 m over a fill "ground" of -15.0 m
    grade[5, :] = hf.WATER_LEVEL_ONLY

    samples = np.zeros((FAKE_H, FAKE_W), np.uint8)
    if density:
        # Half the cliff row is direct and half interpolated, so the split is exercised.
        samples[1, : FAKE_W // 2] = 3
        prov[1, : FAKE_W // 2] = hf.PROV_CLIFF_DIRECT

    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(height))
    (directory / hf.PROV_NAME).write_bytes(hf.encode_u8(prov))
    if density:
        (directory / hf.DENSITY_NAME).write_bytes(hf.encode_u8(samples))
    if water:
        (directory / hf.WATER_NAME).write_bytes(hf.encode_i16(wet))
        if quality:
            (directory / hf.WATER_QUALITY_NAME).write_bytes(hf.encode_u8(grade))
    (directory / hf.META_NAME).write_text(
        json.dumps(
            {
                "grid": {
                    "width": FAKE_W,
                    "height": FAKE_H,
                    "spacing_cm": FAKE_SPACING,
                    "x0_cm": FAKE_X0,
                    "y0_cm": FAKE_Y0,
                },
                "nodata": hf.NODATA,
                "provenance": {
                    "0": {"name": "no data", "accuracy_m": None},
                    "1": {"name": "landscape", "accuracy_m": 0.205},
                    "3": {"name": "fill", "accuracy_m": 3.897},
                    "4": {"name": "cliff", "accuracy_m": 0.21},
                    "5": {"name": "cliff, direct", "accuracy_m": 0.21},
                },
                "sources": {"game": {"game_version_pinned": "buildVersion 495413, a test"}},
            }
        ),
        encoding="utf-8",
    )
    return directory


def build_shaped_field(tmp_path: Path, height_m: F64Grid) -> Path:
    """A field of exactly the given heights, all landscape, no water: the shape slope needs."""
    directory = tmp_path / hf.DIR_NAME
    directory.mkdir(parents=True)
    rows, cols = height_m.shape
    (directory / hf.HEIGHT_NAME).write_bytes(
        hf.encode_i16(np.rint(height_m * hf.DM_PER_M).astype(np.int16))
    )
    (directory / hf.PROV_NAME).write_bytes(
        hf.encode_u8(np.full((rows, cols), hf.PROV_LANDSCAPE, np.uint8))
    )
    (directory / hf.META_NAME).write_text(
        json.dumps(
            {
                "grid": {
                    "width": cols,
                    "height": rows,
                    "spacing_cm": 100.0,
                    "x0_cm": 0.0,
                    "y0_cm": 0.0,
                },
                "nodata": hf.NODATA,
                "provenance": {"1": {"name": "landscape", "accuracy_m": 0.205}},
            }
        ),
        encoding="utf-8",
    )
    return directory


def terrain_raw(z_m: F64Grid) -> U16Grid:
    """Heights in metres as the terrain plane's raw uint16 values."""
    return np.rint((z_m - T_OFFSET) * T_UNITS + T_ZERO).astype(np.uint16)


def build_layered_field(tmp_path: Path) -> Path:
    """A 7x6 ramp (z = col metres) with one rock texel standing on it.

    The terrain plane sits one column east of the main grid and covers columns 1..6. At
    row 2, col 3 the ground is a 50 m rock top, the bare terrain under it 3 m and the top
    plane a 60 m arch over both.
    """
    directory = build_shaped_field(tmp_path, np.tile(np.arange(7, dtype=float), (6, 1)))
    ground = np.tile(np.arange(7, dtype=np.int16) * 10, (6, 1))
    ground[2, 3] = 500
    prov = np.full((6, 7), hf.PROV_LANDSCAPE, np.uint8)
    prov[2, 3] = hf.PROV_CLIFF_DIRECT
    top = ground.copy()
    top[2, 3] = 600
    terrain = terrain_raw(np.tile(np.arange(1, 7, dtype=float), (6, 1)))
    terrain[5, 5] = 0  # a hole
    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(ground))
    (directory / hf.PROV_NAME).write_bytes(hf.encode_u8(prov))
    (directory / hf.TOP_NAME).write_bytes(hf.encode_i16(top))
    (directory / hf.TERRAIN_NAME).write_bytes(hf.encode_u16(terrain))
    meta = json.loads((directory / hf.META_NAME).read_text(encoding="utf-8"))
    meta["provenance"]["5"] = {"name": "cliff, direct", "accuracy_m": 0.17}
    meta["generator_version"] = 4
    meta["terrain_grid"] = {
        "width": 6,
        "height": 6,
        "spacing_cm": 100.0,
        "x0_cm": 100.0,
        "y0_cm": 0.0,
        "zero": T_ZERO,
        "units_per_m": T_UNITS,
        "offset_m": T_OFFSET,
    }
    (directory / hf.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return directory
