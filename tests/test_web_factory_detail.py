"""``/api/factories/aspects`` and ``/api/factories/sites``: the factory detail page's reads."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from test_query import INSIDE, OUTSIDER, ROD_A, SMELTER, _projection

from satisfactory_mcp.domain.world import sites as world_sites
from satisfactory_mcp.domain.world.state import WorldState


@pytest.fixture
def plant(game) -> WorldState:
    st = WorldState(projection=_projection(), game=game)
    st.labels.put("rod line", list(INSIDE))
    return st


@pytest.fixture
def api(plant, game):
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(state_loader=lambda save=None, world=None: plant, game_loader=lambda: game)
    with TestClient(app) as c:
        yield c


def test_aspects_carry_the_balance_with_its_three_verdicts(api):
    body = api.get("/api/factories/aspects", params={"factory": "rod line"}).json()
    assert body["name"] == "rod line"
    assert body["size"] == 3
    rows = {r["item"]: r for r in body["balance"]}
    assert rows["Iron Rod"]["verdict"] == "surplus"
    assert rows["Iron Rod"]["net"] == pytest.approx(30.0)
    assert rows["Iron Ore"]["verdict"] == "needs feeding"
    assert rows["Iron Ingot"]["verdict"] == "internal"
    assert rows["Iron Rod"]["measured_net"] is None, "no monitor anywhere is unknown, not 0"


def test_aspects_list_machines_in_metres_and_the_counts(api):
    body = api.get("/api/factories/aspects", params={"factory": "rod line"}).json()
    machines = {m["instance"]: m for m in body["machines"]}
    assert set(machines) == set(INSIDE)
    assert (machines[ROD_A]["x_m"], machines[ROD_A]["y_m"]) == (10.0, 0.0)
    assert machines[SMELTER]["recipe"] == "Iron Ingot"
    assert {r["name"]: r["count"] for r in body["recipes"]}["Iron Rod"] == 2
    assert sum(b["count"] for b in body["buildings"]) == 3
    assert body["bbox_m"] == [0.0, 0.0, 20.0, 0.0]


def test_aspects_report_power_links_and_issues(api):
    body = api.get("/api/factories/aspects", params={"factory": "rod line"}).json()
    assert body["power"]["draw_mw"] > 0
    assert body["power"]["generation_mw"] == 0
    assert body["links"] == [{"factory": None, "machines": 1}]
    assert body["issues"] == []
    assert body["nodes"] == []


def test_an_unknown_factory_is_a_404(api):
    reply = api.get("/api/factories/aspects", params={"factory": "nope"})
    assert reply.status_code == 404
    assert "nope" in reply.json()["error"]
    assert api.get("/api/factories/sites", params={"factory": "nope"}).status_code == 404


def test_sites_without_a_factory_are_the_whole_world(api):
    body = api.get("/api/factories/sites").json()
    assert body["factory"] is None
    assert body["total"] == len(body["sites"]) >= 1
    assert all(s["mine"] == 0 for s in body["sites"])
    assert all(s["selector"].startswith("near:") for s in body["sites"])


def test_sites_with_a_factory_say_how_much_of_it_each_holds(api):
    body = api.get("/api/factories/sites", params={"factory": "rod line"}).json()
    assert body["factory_machines"] == 3
    assert sum(s["mine"] for s in body["sites"]) == 3
    assert all(s["mine"] > 0 for s in body["sites"])


def test_a_site_lists_its_members_and_a_covering_selector():
    records = [
        {"instance": f"L:P.{name}", "cls": "Build_SmelterMk1_C", "pos": [x, 0, 0]}
        for name, x in (("A", 0), ("B", 10000), (OUTSIDER, 900000))
    ]
    out = world_sites.sites(records)
    assert sorted(out[0]["instances"]) == ["A", "B"]
    assert out[0]["selector"] == "near:50,0@60"
    assert out[1]["instances"] == [OUTSIDER]


def test_an_issue_names_the_building_and_keeps_the_id_for_a_selector():
    from satisfactory_mcp.interfaces.web.routers.factories.factory_detail import _issue

    names = {"Build_ConstructorMk1_C": "Constructor"}.get
    assert _issue("Build_ConstructorMk1_C_2146520259: paused (Copper Sheet)", names) == {
        "text": "Constructor: paused (Copper Sheet)",
        "machine": "Build_ConstructorMk1_C_2146520259",
    }
    assert _issue("3 machines unmonitored", names) == {
        "text": "3 machines unmonitored",
        "machine": None,
    }
