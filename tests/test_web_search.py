"""``/api/search``: the header box over items, recipes and named factories, and its cost."""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata import search
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app

ORIGIN = {"origin": "http://testserver"}


def test_an_empty_query_finds_nothing(client):
    body = client.get("/api/search", params={"q": "  "}).json()
    assert body["items"] == body["recipes"] == body["factories"] == []
    assert body["items_total"] == body["recipes_total"] == 0


def test_items_and_recipes_match_the_codex(client, game, state):
    body = client.get("/api/search", params={"q": "plate"}).json()
    items = search.find_items(game, "plate")
    hits, _ = search.search(game, query="plate", recipe_kind="all")
    assert body["items_total"] == len(items)
    assert body["recipes_total"] == len(hits)
    assert len(body["items"]) <= 8 and len(body["recipes"]) <= 8
    starts = [r["name"].casefold().startswith("plate") for r in body["recipes"]]
    assert starts == sorted(starts, reverse=True)
    for row in body["recipes"]:
        assert row["unlocked"] == (row["cls"] in state.available_recipe_ids)


def test_only_unlocked_drops_locked_recipes_before_the_cut_and_the_count(client, game, state):
    """With spoilers off the page asks for unlocked recipes only; cutting to the first few
    first and hiding locked ones after left most unlocked matches unseen, and the total
    still counted the locked ones."""
    have = state.available_recipe_ids
    hits, _ = search.search(game, query="ingot", recipe_kind="all", unlocked=have)
    unlocked = [h for h in hits if h.unlocked]
    assert len(hits) > len(unlocked) > 0, "the fixture must mix locked and unlocked hits"
    body = client.get("/api/search", params={"q": "ingot", "only_unlocked": True}).json()
    assert body["recipes_total"] == len(unlocked)
    assert len(body["recipes"]) == min(8, len(unlocked))
    assert all(r["unlocked"] for r in body["recipes"])
    full = client.get("/api/search", params={"q": "ingot"}).json()
    assert full["recipes_total"] == len(hits)


def test_named_factories_are_found(tmp_path, monkeypatch, projection, game):
    monkeypatch.setattr(config, "labels_dir", lambda: tmp_path / "labels")
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get(
            "/api/factories/candidates", params={"fed_only": False, "min_machines": 1}
        ).json()
        row = body["candidates"][0]
        named = c.post(
            "/api/labels",
            json={
                "name": "Zebra Works",
                "proposal": row["index"],
                "as_of": body["token"],
                "version": body["version"],
            },
            headers=ORIGIN,
        )
        assert named.status_code == 200, named.text
        found = c.get("/api/search", params={"q": "zebra"}).json()
    assert found["factories"] == [{"name": "Zebra Works", "machines": row["machines"]}]
    assert found["factories_total"] == 1


def test_no_save_still_finds_items_and_recipes(client, monkeypatch):
    def boom(save=None, world=None):
        raise RuntimeError("sidecar produced no output")

    monkeypatch.setattr(client.app.state, "load_state", boom)
    body = client.get("/api/search", params={"q": "plate"}).json()
    assert body["items"] and body["recipes"] and body["factories"] == []
    assert "no save could be read" in body["save_note"]
    assert all(r["unlocked"] is None for r in body["recipes"])


def test_a_keystroke_stays_cheap(client):
    client.get("/api/search", params={"q": "i"})
    start = time.perf_counter()
    for q in ("i", "ir", "iro", "iron", "iron p", "iron pl", "iron pla", "iron plat"):
        assert client.get("/api/search", params={"q": q}).status_code == 200
    assert (time.perf_counter() - start) / 8 < 0.25


def test_recipe_hits_carry_the_spoiler_flag_and_spoilers_0_is_only_unlocked(client):
    full = client.get("/api/search", params={"q": "ingot"}).json()
    for row in full["recipes"]:
        assert row["spoiler"] == (row["unlocked"] is False)
    assert client.get("/api/search", params={"q": "ingot", "spoilers": 1}).json() == full
    hidden = client.get("/api/search", params={"q": "ingot", "spoilers": 0}).json()
    alias = client.get("/api/search", params={"q": "ingot", "only_unlocked": True}).json()
    assert hidden == alias
    assert hidden["recipes_total"] < full["recipes_total"]
    assert not any(r["spoiler"] for r in hidden["recipes"])


def test_buildings_and_crafted_recipes_are_found_and_carry_their_kind(client):
    hub = client.get("/api/search", params={"q": "hub"}).json()["recipes"]
    assert [(r["name"], r["kind"], r["machine"]) for r in hub] == [("The HUB", "building", None)]
    rifle = client.get("/api/search", params={"q": "turbo rifle"}).json()["recipes"]
    machines = [r["machine"] for r in rifle if r["name"] == "Turbo Rifle Ammo"]
    assert len(machines) == 2 and len(set(machines)) == 2 and None not in machines
