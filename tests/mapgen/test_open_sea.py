"""The open sea past the measured bed, the void, the pits and the seabed meshes.

docs/spatial-and-map.md section 26. Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.water.channel import VOID_ARTWORK_LUMA_MAX, artwork_planes
from mapgen.palette.relief import water_tint_plane
from mapgen.palette.rivers import water_sources
from mapgen.palette.shore import OCEAN_LEVEL_M, composite_meshes
from mapgen.palette.styles import RELIEF_PALETTES, SEA_RGB, with_void
from mapgen.palette.water import (
    OPEN_SEA_BLEND_M,
    OPEN_SEA_DEPTH_M,
    OPEN_SEA_SETTLE_M,
    OPEN_SEA_TONE_DEPTH_M,
    open_sea,
)
from mapgen.terrain.fill import SOURCE_HOLE, SOURCE_PIT, fill_field, pits, relax
from mapgen.terrain.rasters import MESH_CORAL, MESH_ROCK, MESH_SHELL
from mapgen.tiles.compose import DIRECT_LIFT_KNEE_M, composite_top, render_layer
from satisfactory_mcp.domain.spatial import heightfield as hf

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
    for band, green in enumerate((120, 136, 152), start=1):  # the shallower tones, in rows
        sheet[band * SHEET_PX // 4 :, : SHEET_PX // 4] = (72, green, green + 16)
    water, void = artwork_planes(sheet)
    assert water.shape == void.shape == (GRID_PX, GRID_PX)
    quarter = GRID_PX // 4
    assert water[:, : quarter - 1].all() and not water[:, quarter + 1 :].any()
    assert not void[:, : 2 * quarter - 1].any(), "the sea and the ground are not void"
    assert void[:, 2 * quarter + 1 :].all(), "a pit and the black past the edge are"
    assert VOID_ARTWORK_LUMA_MAX < 140
    tones = [int(water[(band * 2 + 1) * GRID_PX // 8, 10]) for band in range(4)]
    assert tones == [1, 2, 3, 4], "the open sea's teal to the brightest cyan"


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
    assert depth[45] < 12.0 < depth[200] - 10.0, "a shelf narrower than the blend stays a shelf"
    assert abs(depth[60] - depth[59]) < 1.0, "no step where the level-only water starts"
    assert np.all(np.diff(depth[60:]) >= -0.05), "it only ever deepens away from the shelf"
    settled = 60 + int(OPEN_SEA_SETTLE_M)
    assert 0.5 * OPEN_SEA_DEPTH_M < depth[settled] < depth[-1] > 0.95 * OPEN_SEA_DEPTH_M
    assert np.array_equal(heights, ground)
    assert (sea.grades[:, 60:] == hf.WATER_MEASURED).all(), "its depth is read off the bed"
    assert (grades[:, 60:] == hf.WATER_LEVEL_ONLY).all(), "the field's own planes are untouched"


def test_the_bed_is_continuous_in_value_and_slope_across_the_measured_edge():
    height, water, grades = _coast(40, 1200)
    height[:, 30:330] = -170 - 80  # a measured shelf 300 m wide, 8 m deep
    grades[:, 30:330] = hf.WATER_MEASURED
    grades[:, 330:] = hf.WATER_LEVEL_ONLY
    _open, heights, _ground = _sea(height, water, grades)
    depth = (OCEAN_DM - heights[20]) / hf.DM_PER_M
    edge = 330 - int(OPEN_SEA_BLEND_M)
    assert np.allclose(depth[40:edge], 8.0), "past the blend the measured bed is as measured"
    bend = np.abs(np.diff(depth[edge - 50 : 600], 2)).max()
    # The membrane alone leaves the shelf flat and climbs at once, a kink of ~0.12 m/m.
    assert bend < 0.03, f"the slope turns by {bend:.3f} m/m in a metre: a kink"
    assert depth[330] - 8.0 > 0.5 and np.all(np.diff(depth[edge:]) >= -0.01)


def test_the_artwork_s_tones_draw_the_open_sea_s_bed_to_their_depth():
    height, water, grades = _coast(40, 1200)
    grades[:, 30:] = hf.WATER_LEVEL_ONLY
    tones = np.ones(height.shape, np.uint8)
    tones[:, :30] = 0
    _open, plain, _ground = _sea(height, water, grades, tones)
    tones[:, 600:750] = 4  # the brightest cyan, a shoal the data does not have
    _open, heights, _ground = _sea(height, water, grades, tones)
    depth = (OCEAN_DM - heights[20]) / hf.DM_PER_M
    before = (OCEAN_DM - plain[20]) / hf.DM_PER_M
    assert before[675] > 30.0 and abs(depth[675] - OPEN_SEA_TONE_DEPTH_M[-1]) < 1.0
    assert depth[450] > depth[675] + 8.0 < depth[900], "the shoal is the tone's, not more"
    assert np.abs(np.diff(depth[450:900], 2)).max() < 0.05, "pulled, never pinned to a kink"


def test_the_open_sea_s_bed_rises_to_a_dry_coast():
    height, water, grades = _coast()
    grades[:, 30:60] = hf.WATER_LEVEL_ONLY  # no measured shelf: the sea meets the land
    _open, heights, _ground = _sea(height, water, grades)
    depth = (OCEAN_DM - heights[20]) / hf.DM_PER_M
    assert depth[30] < 2.0 and depth[45] < depth[200] < depth[-1]


def test_no_data_is_sea_where_the_artwork_draws_water_and_void_elsewhere():
    height, water, grades = _coast(400, 400)
    height[:, 120:] = hf.NODATA
    water[:, 120:] = hf.NODATA
    grades[:, 120:] = hf.WATER_DRY
    art = np.zeros(height.shape, bool)
    art[:80, 120:] = True
    sea, heights, _ground = _sea(height, water, grades, art)
    assert (sea.grades[:80, 120:] == hf.WATER_MEASURED).all()
    assert (sea.level[:80, 120:] == OCEAN_DM).all()
    assert (heights[:80, 120:] < OCEAN_DM).all(), "with a bed under it"
    deep = (slice(300, None), slice(330, None))
    assert (sea.grades[deep] == hf.WATER_DRY).all() and (heights[deep] == hf.NODATA).all()
    assert (sea.void.cover[deep] == 255).all() and (sea.void.cover[:70, :] == 0).all()
    assert sea.meta["sea_over_no_data_texels"] == 80 * 280
    assert grades[0, 130] == hf.WATER_DRY, "the field's own planes are not touched"


def test_the_open_sea_fades_into_the_void_rather_than_stopping_at_its_edge():
    height, water, grades = _coast(400, 400)
    height[:, 120:] = water[:, 120:] = hf.NODATA
    grades[:, 120:] = hf.WATER_DRY
    art = np.zeros(height.shape, bool)
    art[:80, 120:] = True
    sea, heights, _ground = _sea(height, water, grades, art)
    cover = sea.void.cover[80:300, 300].astype(np.int16)
    assert cover[0] < 10 and cover[-1] == 255 and np.all(np.diff(cover) >= 0)
    assert 60 < cover[37] < 190, "about half the sea still shows ~37 m into the void"
    under = (slice(80, 120), slice(130, 400))
    assert (sea.grades[under] == hf.WATER_MEASURED).all() and (heights[under] < OCEAN_DM).all()
    assert sea.void.falloff[under].min() > 230, "no lit edge where the sea goes on under it"
    assert sea.void.rim[under].max() < 20, "and no rim"
    assert sea.meta["fringe_texels"] > 0


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


def test_dry_ground_the_shore_draws_as_sea_takes_no_rim_beside_the_void():
    height, water, grades = _coast(400, 600)
    height[:, 300:320] = -1000  # dry cliff 100 m under the surface, as the shore draws it
    water[:, 300:320] = hf.NODATA
    grades[:, 300:320] = hf.WATER_DRY
    height[:, 320:] = water[:, 320:] = hf.NODATA
    grades[:, 320:] = hf.WATER_DRY
    grades[:, 30:300] = hf.WATER_MEASURED
    sea, _heights, _ground = _sea(height, water, grades)
    assert sea.void.rim[200, 315:325].max() < 30 and sea.void.cover[200, 322] < 60
    height[:, 100:320] = -1000  # the same ground, far inland from any sea
    water[:, :320] = hf.NODATA
    grades[:, :320] = hf.WATER_DRY
    inland, _heights, _ground = _sea(height, water, grades)
    assert inland.void.rim[200, 315:325].max() > 150 and inland.void.cover[200, 322] > 200


def _strip(rows=200, cols=300, width=8):
    """Level-only sea in the west, a strip of dry ground 164 m down, then no data."""
    height = np.full((rows, cols), OCEAN_DM, np.int16)  # the fill raster holds the surface
    water = np.full((rows, cols), OCEAN_DM, np.int16)
    grades = np.full((rows, cols), hf.WATER_LEVEL_ONLY, np.uint8)
    height[:, 150 : 150 + width], water[:, 150:] = -1640, hf.NODATA
    grades[:, 150:] = hf.WATER_DRY
    height[:, 150 + width :] = hf.NODATA
    return height, water, grades


def test_sunken_ground_between_the_open_sea_and_the_void_is_sea():
    height, water, grades = _strip()
    height[150:, 100:150], water[150:, 100:150] = 50, hf.NODATA  # a headland in the south
    grades[150:, 100:150] = hf.WATER_DRY
    sea, heights, _ground = _sea(height, water, grades)
    strip = (slice(0, 140), slice(150, 158))
    assert (sea.grades[strip] == hf.WATER_MEASURED).all() and (sea.level[strip] == OCEAN_DM).all()
    depth = (OCEAN_DM - heights[strip]) / hf.DM_PER_M
    assert depth.max() < 70.0 and depth.min() > 0.0, "on the open sea's bed, not 147 m down"
    assert sea.meta["strip_texels_joined"] >= 140 * 5
    assert (sea.grades[150:, 100:150] == hf.WATER_DRY).all(), "ground above the sea stays land"
    assert (sea.grades[180:, 150:158] == hf.WATER_DRY).all(), "past the reach it stays land"
    assert (grades[:, 150:158] == hf.WATER_DRY).all(), "the field's own planes are untouched"


def test_a_sunken_band_wider_than_the_strip_stays_land():
    height, water, grades = _strip(width=20)
    sea, _heights, _ground = _sea(height, water, grades)
    assert sea.meta["strip_texels_joined"] == 0
    assert (sea.grades[:, 150:153] == hf.WATER_MEASURED).all(), "the coast rule's 3 m only"
    assert (sea.grades[:, 153:170] == hf.WATER_DRY).all()


def test_sunken_ground_beside_a_pit_stays_land():
    height, water, grades = _strip()
    height[:, 158:] = 50  # land all round, with a pit beside the strip
    height[40:160, 160:200] = hf.NODATA
    sea, _heights, _ground = _sea(height, water, grades)
    assert sea.meta["strip_texels_joined"] == 0
    assert (sea.grades[60:140, 154:158] == hf.WATER_DRY).all()


def _land_with_a_pit(n=600):
    """Land with a hole in it and no data past its east edge: ``(sea, height)``."""
    height = np.full((n, n), 300, np.int16)
    height[:, 450:] = hf.NODATA  # the void past the world's edge
    height[200:400, 150:350] = hf.NODATA  # a pit
    water = np.full((n, n), hf.NODATA, np.int16)
    sea, _heights, _ground = _sea(height, water, np.zeros((n, n), np.uint8))
    return sea, height


def test_a_pit_is_told_from_the_void_past_the_world_s_edge():
    sea, _height = _land_with_a_pit()
    pit = sea.void.pit
    assert (pit[205:395, 155:345] == 255).all() and not pit[:, 455:].any() and not pit[:150].any()
    assert sea.meta["pit_texels"] == 200 * 200
    # A floor the fill emptied is a pit inside the land and the void beside the void.
    height = np.full((300, 300), 300, np.int16)
    height[:, 250:] = hf.NODATA
    heights = height.astype(np.float32)
    heights[100:200, 200:250] = heights[20:60, 20:60] = hf.NODATA
    water = np.full(height.shape, hf.NODATA, np.int16)
    field = _field(height, water, np.zeros(height.shape, np.uint8))
    art = np.zeros(height.shape, bool)
    got = open_sea(field, (heights, None), None, art, OCEAN_LEVEL_M).void.pit
    assert (got[20:60, 20:60] == 255).all() and not got[:, 200:].any()


def test_the_void_falls_off_from_a_lit_rimmed_edge_and_a_pit_is_never_navy():
    sea, _height = _land_with_a_pit()
    void, row = sea.void, 300
    falloff = void.falloff[row, 450:600].astype(np.int16)
    assert falloff[0] < 30 and falloff[-1] >= 250 and np.all(np.diff(falloff) >= 0)
    assert 60 < falloff[37] < 190, "the artwork's falloff: about half way at ~37 m"
    assert void.rim[row, 448:452].max() > 150 and void.rim[row, 470:].max() == 0
    assert void.rim[row, 148:152].max() > 150, "a pit has the rim too"
    assert 0 < void.cover[row, 449] < 255 and 0 < void.cover[row, 149] < 255, "a soft edge"
    planes = [p[row].astype(np.float32) / 255 for p in void]
    drawn = with_void(np.full((600, 3), 120.0, np.float32), *planes)
    assert np.allclose(drawn[599], SEA_RGB, atol=2), "deep in, the void is the page's sea"
    assert drawn[453].mean() > SEA_RGB.mean() + 20, "beside the land its edge is lit"
    centre, edge = drawn[250], drawn[155]
    assert centre.max() < 25 and edge.mean() > centre.mean() + 20, "a pit darkens inwards"
    navy = np.abs(drawn[160:340] - SEA_RGB).max(axis=-1) <= 8
    assert not navy.any(), "and no pixel of it is the page's navy"


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


class _Surface:
    """What ``render_layer`` hands the lighting stage: the drawn heights and land weight."""

    def __init__(self, n):
        self.z, self.land = np.zeros((n, n), np.float32), np.zeros((n, n), np.float32)

    def put(self, row, z_m, land, columns=slice(None)):
        self.z[row : row + len(z_m), columns] = z_m
        self.land[row : row + len(z_m), columns] = land


def test_a_rock_under_the_sea_s_level_is_the_void_s_where_the_sea_fades_into_it():
    """A rock strip from the open sea into the void: drawn under the sea, gone under the void."""
    n, step_cm = 750, 1000.0  # 10 m texels, one per output pixel
    height = np.full((n, n), -1000, np.int16)  # a measured sea 83 m deep
    height[:, :100] = 300
    height[:, 300:] = hf.NODATA
    water = np.where(height == -1000, OCEAN_DM, hf.NODATA).astype(np.int16)
    grades = np.where(height == -1000, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    field = _field(height, water, grades, step_cm)
    field.x0_cm += step_cm / 2  # texel centres on pixel centres
    field.y0_cm += step_cm / 2
    art = np.zeros((n, n), bool)
    art[:300, 300:] = True  # the artwork's sea north of the void
    heights = height.astype(np.float32)
    ground = heights.copy()
    sea = open_sea(field, (heights, ground), None, art, OCEAN_LEVEL_M)
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((n, n), np.uint8))
    biome = {"width": 1, "area": np.zeros((1, 1), np.uint8)}

    def draw(top_m):
        rock = np.zeros((n, n), np.uint8)
        rock[480:530, 200:] = top_m is not None
        direct = (np.full((n, n), (top_m or 0) * 100.0, np.float32), rock, ground, 1)
        surface = _Surface(n)
        rgb = render_layer("terrain", field, None, biome, borrow, n, False, heights,
                           direct=direct, sea=sea, surface=surface)  # fmt: skip
        return rgb[490:520].astype(np.int16), surface.z[490:520], surface.land[490:520]

    (rgb, z, _land), (rgb_deep, z_deep, land_deep), (rgb_high, _z, land_high) = (
        draw(top) for top in (None, -60.0, 5.0)
    )
    cover = sea.void.cover[490:520].astype(np.float32) / 255
    void = heights[490:520] == hf.NODATA
    fade = ~void & (cover > 0.02) & (cover < 0.98)
    assert void[:, 400:].all() and fade[:, 300:].any()
    assert np.abs(z_deep[:, 250] - (-60.0)).max() < 0.2, "under the open sea it is drawn"
    lift = np.maximum(-60.0 - z, 0.0) + DIRECT_LIFT_KNEE_M
    assert np.all((z_deep - z)[fade] <= ((1 - cover) * lift)[fade] + 1e-3), "it fades with it"
    covered = void | (cover >= 0.5)
    assert np.abs(rgb_deep - rgb).max(axis=-1)[covered].max() <= 1, "drawn as if it were not"
    assert np.array_equal(z_deep[void], z[void]) and not land_deep[void].any(), "no land to light"
    assert (land_high[void] > 0.99).all(), "a rock out of the sea still stands in the void"
    assert (np.abs(rgb_high - rgb).max(axis=-1)[void] > 20).all()
