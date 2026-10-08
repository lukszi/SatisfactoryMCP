"""The light at its edges: the bake's block seams, no data, and the normals beside a hole.

docs/map/light-and-crowns.md section 29, "Edges of the light". Synthetic fixtures throughout.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mapgen.lighting import bake, stage
from mapgen.lighting import horizon as hz
from mapgen.lighting.bake import bake_light
from mapgen.lighting.light_tiles import work_array
from mapgen.lighting.spans import holes
from mapgen.lighting.stage import Surface

SIZE = 512

Baked = tuple[dict[str, np.ndarray], dict[str, bytes]]


def _ridges() -> np.ndarray:
    """Ridges steep enough to cast at the default sun, across every block edge."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    return (300 * np.sin(xx / 7.0) * np.cos(yy / 9.0) + 0.5 * (xx - yy)).astype(np.float32)


def _crowns() -> tuple[np.ndarray, np.ndarray]:
    top = np.full((SIZE, SIZE), np.nan, np.float32)
    top[240:272, 200:320] = 500.0
    return top, np.where(np.isfinite(top), 200, 0).astype(np.uint8)


def _bake(tmp: Path, z: np.ndarray, land: np.ndarray, occluder=None) -> Baked:
    """The bake's terms and every tile it wrote, as bytes."""
    surface = Surface(tmp / "work", SIZE)
    surface.put(0, z, land)
    bake_light(surface, tmp, 1, occluder, progress=False, occluder_layers=["painted"])
    planes = {"terms": np.array(work_array(surface.directory, "terms", np.uint8, "r"))}
    surface.close()
    root = tmp / "light" / "tiles"
    return planes, {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.webp")}


def test_a_bake_in_blocks_writes_the_bytes_of_one_block(tmp_path, monkeypatch):
    """Each block reads past its edge what the next one computes, so no seam is left."""
    z, land = _ridges(), np.ones((SIZE, SIZE), np.float32)
    baked = {}
    for tiles in (1, 2):
        monkeypatch.setattr(bake, "BLOCK_TILES", tiles)
        baked[tiles] = _bake(tmp_path / str(tiles), z, land, _crowns())
    (one_terms, one_tiles), (four_terms, four_tiles) = baked[2], baked[1]
    shade = one_terms["terms"][..., 1]
    assert (shade < 0.5 * shade.max()).mean() > 0.05, "the ridges cast at the default sun"
    assert (one_terms["terms"][..., 2] < shade).any(), "and the crowns do"
    assert four_terms["terms"].tobytes() == one_terms["terms"].tobytes()
    assert four_tiles == one_tiles


def _blocks_at_1_m(tmp: Path, blocks: list[tuple[int, int, int]]) -> np.ndarray:
    """The terms of a 512 px surface at 1 m baked in ``blocks``: at that spacing the sky view
    reaches 10 px, where the sheet's own spacing reaches none."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    z = (12 * np.sin(xx / 9.0) * np.cos(yy / 11.0) + 3 * np.sin(yy / 4.0)).astype(np.float32)
    surface = Surface(tmp / "work", SIZE)
    surface.put(0, z, np.ones((SIZE, SIZE), np.float32))
    surface.close()
    stage.allocate_work_arrays(tmp / "work", SIZE)
    for block in blocks:
        stage.bake_block(stage.BlockJob(str(tmp / "work"), str(tmp / "tiles"), 1, 1.0,
                                        hz.horizon_reach_px(2.0),
                                        int(np.ceil(hz.SKY_RADIUS_M / 2.0)) + 2, True, block))  # fmt: skip
    return np.array(work_array(tmp / "work", "terms", np.uint8, "r"))


def test_the_sky_view_across_a_block_edge_is_the_bytes_of_one_block(tmp_path):
    one = _blocks_at_1_m(tmp_path / "one", [(0, 0, SIZE)])
    half = SIZE // 2
    quarters = [(r, c, half) for r in (0, half) for c in (0, half)]
    four = _blocks_at_1_m(tmp_path / "four", quarters)
    assert np.ptp(one[..., 0]) > 50, "the sky view varies"
    assert four.tobytes() == one.tobytes()


def _hole(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``z`` with no data south-west of the middle, where the default sun stands."""
    z, land = z.copy(), np.ones((SIZE, SIZE), np.float32)
    z[300:, :200] = np.nan
    land[300:, :200] = 0.0
    return z, land


def test_a_hole_only_in_a_block_s_halo_is_opened_as_in_the_block_that_holds_it(
    tmp_path, monkeypatch
):
    """Three of four blocks see the hole only past their edge; unopened, it made their horizons
    NaN, which the atlas read as open sky."""
    z, land = _ridges(), np.ones((SIZE, SIZE), np.float32)
    z[262:, :250], land[262:, :250] = np.nan, 0.0  # within every neighbour's halo of 16 px
    baked = {}
    for tiles in (1, 2):
        monkeypatch.setattr(bake, "BLOCK_TILES", tiles)
        baked[tiles] = _bake(tmp_path / str(tiles), z, land)
    (one_terms, one_tiles), (four_terms, four_tiles) = baked[2], baked[1]
    dry = land > 0
    assert np.array_equal(four_terms["terms"][dry], one_terms["terms"][dry])
    for name in ("1/0_0", "1/1_0", "1/1_1"):  # the blocks whose core holds no hole
        for suffix in (".hz.webp", ".nrm.webp"):
            assert four_tiles[name + suffix] == one_tiles[name + suffix], name + suffix


def test_a_nan_horizon_is_refused_not_stored_as_open_sky():
    with pytest.raises(ValueError, match="NaN"):
        hz.encode_horizon(np.array([[10.0, np.nan]], np.float32))


def test_no_data_casts_nothing_where_the_0_m_plane_cast_a_wall(tmp_path):
    flat = np.full((SIZE, SIZE), -50.0, np.float32)
    z, land = _hole(flat)
    terms, _tiles = _bake(tmp_path / "hole", z, land)
    whole, _ = _bake(tmp_path / "whole", flat, land)
    dry = land > 0
    for k in range(3):
        assert np.array_equal(terms["terms"][..., k][dry], whole["terms"][..., k][dry]), k
    walled, _ = _bake(tmp_path / "walled", np.where(np.isnan(z), 0.0, z), land)
    assert (walled["terms"][..., 1][dry] < whole["terms"][..., 1][dry]).any(), "the old wall"


def test_a_hole_takes_the_light_of_its_nearest_pixel_with_a_height(tmp_path):
    z, land = _hole(_ridges())
    terms, tiles = _bake(tmp_path, z, land)
    assert tiles and all(len(data) for data in tiles.values())
    svf = terms["terms"][..., 0]
    assert svf[300:, :200].min() > 0, "a hole is never the black of a pit"
    found = holes.find_holes(z)
    assert found is not None and found.nearest is not None
    assert not np.isnan(holes.fill_holes(z, found, 0.0)).any()
    assert holes.find_holes(_ridges()) is None


def test_fill_holes_gives_each_hole_its_nearest_value_or_the_empty_one():
    plane = np.array([[1.0, np.nan, np.nan, 4.0]], np.float32)
    found = holes.find_holes(plane)
    assert holes.fill_holes(plane, found, 0.0).tolist() == [[1.0, 1.0, 4.0, 4.0]]
    empty = np.full((2, 2), np.nan, np.float32)
    assert holes.fill_holes(empty, holes.find_holes(empty), 7.0).tolist() == [[7.0] * 2] * 2
    assert holes.opened(plane)[0, 1] == holes.OPEN_M


def _trench_block(tmp: Path, canopy: bool, canopy_m: float = 30.0) -> np.ndarray:
    """One block at 1 m of a trench 40 m deep, under a flat canopy ``canopy_m`` up or bare:
    its terms."""
    n = 512
    z = np.zeros((n, n), np.float32)
    z[200:260] = -40.0
    surface = Surface(tmp / "work", n)
    surface.put(0, z, np.ones((n, n), np.float32))
    surface.close()
    work = tmp / "work"
    top = np.full((n, n), np.nan, np.float32)
    if canopy:
        top[150:310, 100:400] = canopy_m
    np.save(work / "occluder.npy", top)
    np.save(work / "occluder_cover.npy", np.where(np.isfinite(top), 255, 0).astype(np.uint8))
    stage.allocate_work_arrays(work, n)
    job = stage.BlockJob(str(work), str(tmp / "tiles"), 1, 1.0, hz.horizon_reach_px(2.0),
                         int(np.ceil(hz.SKY_RADIUS_M / 2.0)) + 2, True, (0, 0, n))  # fmt: skip
    stage.bake_block(job)
    return np.array(work_array(work, "terms", np.uint8, "r"))


def test_the_canopy_takes_its_own_light_and_not_the_trench_s_beneath_it(tmp_path):
    bare, roofed = (_trench_block(tmp_path / name, name == "roofed") for name in ("bare", "roofed"))
    floor, open_ground = (slice(205, 255), slice(150, 350)), (slice(400, 500), slice(150, 350))
    lit = int(np.median(bare[open_ground][..., 1]))
    assert bare[floor][..., 2].min() < lit - 40 and bare[floor][..., 3].min() < 200
    assert np.all(np.abs(roofed[floor][..., 2].astype(int) - lit) <= 1), "lit as open ground"
    assert roofed[floor][..., 3].min() >= 250, "under its own open sky"
    assert roofed[floor][..., :2].tobytes() == bare[floor][..., :2].tobytes(), "the ground's own"
    away = (slice(400, 500), slice(0, 60))
    assert np.array_equal(roofed[away][..., 3], roofed[away][..., 0]), "no canopy, the ground's"


def test_a_canopy_under_the_surface_is_hidden_and_takes_no_light_of_its_own(tmp_path):
    bare = _trench_block(tmp_path / "bare", False)
    sunk = _trench_block(tmp_path / "sunk", True, canopy_m=-60.0)
    assert np.array_equal(sunk[..., 3], sunk[..., 0])
    floor = (slice(205, 255), slice(150, 350))
    assert sunk[floor][..., 2].tobytes() == bare[floor][..., 2].tobytes()


def test_the_canopy_s_relief_and_blur_are_the_ones_the_crowns_were_drawn_with():
    from mapgen.lighting.spans import canopy
    from mapgen.terrain.crown_stamp import DOME_SIGMA_M

    assert canopy.CANOPY_RELIEF == 0.35, "the painted crowns' dome gain before their sprites"
    assert canopy.CANOPY_SMOOTH_M == DOME_SIGMA_M


def test_the_terms_in_strips_of_rows_are_the_bytes_of_one_strip(tmp_path, monkeypatch):
    whole = _trench_block(tmp_path / "whole", True)
    monkeypatch.setattr(stage, "TERM_ROWS", 96)
    assert _trench_block(tmp_path / "strips", True).tobytes() == whole.tobytes()


def test_relight_rows_lights_the_crowned_style_with_its_own_terms():
    from mapgen.palette.lightparams import shader_light
    from mapgen.render.draw.light import relight_rows

    rgb = np.full((2, 2, 3), 150, np.uint8)
    land = np.full((2, 2), 255, np.uint8)
    terms = np.zeros((2, 2, stage.TERMS), np.uint8)
    terms[..., 0], terms[..., 1] = 100, 40
    terms[..., 2], terms[..., 3] = 127, 255
    painted = relight_rows(rgb, terms, land, shader_light("painted"))
    swapped = terms[..., [3, 2, 1, 0]]
    assert painted.mean() > relight_rows(rgb, swapped, land, shader_light("painted")).mean()
    terrain = relight_rows(rgb, terms, land, shader_light("terrain"))
    assert terrain.tobytes() == relight_rows(rgb, terms[..., [0, 1, 1, 0]], land,
                                             shader_light("terrain")).tobytes()  # fmt: skip


def test_the_titan_trees_join_the_crowns_where_they_stand_higher():
    from mapgen.cache import TitanPlanes
    from mapgen.lighting.occluders import UNDER_TITAN
    from mapgen.render.draw.light import titan_crowns

    n = 64
    z_cm = np.zeros((n // 2, n // 2), np.int32)
    cls = np.zeros((n // 2, n // 2), np.uint8)
    z_cm[4:12, 4:12], cls[4:12, 4:12] = 5000, 1
    top = np.full((n, n), np.nan, np.float32)
    top[10:14, 10:14], top[40:44, 40:44] = 80.0, 20.0
    cover = np.where(np.isfinite(top), 255, 0).astype(np.uint8)
    under = np.where(np.isfinite(top), 100, 0).astype(np.uint8)
    titan_crowns(TitanPlanes(z_cm, cls, 2, 0, 0), top, cover, under)
    assert top[16, 16] == pytest.approx(50.0) and cover[16, 16] == 255, "a Titan crown"
    assert under[16, 16] == UNDER_TITAN, "marked as a Titan tree's, for cells of its own"
    assert top[12, 12] == 80.0 and under[12, 12] == 100, "a higher crown keeps its top"
    assert top[42, 42] == 20.0 and np.isnan(top[60, 60]) and cover[60, 60] == 0


def test_the_titan_trunks_drawn_unlit_stand_flat_for_the_light_to_light():
    import copy
    from types import SimpleNamespace

    from mapgen.palette.painted.trees import titan_over
    from mapgen.palette.styles import PAINTED_PALETTE
    from mapgen.terrain.render_meshes import TITAN_LEAVES, TITAN_TRUNK

    z = np.add.outer(np.zeros(8), np.arange(8) * 400.0).astype(np.float32) + 3000.0
    cls = np.full((8, 8), TITAN_TRUNK, np.uint8)
    leaves = np.array([0.07, 0.1, 0.03], np.float32)
    ground = SimpleNamespace(palette=copy.deepcopy(PAINTED_PALETTE), titan=(z, cls, 2, 0, 0),
                             titan_rgb={TITAN_LEAVES: leaves, TITAN_TRUNK: leaves})  # fmt: skip
    out = np.full((16, 16, 3), 0.3, np.float32)
    scene = {"z_m": np.zeros((16, 16), np.float32), "grid": (None, 0, 16, 0, 16, 0.25),
             "ndl_flat": np.float32(np.sin(np.deg2rad(45)))}  # fmt: skip
    lit = titan_over(out, scene, ground)
    flat = titan_over(out, {**scene, "unlit": True}, ground)
    assert np.ptp(flat[4:12, 4:12], axis=(0, 1)).max() < 1e-6, "one flat colour"
    assert not np.allclose(lit[4:12, 4:12], flat[4:12, 4:12]), "drawn lit, its slope shows"


@pytest.mark.parametrize("spacing", [0.25, 3.0])
def test_normals_beside_a_hole_follow_the_ground(spacing):
    yy, xx = np.mgrid[0:40, 0:40].astype(np.float32)
    plane = (0.3 * xx - 0.2 * yy) * np.float32(spacing)
    want_x, want_y = hz.normals(plane, spacing)
    holed = plane.copy()
    holed[10:20, 15:30] = np.nan
    got_x, got_y = hz.normals(holed, spacing)
    hole = np.isnan(holed[1:-1, 1:-1])
    for got, want in ((got_x, want_x), (got_y, want_y)):
        assert np.allclose(got[~hole], want[~hole], atol=1e-6), "no crease at the rim"
        assert not got[hole].any(), "a hole stands flat"
    far = np.ones(hole.shape, bool)
    far[7:21, 12:31] = False
    assert got_x[far].tobytes() == want_x[far].tobytes(), "away from a hole, the same bits"
