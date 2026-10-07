"""Area targets stay on their own land: an offshore piece of an area takes the area it borders.

docs/spatial-and-map.md section 31. Synthetic fixtures: no install, no paint store.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.colour import oklab
from mapgen.palette.painted.calibration import display_to_ground, rehome_offshore
from mapgen.palette.painted.ground import ROCK_GRID_M, PaintedGround, land_cells
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.domain.spatial import heightfield as hf

SEA, DESERT, SPIRE = 0, 1, 2
NAMES = ["Area_NoMansLand", "Area_RockyDesert", "Area_SpireCoast"]
DESERT_ROCK, SPIRE_ROCK = "#ae8271", "#51524d"


def _coast():
    """A 16x16 coarse map: the desert's land in the south-west, the Spire Coast in the east,
    and a strip of sea north of the Spire Coast that the area map gives to the desert, with an
    island in it. Plus a second desert body on land, cut off by the sea."""
    index = np.full((16, 16), SEA, np.uint8)
    land = np.zeros((16, 16), bool)
    index[10:, :8] = DESERT
    land[10:, :8] = True
    index[6:, 8:] = SPIRE
    land[10:, 8:] = True
    index[:6, 8:] = DESERT
    land[2:4, 11:13] = True
    index[7:9, :2] = DESERT
    land[7:9, :2] = True
    return index, land


def test_an_offshore_piece_takes_the_area_it_borders_and_the_desert_keeps_its_land():
    index, land = _coast()
    out = rehome_offshore(index, NAMES, land, NAMES[SEA])
    assert (out[:6, 8:] == SPIRE).all(), "the strip borders the Spire Coast more than the sea"
    assert (out[10:, :8] == DESERT).all() and (out[7:9, :2] == DESERT).all()
    assert (out[index != DESERT] == index[index != DESERT]).all()


def test_an_offshore_piece_with_only_sea_around_it_belongs_to_no_area():
    index = np.full((12, 12), SEA, np.uint8)
    land = np.zeros((12, 12), bool)
    index[6:, :] = DESERT
    land[6:, :] = True
    index[1:3, 1:3] = DESERT
    out = rehome_offshore(index, NAMES, land, NAMES[SEA])
    assert (out[1:3, 1:3] == SEA).all() and (out[6:] == DESERT).all()


def _field(land_1m):
    level = np.int16(round(OCEAN_LEVEL_M * hf.DM_PER_M))
    return SimpleNamespace(
        height=land_1m.shape[0],
        width=land_1m.shape[1],
        height_dm=np.where(land_1m, 100, -300).astype(np.int16),
        water_raster=lambda: np.where(land_1m, hf.NODATA, level).astype(np.int16),
        water_quality_raster=lambda: np.where(land_1m, hf.WATER_DRY, hf.WATER_MEASURED).astype(
            np.uint8
        ),
    )


def _ground(index_1m, field):
    ground = object.__new__(PaintedGround)
    calibration = {
        **PAINTED_PALETTE["calibration"],
        "area_blur_m": 0.4,
        "rock": "#85816c",
        "areas": [
            {"areas": ["Area_RockyDesert"], "rock": DESERT_ROCK},
            {"areas": ["Area_SpireCoast"], "rock": SPIRE_ROCK},
        ],
    }
    ground.palette = {**PAINTED_PALETTE, "calibration": calibration}
    ground.area_names, ground.area_assets, ground.source = NAMES, [], {}
    ground.meta = {"albedo_linear": {"rock": [0.2, 0.2, 0.2]}}
    ground.coarse_index = ground._coarse_areas(index_1m, field)
    return ground


def test_an_offshore_rock_beside_a_desert_wears_the_spire_coast_rock_not_the_desert_red():
    index, land = _coast()
    up = np.ones((ROCK_GRID_M, ROCK_GRID_M), np.uint8)
    index_1m, land_1m = np.kron(index, up), np.kron(land, up).astype(bool)
    ground = _ground(index_1m, _field(land_1m))
    assert ground.source["offshore_cells_rehomed"] == 6 * 8
    rock = np.stack(ground._rock(np.full((*index_1m.shape, 3), 0.25, np.float32)), -1)
    expected = {
        (2, 11): SPIRE_ROCK,
        (4, 14): SPIRE_ROCK,
        (13, 12): SPIRE_ROCK,
        (13, 3): DESERT_ROCK,
        (7, 0): DESERT_ROCK,
    }
    for (r, c), target in expected.items():
        want = display_to_ground(ground.palette, target)
        np.testing.assert_allclose(oklab(rock[r, c])[1:], want[1:], atol=2e-3)


def test_a_field_without_water_planes_keeps_the_area_map_as_it_is():
    index, _land = _coast()
    field = SimpleNamespace(water_raster=lambda: None, water_quality_raster=lambda: None)
    assert land_cells(field, index.shape) is None
    ground = _ground(np.kron(index, np.ones((ROCK_GRID_M, ROCK_GRID_M), np.uint8)), field)
    assert (ground.coarse_index == index).all() and "offshore_cells_rehomed" not in ground.source


def test_land_is_ground_above_the_sea_and_a_lake_counts_as_land():
    land = np.zeros((8, 8), bool)
    land[:, 4:] = True
    field = _field(land)
    lake = field.water_raster().copy()
    lake[:2, 4:] = 500
    grades = field.water_quality_raster().copy()
    grades[:2, 4:] = hf.WATER_MEASURED
    field.water_raster, field.water_quality_raster = (lambda: lake), (lambda: grades)
    got = land_cells(field, (4, 4))
    assert got.tolist() == [[False, False, True, True]] * 4
