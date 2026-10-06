"""The collision pack and the RockIndex over it, on a few synthetic boxes beside the layered field.

The field is ``build_layered_field``'s 7x6 m ramp (z = col metres, a cliff texel at row 2, col 3).
Over that cliff texel stand two 2 m rock slabs (tops 12 m and 32 m, the upper one rolled 180
degrees and the lower one mirrored) and an arch (top 52 m). A simple-collision box sits on the
ramp at (5, 1) m, and a flat cave-floor sheet lies at -5 m under the cave fixture's sound volume.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from mapgen.gamedata.meshes import winding_sign
from mapgen.gamedata.rocks.collision_pack import _element_points, rock_pack_arrays
from satisfactory_mcp.domain.planning import siting
from satisfactory_mcp.domain.spatial import caves, rocks
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.caves import HULL, fixture_mask, write_caves
from tests.support.heightfields import build_layered_field

ROCK = "/Game/FactoryGame/World/Environment/Rock/Slab"
ARCH = "/Game/FactoryGame/World/Environment/Rock/ArcStone"
PILE = "/Game/FactoryGame/World/Environment/Rock/Pile"
BUILD = "buildVersion 495413, a test"


def box(lo: tuple[float, ...], hi: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    """A closed box, wound outward."""
    v = np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    faces = [
        (0, 1, 3, 2),
        (4, 6, 7, 5),
        (0, 4, 5, 1),
        (2, 3, 7, 6),
        (0, 2, 6, 4),
        (1, 5, 7, 3),
    ]
    tris = [t for a, b, c, d in faces for t in ((a, b, c), (a, c, d))]
    return v.astype(np.float32), np.array(tris, np.int64)


def pack_arrays() -> tuple[dict, dict]:
    slab, arch, pile = (
        box((-100, -100, 0), (100, 100, 200)),
        box((-50, -50, 0), (50, 50, 200)),
        box((-50, -50, 0), (50, 50, 200)),
    )
    collision = {
        "meshes": {
            ROCK: {
                "verts": slab[0],
                "tris": slab[1],
                "winding": winding_sign(*slab),
                "source": "trimesh",
                "render_lod_triangles": 12,
                "cooked_triangles": 12,
            },
            ARCH: {
                "verts": arch[0],
                "tris": arch[1],
                "winding": winding_sign(*arch),
                "source": "trimesh",
                "render_lod_triangles": 12,
                "cooked_triangles": 12,
            },
            PILE: {"verts": pile[0], "tris": pile[1], "winding": 1.0, "source": "simple"},
        },
        "failures": {},
    }
    placements = np.array(
        [
            # mesh, owner, x, y, z, pitch, yaw, roll, sx, sy, sz
            [0, 0, 300, 200, 1000, 0, 0, 0, -1, 1, 1],
            [0, 0, 300, 200, 3200, 0, 0, 180, 1, 1, 1],
            [1, 0, 300, 200, 5000, 0, 0, 0, 1, 1, 1],
            [2, 0, 500, 100, 600, 0, 0, 0, 1, 1, 1],
        ],
        np.float64,
    )
    sweep = {
        "meshes": [ROCK, ARCH, PILE],
        "owners": ["StaticMeshActor"],
        "placements": placements,
        "foliage": {},
    }
    sheet = np.array(
        [[400, 200, -500], [600, 200, -500], [600, 400, -500], [400, 400, -500]], float
    )
    floors = {"pieces": [(sheet, np.array([[0, 1, 2], [0, 2, 3]]))], "actors": 1, "undecoded": 0}
    return rock_pack_arrays(sweep, collision, floors)


def write_pack(directory: Path, build: str = BUILD) -> None:
    """The pack beside a field, and the field's sidecar pinned to ``BUILD``."""
    meta_path = directory / hf.META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sources"] = {"game": {"game_version_pinned": BUILD}}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    arrays, counts = pack_arrays()
    np.savez_compressed(directory / rocks.DATA_NAME, **arrays)
    meta = {
        "rocks_version": rocks.ROCKS_VERSION,
        "counts": counts,
        "sources": {"game": {"game_version_pinned": build}},
    }
    (directory / rocks.META_NAME).write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def field(tmp_path):
    directory = build_layered_field(tmp_path)
    write_pack(directory)
    write_caves(tmp_path / caves.DIR_NAME, fixture_mask(), [HULL])
    return hf.load_field(directory)


def test_the_pack_keeps_every_kind_apart():
    _arrays, counts = pack_arrays()
    assert counts["instances_by_kind"] == {
        "rock": 2,
        "rock, simple collision": 1,
        "arch": 1,
        "cave floor": 1,
    }
    assert counts["trimesh_equals_render_lod"] == 2
    assert counts["open_shells"] == 1, "the cave sheet has no inside"


def test_a_vertical_line_crosses_every_surface_highest_first(field):
    hits = field.rocks().hits(300.0, 200.0)
    assert hits.z_m == pytest.approx((52.0, 50.0, 32.0, 30.0, 12.0, 10.0))
    assert hits.standing() == pytest.approx([52.0, 32.0, 12.0])
    assert hits.standing(rocks.GROUND_KINDS) == pytest.approx([32.0, 12.0])


def test_without_a_hint_the_ground_is_the_highest_rock_and_stays_ambiguous(field):
    reading = field.z(300.0, 200.0)
    assert reading.z_m == pytest.approx(32.0)
    assert reading.ambiguous and reading.terrain_z_m == pytest.approx(3.0, abs=0.01)
    assert field.z(300.0, 200.0, surface="top").z_m == pytest.approx(52.0)
    surfaces = field.collision(300.0, 200.0, field.surfaces(300.0, 200.0))
    assert surfaces.floors == pytest.approx((12.0,))


def test_a_hint_picks_among_the_hits(field):
    def pick(hint_m: float) -> tuple[str, float]:
        reading = field.z(300.0, 200.0, hint_z_cm=hint_m * 100)
        return reading.surface, round(reading.z_m, 2)

    assert pick(13.0) == ("floor", 12.0), "the lower slab, which no plane holds"
    assert pick(33.0) == ("ground", 32.0)
    assert pick(4.0) == ("terrain", 3.0)
    assert pick(53.0) == ("top", 52.0)


def test_landscape_away_from_rock_keeps_the_fast_path(field, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("no ray on bare landscape")

    monkeypatch.setattr(field, "collision", refuse)
    assert field.z(125.0, 100.0).z_m == pytest.approx(1.25)


def test_a_mesh_with_only_simple_collision_falls_back_to_its_hull(field):
    on_pile = field.z(500.0, 100.0, hint_z_cm=850.0)
    assert (on_pile.surface, on_pile.z_m) == ("top", pytest.approx(8.0))
    assert field.z(500.0, 100.0).z_m == pytest.approx(5.0), "not in the ground set"
    assert field.rocks().hits(500.0, 100.0).kind[0] == rocks.KIND_ROCK_SIMPLE


def test_the_simple_elements_become_closed_outward_hulls():
    from scipy.spatial import ConvexHull

    def f32(v: float) -> bytes:
        return struct.pack("<f", v)

    def d3(*v: float) -> bytes:
        return struct.pack("<3d", *v)

    cube = _element_points(
        "BoxElems",
        {"Center": d3(10, 0, 0), "Rotation": d3(0, 0, 0), "X": f32(4), "Y": f32(6), "Z": f32(8)},
    )
    assert cube.min(0) == pytest.approx([8, -3, -4]) and cube.max(0) == pytest.approx([12, 3, 4])
    capsule = _element_points(
        "SphylElems",
        {"Center": d3(0, 0, 0), "Rotation": d3(0, 0, 0), "Radius": f32(10), "Length": f32(40)},
    )
    assert capsule[:, 2].max() == pytest.approx(30.0) and capsule[:, 2].min() == pytest.approx(
        -30.0
    )
    hull = ConvexHull(capsule)
    assert hull.volume == pytest.approx(np.pi * 100 * 40 + 4 / 3 * np.pi * 1000, rel=0.1)


def test_the_cache_evicts_the_least_recent_tile_and_rebuilds_it(field):
    index = rocks.load_rocks(field.directory)
    index.cache_bytes = 1
    first = index.hits(300.0, 200.0)
    index.hits(-1000.0, 200.0)
    assert index.tiles_evicted == 1 and list(index._tiles) == [(-1, 0)]
    assert index.hits(300.0, 200.0) == first
    assert index.tiles_built == 3


def test_in_a_cave_a_hint_just_above_a_collision_floor_gets_that_floor(field):
    reading = field.z(500.0, 300.0, hint_z_cm=-400.0)
    assert reading.cave == caves.INSIDE and reading.cave_floor and reading.height_known
    assert reading.z_m == pytest.approx(-5.0)
    assert (
        reading.cave_note
        == "in a cave: floor -5.0 m, the rock collision just under the given height"
    )
    assert "ceiling" not in reading.cave_note
    point = siting.terrain_z(field, 5.0, 3.0, hint_m=-4.0)
    assert point["z_m"] == pytest.approx(-5.0) and point["cave_floor"]


def test_in_a_cave_with_no_floor_near_the_hint_the_height_stays_unknown(field):
    reading = field.z(500.0, 300.0, hint_z_cm=-850.0)
    assert reading.cave == caves.INSIDE and not reading.cave_floor and not reading.height_known
    assert reading.cave_note.startswith("in a cave: ground height unknown here")
    assert field.z(500.0, 300.0).cave == caves.BELOW, "no hint never finds a cave floor"


def test_a_pack_from_another_build_is_not_read(tmp_path):
    directory = build_layered_field(tmp_path)
    write_pack(directory, build="buildVersion 1, elsewhere")
    field = hf.load_field(directory)
    assert field.rocks() is None
    assert field.z(300.0, 200.0).z_m == pytest.approx(50.0), "the raster answers as before"
