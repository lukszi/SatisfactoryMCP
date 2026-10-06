"""``/api/belts`` and ``/api/pipes``: the two routed networks, and the curve they share.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``,
or by building its own app around a hand-written projection -- so nothing in this file
spawns the sidecar, reads a ``.sav`` or needs the save directory to exist.

One file for both, mirroring ``routers/routes_layer.py`` and for the same reason: the
curvature block at the bottom drives ``_curve_m``, which belts and pipes share and nothing
else calls, and it exercises the belts and the pipes through one loop.
"""

from __future__ import annotations

import math

import pytest

from tests.support.web import client_over

fastapi = pytest.importorskip("fastapi")


from satisfactory_mcp.domain.world.state import WorldState


def test_belts_are_the_network_as_it_was_actually_routed(client, state):
    """Every conveyor piece, un-interned, in metres, in travel order."""
    body = client.get("/api/belts").json()
    raw = state.projection["belts"]
    assert body["count"] == len(raw["segments"]) == len(body["belts"])
    assert body["count"] > 3000, "the reference world is a 320-hour base"
    assert 0 < body["chains"] < body["count"], "pieces group into fewer chains than pieces"

    row = body["belts"][0]
    assert set(row) == {"chain", "cls", "name", "lift", "items_per_min", "points_m", "curve_m"}
    # The class legend is resolved here, or the page would have to carry it.
    assert row["cls"] == raw["classes"][raw["segments"][0][1]]
    assert {r["cls"] for r in body["belts"]} <= set(raw["classes"])
    assert row["name"] == state.game.buildings[row["cls"]].name
    assert not row["name"].startswith("Build_")

    # Metres, like every other coordinate on this surface, and in the projection's order.
    assert len(row["points_m"]) == len(raw["segments"][0][2])
    for out, cm in zip(row["points_m"], raw["segments"][0][2], strict=True):
        assert out == [pytest.approx(round(v / 100.0, 1)) for v in cm]
    for r in body["belts"]:
        assert r["points_m"], "a piece with no geometry is not a piece"
        for x_m, y_m, _z_m in r["points_m"]:
            assert abs(x_m) < 5000 and abs(y_m) < 5000


def test_a_lift_is_told_apart_by_its_native_class_not_by_its_name(client):
    """The one structural distinction the map draws, and where it comes from.

    A lift's top-down polyline is a single point, so the map has to know which pieces need
    a glyph instead of a line. That is asserted here twice over: the flag agrees with the
    docs dump's own native class, and the geometry agrees with the flag.
    """
    body = client.get("/api/belts").json()
    lifts = [r for r in body["belts"] if r["lift"]]
    belts = [r for r in body["belts"] if r["lift"] is False]
    assert lifts and belts, "the reference world has both"
    assert all("Lift" in r["cls"] for r in lifts)
    assert not any("Lift" in r["cls"] for r in belts)

    # Zero horizontal extent, measured: this is why a lift cannot be drawn as a line.
    for r in lifts:
        first, last = r["points_m"][0], r["points_m"][-1]
        assert (first[0], first[1]) == (last[0], last[1])
    assert any(r["points_m"][0][2] != r["points_m"][-1][2] for r in lifts), "lifts rise"
    assert any(r["points_m"][0][:2] != r["points_m"][-1][:2] for r in belts), "belts run"

    # The tier's own rate, rather than a "Mk3" the page would have to parse out of a name.
    rates = {r["items_per_min"] for r in body["belts"]}
    assert rates <= {60.0, 120.0, 270.0, 480.0, 780.0}


def test_a_world_with_no_belts_answers_with_an_empty_network(game):
    """Asserted through the three shapes the projection has carried, exactly as the floor
    plan next door is: a young save has laid no belt, and that is not an error."""
    for projection in ({}, {"belts": {}}, {"belts": {"classes": [], "segments": []}}):
        with client_over(WorldState(projection=projection, game=game), game) as c:
            assert c.get("/api/belts").json() == {
                "belts": [],
                "count": 0,
                "chains": 0,
                "attachments": [],
                "attachment_count": 0,
            }


def test_a_malformed_belt_segment_costs_one_piece_not_the_network(game):
    """Raw projection data, read guarded field by field -- the structures rule, again."""
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                [0, 0, [[100, 200, 300], [400, 500, 600]]],
                [1, 0],  # short: no points
                ["chain", 0, [[100, 200, 300]]],  # unparseable chain index
                [2, 9, [[700, 800, 900], [1, 2, 3]]],  # class index off the end
                [3, 0, [[100, 200], "not a point", [100, 200, 300]]],  # one usable point
                [4, 0, []],  # no geometry at all
                "not a segment",
            ],
        }
    }
    with client_over(
        lambda save=None, world=None: WorldState(projection=projection, game=game), game
    ) as c:
        body = c.get("/api/belts").json()
    assert body["count"] == 3
    assert body["belts"][0] == {
        "chain": 0,
        "cls": "Build_ConveyorBeltMk1_C",
        "name": "Conveyor Belt Mk.1",
        "lift": False,
        "items_per_min": 60.0,
        "points_m": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        # Three columns wide, so it predates the curve column or has no bend: either way the
        # client draws the polyline it always drew.
        "curve_m": None,
    }
    # A piece whose class the legend cannot name is still a piece on real ground: it keeps
    # its route and loses the two things the class would have told us. `lift` is null, not
    # false -- "not a lift" would be a guess, and the two are drawn differently.
    unnamed = body["belts"][1]
    assert (unnamed["cls"], unnamed["lift"], unnamed["items_per_min"]) == (None, None, None)
    assert unnamed["points_m"] == [[7.0, 8.0, 9.0], [0.0, 0.0, 0.0]]
    assert body["belts"][2]["points_m"] == [[1.0, 2.0, 3.0]]
    assert body["chains"] == 3


def test_the_splitters_and_mergers_ride_with_the_belts_and_with_nothing_else(client, state):
    """A belt run passes THROUGH a splitter, so the belts payload carries them.

    Two claims, and the second is the one that keeps the map honest. First: every splitter
    and merger the projection holds comes back as a placement -- where it stands, which way
    it faces, what it is -- because that is all a splitter is. Second: it comes back HERE and
    nowhere else, so the belts layer can draw them without any risk of the machines layer
    drawing the same square underneath.
    """
    body = client.get("/api/belts").json()
    raw = state.projection["attachments"]
    assert body["attachment_count"] == len(raw) == len(body["attachments"])
    assert body["attachment_count"] > 800, "the reference world splits and merges a great deal"

    row = body["attachments"][0]
    assert set(row) == {"instance_leaf", "cls", "name", "x_m", "y_m", "z_m", "yaw", "w_m", "l_m"}
    assert "." not in row["instance_leaf"]
    # Metres, like every other coordinate on this surface. A regression here is silent.
    assert row["x_m"] == pytest.approx(round(raw[0]["pos"][0] / 100.0, 1))
    assert row["y_m"] == pytest.approx(round(raw[0]["pos"][1] / 100.0, 1))
    for r in body["attachments"]:
        assert abs(r["x_m"]) < 5000 and abs(r["y_m"]) < 5000
        assert r["name"] and not r["name"].startswith("Build_")
        # The dump's own soft clearance box, now that soft-only buildables read it:
        # every splitter and merger is the same 4x4 m piece, and a measured square
        # beats the client's guessed one. Asserted as the value rather than a range
        # because all four classes genuinely share one box.
        assert (r["w_m"], r["l_m"]) == (4.0, 4.0)
        assert r["yaw"] is None or -180 <= r["yaw"] <= 180

    kinds = {r["name"] for r in body["attachments"]}
    assert kinds == {"Conveyor Splitter", "Conveyor Merger", "Smart Splitter"}

    # The no-double-draw claim, checked rather than asserted in a comment.
    machines = client.get("/api/machines").json()
    drawn = {r["instance_leaf"] for kind in machines for r in machines[kind]}
    assert not drawn & {r["instance_leaf"] for r in body["attachments"]}


def test_a_world_that_split_no_belt_answers_with_no_attachments(game):
    """A young save has laid no belt and split nothing, and neither is an error."""
    for projection in ({}, {"attachments": []}, {"attachments": ["not a record"]}):
        with client_over(WorldState(projection=projection, game=game), game) as c:
            body = c.get("/api/belts").json()
        assert (body["attachments"], body["attachment_count"]) == ([], 0)


def test_pipes_are_the_plumbing_as_it_was_actually_routed(client, state):
    """Every fluid pipe, un-interned, in metres, in the order the file stores it."""
    body = client.get("/api/pipes").json()
    raw = state.projection["pipes"]
    assert body["count"] == len(raw["segments"]) == len(body["pipes"])
    assert body["count"] > 400, "the reference world plumbs oil, water and fuel"
    assert 0 < body["networks"] < body["count"], "pipes group into fewer networks than pipes"

    row = body["pipes"][0]
    assert set(row) == {
        "row",
        "network",
        "fluid",
        "fluid_name",
        "cls",
        "name",
        "flow_m3_min",
        "points_m",
        "curve_m",
        "direction",
        "basis",
    }
    # The join /api/floors keys a pipe run by. It is the position in the raw table, which is
    # the position in this list only for as long as nothing is torn -- which is exactly why
    # it is sent rather than counted.
    assert [r["row"] for r in body["pipes"]] == list(range(len(raw["segments"])))
    # The class legend is resolved here, or the page would have to carry it.
    assert row["cls"] == raw["classes"][raw["segments"][0][1]]
    assert {r["cls"] for r in body["pipes"]} <= set(raw["classes"])
    assert row["name"] == state.game.buildings[row["cls"]].name
    assert not row["name"].startswith("Build_")

    # Metres, like every other coordinate on this surface, and in the projection's order.
    assert len(row["points_m"]) == len(raw["segments"][0][2])
    for out, cm in zip(row["points_m"], raw["segments"][0][2], strict=True):
        assert out == [pytest.approx(round(v / 100.0, 1)) for v in cm]
    for r in body["pipes"]:
        assert len(r["points_m"]) >= 2, "a pipe is a line; there are no vertical pipes"
        for x_m, y_m, _z_m in r["points_m"]:
            assert abs(x_m) < 5000 and abs(y_m) < 5000


def test_every_pipe_says_which_fluid_it_carries(client, state):
    """The thing a belt cannot say, and it comes from the world rather than from a guess.

    The game keeps an ``FGPipeNetwork`` per connected plumbing system with the fluid on it,
    so this is the save's own answer. Asserted against the projection's own network table
    rather than against a list of fluid names, which would pin this world's contents.
    """
    body = client.get("/api/pipes").json()
    by_id = {n["id"]: n["fluid"] for n in state.projection["pipes"]["networks"]}
    assert all(r["fluid"] == by_id[r["network"]] for r in body["pipes"])
    assert all(r["fluid"] is not None for r in body["pipes"]), (
        "every pipe on this world is claimed by a network"
    )
    # Resolved against the dump, so a popup never shows a reader a `Desc_…_C`.
    for r in body["pipes"]:
        assert r["fluid_name"] == state.game.items[r["fluid"]].name
        assert state.game.items[r["fluid"]].is_fluid
    assert len({r["fluid"] for r in body["pipes"]}) > 1, "this world plumbs more than one"

    # The tier's own rate, rather than an "MK2" the page would have to parse out of a name.
    assert {r["flow_m3_min"] for r in body["pipes"]} <= {300.0, 600.0}
    assert len({r["flow_m3_min"] for r in body["pipes"]}) == 2, "Mk1 and Mk2 both built here"


def test_every_pipe_row_says_which_way_it_flows_or_says_it_does_not_know(client, state):
    """The refusal replaced by a labelled answer, and the label is the point.

    There is still no direction stored ON a pipe -- its two connectors are numbered rather
    than named input and output. What there IS, and what the old refusal never interrogated,
    is the rest of the network: the save serialises every fluid coupling and TYPES a machine's
    ports. So a row now carries a direction where the plumbing admits only one, and ``unknown``
    where it admits two, with ``basis`` naming which of those a reader is looking at.

    Pinned here so that a direction can never arrive unlabelled, which is the invention the
    old test was guarding against: ``unknown`` and ``unresolved`` go together in both
    directions, and nothing else does.
    """
    body = client.get("/api/pipes").json()
    rows = body["pipes"]
    for r in rows:
        assert r["direction"] in {"forward", "reverse", "unknown"}
        assert r["basis"] in {"machine port", "pump", "propagated", "unresolved"}
        assert (r["direction"] == "unknown") == (r["basis"] == "unresolved"), r

    directed = [r for r in rows if r["direction"] != "unknown"]
    assert body["directed"] == len(directed)
    assert directed, "this world's plumbing is not one giant ambiguity"
    assert len(directed) < len(rows), "nor is any of it free"
    # Both readings occur: the spline's own order is the order the player dragged it, so a
    # projection where every pipe came out `forward` would mean the direction was being read
    # off the point order rather than off the network.
    assert {r["direction"] for r in directed} == {"forward", "reverse"}
    # And all three warrants are exercised, or a basis is dead code nobody would notice.
    assert {r["basis"] for r in directed} == {"machine port", "pump", "propagated"}
    assert len(directed) == sum(1 for f in state.pipe_flow if f["direction"] != "unknown"), (
        "the surface reports exactly what the domain service decided"
    )


def test_a_world_with_no_pipes_answers_with_empty_plumbing(game):
    """Asserted through the shapes the projection has carried, exactly as the belts next
    door are: a young save has laid no pipe, and that is not an error."""
    for projection in (
        {},
        {"pipes": {}},
        {"pipes": {"classes": [], "networks": [], "segments": []}},
    ):
        with client_over(WorldState(projection=projection, game=game), game) as c:
            assert c.get("/api/pipes").json() == {
                "pipes": [],
                "count": 0,
                "networks": 0,
                "directed": 0,
            }


def test_a_malformed_pipe_segment_costs_one_piece_not_the_plumbing(game):
    """Raw projection data, read guarded field by field -- the structures rule, again."""
    projection = {
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 7, "fluid": "Desc_Water_C"}, "not a network"],
            "segments": [
                [0, 0, [[100, 200, 300], [400, 500, 600]]],
                [1, 0],  # short: no points
                ["net", 0, [[100, 200, 300]]],  # unparseable network index
                [9, 0, [[700, 800, 900], [1, 2, 3]]],  # network index off the end
                [1, 0, [[10, 20, 30], [40, 50, 60]]],  # network entry is not a dict
                [0, 9, [[700, 800, 900], [1, 2, 3]]],  # class index off the end
                [0, 0, [[100, 200], "not a point", [100, 200, 300]]],  # one usable point
                [0, 0, []],  # no geometry at all
                "not a segment",
            ],
        }
    }
    with client_over(
        lambda save=None, world=None: WorldState(projection=projection, game=game), game
    ) as c:
        body = c.get("/api/pipes").json()
    assert body["count"] == 5
    # And the join survives the tearing, which is the whole reason `row` is a field rather
    # than this list's index: the five that decoded sat at 0, 3, 4, 5 and 6 in the raw table,
    # so counting would have renumbered four of them -- and `/api/floors` keys a pipe run by
    # that number, so four runs would have been drawn on somebody else's floor.
    assert [r["row"] for r in body["pipes"]] == [0, 3, 4, 5, 6]
    assert body["pipes"][0] == {
        "row": 0,
        "network": 7,
        "fluid": "Desc_Water_C",
        "fluid_name": "Water",
        "cls": "Build_Pipeline_C",
        "name": "Pipeline Mk.1",
        "flow_m3_min": 300.0,
        "points_m": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        # No fourth column and no graph, so nothing to join and nothing to infer -- which is
        # exactly the schema-13 projection's answer too, rather than a crash or a guess. And
        # no fifth column either, so no curve: the same degradation, one schema later.
        "curve_m": None,
        "direction": "unknown",
        "basis": "unresolved",
    }
    # A pipe whose network the table cannot name is still a pipe on real ground: it keeps
    # its route and loses what the network would have told us. Null, not a guessed fluid.
    for orphan in (body["pipes"][1], body["pipes"][2]):
        assert (orphan["network"], orphan["fluid"], orphan["fluid_name"]) == (None, None, None)
        assert orphan["points_m"], "an unclaimed pipe is still drawn"
    # A piece whose class the legend cannot name keeps its route and its fluid.
    unnamed = body["pipes"][3]
    assert (unnamed["cls"], unnamed["flow_m3_min"]) == (None, None)
    assert unnamed["fluid"] == "Desc_Water_C"
    assert body["pipes"][4]["points_m"] == [[1.0, 2.0, 3.0]]
    assert body["networks"] == 1


# ------------------------------------------------------------------ route curvature


def test_a_route_sends_its_curve_alongside_its_points(client, state):
    """``curve_m``: one entry per span, in step with ``points_m``, metres like everything else.

    The pairing is the thing to pin. A span is drawn between ``points_m[i]`` and
    ``points_m[i+1]``, and its entry is ``[leave, arrive]`` -- the tangent leaving the first
    and the tangent arriving at the second. Off by one, or with the two swapped, every bend on
    the map is still a bend and is the wrong one, which is precisely the kind of fault a
    screenshot does not catch.
    """
    for path, key, at in (("/api/belts", "belts", 4), ("/api/pipes", "pipes", 4)):
        body = client.get(path).json()
        raw = state.projection[key]["segments"]
        assert len(body[key]) == len(raw)
        curved = 0
        for row, seg in zip(body[key], raw, strict=True):
            if len(seg) <= at:
                assert row["curve_m"] is None, "a straight route claims a curve"
                continue
            curved += 1
            assert len(row["curve_m"]) == len(row["points_m"]) - 1, "one entry per span"
            for entry, stored in zip(row["curve_m"], seg[at], strict=True):
                if stored == 0:
                    assert entry is None, "a flat span is null, not a zero vector"
                    continue
                assert entry == [
                    [pytest.approx(round(v / 100.0, 1)) for v in stored[:3]],
                    [pytest.approx(round(v / 100.0, 1)) for v in stored[3:]],
                ]
        assert curved > 200, f"{key}: nothing in the reference world bends"


def test_a_curve_is_the_metres_a_position_is_and_not_the_flip_a_client_applies(client):
    """A tangent is a displacement in the same space as the points, so it takes the same
    divide-by-100 and NOTHING else -- no y-flip, no re-origin. The flip belongs to the client
    and is applied to both together, which is what makes the pair usable as it stands.

    Checked by scale rather than by value: a tangent that had been through a coordinate
    transform of its own would not land in the same order of magnitude as the span it bends.
    """
    body = client.get("/api/belts").json()
    checked = 0
    for r in body["belts"]:
        if not r["curve_m"]:
            continue
        for i, entry in enumerate(r["curve_m"]):
            if entry is None:
                continue
            span = math.dist(r["points_m"][i][:2], r["points_m"][i + 1][:2])
            for vec in entry:
                assert len(vec) == 3
                # A tangent is metres of the same size as the span it bends: the game stores
                # roughly half the chord and never more than a few times it.
                assert math.hypot(vec[0], vec[1]) < max(20.0, span * 12), (span, vec)
            checked += 1
    assert checked > 500


def test_a_world_whose_routes_predate_the_curve_column_still_draws_them(game):
    """A schema-14 projection through a schema-15 server: polylines, and no error.

    The degradation that matters, because the disk cache is keyed on the schema and a stale
    pickle is refused rather than served -- but a save re-read by an older sidecar is not, and
    a client that got a 500 here would show an empty map instead of the map it had yesterday.
    """
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [[0, 0, [[0, 0, 0], [400, 0, 0]]]],
        },
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 1, "fluid": "Desc_Water_C"}],
            "segments": [[0, 0, [[0, 0, 0], [400, 0, 0]], -1]],
        },
    }
    with client_over(
        lambda save=None, world=None: WorldState(projection=projection, game=game), game
    ) as c:
        assert c.get("/api/belts").json()["belts"][0]["curve_m"] is None
        assert c.get("/api/pipes").json()["pipes"][0]["curve_m"] is None


def test_a_malformed_curve_costs_the_curve_and_not_the_route(game):
    """Read guarded entry by entry, the rule every other field on this surface follows.

    A route whose curve column is the wrong length, or holds something that is not six
    numbers, still has its points -- and the points are what put it on the map. Losing the
    piece to save the bend would be the wrong trade in every case.
    """
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                # Right length, one good span and three refusals of different kinds.
                [
                    0,
                    0,
                    [[0, 0, 0], [400, 0, 0], [800, 0, 0], [1200, 0, 0], [1600, 0, 0]],
                    -1,
                    [[100, 200, 0, 300, 400, 0], 0, "not a span", [1, 2, 3]],
                ],
                # Column present and the wrong length for the points: unusable as a whole,
                # because there is no way to tell which span each entry belongs to.
                [1, 0, [[0, 0, 0], [400, 0, 0], [800, 0, 0]], -1, [[1, 2, 3, 4, 5, 6]]],
                # Column present and entirely unusable: null, not an empty list, so a client
                # takes the same branch it takes for a straight run.
                [2, 0, [[0, 0, 0], [400, 0, 0]], -1, ["rubbish"]],
                [3, 0, [[0, 0, 0], [400, 0, 0]], -1, "not a column"],
            ],
        }
    }
    with client_over(
        lambda save=None, world=None: WorldState(projection=projection, game=game), game
    ) as c:
        rows = c.get("/api/belts").json()["belts"]
    assert len(rows) == 4, "every piece kept its geometry"
    assert rows[0]["curve_m"] == [[[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]], None, None, None]
    assert rows[1]["curve_m"] is None
    assert rows[2]["curve_m"] is None
    assert rows[3]["curve_m"] is None
