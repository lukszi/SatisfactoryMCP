"""``/api/power``: every pole and tower, and the span of every wire between them.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``,
or by building its own app around a hand-written projection -- so nothing in this file
spawns the sidecar, reads a ``.sav`` or needs the save directory to exist.
"""

from __future__ import annotations

import math

import pytest

fastapi = pytest.importorskip("fastapi")


from satisfactory_mcp.domain.world.state import WorldState
from tests.support.web import client_over


def test_power_is_the_poles_and_the_span_of_every_wire(client, state):
    """The geometry half of a network whose connectivity has been here since schema 11."""
    body = client.get("/api/power").json()
    assert body["pole_count"] == 701
    assert body["wire_count"] == body["edge_count"] == len(state.projection["graph"]["power"])

    # Metres, like everything else that leaves this module, and both ends of every wire.
    for wire in body["wires"]:
        assert len(wire["a_m"]) == len(wire["b_m"]) == 3
    x = [w["a_m"][0] for w in body["wires"]]
    assert max(abs(v) for v in x) < 400_000 / 100, "centimetres reached the payload"

    # The poles are named, not spelled as engine ids, and the count is the mix the world has.
    names = {p["name"] for p in body["poles"]}
    assert "Power Pole Mk.1" in names
    assert sum(1 for p in body["poles"] if p["cls"] == "Build_PowerTowerPlatform_C") == 137


def test_a_wire_says_what_is_at_each_end_and_how_far_apart_they_are(client):
    """``from``/``to`` come off ``graph["power"]``, which is the one copy of the wiring."""
    body = client.get("/api/power").json()
    named = [w for w in body["wires"] if w["from"] and w["to"]]
    assert len(named) >= 1200, "almost every endpoint should resolve to a building"

    # The span is the straight line between the two published ends, in three dimensions.
    # Loosely, and the slack is the rounding: the server measures in centimetres and then
    # rounds six coordinates to a decimetre each, so a span recomputed from the ROUNDED
    # numbers can differ by a couple of them. Tightening this would be pinning the rounding.
    for wire in named[:200]:
        want = math.dist(wire["a_m"], wire["b_m"])
        assert wire["span_m"] == pytest.approx(want, abs=0.2)

    # A pole's connection count is a count off the edge list. 1,297 wires have 2,594 ends and
    # 1,991 of them land on a pole; the other 603 land straight on a machine, a generator or a
    # water pump, which is why the poles alone do not add up to twice the wires.
    assert sum(p["connections"] for p in body["poles"]) == 1_991


def test_a_wire_joins_each_end_to_the_pole_it_terminates_at(client):
    """``a_pole``/``b_pole``: the same join ``connections`` is counted off, sent per end.

    The reason the join exists is a floor-mode measurement: an endpoint is a CONNECTOR, 7 m
    over a Mk1's base and 24 m over a tower's, so a client filing wires on storeys by
    endpoint height misplaces them around 1-2 m mezzanine half-bands. The pole's own row is
    the storey the wire serves, and the index has to point at the pole actually under the
    endpoint -- which is what the planar check pins, since every measured connector offset
    is vertical (poles exactly, towers within their own 12 m platform).
    """
    body = client.get("/api/power").json()
    poles = body["poles"]
    ends = [(w[key + "_m"], w[key + "_pole"]) for w in body["wires"] for key in ("a", "b")]
    joined = [(at, ix) for at, ix in ends if ix is not None]
    # The same 1,991 the connection census counts: two numbers derived from one edge list
    # through two code paths, so a join counted off the wrong end moves exactly one of them.
    assert len(joined) == 1_991
    assert len(ends) - len(joined) == 603, "machine-fed ends carry null, not a guess"
    for at, ix in joined:
        pole = poles[ix]
        gap = math.hypot(at[0] - pole["x_m"], at[1] - pole["y_m"])
        assert gap < 15, (at, ix, "the joined pole does not stand under its endpoint")


def test_a_pole_nothing_is_wired_to_reports_zero_rather_than_nothing(client):
    """2 of the reference world's 701 -- tower platforms built and never strung.

    Zero is a measurement here: the pole is in the geometry table and in no edge, which is
    exactly what an ``actor_index`` of -1 means. A null would read as "not recorded".
    """
    body = client.get("/api/power").json()
    unstrung = [p for p in body["poles"] if p["connections"] == 0]
    assert len(unstrung) == 2
    assert all(p["cls"] == "Build_PowerTowerPlatform_C" for p in unstrung)


def test_a_world_with_no_power_at_all_answers_with_an_empty_network(game):
    """A save from before schema 17, and a world nobody has wired, read the same way."""
    for projection in ({}, {"power": None}, {"power": {"poles": {}, "wires": []}}):
        with client_over(WorldState(projection=projection, game=game), game) as c:
            assert c.get("/api/power").json() == {
                "poles": [],
                "pole_count": 0,
                "wires": [],
                "wire_count": 0,
                "edge_count": 0,
            }


def test_a_wire_with_no_geometry_costs_that_span_and_not_the_join(game):
    """The one case the positional join has to survive: a null row in ``wires``.

    ``wires[i]`` is the span of ``graph["power"][i]``, so a wire that published no geometry
    must not renumber the ones after it -- the second wire below would otherwise be drawn
    between the first one's actors.
    """
    projection = {
        "graph": {
            "actors": ["Build_PowerPoleMk1_C_1", "Build_SmelterMk1_C_2", "Build_PowerPoleMk1_C_3"],
            "power": [[0, 1], [0, 2]],
        },
        "machines": [{"cls": "Build_SmelterMk1_C", "instance": "x.Build_SmelterMk1_C_2"}],
        "power": {
            "poles": {
                "classes": ["Build_PowerPoleMk1_C"],
                "instances": [[0, 100, 200, 300, 0.0, 0], [0, 400, 200, 300, 0.0, 2]],
            },
            "wires": [None, [100, 200, 1000, 400, 200, 1000]],
        },
    }
    with client_over(
        lambda save=None, world=None: WorldState(projection=projection, game=game), game
    ) as c:
        body = c.get("/api/power").json()
    assert (body["wire_count"], body["edge_count"]) == (1, 2)
    (wire,) = body["wires"]
    assert (wire["from"], wire["to"]) == ("Power Pole Mk.1", "Power Pole Mk.1")
    assert wire["span_m"] == 3.0
    assert [p["connections"] for p in body["poles"]] == [2, 1]
    # And the pole join survives the same hole: the surviving wire is edge [0, 2], whose
    # actors are the FIRST and SECOND rows of ``poles`` -- a join renumbered by the null
    # row, or read off the wrong list, lands somewhere else.
    assert (wire["a_pole"], wire["b_pole"]) == (0, 1)


def test_power_takes_the_save_and_world_parameters_and_404s_on_an_unreadable_one(game):
    """The ``?save`` / ``?world`` contract every endpoint here shares."""
    asked: list[tuple] = []

    def loader(save=None, world=None):
        asked.append((save, world))
        if world == "nope":
            raise RuntimeError("no world matching 'nope'")
        return WorldState(projection={}, game=game)

    with client_over(loader, game) as c:
        assert c.get("/api/power?world=Han%20Solo&save=x.sav").status_code == 200
        bad = c.get("/api/power?world=nope")
    assert asked[0] == ("x.sav", "Han Solo"), "the query never reached the loader"
    assert bad.status_code == 404
    assert "no world matching" in bad.json()["error"]
