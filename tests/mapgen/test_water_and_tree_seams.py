"""Where the wave-2 map features meet: river ribbons and water classes, the inland floor and
class turbidity, and the paint store's crown tops as the lighting stage's occluder.

docs/spatial-and-map.md section 37. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.water.bodies import CLASSES, OCEAN
from mapgen.lighting.occluders import sheet_crowns
from mapgen.palette.painted import (
    WATER_TABLE_COLUMNS,
    PaintedGround,
    painted_colours,
    water_table,
)
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.terrain.sample import taps_linear
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.map_scenes import class_optics, painted_ground_stub, water_scene

RIVER = CLASSES.index("river")


def _ground(plane) -> PaintedGround:
    ground = object.__new__(PaintedGround)
    ground.water_class = plane
    ground.water_rows = water_table(PAINTED_PALETTE)
    ground.water = {"inland_floor": np.float32(0.35)}
    return ground


def _taps(n):
    rows = taps_linear(np.zeros(1, np.float32), 4)
    cols = taps_linear(np.arange(n, dtype=np.float32), 4)
    return rows, cols


def test_a_ribbon_over_dry_class_texels_draws_with_the_river_row():
    ground = _ground(np.zeros((4, 4), np.uint8))
    share = np.array([[1.0, 0.5, 0.0]], np.float32)

    assert ground.water_optics(_taps(3)) is None
    optics = ground.water_optics(_taps(3), share)

    river_k = ground.water_rows[RIVER][:3]
    ocean_k = ground.water_rows[OCEAN][:3]
    np.testing.assert_allclose(optics["k"][0, 0], river_k, rtol=1e-6)
    np.testing.assert_allclose(optics["k"][0, 1], (river_k + ocean_k) / 2, rtol=1e-6)
    np.testing.assert_allclose(optics["k"][0, 2], ocean_k, rtol=1e-6)
    assert sum(WATER_TABLE_COLUMNS) == ground.water_rows.shape[1]


def test_class_turbidity_and_the_inland_floor_take_the_larger_not_both():
    n = 16
    ground = painted_ground_stub(n)
    ground.water["inland_floor"] = np.float32(0.35)
    same = lambda plane: plane
    lake = class_optics(ground, "lake", n)
    murk = {**lake, "turbidity": np.full_like(lake["turbidity"], 0.35)}

    floored = painted_colours(water_scene(n, lake), ground, same, same)
    np.testing.assert_allclose(floored, painted_colours(water_scene(n, murk), ground, same, same))
    swamp = class_optics(ground, "swamp", n)
    assert float(swamp["turbidity"].min()) > 0.35, "the swamp's own murk is the larger"


def test_sheet_crowns_keeps_a_crown_a_coarse_pixel_would_miss():
    grid = {"x0_cm": BOUNDS_M["x_min_m"] * 100.0, "y0_cm": BOUNDS_M["y_min_m"] * 100.0,
            "spacing_cm": 100.0}  # fmt: skip
    top = np.full((64, 64), hf.NODATA, np.int16)
    top[5, 6] = 250
    size = int((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / 8.0)
    out = np.empty((size, size), np.float32)

    sheet = sheet_crowns(top, grid, size, out)

    assert np.nanmax(sheet) == pytest.approx(25.0)
    assert np.isfinite(sheet).sum() >= 1
    assert np.isnan(sheet[-1, -1])


def test_an_unlit_run_deletes_its_crown_occluder_with_the_light_cache(tmp_path):
    from types import SimpleNamespace

    from mapgen.tiles.lit import LIGHT_CACHE_DIR_NAME, UnlitRun, crown_occluder

    grid = {"x0_cm": BOUNDS_M["x_min_m"] * 100.0, "y0_cm": BOUNDS_M["y_min_m"] * 100.0,
            "spacing_cm": 100.0}  # fmt: skip
    painted = SimpleNamespace(crown=np.full((8, 8), 120, np.int16), meta={"grid": grid})
    run = UnlitRun(tmp_path, 16, crown_occluder(painted, tmp_path, 16))

    assert (tmp_path / LIGHT_CACHE_DIR_NAME / "occluder.npy").is_file()
    assert not (tmp_path / LIGHT_CACHE_DIR_NAME / "crowns.npy").exists(), "written once"
    run.close()
    assert not (tmp_path / LIGHT_CACHE_DIR_NAME).exists()
    assert crown_occluder(SimpleNamespace(), tmp_path, 16) is None
