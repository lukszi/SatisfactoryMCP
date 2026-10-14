"""The sea's physics row: the game's ocean absorption over the bed, the sky term at depth.

docs/map/water.md section 33, "The sea's physics row".
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.colour import linear_to_srgb, oklab, srgb_to_linear
from mapgen.gamedata.water.ocean import OceanAbsorption
from mapgen.palette.painted.optics import base_water, underwater, water_table
from mapgen.palette.painted.sea import sea_row, surface_sky
from mapgen.palette.painted.shapes import UnderwaterScene
from mapgen.palette.styles import PAINTED_PALETTE
from tests.support.map_scenes import CLASS_ID, painted_ground_stub, water_scene

WATER = PAINTED_PALETTE["water"]
#: ``MM_OceanMaster`` on build 502094: ``Ocean Absorption Color`` and ``Ocean Murkyness``.
COLOUR, MURKINESS = (0.520833, 0.1531489, 0.111624), 1.5
GAME = OceanAbsorption(tuple(c * MURKINESS for c in COLOUR), COLOUR, MURKINESS)
SKY_TERM = "#3c597d"
WET_SAND = np.array([0.36, 0.3, 0.22], np.float32)


def _hex(linear: np.ndarray) -> str:
    rgb = np.clip(np.round(linear_to_srgb(np.clip(linear, 0.0, 1.0))), 0, 255).astype(int)
    return "#" + "".join(f"{v:02x}" for v in rgb)


def _drawn(depths: list[float], absorption: OceanAbsorption | None = GAME) -> np.ndarray:
    """The sea over a wet-sand bed at each depth, linear, by the numpy reference."""
    n = len(depths)
    ground = painted_ground_stub(n)
    ground.water = base_water(PAINTED_PALETTE, absorption)
    scene = water_scene(n)
    scene["water"]["depth_m"] = np.asarray(depths, np.float32)[None, :]
    scene["water"]["ocean"] = np.ones((1, n), np.float32)
    bed = np.broadcast_to(WET_SAND, (1, n, 3)).astype(np.float32)
    whole = UnderwaterScene(scene["z_m"], scene["water"], None)
    return underwater(bed, whole, ground, lambda p: p, lambda p: p, np.float32(1.0))[0]


def test_the_sea_absorbs_the_game_s_absorption_times_the_fitted_scale():
    row = sea_row(WATER, GAME)
    np.testing.assert_allclose(row.k, [2.17 * a for a in GAME.per_m], rtol=1e-6)
    assert WATER["absorption_scale"] == 2.17


def test_the_palette_k_is_what_the_scale_gives_on_the_1_0_material():
    """``k_per_m`` is drawn only where the install's material does not read."""
    fallback = sea_row(WATER, None).k
    np.testing.assert_allclose(fallback, sea_row(WATER, GAME).k, atol=1e-4)


def test_body_and_deep_are_the_sky_term_with_the_surface_s_reflection():
    row = sea_row(WATER, GAME)
    np.testing.assert_allclose(row.deep, srgb_to_linear(WATER["sky_term"]))
    np.testing.assert_allclose(row.body + surface_sky(WATER), row.deep, atol=1e-7)
    assert _hex(row.deep) == SKY_TERM


def test_at_no_depth_the_sea_shows_its_bed():
    got = _drawn([0.0])[0]
    want = WET_SAND * np.float32(WATER["bed_wet"]) + surface_sky(WATER)
    np.testing.assert_allclose(got, want, rtol=1e-6)


def test_deep_water_draws_the_sky_term():
    got = _drawn([60.0, 200.0, 1000.0])
    np.testing.assert_allclose(got[-1], srgb_to_linear(WATER["sky_term"]), atol=1e-6)
    assert all(_hex(c) == SKY_TERM for c in got)


def test_each_channel_runs_monotonically_from_the_bed_to_the_sky_term():
    depths = list(np.linspace(0.0, 80.0, 401))
    got = _drawn(depths)
    steps = np.diff(got, axis=0)
    for c in range(3):
        sign = np.sign(got[-1, c] - got[0, c])
        assert (steps[:, c] * sign >= -1e-7).all(), f"channel {c} turns back"


def test_the_lagoons_read_blue_where_the_fitted_row_read_teal():
    """2 to 3 m over wet sand: hue about 228 (OKLab), where the teal row drew about 210."""
    for colour in _drawn([2.0, 2.5, 3.0]):
        lab = oklab(colour)
        hue = float(np.degrees(np.arctan2(lab[2], lab[1])) % 360)
        assert 215.0 < hue < 240.0


def test_the_table_s_ocean_row_is_the_sea_row_and_inland_rows_do_not_move():
    with_game = water_table(PAINTED_PALETTE, GAME)
    without = water_table(PAINTED_PALETTE)
    row = sea_row(WATER, GAME)
    np.testing.assert_array_equal(with_game[CLASS_ID["ocean"], :3], row.k)
    np.testing.assert_array_equal(with_game[CLASS_ID["ocean"], 3:6], row.body)
    for name in ("river", "lake", "lake_blue", "cave", "sulfur", "hot_spring", "translucent"):
        np.testing.assert_array_equal(with_game[CLASS_ID[name]], without[CLASS_ID[name]])


@pytest.mark.integration
def test_the_ocean_absorption_read_from_the_installed_game():
    from mapgen.common import DEFAULT_GAME
    from mapgen.gamedata.install import missing_container, open_game
    from mapgen.gamedata.water.ocean import read_ocean_absorption

    if missing_container(DEFAULT_GAME):
        pytest.skip(f"needs the installed game at {DEFAULT_GAME}")
    game = open_game(DEFAULT_GAME)
    found = read_ocean_absorption(game.store, game.scripts, game.index)
    np.testing.assert_allclose(found.colour, COLOUR, rtol=1e-5)
    assert found.murkiness == MURKINESS
    np.testing.assert_allclose(found.per_m, GAME.per_m, rtol=1e-5)
