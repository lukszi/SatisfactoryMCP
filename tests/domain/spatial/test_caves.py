"""The cave flag: a small fixture mask beside the layered test field, and the generator's half.

The field is ``build_layered_field``'s 7x6 m ramp; ``tests.support.caves`` describes the mask.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM
from mapgen.gamedata.rocks.caves import CAVE_BUFFER_CELLS, CAVE_CELL_CM, CAVE_MASK_PX, build_caves
from satisfactory_mcp.domain.planning import siting
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.domain.spatial.heightfield import cave_masks
from tests.support.caves import HULL, fixture_mask, write_caves
from tests.support.heightfields import build_field, build_layered_field
from tests.support.web import client_over


@pytest.fixture
def field(tmp_path):
    directory = build_layered_field(tmp_path)
    write_caves(tmp_path / cave_masks.DIR_NAME, fixture_mask(), [HULL])
    return hf.load_field(directory)


def test_without_cave_masks_every_answer_is_none(tmp_path):
    field = hf.load_field(build_layered_field(tmp_path))
    assert field.caves() is None
    assert field.height_at(100, 100).cave == cave_masks.NONE
    assert field.height_at(500, 300, hint_z_cm=-500).cave == cave_masks.NONE
    assert field.area(0, 0, 600, 500).cave_pct == 0.0


def test_without_a_hint_a_flagged_cell_reads_below_and_never_inside(field):
    assert field.height_at(100, 100).cave == cave_masks.BELOW
    assert field.texel_reading(100, 100).cave == cave_masks.BELOW
    assert field.height_at(500, 300).cave == cave_masks.BELOW
    assert field.height_at(100, 300).cave == cave_masks.NONE


def test_a_hint_inside_a_sound_volume_reads_inside(field):
    assert field.height_at(500, 300, hint_z_cm=-500).cave == cave_masks.INSIDE
    assert field.height_at(500, 300, hint_z_cm=500).cave == cave_masks.BELOW, (
        "standing on the ramp above"
    )


def test_a_hint_deep_under_every_surface_over_cave_decoration_reads_inside(field):
    # Ground at (100, 100) is 1 m; the rule is more than 3 m under the lowest surface.
    assert field.height_at(100, 100, hint_z_cm=-250).cave == cave_masks.INSIDE
    assert field.height_at(100, 100, hint_z_cm=-150).cave == cave_masks.BELOW


def test_a_deep_hint_over_unflagged_ground_is_not_a_cave(field):
    assert field.height_at(100, 300, hint_z_cm=-5000).cave == cave_masks.NONE


def test_cave_is_kept_apart_from_ambiguous(field):
    rock = field.height_at(300, 200)
    assert rock.ambiguous and rock.cave == cave_masks.NONE
    decorated = field.height_at(100, 100)
    assert decorated.cave == cave_masks.BELOW and not decorated.ambiguous


def test_a_pad_reports_the_share_with_a_cave_under_it(field):
    area = field.area(0, 0, 600, 500)
    # 42 texels: 4 under the decoration cell, 12 under the hull cells (x 400..600, y 200..500).
    assert area.cave_pct == pytest.approx(100 * (4 + 12) / 42, abs=0.1)
    assert area.ambiguous_pct > 0, "the rock still counts on its own"


def test_rewritten_masks_are_picked_up(field, tmp_path, monkeypatch):
    monkeypatch.setattr(hf.store, "SIDECAR_RECHECK_S", 0.0)
    assert field.height_at(100, 300).cave == cave_masks.NONE
    mask = fixture_mask()
    mask[1, 0] = cave_masks.BIT_MARKERS
    write_caves(tmp_path / cave_masks.DIR_NAME, mask, [HULL])
    meta = tmp_path / cave_masks.DIR_NAME / cave_masks.META_NAME
    os.utime(meta, ns=(meta.stat().st_atime_ns, meta.stat().st_mtime_ns + 10**9))
    assert field.height_at(100, 300).cave == cave_masks.BELOW


def test_a_broken_mask_reads_as_no_caves(tmp_path):
    directory = build_layered_field(tmp_path)
    write_caves(tmp_path / cave_masks.DIR_NAME, fixture_mask(), [HULL])
    (tmp_path / cave_masks.DIR_NAME / cave_masks.DATA_NAME).write_bytes(b"not an npz")
    field = hf.load_field(directory)
    assert field.caves() is None
    assert field.height_at(100, 100).cave == cave_masks.NONE


def test_the_note_names_the_surface_and_never_a_ceiling():
    inside = cave_masks.note(cave_masks.INSIDE, 233.4)
    assert inside == "in a cave: ground height unknown here (the surface above is 233 m)"
    assert "surface" in cave_masks.note(cave_masks.BELOW, 233.4)
    assert cave_masks.note(cave_masks.NONE, 233.4) is None
    for line in (
        inside,
        cave_masks.note(cave_masks.BELOW, 1.0),
        cave_masks.note(cave_masks.INSIDE, None),
    ):
        assert "ceiling" not in line


# -- callers -------------------------------------------------------------------------------


def test_a_site_inside_a_cave_gets_no_z(field):
    point = siting.terrain_z(field, 5.0, 3.0, hint_m=-5.0)
    assert point["z_m"] is None and point["cave"] == cave_masks.INSIDE
    assert point["reason"].startswith("in a cave: ground height unknown here")
    pad = siting.terrain_z(field, 5.0, 3.5, width_m=2, depth_m=2, hint_m=-5.0)
    assert pad["z_m"] is None and pad["cave"] == cave_masks.INSIDE


def test_a_site_over_a_cave_keeps_the_surface_and_says_so(field):
    sit = siting.Siting(x_m=3.0, y_m=2.5, z_m=None, yaw_deg=0.0, width_m=6.0, depth_m=5.0)
    settled = siting.settle_z(None, sit, None, "", field)
    assert settled.z_m is not None
    assert settled.terrain["cave_pct"] > 0
    assert "a cave lies under" in settled.terrain_line()
    point = siting.terrain_z(field, 1.0, 1.0)
    assert point["cave"] == cave_masks.BELOW and point["z_m"] == pytest.approx(1.0)


def test_the_inspector_sends_the_cave_line(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from satisfactory_mcp.interfaces.web import terrain as web_terrain

    directory = build_field(tmp_path)
    mask = np.zeros((4, 5), np.uint8)
    mask[0, 0] = cave_masks.BIT_MARKERS
    write_caves(tmp_path / cave_masks.DIR_NAME, mask, [])
    meta = json.loads((tmp_path / cave_masks.DIR_NAME / cave_masks.META_NAME).read_text())
    meta["grid"].update(x0_cm=-300.0, y0_cm=-200.0)
    (tmp_path / cave_masks.DIR_NAME / cave_masks.META_NAME).write_text(json.dumps(meta))
    field = hf.load_field(directory)
    monkeypatch.setattr(web_terrain, "field", lambda: field)
    with client_over(None, None) as client:
        under = client.get("/api/inspect", params={"x_m": -3.0, "y_m": -2.0}).json()
        clear = client.get("/api/inspect", params={"x_m": 2.0, "y_m": -2.0}).json()
    assert under["elevation"]["terrain_cave"] == cave_masks.BELOW
    assert under["elevation"]["terrain_m"] == 12.3, "below keeps the surface"
    assert "cave lies under" in under["elevation"]["terrain_cave_note"]
    assert clear["elevation"]["terrain_cave"] == cave_masks.NONE
    assert clear["elevation"]["terrain_cave_note"] is None


# -- the generator ---------------------------------------------------------------------------


def test_the_generator_builds_a_mask_the_reader_answers_from(tmp_path):
    """Decoration under the ground and a sound-volume box, through ``build_caves`` and back."""
    corners = np.array(
        [[x, y, z] for x in (0.0, 4000.0) for y in (0.0, 4000.0) for z in (-6000.0, -1000.0)]
    )
    centre = ORIGIN_X_CM + 380.5 * CAVE_CELL_CM
    markers = np.array([[centre, centre, 0.0], [20000.0, 20000.0, 900.0]])
    found = {"hulls": [corners], "volumes": 1, "volumes_without_hull": 0, "markers": markers}
    ground_dm = np.broadcast_to(np.int16(100), (GRID_PX, GRID_PX))
    arrays, counts = build_caves(found, ground_dm)
    assert counts["markers_under_ground"] == 1, "a marker 1 m under the ground is not a cave"
    assert counts["hulls"] == 1

    directory = tmp_path / cave_masks.DIR_NAME
    directory.mkdir()
    np.savez_compressed(directory / cave_masks.DATA_NAME, **arrays)
    grid = {
        "width": CAVE_MASK_PX,
        "height": CAVE_MASK_PX,
        "cell_cm": CAVE_CELL_CM,
        "x0_cm": ORIGIN_X_CM,
        "y0_cm": ORIGIN_Y_CM,
    }
    (directory / cave_masks.META_NAME).write_text(json.dumps({"grid": grid}))
    loaded = cave_masks.load_caves(directory)
    assert loaded.classify(2000, 2000, -3000) == cave_masks.INSIDE
    assert loaded.classify(2000, 2000, 1000) == cave_masks.BELOW
    assert loaded.classify(centre, centre) == cave_masks.BELOW
    buffer_cm = CAVE_BUFFER_CELLS * CAVE_CELL_CM
    assert loaded.classify(centre + buffer_cm, centre) == cave_masks.BELOW, "the buffer"
    assert loaded.classify(centre + buffer_cm + CAVE_CELL_CM, centre) == cave_masks.NONE
    assert loaded.classify(20000, 20000) == cave_masks.NONE
