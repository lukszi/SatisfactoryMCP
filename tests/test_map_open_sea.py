"""The open sea past the measured bed, the void, the pits and the seabed meshes.

docs/spatial-and-map.md section 26. Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.gamedata.frame import BOUNDS_M  # noqa: E402
from mapgen.gamedata.water import VOID_ARTWORK_LUMA_MAX, artwork_planes  # noqa: E402
from mapgen.palette.relief import water_tint_plane  # noqa: E402
from mapgen.palette.rivers import water_sources  # noqa: E402
from mapgen.palette.shore import OCEAN_LEVEL_M, composite_meshes  # noqa: E402
from mapgen.palette.styles import RELIEF_PALETTES, SEA_RGB, with_void  # noqa: E402
from mapgen.palette.water import OPEN_SEA_DEPTH_M, OPEN_SEA_SETTLE_M, open_sea  # noqa: E402
from mapgen.terrain.fill import SOURCE_HOLE, SOURCE_PIT, fill_field, pits, relax  # noqa: E402
from mapgen.terrain.rasters import MESH_CORAL, MESH_ROCK, MESH_SHELL  # noqa: E402
from mapgen.tiles.compose import composite_top, render_layer  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402

OCEAN_DM = round(OCEAN_LEVEL_M * hf.DM_PER_M)


def _field(height_dm, water_dm, grades, spacing_cm=100.0):
    rows, cols = height_dm.shape
    return SimpleNamespace(
        _height_dm=height_dm,
        _prov=np.where(height_dm == hf.NODATA, hf.PROV_NODATA, hf.PROV_LANDSCAPE).astype(np.uint8),
        _water_raster=lambda: water_dm,
        _water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100,
        y0_cm=BOUNDS_M["y_min_m"] * 100,
        spacing_cm=spacing_cm,
        width=cols,
        height=rows,
    )


def _coast(rows=40, cols=2000, shelf_m=8.0):
    """Land in the west, a measured shelf ``shelf_m`` deep, level-only sea to the east."""
    height = np.full((rows, cols), -170 - int(shelf_m * hf.DM_PER_M), np.int16)
    height[:, :30] = 50
    water = np.where(height < 0, OCEAN_DM, hf.NODATA).astype(np.int16)
    grades = np.where(height < 0, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    grades[:, 60:] = hf.WATER_LEVEL_ONLY
    height[:, 60:] = OCEAN_DM  # the fill raster holds the surface, not a bed
    return height, water, grades


# ------------------------------------------------------------------- the artwork


def test_the_artwork_tells_its_sea_from_its_void():
    from mapgen.gamedata.frame import GRID_PX
    from satisfactory_mcp.core.gameassets.container import SHEET_PX

    sheet = np.zeros((SHEET_PX, SHEET_PX, 3), np.uint8)
    sheet[:, : SHEET_PX // 4] = (79, 112, 122)  # the open sea's teal
    sheet[:, SHEET_PX // 4 : SHEET_PX // 2] = (170, 160, 145)  # beige ground
    sheet[:, SHEET_PX // 2 : 3 * SHEET_PX // 4] = (76, 76, 76)  # a pit's flat grey
    water, void = artwork_planes(sheet)
    assert water.shape == void.shape == (GRID_PX, GRID_PX)
    quarter = GRID_PX // 4
    assert water[:, : quarter - 1].all() and not water[:, quarter + 1 :].any()
    assert not void[:, : 2 * quarter - 1].any(), "the sea and the ground are not void"
    assert void[:, 2 * quarter + 1 :].all(), "a pit and the black past the edge are"
    assert VOID_ARTWORK_LUMA_MAX < 140


# ----------------------------------------------------------------------- the pits


def test_an_interior_hole_drawn_as_a_pit_is_left_empty():
    nodata = np.zeros((60, 60), bool)
    nodata[10:20, 10:20] = True  # drawn dark: a pit
    nodata[30:40, 30:40] = True  # drawn as ground: a hole in the data
    nodata[:, 55:] = True  # the edge: never a pit, whatever it is drawn as
    void = np.zeros_like(nodata)
    void[10:20, 10:20] = True
    void[12:15, 30:40] = True
    void[:, 55:] = True
    got = pits(nodata, void)
    assert got[10:20, 10:20].all()
    assert not got[30:40, 30:40].any() and not got[:, 55:].any()


def test_a_floor_the_artwork_draws_as_void_is_a_pit_wherever_it_lies():
    nodata = np.zeros((60, 60), bool)
    nodata[:, 55:] = True
    floor = np.zeros_like(nodata)
    floor[10:20, 10:20] = True  # the landscape's floor, drawn as a pit
    floor[30:40, 30:40] = True  # as deep, but drawn as ground
    floor[20:30, 50:55] = True  # a floor at the void past the edge
    void = np.zeros_like(nodata)
    void[10:20, 10:20] = void[:, 50:] = True
    got = pits(nodata, void, floor)
    assert got[10:20, 10:20].all() and got[20:30, 50:55].all()
    assert not got[30:40, 30:40].any() and not got[:, 55:].any()


def test_the_fill_leaves_a_pit_empty_and_fills_the_hole_beside_it():
    n = 80
    rows, cols = np.mgrid[0:n, 0:n]
    land = (rows * 0.05 + cols * 0.03).astype(np.float32)
    prov = np.full((n, n), hf.PROV_LANDSCAPE, np.uint8)
    prov[20:30, 20:30] = hf.PROV_NODATA
    prov[50:58, 50:58] = hf.PROV_NODATA
    height = np.where(prov == hf.PROV_NODATA, hf.NODATA, np.round(land * 10)).astype(np.int16)
    ground = np.where(prov == hf.PROV_LANDSCAPE, land * 10, hf.NODATA).astype(np.float32)
    void = np.zeros((n, n), bool)
    void[20:30, 20:30] = True
    heights, rebuilt, source, meta = fill_field(
        ground_dm=ground,
        height_dm=height,
        prov=prov,
        water_quality=np.zeros((n, n), np.uint8),
        water_dm=np.full((n, n), hf.NODATA, np.int16),
        raster_m=np.zeros((8, 8), np.float32),
        raster_ok=np.ones((8, 8), bool),
        field_origin_cm=(0.0, 0.0),
        spacing_cm=100.0,
        raster_box_cm=(-50.0, n * 100.0 - 50.0, -50.0, n * 100.0 - 50.0),
        nodata=hf.NODATA,
        fill_value=hf.PROV_FILL,
        rock_values=hf.PROV_CLIFF_VALUES,
        void=void,
    )
    assert (heights[20:30, 20:30] == hf.NODATA).all() and (source[20:30, 20:30] == SOURCE_PIT).all()
    assert (source[50:58, 50:58] == SOURCE_HOLE).all()
    assert np.abs(rebuilt[50:58, 50:58] / 10.0 - land[50:58, 50:58]).max() < 0.5
    assert meta["pits"]["holes"] == 1 and meta["pits"]["texels"] == 100


# -------------------------------------------------------------------- the open sea


def test_the_membrane_settles_towards_the_far_value_over_its_scale():
    values = np.zeros((1, 200), np.float64)
    values[0, 0] = 10.0
    known = np.zeros((1, 200), bool)
    known[0, 0] = True
    got = relax(values, known, ~known, 20.0, 50.0)[0]
    assert got[0] == 10.0 and abs(got[-1] - 50.0) < 0.1
    assert np.all(np.diff(got) > 0), "deeper with distance, never a step back"
    assert abs((50.0 - got[20]) / 40.0 - np.exp(-1.0)) < 0.05


def _sea(height, water, grades, art=None):
    """``open_sea`` over a copy of ``height`` as both lattices: ``(sea, heights, ground)``."""
    heights, ground = height.astype(np.float32), height.astype(np.float32)
    art = np.zeros(height.shape, bool) if art is None else art
    sea = open_sea(_field(height, water, grades), (heights, ground), None, art, OCEAN_LEVEL_M)
    return sea, heights, ground


def test_level_only_sea_gets_a_bed_from_the_measured_one_beside_it():
    height, water, grades = _coast()
    sea, heights, ground = _sea(height, water, grades)
    depth = (OCEAN_DM - heights[20]) / hf.DM_PER_M
    assert abs(depth[55] - 8.0) < 0.05, "the measured shelf keeps its own bed"
    assert abs(depth[60] - depth[59]) < 1.0, "no step where the level-only water starts"
    assert np.all(np.diff(depth[60:]) >= -0.05), "it only ever deepens away from the shelf"
    settled = 60 + int(OPEN_SEA_SETTLE_M)
    assert 0.5 * OPEN_SEA_DEPTH_M < depth[settled] < depth[-1] > 0.95 * OPEN_SEA_DEPTH_M
    assert np.array_equal(heights, ground)
    assert (sea.grades[:, 60:] == hf.WATER_MEASURED).all(), "its depth is read off the bed"
    assert (grades[:, 60:] == hf.WATER_LEVEL_ONLY).all(), "the field's own planes are untouched"


def test_the_open_sea_s_bed_rises_to_a_dry_coast():
    height, water, grades = _coast()
    grades[:, 30:60] = hf.WATER_LEVEL_ONLY  # no measured shelf: the sea meets the land
    _open, heights, _ground = _sea(height, water, grades)
    depth = (OCEAN_DM - heights[20]) / hf.DM_PER_M
    assert depth[30] < 2.0 and depth[45] < depth[200] < depth[-1]


def test_no_data_is_sea_where_the_artwork_draws_water_and_void_elsewhere():
    height, water, grades = _coast(160, 160)
    height[:, 120:] = hf.NODATA
    water[:, 120:] = hf.NODATA
    grades[:, 120:] = hf.WATER_DRY
    art = np.zeros(height.shape, bool)
    art[:80, 120:] = True
    sea, heights, _ground = _sea(height, water, grades, art)
    assert (sea.grades[:80, 120:] == hf.WATER_MEASURED).all()
    assert (sea.level[:80, 120:] == OCEAN_DM).all()
    assert (heights[:80, 120:] < OCEAN_DM).all(), "with a bed under it"
    assert (sea.grades[85:, 120:] == hf.WATER_DRY).all() and (heights[85:, 120:] == hf.NODATA).all()
    assert (sea.void[100:, 130:] == 255).all() and (sea.void[:70, :] == 0).all()
    assert 0 < sea.void[100, 119] < 255, "the void's edge is soft"
    assert sea.meta["sea_over_no_data_texels"] == 80 * 40
    assert grades[0, 130] == hf.WATER_DRY, "the field's own planes are not touched"


def test_the_renderer_draws_its_wet_and_measured_planes_from_the_grades_it_is_given():
    height, water, grades = _coast()
    field = _field(height, water, grades)
    rewet = grades.copy()
    rewet[:, 30:35] = hf.WATER_MEASURED
    plane, wet, measured = water_sources(field, None, water, rewet)
    assert plane is water and wet[:, 30:35].all() and measured[:, 30:35].all()
    rivers = SimpleNamespace(water_dm=water, grades=grades)
    _plane, wet, _measured = water_sources(field, rivers, None, rewet)
    assert wet[:, 30:35].all(), "the grades passed win over the rivers' own"


def test_relief_tints_the_open_sea_by_the_depth_over_its_bed():
    height, water, grades = _coast()
    field = _field(height, water, grades)
    palette = RELIEF_PALETTES["relief"][0]["water"]
    flat = water_tint_plane(field, palette)
    sea, heights, _ground = _sea(height, water, grades)
    tinted = water_tint_plane(field, palette, sea.planes, heights)
    assert flat[20, 100] == flat[20, 600], "without the open sea level-only water is one tint"
    assert tinted[20, 100] < tinted[20, 600], "with it, the sea deepens away from the shelf"
    steps = np.abs(np.diff(tinted[20, 50:70].astype(np.int16)))
    assert steps.max() <= 3, "and the shelf's edge is no step"


# ---------------------------------------------------------------- meshes and void


def test_render_only_meshes_never_break_the_water_in_the_ground_and_water_styles():
    z = np.full((1, 5), -20.0, np.float32)
    level = np.array([[OCEAN_LEVEL_M] * 4 + [np.nan]], np.float32)
    mesh_z = np.array([[-17.3, -10.0, -10.0, -17.5, 5.0]], np.float32) * 100
    cls = np.array([[MESH_CORAL, MESH_SHELL, MESH_ROCK, MESH_ROCK, MESH_CORAL]], np.uint8)
    _raised, painted, _kept = composite_meshes(z, mesh_z, cls, level, composite_top)
    _raised, seabed, kept = composite_meshes(z, mesh_z, cls, level, composite_top, seabed=True)
    assert painted[0, :3].all() and painted[0, 4] > 0, "the painted rule is unchanged"
    assert seabed[0, 0] == seabed[0, 1] == 0 == kept[0, 0], "coral and shells under water"
    assert seabed[0, 2] > 0 and seabed[0, 3] == 0, "a rock only where it stands out"
    assert seabed[0, 4] > 0, "coral on dry ground is drawn"


def test_the_void_is_the_page_s_sea_and_softens_its_edge():
    rgb = np.full((1, 3, 3), 200.0, np.float32)
    got = with_void(rgb, np.array([[0.0, 0.5, 1.0]], np.float32))
    assert np.allclose(got[0, 0], 200.0) and np.allclose(got[0, 2], SEA_RGB)
    assert np.allclose(got[0, 1], (200.0 + SEA_RGB) / 2)


def test_a_layer_draws_the_artwork_s_sea_and_the_void_past_the_data():
    """``render_layer`` end to end on a coarse field: no navy where the artwork has sea."""
    n = 64
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / n
    height = np.full((n, n), -200, np.int16)
    height[:, :20] = 300
    height[:, 40:] = hf.NODATA
    water = np.where(height == -200, OCEAN_DM, hf.NODATA).astype(np.int16)
    grades = np.where(height == -200, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    field = _field(height, water, grades, step_cm)
    art = np.zeros((n, n), bool)
    art[: n // 2, 40:] = True
    heights = height.astype(np.float32)
    sea = open_sea(field, (heights, None), None, art, OCEAN_LEVEL_M)
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((n, n), np.uint8))
    biome = {"width": 1, "area": np.zeros((1, 1), np.uint8)}
    old = render_layer("terrain", field, None, biome, borrow, n, False)
    new = render_layer("terrain", field, None, biome, borrow, n, False, heights, sea=sea)
    navy = np.all(np.abs(new.astype(np.float32) - SEA_RGB) <= 1, axis=-1)
    assert np.all(np.abs(old[:, 45:].astype(np.float32) - SEA_RGB) <= 1), "before: all navy"
    assert not navy[4:28, 45:].any(), "the artwork's sea is drawn as sea"
    assert navy[36:, 45:].all(), "and the rest stays the void"
    assert not navy[:, :38].any()
