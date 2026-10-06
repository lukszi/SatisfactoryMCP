"""Schema 14's pipes: interned polylines in the world frame, each on a named network.

A spline left in its actor's own frame is still a plausible polyline, far from where the
pipe is, so the points are checked against where the buildings stand.
"""

from __future__ import annotations

import math

from satisfactory_mcp.core.saveio.extract import (
    PIPE_CLASSES,
    Drops,
    _pipes,
)


def _spline(*points) -> list:
    """An ``mSplineData`` the way the parser hands it over: a list of struct entries, each
    ``[values, propertyTypes]``, with a ``Location`` among the values.

    The tangents are in here because the real property has them and ``_pipes`` has to ignore
    them: a reader that took field 0 positionally rather than by name would pass every test
    written against a Location-only stand-in and draw the world's curvature as its geometry.
    """
    out = []
    for p in points:
        values = [
            ["Location", list(p)],
            ["ArriveTangent", [0.0, 50.0, 0.0]],
            ["LeaveTangent", [0.0, 50.0, 0.0]],
        ]
        types = [
            [n, "StructProperty", 1, "Vector", 1, "/Script/CoreUObject", 0, 8] for n, _ in values
        ]
        out.append([values, types])
    return out


def test_the_pipes_key_is_interned_polylines_in_whole_centimetres(projection):
    """The shape, field by field: ``[networkIndex, classIndex, [[x, y, z], ...], actorIndex]``."""
    pipes = projection["pipes"]
    classes = pipes["classes"]
    networks = pipes["networks"]
    rows = pipes["segments"]
    actors = projection["graph"]["actors"]
    assert set(classes) <= set(PIPE_CLASSES) and classes
    assert rows, "the reference world has 503 pipes"
    assert networks, "and 19 pipe networks"

    for row in rows:
        # Positional with a width guard, like the belts next door: schema 15 puts the tangents
        # in a fifth column on the pipes that bend, and a straight pipe still has four.
        assert 4 <= len(row) <= 5, row[:2]
        net, ci, points, actor = row[0], row[1], row[2], row[3]
        assert 0 <= ci < len(classes)
        assert -1 <= net < len(networks)
        assert len(points) >= 2, "a polyline needs two points"
        for p in points:
            assert len(p) == 3 and all(isinstance(c, int) for c in p), p
        # Schema 14. The whole of it: an index INTO an existing list, never past its end.
        assert -1 <= actor < len(actors)


def test_a_pipes_actor_index_names_that_very_pipe_in_the_connection_graph(projection):
    """The join schema 14 exists for, checked against the class it claims to point at.

    The segment says which ``graph["actors"]`` entry owns it. If that index were off by one --
    or interned after the graph's own list was snapshotted, which is the bug the read-only
    lookup in ``extract`` prevents -- it would still be a valid index and would still resolve,
    to the wrong actor. So it is checked by NAME: the entry it points at has to be a pipeline
    of the very class the row's own ``classIndex`` interns.
    """
    pipes = projection["pipes"]
    classes = pipes["classes"]
    actors = projection["graph"]["actors"]
    unclaimed = 0
    for row in pipes["segments"]:
        ci, actor = row[1], row[3]
        if actor < 0:
            unclaimed += 1  # a pipe connected to nothing at all: legal, and none here
            continue
        assert actors[actor].startswith(classes[ci] + "_"), (actors[actor], classes[ci])
    assert unclaimed == 0, "every pipe on this world is plugged into something"
    # And distinct, which a shared or defaulted index would break.
    claimed = [row[3] for row in pipes["segments"]]
    assert len(set(claimed)) == len(claimed)


def test_a_pipe_is_a_fluid_pipe_and_a_hypertube_is_not(projection):
    """``Build_PipeHyper_C`` carries the identical ``mSplineData`` and belongs to no plumbing.

    This is the test that fails if the class filter becomes a substring match on ``Pipe``:
    the reference world has 60 hypertube segments and 215 pipeline supports, both of which
    would then be drawn as pipes, and one of them even has geometry to draw.
    """
    classes = set(projection["pipes"]["classes"])
    assert "Build_PipeHyper_C" not in classes
    assert "Build_PipelineSupport_C" not in classes
    assert projection["building_counts"]["Build_PipeHyper_C"] == 60, "and they are in the world"
    # Every pipeline class the census found IS drawn, so the filter is not merely narrow.
    built = {c for c in projection["building_counts"] if c in PIPE_CLASSES}
    assert classes == built
    assert sum(projection["building_counts"][c] for c in built) == len(
        projection["pipes"]["segments"]
    ), "every pipe in the census is a row"


def test_pipes_are_placed_in_the_world_and_not_in_the_actors_own_frame(projection):
    """The mistake this field is one line away from, and it looks like nothing downstream.

    Every pipe's spline is stored relative to its own actor -- the first point of all 503 is
    exactly ``(0, 0, 0)`` -- so an untranslated network is 503 perfectly well-formed polylines
    piled on the map origin, out at sea. Checking them against the foundations is what catches
    it, the same way the belts above are checked.
    """
    points = [p for row in projection["pipes"]["segments"] for p in row[2]]
    rows = projection["structures"]["instances"]
    for axis in (0, 1, 2):
        lo = min(r[axis + 1] for r in rows) - 20_000
        hi = max(r[axis + 1] for r in rows) + 20_000
        assert lo <= min(p[axis] for p in points), f"pipes run off axis {axis} at the low end"
        assert max(p[axis] for p in points) <= hi, f"pipes run off axis {axis} at the high end"
    assert min(math.hypot(p[0], p[1]) for p in points) > 20_000


def test_every_pipe_belongs_to_a_network_that_names_a_fluid(projection):
    """What a pipe has and a belt does not: the game's own answer to what is inside it."""
    networks = projection["pipes"]["networks"]
    rows = projection["pipes"]["segments"]
    assert all(n["fluid"] for n in networks), "a network with no fluid on this world"
    assert all(isinstance(n["id"], int) for n in networks)
    assert all(r[0] >= 0 for r in rows), "every pipe here is claimed by a network"
    fluids = {networks[r[0]]["fluid"] for r in rows}
    assert len(fluids) > 1 and all(f.startswith("Desc_") for f in fluids)


def test_no_pipe_is_vertical_so_none_needs_a_glyph(projection):
    """The measurement the client's drawing rests on, and the counterpart of the lifts above.

    A belt network needs a ring for its lifts because a lift's top-down polyline is a single
    point. Pipes have no such piece: the tightest of the 503 still spans 11.6 cm horizontally,
    so every one of them is drawable as a line and the layer needs no second glyph.
    """
    spans = [
        math.dist(
            (min(p[0] for p in pts), min(p[1] for p in pts)),
            (max(p[0] for p in pts), max(p[1] for p in pts)),
        )
        for pts in (row[2] for row in projection["pipes"]["segments"])
    ]
    assert min(spans) > 10.0, "a pipe with no horizontal extent would draw as nothing"


def test_pipes_are_translated_by_their_actor_and_not_rotated_by_it():
    """The frame correction itself, isolated: move the actor and every point moves with it.

    All 18,069 pipeline actors across the 66 saves on this disk carry an identity rotation, so
    the correction is a translation and nothing else. This pins that: the same spline read at
    two actor positions differs by exactly the offset, on every axis, with no rounding drift --
    which is what makes the whole-centimetre rounding commutative with the translation.
    """
    spline = _spline((0.0, 0.0, 0.0), (0.0, 100.0, 0.0), (0.0, 100.0, 250.0))
    nets = [(3, "Desc_Water_C", ["Persistent_Level:PersistentLevel.Build_Pipeline_C_1"])]
    base = _pipes(
        [
            (
                "Build_Pipeline_C",
                "Persistent_Level:PersistentLevel.Build_Pipeline_C_1",
                (0, 0, 0),
                spline,
            )
        ],
        nets,
        {"Build_Pipeline_C_1": 0},
        Drops(),
    )
    moved = _pipes(
        [
            (
                "Build_Pipeline_C",
                "Persistent_Level:PersistentLevel.Build_Pipeline_C_1",
                (1000.5, -2000.5, 3000.0),
                spline,
            )
        ],
        nets,
        {"Build_Pipeline_C_1": 0},
        Drops(),
    )
    assert base["segments"][0][2] == [[0, 0, 0], [0, 100, 0], [0, 100, 250]]
    assert [[p[0] - 1000, p[1] + 2000, p[2] - 3000] for p in moved["segments"][0][2]] == base[
        "segments"
    ][0][2]
    # And the network the member list claims it for, resolved by instance name.
    assert base["networks"] == [{"id": 3, "fluid": "Desc_Water_C"}]
    assert base["segments"][0][0] == 0


def test_a_pipe_no_network_claims_is_still_drawn():
    """``-1``, not dropped: an unclaimed pipe is a pipe on real ground whose contents are
    unknown, and a half-built or drained network is exactly how one arises."""
    out = _pipes(
        [
            (
                "Build_PipelineMK2_C",
                "x.Build_PipelineMK2_C_9",
                (0, 0, 0),
                _spline((0, 0, 0), (0, 800, 0)),
            )
        ],
        [(3, "Desc_Water_C", ["x.Build_Pipeline_C_1"])],
        {"Build_PipelineMK2_C_9": 4},
        Drops(),
    )
    assert out["segments"] == [[-1, 0, [[0, 0, 0], [0, 800, 0]], 4]]
    assert out["networks"] == [{"id": 3, "fluid": "Desc_Water_C"}]


def test_a_pipe_of_nothing_recognisable_is_dropped_rather_than_raising():
    """Same reasoning as ``_belts``: this runs on whatever the property decoder produced.

    And the same second claim: each drop is one count in ``warnings``, so a save whose
    splines have stopped decoding says so rather than publishing a world with no pipes.
    """
    empty = {"classes": [], "networks": [], "segments": []}
    drops = Drops()
    assert _pipes([], [], {}, drops) == empty
    assert sum(drops.values()) == 0, "nothing in, nothing dropped"
    assert _pipes([("Build_Pipeline_C", "i", (0, 0, 0), None)], [], {}, drops) == empty
    assert (
        _pipes([("Build_Pipeline_C", "i", None, _spline((0, 0, 0), (1, 1, 1)))], [], {}, drops)
        == empty
    )
    assert _pipes([("Build_Pipeline_C", "i", ("x", 0, 0), _spline((0, 0, 0)))], [], {}, drops) == (
        empty
    )
    # One point is not a route, the same bar the belts set.
    assert (
        _pipes([("Build_Pipeline_C", "i", (0, 0, 0), _spline((0, 0, 0)))], [], {}, drops) == empty
    )
    assert sum(drops.values()) == 4, "four unreadable pipes, four counted"
    # A struct with no Location among its fields costs that point, not the pipe.
    point_drops = Drops()
    assert _pipes(
        [
            (
                "Build_Pipeline_C",
                "i",
                (0, 0, 0),
                [[[["ArriveTangent", [1, 2, 3]]], []], *_spline((0, 0, 0), (0, 400, 0))],
            )
        ],
        [],
        {},
        point_drops,
    )["segments"] == [[-1, 0, [[0, 0, 0], [0, 400, 0]], -1]]
    assert sum(point_drops.values()) == 1, "the point, and only the point"
    # A network whose id is not an integer keeps its fluid and loses its id.
    assert _pipes([], [(None, "Desc_Water_C", [])], {}, Drops())["networks"] == [
        {"id": None, "fluid": "Desc_Water_C"}
    ]
