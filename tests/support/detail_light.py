"""A small field, a bare painted ground and a stand-in light, for the tests that draw the
ground and read what the pass hands the light.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M

#: One band of output, one texel of the field a pixel.
N = 64


class Capture:
    """What the pass hands the light, the detail normal's bytes included."""

    def __init__(self) -> None:
        self.detail = np.full((N, N, 2), -128, np.int16)
        self.calls = 0

    def put(self, row, z_m, land, columns=slice(None), slabs=None, *, detail=None):
        self.calls += 1
        if detail is not None:
            self.detail[row : row + len(z_m), columns] = detail


def sloped_field() -> SimpleNamespace:
    """An ``N`` x ``N`` field over the whole map rising along both axes."""
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    rows, cols = np.mgrid[0:N, 0:N]
    height = (300 + 3 * rows + 2 * cols).astype(np.int16)
    return SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: None, water_quality_raster=lambda: None,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2, spacing_cm=step_cm, width=N, height=N,
    )  # fmt: skip


def bare_painted_ground() -> SimpleNamespace:
    """A painted ground with no rock, crowns or water optics."""
    rock = [np.zeros((N // 4, N // 4), np.float32)]
    return SimpleNamespace(rock=rock, rock_family=None, rock_top_rule=None, titan=None,
                           crowns=None, titan_crowns=None,
                           water_optics=lambda taps, river=None: None)  # fmt: skip
