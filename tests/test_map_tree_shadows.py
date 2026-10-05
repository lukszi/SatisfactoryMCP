"""Tree shadows: crowns from foliage, the occluder raster, and the horizon hook it feeds.

tools/mapgen/README.md, "Horizons and tree shadows". Synthetic fixtures, except the last
test, which reads the installed game.
"""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.gamedata import trees as tr  # noqa: E402
from mapgen.lighting import horizon as hz  # noqa: E402
from mapgen.lighting import occluders as oc  # noqa: E402
from mapgen.terrain import rasters  # noqa: E402

STEP = 0.5
OAK = "/Game/FactoryGame/World/Environment/Foliage/Trees/Oak/SM_Oak_01"


def _matrix(x_cm, y_cm, z_cm, scale=(1.0, 1.0, 1.0), yaw_deg=0.0):
    m = np.eye(4)
    a = np.deg2rad(yaw_deg)
    turn = np.array([[np.cos(a), np.sin(a), 0], [-np.sin(a), np.cos(a), 0], [0, 0, 1]])
    m[:3, :3] = np.diag(scale) @ turn
    m[3, :3] = (x_cm, y_cm, z_cm)
    return m


class _Bounds:
    def __init__(self, table):
        self.table = table

    def of(self, mesh):
        return self.table.get(mesh)


def _one_tree(x=0.0, y=0.0, base=0.0, height=20.0, radius=5.0) -> tr.TreeTable:
    f = lambda v: np.array([v], np.float32)
    return tr.TreeTable(f(x), f(y), f(base), f(height), f(radius), np.zeros(1, np.uint8), ("oak",))


# ---------------------------------------------------------------------- crowns


def test_crown_species_reads_top_and_radius_from_the_bounds():
    bounds = _Bounds({OAK: ((50.0, -20.0, 1000.0), (400.0, 900.0, 1000.0))})

    crowns = tr.crown_species(bounds, [OAK, "/Game/missing"])

    assert list(crowns) == [OAK]
    assert crowns[OAK].top_m == pytest.approx(20.0)
    assert crowns[OAK].radius_m == pytest.approx(6.0)
    assert crowns[OAK].centre_xy_m == (0.5, -0.2)


def test_tree_table_scales_turns_and_filters_each_instance():
    crown = tr.Crown(top_m=20.0, radius_m=4.0, centre_xy_m=(1.0, 0.0))
    mats = np.stack(
        [
            _matrix(1000, 2000, 300, (2.0, 2.0, 1.5), yaw_deg=90.0),
            _matrix(0, 0, 0, (10.0, 10.0, 10.0)),
            _matrix(0, 0, 0, (0.1, 0.1, 1.0)),
        ]
    )

    table = tr.tree_table({OAK: mats, "/Game/unknown": mats}, {OAK: crown})

    assert table.names == (OAK,)
    assert len(table) == 2
    assert table.x_m[0] == pytest.approx(10.0, abs=1e-4)
    assert table.y_m[0] == pytest.approx(22.0, abs=1e-4)
    assert table.base_m[0] == pytest.approx(3.0)
    assert table.height_m[0] == pytest.approx(30.0)
    assert table.radius_m[0] == pytest.approx(8.0)
    assert table.height_m[1] == tr.CROWN_TOP_MAX_M


def test_within_keeps_crowns_that_reach_into_the_box():
    table = tr.TreeTable(
        *(np.array(v, np.float32) for v in ([0, 50, 200], [0, 0, 0], [0] * 3, [10] * 3, [5] * 3)),
        np.zeros(3, np.uint8),
        ("oak",),
    )

    assert table.within(4.0, -1.0, 52.0, 1.0).x_m.tolist() == [0.0, 50.0]


def test_sweep_world_splits_trees_from_the_render_only_foliage(monkeypatch):
    coral = "/Game/FactoryGame/World/Environment/Foliage/Coral/CoralTree/SM_CoralTreeSmall_01"
    shell = "/Game/FactoryGame/World/Environment/UnderWater/SM_Shell"
    seen = {}

    def fake_sweep(store, scripts, classes, meshes, progress, extra_foliage, read_actor=None):
        seen["asked"] = {m: extra_foliage(m) for m in (OAK, coral, shell, "/Game/Rock/X")}
        return {"extra_foliage": {OAK: 1, coral: 2, shell: 3}}

    monkeypatch.setattr(rasters, "sweep_levels", fake_sweep)
    monkeypatch.setattr(rasters, "MeshBounds", lambda *a: None)

    sweep = rasters.sweep_world(None, None, None, None, False)

    assert seen["asked"] == {OAK: True, coral: True, shell: True, "/Game/Rock/X": False}
    assert sweep["trees"] == {OAK: 1, coral: 2}
    assert sweep["extra_foliage"] == {coral: 2, shell: 3}


# ---------------------------------------------------------------------- occluder raster


def test_canopy_top_is_a_dome_on_a_rim_and_nan_beyond_it():
    occ = oc.canopy_top(_one_tree(base=100.0), -10.0, -10.0, STEP, (40, 40))

    assert occ[20, 20] == pytest.approx(120.0, abs=0.2)
    rim = 100.0 + 20.0 * oc.CROWN_RIM
    assert np.nanmin(occ) >= rim - 1e-3
    assert np.isnan(occ[0, 0]) and np.isnan(occ[20, 39])
    assert np.isfinite(occ).sum() == pytest.approx(np.pi * 10.0**2, rel=0.08)


def test_canopy_top_keeps_the_taller_of_two_crowns():
    pair = tr.TreeTable(
        *(np.array(v, np.float32) for v in ([0, 3], [0, 0], [0, 0], [10, 30], [4, 4])),
        np.zeros(2, np.uint8),
        ("oak",),
    )

    occ = oc.canopy_top(pair, -10.0, -10.0, STEP, (40, 40))

    assert occ[20, 26] == pytest.approx(30.0, abs=0.2)
    assert np.nanmax(occ[:, :20]) > 10.0


# ---------------------------------------------------------------------- the horizon hook


def _flat_with_tree(height=20.0, radius=3.0):
    halo = hz.halo_px(STEP)
    n = 2 * halo + 41
    z = np.zeros((n, n), np.float32)
    x0 = -(n * STEP) / 2
    occ = oc.canopy_top(_one_tree(height=height, radius=radius), x0, x0, STEP, (n, n))
    core = (slice(halo, halo + 41), slice(halo, halo + 41))
    return z, occ, core


def test_flat_ground_has_no_horizon_and_no_shadow():
    z, _occ, core = _flat_with_tree()

    out = hz.faded_horizons(z, core, STEP, dirs=8)

    assert out.shape == (41, 41, 8)
    assert float(out.max()) == 0.0


def test_a_tree_shadows_the_ground_away_from_the_sun_and_follows_it():
    z, occ, core = _flat_with_tree()
    four = hz.faded_horizons(z, core, STEP, dirs=4, occluder=occ)
    morning = hz.shadow(four, 90.0, 30.0)
    evening = hz.shadow(four, 270.0, 30.0)

    # the tree stands at the core's centre, column 20; column 4 is 8 m west of it
    assert morning[20, 4] == 1.0 and morning[20, 36] == 0.0
    assert evening[20, 36] == 1.0 and evening[20, 4] == 0.0


def test_occluders_fade_sooner_than_the_ground():
    halo = hz.halo_px(STEP)
    n = 2 * halo + 1
    z = np.zeros((n, n), np.float32)
    far = int(60.0 / STEP)
    wall = np.full((n, n), np.nan, np.float32)
    wall[:, halo + far : halo + far + 10] = 20.0
    ground = np.fmax(z, wall)
    core = (slice(halo, halo + 1), slice(halo, halo + 1))

    as_tree = hz.faded_horizon(z, core, 90.0, STEP, wall)
    as_rock = hz.faded_horizon(ground, core, 90.0, STEP)

    assert 0.0 < float(as_tree[0, 0]) < float(as_rock[0, 0])


def test_a_receiver_on_the_crown_top_is_lit():
    z, occ, core = _flat_with_tree(radius=6.0)
    ground_receiver = z[core]

    on_top = hz.faded_horizon(z, core, 0.0, STEP, occ)
    on_ground = hz.faded_horizon(z, core, 0.0, STEP, occ, receiver=ground_receiver)

    assert on_top[20, 20] == 0.0
    assert on_ground[22, 20] > 60.0


def test_horizon_bytes_round_trip_finer_near_the_ground():
    deg = np.array([0.0, 1.0, 10.0, 45.0, 90.0], np.float32)

    back = hz.decode_horizon(hz.encode_horizon(deg))

    assert np.abs(back - deg).max() < 0.75
    assert abs(float(back[1]) - 1.0) < 0.1


def test_direction_pair_and_soft_shadow():
    assert hz.direction_pair(0.0) == (0, 1, 0.0)
    assert hz.direction_pair(-5.625) == (31, 0, pytest.approx(0.5))
    edge = np.full((1, 1, hz.DIRS), 30.0, np.float32)

    assert float(hz.shadow(edge, 100.0, 30.0)[0, 0]) == pytest.approx(0.5)
    assert float(hz.shadow(edge, 100.0, 40.0)[0, 0]) == 0.0
    assert float(hz.shadow(edge, 100.0, 20.0)[0, 0]) == 1.0


def test_light_is_one_on_open_flat_ground_and_never_below_the_floor():
    el = 62.25
    flat = np.array([np.sin(np.deg2rad(el))], np.float32)

    assert hz.light(flat, np.zeros(1), 1.0, 0.4, el)[0] == pytest.approx(1.0)
    assert hz.light(flat, np.ones(1), 0.5, 0.4, el)[0] == pytest.approx(hz.LIGHT_FLOOR)
    assert 0.35 <= hz.LIGHT_FLOOR <= 0.40


# ---------------------------------------------------------------------- the game itself

GAME = Path("G:/SteamLibrary/steamapps/common/Satisfactory")
PAKS = GAME / "FactoryGame" / "Content" / "Paks"


@pytest.mark.integration
def test_tree_bounds_from_the_installed_game():
    if not (PAKS / "FactoryGame-Windows.utoc").exists():
        pytest.skip(f"needs the installed game at {GAME}")
    from mapgen.gamedata.mesh import MeshBounds
    from satisfactory_mcp.core.gameassets.container import open_container
    from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
    from satisfactory_mcp.core.gameassets.packages import AssetIndex, ScriptObjects

    store = open_container(GAME)
    scripts = ScriptObjects(PAKS, oodle_decompress)
    trees = "/Game/FactoryGame/World/Environment/Foliage/Trees/"
    names = [trees + "Kapok/SM_Kapok_01", trees + "Mangrove/SM_Mangrove_Tall_01"]

    crowns = tr.crown_species(MeshBounds(store, scripts, AssetIndex(store)), names)

    assert crowns[names[0]].top_m == pytest.approx(22.0, abs=1.0)
    assert crowns[names[1]].top_m == pytest.approx(114.3, abs=2.0)
    assert all(tr.is_tree(name) for name in names)
