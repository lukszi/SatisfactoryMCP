"""Schema 24: hypertubes read the pipe way, their entrances as placements, and the map markers."""

from __future__ import annotations

import math

from satisfactory_mcp.core.saveio.extract import markers, routes
from satisfactory_mcp.core.saveio.extract.census import Drops
from satisfactory_mcp.core.saveio.extract.interning import Interner


def _spline(*points) -> list:
    """An ``mSplineData`` as the parser hands it over: ``[values, propertyTypes]`` per point."""
    out = []
    for p in points:
        values = [
            ["Location", list(p)],
            ["ArriveTangent", [0.0, 0.0, 0.0]],
            ["LeaveTangent", [0.0, 0.0, 0.0]],
        ]
        types = [
            [n, "StructProperty", 1, "Vector", 1, "/Script/CoreUObject", 0, 8] for n, _ in values
        ]
        out.append([values, types])
    return out


def test_a_hypertube_is_translated_like_a_pipe_and_claims_no_network():
    actors = Interner()
    actors.intern("Build_PipeHyper_C_1")
    drops = Drops()
    block = routes.hypertubes(
        [
            (
                "Build_PipeHyper_C",
                "Persistent_Level:PersistentLevel.Build_PipeHyper_C_1",
                (1000.0, 2000.0, 300.0),
                _spline((0, 0, 0), (500.4, 0, -20)),
            ),
            ("Build_PipeHyper_C", "x.Build_PipeHyper_C_2", None, _spline((0, 0, 0), (1, 1, 1))),
        ],
        actors,
        drops,
    )
    assert block["classes"] == ["Build_PipeHyper_C"]
    assert block["segments"] == [[-1, 0, [[1000, 2000, 300], [1500, 2000, 280]], 0]]
    assert drops == {"hypertube(s) dropped: the actor position would not read": 1}


def test_the_reference_world_has_its_hypertubes_and_entrances(projection):
    tubes = projection["hypertubes"]
    actors = projection["graph"]["actors"]
    assert tubes["classes"] == ["Build_PipeHyper_C"]
    assert len(tubes["segments"]) == 60
    for row in tubes["segments"]:
        assert row[0] == -1 and len(row[2]) >= 2
        assert actors[row[3]].startswith("Build_PipeHyper_C_"), "the actor index names the tube"
    entrances = projection["hypertube_entrances"]
    assert len(entrances) == 8
    assert {e["cls"] for e in entrances} == {"Build_PipeHyperStart_C"}
    # Every entrance has a tube end within a few metres: the tubes are in the world frame.
    ends = [row[2][k] for row in tubes["segments"] for k in (0, -1)]
    for entrance in entrances:
        nearest = min(math.dist(entrance["pos"][:2], end[:2]) for end in ends)
        assert nearest < 1500, entrance["instance"]


def _marker(name, location, colour, icon=350, kind="RT_MapMarker") -> list:
    values = [
        ["Location", [[["X", location[0]], ["Y", location[1]], ["Z", location[2]]], []]],
        ["Name", name],
        ["CategoryName", ""],
        ["MapMarkerType", ["ERepresentationType", "ERepresentationType::" + kind]],
        ["IconID", icon],
        ["Color", list(colour)],
        ["CompassViewDistance", ["ECompassViewDistance", "ECompassViewDistance::CVD_Near"]],
    ]
    return [values, [[n, "StructProperty"] for n, _ in values]]


def test_map_markers_read_name_place_colour_and_icon():
    drops = Drops()
    out = markers.map_markers(
        [
            _marker("ore", (-13323.787, -105493.234, 10741.567), (0.5, 0.25, 0.125, 1.0), 661),
            [[["Name", "nowhere"]], []],
        ],
        drops,
    )
    assert out == [
        {
            "name": "ore",
            "pos": [-13323.8, -105493.2, 10741.6],
            "colour": [0.5, 0.25, 0.125, 1.0],
            "icon_id": 661,
            "type": "RT_MapMarker",
            "category": "",
            "compass": "CVD_Near",
        }
    ]
    assert drops == {"map marker(s) dropped: the location would not read": 1}
    assert markers.map_markers(None, drops) == []


def test_the_reference_world_has_its_six_map_markers(projection):
    marks = projection["map_markers"]
    assert len(marks) == 6
    assert {m["type"] for m in marks} == {"RT_MapMarker"}
    assert "caterium" in {m["name"] for m in marks}
    assert all(m["colour"] and len(m["colour"]) == 4 for m in marks)
