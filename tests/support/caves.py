"""A small cave mask written beside ``build_layered_field``'s 7x6 m ramp.

The mask has 2 m cells: cave decoration under cell (0, 0), and one sound-volume box under the
ramp's east half whose plan touches cells (1..2, 2..3).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from satisfactory_mcp.domain.spatial import caves

CELL_CM = 200.0
#: x0, y0, z0, x1, y1, z1 in cm: 2..9 m under a ramp standing 4..6 m high.
HULL = (450.0, 250.0, -900.0, 650.0, 450.0, -200.0)


def box_planes(box: tuple[float, ...]) -> np.ndarray:
    x0, y0, z0, x1, y1, z1 = box
    return np.array(
        [
            [-1, 0, 0, x0],
            [1, 0, 0, -x1],
            [0, -1, 0, y0],
            [0, 1, 0, -y1],
            [0, 0, -1, z0],
            [0, 0, 1, -z1],
        ],
        np.float64,
    )


def write_caves(directory: Path, mask: np.ndarray, hulls: list[tuple[float, ...]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    planes = [box_planes(h) for h in hulls]
    np.savez_compressed(
        directory / caves.DATA_NAME,
        mask=mask.astype(np.uint8),
        planes=np.concatenate(planes) if planes else np.zeros((0, 4)),
        starts=np.arange(len(hulls) + 1, dtype=np.int64) * 6,
        boxes=np.array(hulls, np.float64).reshape(-1, 6),
    )
    h, w = mask.shape
    meta = {"grid": {"width": w, "height": h, "cell_cm": CELL_CM, "x0_cm": 0.0, "y0_cm": 0.0}}
    (directory / caves.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return directory


def fixture_mask() -> np.ndarray:
    mask = np.zeros((3, 4), np.uint8)
    mask[0, 0] = caves.BIT_MARKERS
    mask[1:3, 2:4] |= caves.BIT_HULL
    return mask
