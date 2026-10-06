"""``/api/inspect``: the point inspector, and every number it refuses to invent.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``
or by building its own app -- so nothing in this file spawns the sidecar or reads a
``.sav``. The terrain-field half of this endpoint is exercised in ``test_heightfield.py``,
which owns the synthetic field.
"""

from __future__ import annotations

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.domain.world import conduits as conduits_mod
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app
from satisfactory_mcp.interfaces.web.routers import inspect as web_inspect

#: The same three coordinates ``test_elevation`` probes, and for the same reasons: a
#: developed platform where the built population swamps the ground one, open field with
#: nodes and nothing built, and water far enough out that nothing is in range.
ON_PLATFORM = (-1216.0, -1127.0)
IN_THE_FIELD = (2000.0, -2400.0)
OPEN_OCEAN = (-3500.0, 3500.0)


def test_inspect_answers_the_three_questions_a_site_starts_with(client):
    """Region, measured ground, nearest nodes -- none of which was on the map before."""
    x_m, y_m = IN_THE_FIELD
    body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()
    assert set(body) >= {"region", "elevation", "nearest"}
    assert body["at"] == {"x_m": x_m, "y_m": y_m}

    # Advisory, and never separable from how far it can be trusted. 64 m rather than 256:
    # the region table publishes a 256 m grid and carries a 64 m one, and a lookup reads the
    # finer of the two, so the accuracy travelling with the name is the finer cell.
    assert body["region"]["name"] == "Spire Coast"
    assert body["region"]["confidence"] == "interior"
    assert body["region"]["accuracy_m"] == 64

    e = body["elevation"]
    assert e["radius_m"] == web_inspect.INSPECT_RADIUS_M == 200.0
    assert e["ground_m"] is not None and e["ground_spread_m"] is not None
    assert e["counts"] == {"node": e["ground_count"]}, "open field: nothing built out here"
    assert e["built_m"] is None and e["built_count"] == 0

    near = body["nearest"]
    assert len(near) == web_inspect.INSPECT_NEAREST == 5
    assert [n["distance_m"] for n in near] == sorted(n["distance_m"] for n in near)
    row = near[0]
    assert set(row) >= {"resource", "purity", "occupied", "distance_m", "id", "x_m", "y_m"}
    assert row["resource"].startswith("Desc_")
    # Metres, like every other coordinate here, and the distance agrees with the position.
    assert row["distance_m"] == pytest.approx(
        ((row["x_m"] - x_m) ** 2 + (row["y_m"] - y_m) ** 2) ** 0.5, abs=0.15
    )


def test_inspect_measures_a_platform_and_still_refuses_the_fill_depth(client):
    """The trap this whole feature could have walked into, pinned at the coordinate where
    it is worst: 805 built samples against one node. The built median is real and is
    reported; the fill depth is a difference from a single ground point, so it is refused
    -- and the refusal carries its reason, because a bare null reads as a broken endpoint.
    """
    x_m, y_m = ON_PLATFORM
    e = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()["elevation"]
    assert e["built_count"] > 500 and e["built_m"] is not None
    assert e["counts"]["structure"] > e["counts"]["node"] * 100
    assert e["ground_count"] < 3
    assert e["fill_m"] is None, "a fill from one ground sample is an invented number"
    assert e["fill_note"] == "not enough ground samples (1 of 3 within 200 m)"


def test_inspect_quotes_a_fill_depth_once_both_populations_are_real(game):
    """And when it can be measured, it is: the difference of the two medians, in metres.

    Driven off a synthetic floor plan, for two reasons. The reference world has no spot
    where three nodes and a platform share a 200 m circle, so the refusal above is the
    only thing it can exercise; and a world with nothing else built makes the built median
    a number this test chose, which is what turns "some fill" into an exact 25.0.
    """
    from satisfactory_mcp.domain.spatial import geo
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()
    radius = web_inspect.INSPECT_RADIUS_M
    found = None
    for n in table.nodes:
        # The same circle the probe will use, or the ground median under test is not the
        # ground median the endpoint computes.
        near = [
            m
            for m in table.nodes
            if geo.distance_m((n["x"], n["y"]), (m["x"], m["y"])) <= radius and m.get("z")
        ]
        if len(near) % 2 == 1 and len(near) >= 3:
            found = (n, sorted(float(m["z"]) for m in near))
            break
    assert found, "the node table has no odd cluster of three within the probe radius"
    centre, heights = found
    ground_cm = heights[len(heights) // 2]

    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [[0, centre["x"], centre["y"], ground_cm + 2500.0]],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        e = c.get(
            "/api/inspect", params={"x_m": centre["x"] / 100.0, "y_m": centre["y"] / 100.0}
        ).json()["elevation"]
    assert e["ground_count"] == len(heights)
    assert e["built_count"] == 1
    assert e["fill_m"] == pytest.approx(25.0, abs=0.1)
    assert e["fill_note"] is None


def test_inspect_says_off_the_map_rather_than_naming_the_nearest_land(client):
    """Ocean and off-map are the same answer, and it is a refusal, not a nearby biome."""
    for x_m, y_m in (OPEN_OCEAN, (99999.0, 99999.0)):
        body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()
        assert body["region"] is None
        e = body["elevation"]
        assert e["ground_count"] == 0 and e["ground_m"] is None
        assert e["counts"] == {}
        assert e["fill_m"] is None and "not enough ground samples" in e["fill_note"]
        # The node table is map-wide, so "nearest" is always answerable -- and the
        # distances say plainly how far away the answer is.
        assert len(body["nearest"]) == 5
        assert all(n["distance_m"] > 1000 for n in body["nearest"])


def test_inspect_still_answers_when_the_save_cannot_be_read(game):
    """The node table needs no ``.sav``, so a broken world keeps its geography.

    What it loses is the built population and the occupancy join, and ``save_error`` has
    to say so: without it every node would read as free because nothing was there to say
    otherwise, which is the exact failure mode this project keeps refusing.
    """
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/inspect", params={"x_m": IN_THE_FIELD[0], "y_m": IN_THE_FIELD[1]})
    assert r.status_code == 200
    body = r.json()
    assert "sidecar produced no output" in body["save_error"]
    assert body["region"]["name"] == "Spire Coast"
    assert body["elevation"]["ground_count"] > 0, "the node table is the ground truth"
    assert body["elevation"]["built_count"] == 0
    assert body["nearest"] and all(n["occupied"] is False for n in body["nearest"])


def test_the_inspector_names_a_resource_the_same_way_a_node_dot_does(client):
    """format.ts says the node popup and the right-click card must not name one fact two
    ways, and the card said "OreIron impure" while the dot beside it said "Iron Ore".

    Both now read the server's own ``resource_name``, from the one helper in ``serial``."""
    body = client.get("/api/inspect", params={"x_m": IN_THE_FIELD[0], "y_m": IN_THE_FIELD[1]})
    nearest = body.json()["nearest"]
    assert nearest
    assert not any(n["resource_name"].startswith("Desc_") for n in nearest), nearest
    dots = {r["resource"]: r["resource_name"] for r in client.get("/api/nodes").json()["nodes"]}
    for n in nearest:
        assert n["resource_name"] == dots[n["resource"]]


def test_inspect_needs_a_coordinate(client):
    assert client.get("/api/inspect").status_code == 422


def test_inspect_adds_grid_fields_conduits_and_pickups(client):
    body = client.get(
        "/api/inspect", params={"x_m": IN_THE_FIELD[0], "y_m": IN_THE_FIELD[1]}
    ).json()
    assert body["grid"] == "X5Y5" and body["direction"] == "northeast"
    assert body["conduits"] == {"belt": 0, "pipe": 0, "radius_m": 250.0}
    assert 0 < len(body["fields"]) <= 3
    assert all(f["distance_m"] <= 500 for f in body["fields"])
    assert all(len(f["resources"]) == 1 for f in body["fields"])
    assert 0 < len(body["pickups"]) <= 5
    pickup = body["pickups"][0]
    assert {"label", "spoiler", "category", "distance_m"} <= set(pickup)
    assert [p["distance_m"] for p in body["pickups"]] == sorted(
        p["distance_m"] for p in body["pickups"]
    )
    assert all(isinstance(n["spoiler"], bool) for n in body["nearest"])
    assert body["pickups_within"] >= len(body["pickups"])
    assert 0 <= body["pickups_within_spoilers"] <= body["pickups_within"]
    assert body["stale"] == []


def test_inspect_counts_every_pickup_within_reach_not_just_the_five_it_lists(client, state):
    from satisfactory_mcp.domain.spatial import surroundings

    x_m, y_m = IN_THE_FIELD
    found = surroundings._pickups_near(state, x_m * 100, y_m * 100)
    body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()
    assert body["pickups_within"] == len(found)
    assert body["pickups_within_spoilers"] == sum(1 for p in found if p["spoiler"])


def test_inspect_radius_widens_the_elevation_reach_but_not_the_conduit_reach(client):
    x_m, y_m = ON_PLATFORM
    near = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m, "radius_m": 50}).json()
    far = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m, "radius_m": 400}).json()
    assert near["elevation"]["radius_m"] == 50 and far["elevation"]["radius_m"] == 400
    assert near["conduits"] == far["conduits"]
    assert far["conduits"]["radius_m"] == conduits_mod.NEAR_RADIUS_M
    assert client.get("/api/inspect", params={"x_m": 0, "y_m": 0, "radius_m": 0}).status_code == 422


def test_inspect_without_a_save_keeps_nodes_and_nulls_conduits(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        body = c.get("/api/inspect", params={"x_m": IN_THE_FIELD[0], "y_m": IN_THE_FIELD[1]}).json()
    assert body["nearest"] and body["fields"]
    assert body["conduits"] is None and body["pickups"] == [] and body["stale"] == []
    assert body["pickups_within"] is None


def test_locked_nodes_stay_in_nearest_flagged_for_the_page_to_fade(client):
    at = {"x_m": -430.0, "y_m": -66.0}
    nearest = client.get("/api/inspect", params=at).json()["nearest"]
    assert any(n["spoiler"] for n in nearest)
    assert len(nearest) == web_inspect.INSPECT_NEAREST
    assert [n["distance_m"] for n in nearest] == sorted(n["distance_m"] for n in nearest)
