"""Waterfalls and the small-mesh batch: docs/spatial-and-map.md section 35.

Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.gamedata.water import falls as waterfalls
from mapgen.gamedata.water.falls import (
    FALLS_CACHE_DIR_NAME,
    FALLS_CACHE_NAME,
    fall_from_modules,
    load_or_sweep_falls,
)
from mapgen.palette.water import falls as fallpaint
from mapgen.palette.water.falls import FALL_STYLES, draw_falls, prepare_falls
from mapgen.terrain.render_meshes import (
    MESH_ROCK,
    MESH_TERRACE,
    is_render_only_foliage,
    is_render_only_static,
    mesh_class,
)
from satisfactory_mcp.core.gameassets import versions
from satisfactory_mcp.domain.spatial import heightfield as hf


def _matrices(positions, scale=(1.0, 1.0, 1.0)):
    mats = np.zeros((len(positions), 4, 4))
    for i, p in enumerate(positions):
        mats[i, :3, :3] = np.diag(scale)
        mats[i, 3, :3] = p
        mats[i, 3, 3] = 1.0
    return mats


def _fall(x=0.0, y=0.0, z=50.0, half=10.0, top=8.0, landing=0.0, base=10.0):
    """One prepared row: the lip runs along +x, the water falls towards +y."""
    return np.array([[x, y, z, 1.0, 0.0, 0.0, 1.0, half, top, landing, base]])


def _grid(n=161, step=0.25):
    centre = (np.arange(n) - n // 2) * step
    return centre * 100, centre * 100


# ------------------------------------------------------------------ the data input


def test_a_record_reads_the_lip_width_drop_and_splashes_off_the_modules():
    # Five 2 m columns along x, three 10 m modules deep, hung from z = 100 m.
    side = _matrices([(x * 200.0, 0.0, 10000.0 - k * 1000.0) for x in range(5) for k in range(3)])
    top = _matrices([(x * 200.0, 0.0, 10000.0) for x in range(5)], scale=(1.0, 2.0, 1.0))
    splash = _matrices([(400.0, -500.0, 7000.0)], scale=(1.0, 4.0, 3.0))
    axes = np.eye(3)
    record = fall_from_modules((0.0, 0.0, 10000.0), axes, side, top, splash)
    assert record["x"] == pytest.approx(4.0) and record["y"] == pytest.approx(0.0)
    assert record["z"] == pytest.approx(100.0)
    assert record["width_m"] == pytest.approx(10.0), "four gaps of 2 m plus one module"
    assert record["height_m"] == pytest.approx(30.0)
    assert record["top_len_m"] == pytest.approx(16.1)
    assert record["out"] == [pytest.approx(0.0), pytest.approx(-1.0)], "out is the local -Y"
    assert record["splash"] == [[4.0, -5.0, 70.0, 4.0]]
    assert fall_from_modules((0, 0, 0), axes, None, top, splash) is None


def test_the_falls_are_cached_per_build_and_reader(tmp_path):
    calls = []

    def sweep_once():
        calls.append(1)
        return {"actors": [{"x": 1.0, "y": 2.0, "z": 3.0, "width_m": 4.0}, {"not": "a fall"}]}

    falls, meta = load_or_sweep_falls(tmp_path, "502094", sweep_once)
    assert (
        falls == [{"x": 1.0, "y": 2.0, "z": 3.0, "width_m": 4.0}]
        and not meta["waterfalls"]["reused"]
    )
    again, meta = load_or_sweep_falls(tmp_path, "502094", sweep_once)
    assert again == falls and meta["waterfalls"]["reused"] and len(calls) == 1
    load_or_sweep_falls(tmp_path, "999999", sweep_once)
    assert len(calls) == 2, "another build is read again"
    assert (tmp_path / FALLS_CACHE_DIR_NAME / FALLS_CACHE_NAME).is_file()
    assert meta["waterfalls"]["reader_version"] == versions.READER_VERSIONS["waterfalls"]


def test_a_sweep_draws_and_digests_the_falls_in_the_order_the_cache_keeps(tmp_path):
    """Overlapping falls blend in turn, so a run that sweeps draws what a restyle draws."""
    swept = [{"x": float(x), "y": 0.0, "z": 1.0, "width_m": 4.0} for x in (3, -2, 7, 0)]

    def sweep_once():
        return {"actors": swept}

    fresh, meta = load_or_sweep_falls(tmp_path, "502094", sweep_once)
    assert [f["x"] for f in fresh] == [-2.0, 0.0, 3.0, 7.0]
    cached, again = load_or_sweep_falls(tmp_path, "502094", sweep_once)
    assert again["waterfalls"]["reused"] and cached == fresh
    assert again["waterfalls"]["digest"] == meta["waterfalls"]["digest"]


def test_the_reader_only_answers_for_the_waterfall_tool():
    assert waterfalls.read_fall(None, 0, "/Game/X/BP_Something", None) is None


# ------------------------------------------------------------------ the preparation


def _field(ground_m, water_m=None):
    n = ground_m.shape[0]
    water = np.full_like(ground_m, np.nan) if water_m is None else water_m
    return SimpleNamespace(
        x0_cm=-n * 50.0,
        y0_cm=-n * 50.0,
        spacing_cm=100.0,
        width=n,
        height=n,
        height_dm=np.where(np.isnan(ground_m), hf.NODATA, ground_m * 10).astype(np.int16),
        water_raster=lambda: np.where(np.isnan(water), hf.NODATA, water * 10).astype(np.int16),
    )


def _record(z=40.0, height=60.0):
    return {"x": 0.0, "y": 0.0, "z": z, "along": [1.0, 0.0], "out": [0.0, 1.0],
            "width_m": 10.0, "height_m": height, "top_len_m": 8.0, "splash": []}  # fmt: skip


def test_the_drop_is_measured_to_the_ground_below_the_lip():
    ground = np.full((80, 80), 40.0)
    ground[41:, :] = 5.0  # a cliff 35 m high, falling towards +y
    prepared = prepare_falls([_record()], _field(ground))
    assert prepared.shape == (1, 11)
    assert prepared[0, 10] == pytest.approx(5.0), "base is the ground, not the curtain's end"


def test_falls_without_a_drop_or_at_the_sea_or_off_the_data_are_left_out():
    flat = np.full((80, 80), 40.0)
    assert not len(prepare_falls([_record()], _field(flat))), "no drop"
    sea = _record(z=-18.0)
    assert not len(prepare_falls([sea], _field(np.full((80, 80), -30.0)))), "the world's edge"
    nothing = np.full((80, 80), np.nan)
    assert not len(prepare_falls([_record()], _field(nothing))), "no field under the lip"
    buried = np.full((80, 80), 90.0)
    assert not len(prepare_falls([_record()], _field(buried))), "inside a rock or a cave"


# ------------------------------------------------------------------ the colour step


def test_falls_only_ever_raise_a_pixel_and_stay_near_the_lip():
    x_cm, y_cm = _grid(401)
    rng = np.random.default_rng(1)
    rgb = rng.uniform(0, 255, (len(y_cm), len(x_cm), 3))
    surface = np.where(y_cm[:, None] > 0, 10.0, 50.0) + np.zeros((1, len(x_cm)))
    out = draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, surface, 0.25)
    assert (out >= rgb - 1e-9).all(), "raise-only"
    assert (out > rgb + 1).any()
    far = (np.abs(x_cm) > 45 * 100)[None, :] & np.ones((len(y_cm), 1), bool)
    assert np.allclose(out[far], rgb[far]), "nothing beyond the fall's reach"


def test_the_streak_hides_under_an_overhang_and_the_pool_needs_a_drop():
    x_cm, y_cm = _grid()
    rgb = np.full((len(y_cm), len(x_cm), 3), 60.0)
    over = np.full(rgb.shape[:2], 80.0)  # a rock roof 30 m above the lip
    assert np.allclose(draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, over, 0.25), rgb)
    lip_level = np.full(rgb.shape[:2], 50.0)
    shallow = draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, lip_level, 0.25)
    deep = draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, lip_level - 40.0, 0.25)
    assert deep.sum() > shallow.sum(), "the foam pool lies only well below the lip"


def test_a_style_without_falls_draws_none_and_no_data_is_lip_level():
    x_cm, y_cm = _grid(41)
    rgb = np.full((41, 41, 3), 60.0)
    assert FALL_STYLES.get("terrain") is None
    same = draw_falls(rgb.copy(), _fall(), "terrain", x_cm, y_cm, np.zeros((41, 41)), 0.25)
    assert np.array_equal(same, rgb)
    holes = draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, np.full((41, 41), np.nan), 0.25)
    assert np.isfinite(holes).all() and (holes >= rgb).all()


def test_the_streak_fades_out_towards_the_ends_of_its_lip_and_its_far_end():
    """No hard white box: across the lip and at its far end the foam fades over a share of
    the lip's half width and of the spread, not over a pixel."""
    cfg = FALL_STYLES["painted"]
    fall = _fall(half=10.0)[0]
    xs = np.arange(-16.0, 16.01, 0.25)
    near_lip = 47.0  # ground just under the lip: the streak, no pool
    foam, _mist = fallpaint._fall_alpha(fall, cfg, xs, np.array([0.5]),
                                        np.full((1, len(xs)), near_lip), 0.25)  # fmt: skip
    row = foam[0]
    assert row[np.abs(xs) < 2].min() > 0.5 * row.max()
    assert np.abs(np.diff(row)).max() < 0.1, "no step across the streak's sides"
    assert row[np.abs(xs) > 10.0 * (1 + cfg["soft"] / 2)].max() == 0.0
    ys = np.arange(0.0, 12.0, 0.25)
    along, _mist = fallpaint._fall_alpha(fall, cfg, np.array([0.0]), ys,
                                         np.full((len(ys), 1), near_lip), 0.25)  # fmt: skip
    streak = along[:, 0][ys < 8.0]
    assert np.abs(np.diff(streak)).max() < 0.15, "the far end fades too"


def test_the_foam_goes_under_what_hides_it_and_is_asked_only_near_a_fall():
    x_cm, y_cm = _grid()
    rgb = np.full((len(y_cm), len(x_cm), 3), 60.0)
    surface = np.where(y_cm[:, None] > 0, 10.0, 50.0) + np.zeros((1, len(x_cm)))
    asked: list[int] = []

    def canopy():
        asked.append(1)
        return np.ones(rgb.shape[:2])

    hidden = draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, surface, 0.25, canopy)
    assert np.array_equal(hidden, rgb) and asked == [1], "a crown over the fall hides its foam"
    seen = draw_falls(rgb.copy(), _fall(), "painted", x_cm, y_cm, surface, 0.25, lambda: None)
    assert (seen > rgb + 1).any()
    far = _fall(x=500.0)
    draw_falls(rgb.copy(), far, "painted", x_cm, y_cm, surface, 0.25, canopy)
    assert asked == [1], "no fall near: the cover is never worked out"


def test_a_coarse_pixel_softens_the_foam():
    cfg = FALL_STYLES["painted"]
    xs = ys = np.array([0.0, 30.0])
    surface = np.full((2, 2), 10.0)
    fine, _ = fallpaint._fall_alpha(_fall()[0], cfg, xs, ys, surface, 0.25)
    coarse, _ = fallpaint._fall_alpha(_fall()[0], cfg, xs, ys, surface, 30.0)
    assert coarse[0, 0] <= fine[0, 0]


# ------------------------------------------------------------------ the small-mesh batch


def test_hot_spring_terraces_and_two_foliage_rocks_are_render_only():
    terrace = "/Game/FactoryGame/World/Environment/HotSpring/Mesh/Hotspring_Plateau_01"
    smooth = "/Game/FactoryGame/World/Environment/Rock/SmoothRock/Mesh/SmoothRock_03"
    snake = "/Game/FactoryGame/World/Environment/Rock/SnakeStone/Mesh/SnakeStone_01"
    assert is_render_only_static(terrace) and mesh_class(terrace) == MESH_TERRACE
    assert is_render_only_foliage(smooth) and mesh_class(smooth) == MESH_ROCK
    assert is_render_only_foliage(snake) and mesh_class(snake) == MESH_ROCK
    assert not is_render_only_foliage(smooth.replace("_03", "_01")), "only the two named"
    assert versions.READER_VERSIONS["render_meshes"] >= 2
