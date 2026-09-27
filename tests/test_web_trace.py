"""``/api/trace``: the walk ``trace_upstream`` does, with geometry, rates and states."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from test_trace_seeds import CONSTRUCTOR, SMELTER, _projection

from satisfactory_mcp.domain.world.state import WorldState


@pytest.fixture
def plant(game) -> WorldState:
    projection = _projection()
    projection["belts"] = {
        "classes": ["Build_ConveyorBeltMk1_C"],
        "segments": [[7, 0, [[0, 0, 0], [400, 0, 0], [800, 0, 0]], 1]],
    }
    return WorldState(projection=projection, game=game)


@pytest.fixture
def api(plant, game):
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(state_loader=lambda save=None, world=None: plant, game_loader=lambda: game)
    with TestClient(app) as c:
        yield c


def test_a_factory_label_is_a_seed(api, plant):
    plant.labels.put("rod line", [CONSTRUCTOR])
    body = api.get("/api/trace", params={"seed": "rod line"}).json()
    assert body["subject"] == "factory 'rod line' (1 machines)"
    rows = {m["instance"]: m for m in body["machines"]}
    assert rows[CONSTRUCTOR]["seed"] and rows[CONSTRUCTOR]["hops"] == 0
    assert not rows[SMELTER]["seed"] and rows[SMELTER]["hops"] > 0


def test_upstream_carries_items_rates_and_the_flow_between_groups(api):
    body = api.get("/api/trace", params={"seed": CONSTRUCTOR}).json()
    assert body["direction"] == "up"
    assert [r["item"] for r in body["items"]] == ["Iron Ingot"]
    assert body["items"][0]["per_min"] > 0
    flows = [e for e in body["edges"] if e["item"] == "Iron Ingot"]
    assert any("Smelter" in e["source"] and "Constructor" in e["target"] for e in flows)


def test_downstream_reaches_the_consumer(api):
    body = api.get("/api/trace", params={"seed": SMELTER, "direction": "down"}).json()
    reached = [m for m in body["machines"] if not m["seed"]]
    assert [m["instance"] for m in reached] == [CONSTRUCTOR]
    assert body["items"][0]["item"] == "Iron Ingot"


def test_machines_are_placed_in_metres_with_their_state(api):
    body = api.get("/api/trace", params={"seed": CONSTRUCTOR}).json()
    rows = {m["instance"]: m for m in body["machines"]}
    assert (rows[CONSTRUCTOR]["x_m"], rows[CONSTRUCTOR]["y_m"]) == (8.0, 0.0)
    for m in body["machines"]:
        assert m["state"]
        assert m["actionable"] in (True, False)
    assert body["bbox_m"] == [0.0, 0.0, 8.0, 0.0]


def test_the_belt_it_crossed_is_drawn_as_a_run(api):
    runs = api.get("/api/trace", params={"seed": CONSTRUCTOR}).json()["runs"]
    assert len(runs) == 1
    assert runs[0]["medium"] == "belt"
    assert runs[0]["ident"] == "chain:7"
    assert runs[0]["lines_m"] == [[[0.0, 0.0], [4.0, 0.0], [8.0, 0.0]]]


def test_a_bad_direction_and_an_unknown_seed_are_refused(api):
    assert (
        api.get("/api/trace", params={"seed": CONSTRUCTOR, "direction": "sideways"}).status_code
        == 400
    )
    missing = api.get("/api/trace", params={"seed": "no such thing"})
    assert missing.status_code == 404
    assert "error" in missing.json()


def test_a_stale_pin_is_refused(api):
    assert (
        api.get("/api/trace", params={"seed": CONSTRUCTOR, "as_of": "sav:000000000000"}).status_code
        == 409
    )
