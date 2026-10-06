"""The layered field's surfaces: ground, bare terrain and top, read bilinearly or by hint.

Every test reads ``build_layered_field``'s 7x6 m ramp with one rock texel standing on it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.heightfields import (
    FAKE_SPACING,
    FAKE_X0,
    FAKE_Y0,
    build_field,
    build_layered_field,
    terrain_raw,
)


def test_a_bilinear_read_on_a_ramp_lands_between_the_vertices(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    reading = field.height_at(125.0, 100.0)
    assert reading.z_m == pytest.approx(1.25)
    assert reading.surface == "ground"
    assert field.height_at(125.0, 100.0, bilinear=False).z_m == pytest.approx(1.0)
    assert field.texel_reading(125.0, 100.0).z_m == pytest.approx(1.0)
    assert field.height_at(125.0, 100.0, surface="terrain").z_m == pytest.approx(1.25, abs=0.01)


def test_a_bilinear_read_beside_no_data_uses_the_nearest_valid_vertex(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    # Between the hole at x 600 and its western neighbour at x 500.
    assert field.height_at(560.0, 500.0, surface="terrain").z_m == pytest.approx(5.0, abs=0.01)
    assert field.height_at(540.0, 500.0, surface="terrain").z_m == pytest.approx(5.0, abs=0.01)
    assert field.height_at(600.0, 500.0, surface="terrain") is None
    assert field.height_at(0.0, 0.0, surface="terrain") is None, "west of the terrain frame"
    assert field.height_at(-500.0, 0.0) is None, "off the grid"


def test_a_rock_texel_is_ambiguous_and_carries_the_terrain_under_it(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    rock = field.height_at(300.0, 200.0)
    assert rock.z_m == pytest.approx(50.0)
    assert rock.terrain_z_m == pytest.approx(3.0, abs=0.01)
    assert rock.ambiguous
    assert not field.height_at(100.0, 100.0).ambiguous
    surfaces = field.surfaces(300.0, 200.0)
    assert (surfaces.ground_m, surfaces.top_m) == (50.0, 60.0)


def test_a_hint_picks_the_surface_at_or_below_it(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    on_floor = field.height_at(300.0, 200.0, hint_z_cm=400.0)
    assert (on_floor.surface, on_floor.provenance) == ("terrain", hf.PROV_LANDSCAPE)
    assert on_floor.z_m == pytest.approx(3.0, abs=0.01)
    on_rock = field.height_at(300.0, 200.0, hint_z_cm=5100.0)
    assert (on_rock.surface, on_rock.z_m) == ("ground", 50.0)
    # 58.5 m is within the 2 m slack below the arch at 60 m, so the arch is the floor.
    assert field.height_at(300.0, 200.0, hint_z_cm=5850.0).surface == "top"
    # Above everything: the highest surface below the hint, not the nearest roof.
    assert field.height_at(300.0, 200.0, hint_z_cm=9000.0).surface == "top"


def test_a_window_reads_the_surface_it_is_asked_for(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    ground = field.area(200.0, 100.0, 400.0, 300.0)
    terrain = field.area(200.0, 100.0, 400.0, 300.0, surface="terrain")
    assert ground.z_max_m == 50.0 and terrain.z_max_m == pytest.approx(4.0, abs=0.05)
    assert ground.ambiguous_pct == pytest.approx(100.0 / 9, abs=0.1)
    assert terrain.provenance_pct == {hf.PROV_LANDSCAPE: 100.0}
    edge = field.area(0.0, 0.0, 100.0, 0.0, surface="terrain")
    assert edge.nodata_pct == 50.0, "column 0 lies west of the terrain frame"
    with pytest.raises(ValueError):
        hf.load_field(build_field(tmp_path / "old")).area(0, 0, 100, 100, surface="terrain")


def test_without_a_terrain_plane_every_cliff_texel_is_ambiguous(tmp_path):
    field = hf.load_field(build_field(tmp_path))
    assert field.height_at(FAKE_X0, FAKE_Y0 + FAKE_SPACING).ambiguous
    assert not field.height_at(FAKE_X0, FAKE_Y0).ambiguous
    assert field.height_at(FAKE_X0, FAKE_Y0).terrain_z_m is None


def test_a_cliff_edge_reads_the_nearest_vertex_rather_than_a_blend(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    assert field.height_at(240.0, 200.0).z_m == pytest.approx(2.0)
    assert field.height_at(260.0, 200.0).z_m == pytest.approx(50.0)
    assert field.height_at(225.0, 100.0).z_m == pytest.approx(2.25), "a gentle slope still blends"


def test_a_landscape_reading_takes_the_terrain_planes_finer_value(tmp_path):
    directory = build_layered_field(tmp_path)
    terrain = terrain_raw(np.tile(np.arange(1, 7, dtype=float), (6, 1)))
    terrain[0, 0] = terrain_raw(np.array([1.04]))[0]
    (directory / hf.TERRAIN_NAME).write_bytes(hf.encode_u16(terrain))
    field = hf.load_field(directory)
    assert field.height_at(100.0, 0.0).z_m == pytest.approx(1.04, abs=0.008)
    assert field.texel_reading(100.0, 0.0).z_m == pytest.approx(1.0)


def _twisted_field(tmp_path: Path) -> hf.Field:
    """The layered field with terrain quad (1,1)-(2,2) twisted: a = d = 0 m, b = c = 1 m."""
    directory = build_layered_field(tmp_path)
    terrain = terrain_raw(np.tile(np.arange(1, 7, dtype=float), (6, 1)))
    terrain[1, 1] = terrain[2, 2] = terrain_raw(np.array([0.0]))[0]
    terrain[1, 2] = terrain[2, 1] = terrain_raw(np.array([1.0]))[0]
    terrain[4, 4] = terrain_raw(np.array([40.0]))[0]
    (directory / hf.TERRAIN_NAME).write_bytes(hf.encode_u16(terrain))
    return hf.load_field(directory)


def test_terrain_reads_the_engines_triangles_split_on_the_a_d_diagonal(tmp_path):
    field = _twisted_field(tmp_path)
    # Terrain col 1 is x 200 (the frame starts at x 100); row 1 is y 100.
    centre = field.height_at(250.0, 150.0, surface="terrain").z_m
    assert centre == pytest.approx(0.0, abs=0.01), "bilinear says 0.5, the other diagonal 1"
    upper = field.height_at(275.0, 125.0, surface="terrain").z_m
    assert upper == pytest.approx(0.5, abs=0.01), "triangle a-b-d; bilinear says 0.625"
    lower = field.height_at(225.0, 175.0, surface="terrain").z_m
    assert lower == pytest.approx(0.5, abs=0.01), "triangle a-c-d"


def test_terrain_follows_a_steep_quad_that_ground_snaps_to_a_vertex(tmp_path):
    field = _twisted_field(tmp_path)
    # Terrain vertex (4,4) stands 40 m up, x 500 y 400; a quarter of the way to (3,3).
    steep = field.height_at(475.0, 375.0, surface="terrain").z_m
    assert steep == pytest.approx(0.75 * 40.0 + 0.25 * 4.0, abs=0.01)
