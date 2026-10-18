"""``/api/gamedata/…``: the Recipes codex routes agree with the functions the MCP tools call."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.core.gamedata import search
from satisfactory_mcp.core.gamedata.categories import item_categories
from satisfactory_mcp.core.gamedata.search import find_recipe
from satisfactory_mcp.core.gamedata.unlocks import granted_by
from satisfactory_mcp.domain import rules
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.web import client_over, failing_state_loader


def test_items_follow_the_search_items_order(client, game):
    body = client.get("/api/gamedata/items", params={"q": "iron"}).json()
    hits = search.find_items(game, "iron")
    assert body["total"] == len(hits)
    assert [i["cls"] for i in body["items"]] == [i.cls for i in hits]
    assert body["items"][0]["name"].casefold().startswith("iron")


def test_an_empty_query_lists_every_item_up_to_the_cap(client, game):
    body = client.get("/api/gamedata/items").json()
    assert body["total"] == len(search.find_items(game, ""))
    assert len(body["items"]) == min(body["total"], 200)


def test_recipes_mark_have_and_locked_against_the_save(client, state):
    body = client.get("/api/gamedata/recipes", params={"q": "plate"}).json()
    have = state.available_recipe_ids
    assert body["save_note"] is None and body["recipes"]
    for row in body["recipes"]:
        assert row["unlocked"] == (row["cls"] in have)
        assert row["kind"] == "part"
    census = body["census"]
    assert census["total"] >= len(body["recipes"])


def test_consumes_is_the_reverse_lookup(client, game):
    body = client.get("/api/gamedata/recipes", params={"consumes": "Rubber"}).json()
    rubber = next(c for c, i in game.items.items() if i.name == "Rubber")
    assert body["recipes"]
    for row in body["recipes"]:
        assert any(f.item == rubber for f in game.recipes[row["cls"]].ingredients)
        assert row["qty"] > 0


def test_an_unknown_item_or_kind_is_an_error(client):
    assert client.get("/api/gamedata/recipes", params={"consumes": "zzzz"}).status_code == 404
    assert client.get("/api/gamedata/recipes", params={"recipe_kind": "zzzz"}).status_code == 400


def test_recipes_still_answer_without_a_save_and_say_why(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    body = client.get("/api/gamedata/recipes", params={"q": "plate"}).json()
    assert "no save could be read" in body["save_note"]
    assert all(row["unlocked"] is None for row in body["recipes"])


def test_recipe_detail_by_id_and_by_name(client, game, state):
    r, _ = find_recipe(game, "Iron Plate")
    assert r is not None
    for key in (r.cls, r.name):
        body = client.get("/api/gamedata/recipe", params={"recipe": key}).json()
        assert body["cls"] == r.cls
        assert body["power_mw"] == pytest.approx(game.recipe_power_mw(r))
        assert [f["item"] for f in body["ingredients"]] == [f.item for f in r.ingredients]
        assert body["granted_by"] == granted_by(game, r)
        assert body["unlocked"] == (r.cls in state.available_recipe_ids)


def test_an_ambiguous_recipe_lists_the_candidates_and_unknown_is_404(client):
    ambiguous = client.get("/api/gamedata/recipe", params={"recipe": "Iron"})
    assert ambiguous.status_code == 409
    assert "matches" in ambiguous.json()["error"]
    unknown = client.get("/api/gamedata/recipe", params={"recipe": "zzzz-no-such"})
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "no recipe named “zzzz-no-such”"


def test_an_ambiguous_name_counts_only_unlocked_candidates_with_spoilers_0(client, game, state):
    _, hits = find_recipe(game, "Iron")
    have = state.available_recipe_ids
    unlocked = [h for h in hits if h in have]
    assert len(hits) > len(unlocked) > 1, "the fixture must mix locked and unlocked candidates"
    full = client.get("/api/gamedata/recipe", params={"recipe": "Iron"}).json()["error"]
    hidden = client.get("/api/gamedata/recipe", params={"recipe": "Iron", "spoilers": 0})
    assert f"matches {len(hits)} recipes" in full
    assert hidden.status_code == 409
    assert f"matches {len(unlocked)} recipes" in hidden.json()["error"]


def test_alternates_list_every_maker_alternates_first(client, game, state):
    body = client.get("/api/gamedata/alternates", params={"item": "Iron Plate"}).json()
    makers = search.makers_of(game, body["item"])
    assert [r["cls"] for r in body["recipes"]] == [r.cls for r in makers]
    flags = [r["alternate"] for r in body["recipes"]]
    assert flags == sorted(flags, reverse=True)
    for row in body["recipes"]:
        assert row["unlocked"] == (row["cls"] in state.available_recipe_ids)
        assert row["granted_by"] == granted_by(game, game.recipes[row["cls"]])
    gone = client.get("/api/gamedata/alternates", params={"item": "zzzz"})
    assert gone.status_code == 404 and gone.json() == {"error": "no item named “zzzz”"}


def test_unlocked_counts_agree_with_the_state(client, state):
    body = client.get("/api/gamedata/unlocked").json()
    assert body["alternates_unlocked"] == len(state.unlocked_alternates)
    assert [r["cls"] for r in body["recipes"]] == sorted(
        (r.cls for r in state.unlocked_alternates),
        key=lambda c: state.game.recipes[c].name,
    )
    assert body["save_kind"] in ("autosave", "manual save")
    assert "saveVersion" not in (body["written_ago"] or "")
    every = client.get("/api/gamedata/unlocked", params={"only_alternates": False}).json()
    assert (
        len(every["recipes"]) == every["automatable_total"] == len(state.unlocked_recipes("part"))
    )


def test_unlocked_needs_a_save(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    r = client.get("/api/gamedata/unlocked")
    assert r.status_code == 404
    assert "could not read save" in r.json()["error"]


def test_a_building_recipe_is_read_per_build_not_per_minute(client):
    """A build recipe has no cycle; its per-minute rate rests on a placeholder."""
    body = client.get("/api/gamedata/recipe", params={"recipe": "Recipe_AssemblerMk1_C"}).json()
    rotor = next(f for f in body["ingredients"] if f["name"] == "Rotor")
    assert rotor["amount"] == 4


def test_items_sort_regardless_of_case_with_event_items_last(game):
    events = game.event_items()
    assert "Desc_Gift_C" in events and "Desc_Wire_C" not in events
    hits = search.find_items(game, "")
    flags = [i.cls in events for i in hits]
    assert flags == sorted(flags), "every event item after every other"
    plain = [i.name for i in hits if i.cls not in events]
    assert plain == sorted(plain, key=str.casefold)


def test_a_locked_recipe_is_a_spoiler_and_spoilers_0_drops_it_from_rows_and_census(client):
    full = client.get("/api/gamedata/recipes", params={"q": "ingot"}).json()
    assert any(r["spoiler"] for r in full["recipes"]) and not all(
        r["spoiler"] for r in full["recipes"]
    ), "the fixture must mix locked and unlocked recipes"
    for row in full["recipes"]:
        assert row["spoiler"] == (row["unlocked"] is False)
    assert client.get("/api/gamedata/recipes", params={"q": "ingot", "spoilers": 1}).json() == full
    hidden = client.get("/api/gamedata/recipes", params={"q": "ingot", "spoilers": 0}).json()
    assert hidden["recipes"] == [r for r in full["recipes"] if not r["spoiler"]]
    census = hidden["census"]
    assert census["locked"] == {}
    assert census["have"] == full["census"]["have"]
    assert census["total"] == full["census"]["total"] - sum(full["census"]["locked"].values())
    assert census["total"] == len(hidden["recipes"])


def test_without_a_save_nothing_is_a_spoiler(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    body = client.get("/api/gamedata/recipes", params={"q": "plate", "spoilers": 0}).json()
    assert body["recipes"] and not any(r["spoiler"] for r in body["recipes"])


def test_makers_and_the_recipe_card_carry_the_spoiler_flag(client, game, state):
    full = client.get("/api/gamedata/alternates", params={"item": "Iron Ingot"}).json()
    assert any(r["spoiler"] for r in full["recipes"])
    for row in full["recipes"]:
        assert row["spoiler"] == (row["unlocked"] is False)
    hidden = client.get(
        "/api/gamedata/alternates", params={"item": "Iron Ingot", "spoilers": 0}
    ).json()
    assert hidden["recipes"] == [r for r in full["recipes"] if not r["spoiler"]]
    locked = next(r["cls"] for r in full["recipes"] if r["spoiler"])
    card = client.get("/api/gamedata/recipe", params={"recipe": locked}).json()
    assert card["spoiler"] is True and card["unlocked"] is False
    r, _ = find_recipe(game, "Iron Plate")
    assert client.get("/api/gamedata/recipe", params={"recipe": r.cls}).json()["spoiler"] is (
        r.cls not in state.available_recipe_ids
    )


def test_a_building_descriptor_points_at_its_build_recipe(client):
    body = client.get("/api/gamedata/alternates", params={"item": "Desc_AssemblerMk1_C"}).json()
    assert body["build_recipe"] == "Recipe_AssemblerMk1_C"
    plate = client.get("/api/gamedata/alternates", params={"item": "Iron Plate"}).json()
    assert plate["build_recipe"] is None


def test_items_carry_their_category_and_whether_the_save_can_make_them(client, game, state):
    rows = {r["name"]: r for r in client.get("/api/gamedata/items").json()["items"]}
    cats = item_categories(game)
    assert all(r["category"] == cats[r["cls"]] for r in rows.values())
    assert rows["Iron Plate"]["category"] == "standard parts"
    assert rows["Iron Ore"]["locked"] is False
    have = state.available_recipe_ids
    for row in rows.values():
        makers = game.producers_of(row["cls"], "part") + game.producers_of(row["cls"], "manual")
        if makers and not game.items[row["cls"]].is_resource:
            assert row["locked"] == (not any(m.cls in have for m in makers))
    locks = [r["locked"] for r in rows.values()]
    assert any(locks) and not all(locks), "the fixture must mix locked and unlocked items"


def test_items_answer_without_a_save_with_locked_unknown(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    rows = client.get("/api/gamedata/items", params={"q": "iron"}).json()["items"]
    assert rows and all(r["locked"] is None for r in rows)


def test_makers_name_the_machine_icon_and_users_their_main_product(client, game):
    plate = client.get("/api/gamedata/alternates", params={"item": "Iron Plate"}).json()
    makers = plate["recipes"]
    for m in makers:
        assert m["machine_icon"] == game.machine(game.recipes[m["cls"]]).descriptor
    assert any(m["machine_icon"] == "Desc_ConstructorMk1_C" for m in makers)
    users = client.get("/api/gamedata/recipes", params={"consumes": "Iron Plate"}).json()
    assert users["recipes"]
    for u in users["recipes"]:
        assert u["product"] == game.recipes[u["cls"]].main_product


def _ingots(rates: list[dict]) -> float:
    return next(r["amount"] for r in rates if r["item"] == "Desc_IronIngot_C")


def test_a_game_mode_world_shows_its_own_amounts_and_the_multiplier(client, game, projection):
    mode = {**rules.DEFAULT, "parts_cost": 0.75}
    cheap = WorldState(projection={**projection, "game_mode": mode}, game=game)
    with client_over(cheap, game) as moded:
        detail = moded.get("/api/gamedata/recipe", params={"recipe": "Iron Plate"}).json()
        makers = moded.get("/api/gamedata/alternates", params={"item": "Iron Plate"}).json()
        users = moded.get("/api/gamedata/recipes", params={"consumes": "Iron Ingot"}).json()
    assert detail["parts_cost"] == makers["parts_cost"] == users["parts_cost"] == 0.75
    assert _ingots(detail["ingredients"]) == 2.0
    plate = next(m for m in makers["recipes"] if m["cls"] == "Recipe_IronPlate_C")
    assert _ingots(plate["ingredients"]) == 2.0
    assert next(u for u in users["recipes"] if u["cls"] == "Recipe_IronPlate_C")["qty"] == 20.0

    vanilla = client.get("/api/gamedata/recipe", params={"recipe": "Iron Plate"}).json()
    assert vanilla["parts_cost"] == 1.0 and _ingots(vanilla["ingredients"]) == 3.0


def test_without_a_save_the_amounts_are_vanilla(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    body = client.get("/api/gamedata/recipe", params={"recipe": "Iron Plate"}).json()
    assert body["parts_cost"] == 1.0 and _ingots(body["ingredients"]) == 3.0
