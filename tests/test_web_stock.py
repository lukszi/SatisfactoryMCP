"""``/api/stock``: the dashboard's Inventory section.

``importorskip`` at module scope: ``fastapi`` lives in the optional ``web`` extra. Both
loaders are injected, so nothing here reads a ``.sav``.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.domain.world.inventory import CRATE_KIND_TEXT
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app


def _body(projection: dict, game) -> dict:
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        r = c.get("/api/stock")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_piles_are_the_breakdown_the_stock_tool_reads(client, state):
    body = client.get("/api/stock").json()
    breakdown = state.inventory.breakdown()
    assert {r["item"] for r in body["items"]} == set(breakdown)
    for row in body["items"]:
        piles = breakdown[row["item"]]
        assert row["spendable"] == pytest.approx(piles["spendable"])
        assert row["carried"] == pytest.approx(piles["player"])
        assert row["storage"] == pytest.approx(piles["storage"])
        assert row["depot"] == pytest.approx(piles["depot"])
        assert row["buffers"] == pytest.approx(piles["machine"])
        assert row["crates"] == pytest.approx(piles["crate"])
        assert row["name"] == state.game.item_name(row["item"])


def test_spendable_matches_what_every_affordability_check_spends(client, state):
    body = client.get("/api/stock").json()
    spend = state.inventory.stock()
    for row in body["items"]:
        assert row["spendable"] == pytest.approx(spend.get(row["item"], 0.0))


def test_items_run_by_spendable_then_buffers(client):
    rows = client.get("/api/stock").json()["items"]
    keys = [(-r["spendable"], -r["buffers"]) for r in rows]
    assert keys == sorted(keys)


def test_places_are_the_holdings_in_their_order(client, state):
    body = client.get("/api/stock").json()
    holdings = state.inventory.holdings()
    assert [p["instance_leaf"] for p in body["places"]] == [h.instance for h in holdings]
    for place, h in zip(body["places"], holdings, strict=True):
        assert place["source"] == h.source
        assert place["kind"] == h.kind
        assert place["total"] == pytest.approx(h.total)
        assert [i["item"] for i in place["items"]] == [i for i, _ in h.items]
        assert place["slots_used"] == h.slots_used
        if h.fill is None:
            assert place["fill"] is None
        else:
            assert place["fill"] == pytest.approx(h.fill, abs=1e-4)


def test_the_census_counts_the_world_as_the_storage_route_does(client):
    body = client.get("/api/stock").json()
    storage = client.get("/api/storage").json()
    crates = client.get("/api/crates").json()
    census = body["census"]
    assert census["containers"] == storage["count"]
    assert census["filled"] == storage["filled"]
    assert census["solid"] + census["fluid"] == census["containers"]
    assert census["crates"] == crates["count"]
    assert census["deaths"] == crates["deaths"]


def test_a_crate_says_what_kind_it_is_in_words(game):
    projection = {
        "crates": [
            {
                "cls": "BP_Crate_C",
                "instance": "x.BP_Crate_C_1",
                "pos": [100, 200, 300],
                "kind": "death",
                "items": [["Desc_Wire_C", 3]],
                "slots": 1,
            }
        ]
    }
    place = _body(projection, game)["places"][0]
    assert place["source"] == "crate"
    assert place["crate_kind"] == "death"
    assert place["crate_kind_text"] == CRATE_KIND_TEXT["death"]
    assert (place["x_m"], place["y_m"], place["z_m"]) == (1.0, 2.0, 3.0)
    assert place["items"] == [{"item": "Desc_Wire_C", "name": game.item_name("Desc_Wire_C"), "amount": 3.0}]


def test_distance_is_measured_from_the_player_across_the_ground(game, monkeypatch):
    projection = {
        "crates": [
            {"cls": "BP_Crate_C", "instance": "x.a", "pos": [300_00, 400_00, 9_000_00], "kind": "none"},
            {"cls": "BP_Crate_C", "instance": "x.b", "kind": "none"},
        ]
    }
    monkeypatch.setattr(WorldState, "player_position", lambda self: (0.0, 0.0, 0.0))
    body = _body(projection, game)
    assert body["player"] == {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0}
    by_leaf = {p["instance_leaf"]: p for p in body["places"]}
    assert by_leaf["a"]["distance_m"] == pytest.approx(500.0)
    assert by_leaf["b"]["distance_m"] is None
    assert by_leaf["b"]["region"] is None


def test_an_empty_world_is_an_empty_answer(game):
    body = _body({}, game)
    assert body["items"] == [] and body["places"] == []
    assert body["census"] == {
        "containers": 0,
        "solid": 0,
        "fluid": 0,
        "filled": 0,
        "crates": 0,
        "deaths": 0,
    }


def test_stock_takes_the_save_and_world_parameters_and_404s_on_an_unreadable_one(game):
    asked: list[tuple] = []

    def loader(save=None, world=None):
        asked.append((save, world))
        if world == "nope":
            raise RuntimeError("no world matching 'nope'")
        return WorldState(projection={}, game=game)

    app = create_app(state_loader=loader, game_loader=lambda: game)
    with TestClient(app) as c:
        assert c.get("/api/stock?world=Some%20World&save=x.sav").status_code == 200
        bad = c.get("/api/stock?world=nope")
    assert asked[0] == ("x.sav", "Some World")
    assert bad.status_code == 404
    assert "no world matching" in bad.json()["error"]


def test_the_piles_sum_to_the_places_that_hold_them(client):
    """Per item, the storage pile is every container and fluid buffer, the crate pile every
    crate: a fluid buffer's contents counted nowhere read as stock that does not exist."""
    body = client.get("/api/stock").json()
    held: dict[tuple[str, str], float] = {}
    for place in body["places"]:
        for entry in place["items"]:
            key = (entry["item"], place["source"])
            held[key] = held.get(key, 0.0) + entry["amount"]
    piles = {p["item"]: p for p in body["items"]}
    assert any(p["kind"] == "fluid" and p["total"] for p in body["places"])
    for (item, source), amount in held.items():
        pile = piles[item]["storage" if source == "storage" else "crates"]
        assert pile == pytest.approx(amount, abs=0.01), (item, source)
