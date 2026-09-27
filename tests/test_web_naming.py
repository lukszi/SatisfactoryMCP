"""``/api/factories/candidates`` and ``/api/labels``: detect, name, rename and forget from the page.

Every write lands in temporary labels and plans directories; the fixture world is never written.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.factories import fed, flowgraph, identity, naming
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app

ORIGIN = {"origin": "http://testserver"}

EVERYTHING = {"fed_only": False, "min_machines": 1}


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "labels_dir", lambda: tmp_path / "labels")
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    return tmp_path / "labels"


@pytest.fixture
def empty(store_dir, projection, game):
    """A client over the fixture world whose label store starts empty and is re-read per call."""
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def _detect(client, **params):
    return client.get("/api/factories/candidates", params=params or EVERYTHING).json()


def _first(client):
    body = _detect(client)
    return body, body["candidates"][0]


def _name(client, body, row, name=None, version=None):
    payload = {
        "name": name or row["suggested_name"],
        "proposal": row["index"],
        "as_of": body["token"],
        "version": body["version"] if version is None else version,
    }
    return client.post("/api/labels", json=payload, headers=ORIGIN)


def _stored(store_dir) -> dict:
    return json.loads(next(store_dir.glob("*.json")).read_text())


def _version(client) -> int:
    return client.get("/api/factories/health").json()["labels_version"]


# ------------------------------------------------------------------ detection


def test_an_empty_store_offers_every_proposal_with_a_name(empty, projection, game):
    body = _detect(empty)
    st = WorldState(projection=projection, game=game)
    assert body["named"] == 0 and body["version"] == 0
    assert body["hidden"] == {"small": 0, "not_fed": 0}
    assert [r["index"] for r in body["candidates"]] == list(range(len(st.proposals)))
    names = [r["suggested_name"] for r in body["candidates"]]
    assert len({n.casefold() for n in names}) == len(names), "suggestions never collide"
    first = body["candidates"][0]
    assert first["selector"] == f"proposal:{first['index']}"
    assert first["machines"] == st.proposals[first["index"]].size
    assert body["token"].startswith("sav:")
    assert all(r["fed"] in fed.VERDICTS for r in body["candidates"])


def test_the_filters_say_what_they_hid(empty):
    everything = _detect(empty)["candidates"]
    body = _detect(empty, min_machines=2, fed_only=True)
    small = [r for r in everything if r["machines"] < 2]
    unfed = [r for r in everything if r["machines"] >= 2 and r["fed"] == fed.NOT_FED]
    assert body["hidden"] == {"small": len(small), "not_fed": len(unfed)}
    assert len(body["candidates"]) == len(everything) - len(small) - len(unfed)
    assert all(r["fed"] != fed.NOT_FED and r["machines"] >= 2 for r in body["candidates"])


def test_a_bad_filter_or_style_is_refused_and_names_the_known_styles(empty):
    reply = empty.get("/api/factories/candidates", params={"style": "fancy"})
    assert reply.status_code == 400
    assert all(style in reply.json()["error"] for style in naming.STYLES)
    assert empty.get("/api/factories/candidates", params={"min_machines": 0}).status_code == 400


# ------------------------------------------------------------------ flows


def test_every_made_item_has_one_role_and_products_say_where_they_go(empty):
    for row in _detect(empty)["candidates"]:
        made = [
            f["name"] for k in ("products", "intermediates", "sunk", "unrouted") for f in row[k]
        ]
        assert len(made) == len(set(made)), row["selector"]
        for f in row["products"]:
            assert {"storage", "export"} & set(f["to"]), (row["selector"], f)
        for f in row["sunk"]:
            assert "sink" in f["to"] and not {"storage", "export"} & set(f["to"])


def test_a_named_suggestion_leads_with_a_product(empty):
    for row in _detect(empty)["candidates"]:
        if row["confident"] and row["products"]:
            heads = [f["name"].lower() for f in row["products"]]
            assert any(row["suggested_name"].startswith(h) for h in heads) or row["buildings"]


def _cand(**buildings):
    return identity.Candidate(machines=["a", "b"], source="p", buildings=Counter(buildings))


def test_no_product_falls_back_to_what_is_made_and_says_it_guessed(game):
    fg = flowgraph.FlowGraph(produced={"Iron Ingot": 60.0}, roles={"Iron Ingot": "intermediate"})
    assert naming.lead(fg, _cand(), game, set()) == ("Iron Ingot", False)
    assert naming.lead(flowgraph.FlowGraph(), _cand(Build_SmelterMk1_C=2), game, set())[1] is False


def test_a_made_product_outranks_an_extracted_one(game):
    fg = flowgraph.FlowGraph(
        produced={"Iron Ore": 480.0, "Computer": 6.0},
        roles={"Iron Ore": "product", "Computer": "product"},
    )
    assert naming.lead(fg, _cand(), game, {"Iron Ore"}) == ("Computer", True)


# ------------------------------------------------------------------ graph


def _graph(client, **params):
    return client.get("/api/factories/graph", params=params)


def test_a_candidate_graph_groups_by_recipe_and_ends_at_terminals(empty):
    body, row = _first(empty)
    reply = _graph(empty, candidate=row["selector"], token=body["token"])
    assert reply.status_code == 200, reply.text
    graph = reply.json()
    groups = [n for n in graph["nodes"] if n["kind"] == "group"]
    assert sum(n["machines"] for n in groups) == row["machines"]
    assert len(groups) < row["machines"], "grouped by recipe, not per machine"
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    for n in groups:
        assert n["running"] + n["blocked"] + n["stopped"] == n["machines"]
    if row["products"]:
        assert {"storage", "export"} & ids


def test_a_named_factory_draws_the_same_graph_as_its_candidate(empty):
    body, row = _first(empty)
    _name(empty, body, row, name="graph me")
    named = _graph(empty, factory="graph me").json()
    unnamed = _graph(empty, candidate=row["selector"], token=body["token"]).json()
    assert named["nodes"] == unnamed["nodes"] and named["edges"] == unnamed["edges"]


def test_the_graph_route_refuses_what_it_cannot_answer(empty):
    _body, row = _first(empty)
    assert _graph(empty).status_code == 400
    assert _graph(empty, factory="nope").status_code == 404
    assert _graph(empty, candidate=row["selector"]).status_code == 400
    stale = _graph(empty, candidate=row["selector"], token="sav:000000000000")
    assert stale.status_code == 409


# ------------------------------------------------------------------ styles


def test_mine_is_the_default_and_is_lowercase_with_no_region(empty):
    body = empty.get("/api/factories/candidates").json()
    assert body["style"] == naming.DEFAULT_STYLE == "mine"
    for row in body["candidates"]:
        name = row["suggested_name"]
        assert name == name.lower() and " factory" in name
        assert not row["region"] or row["region"].lower() not in name


def test_product_region_names_the_region(empty):
    body = _detect(empty, style="product, region", **EVERYTHING)
    for row in body["candidates"]:
        if row["region"]:
            assert f", {row['region']}" in row["suggested_name"]


def test_a_suggestion_steps_past_a_taken_name_in_either_style():
    pr = "product, region"
    assert naming.suggest("Wire", "Spire Coast", [], pr) == "Wire, Spire Coast"
    assert naming.suggest("Wire", "Spire Coast", ["wire, spire coast"], pr) == (
        "Wire, Spire Coast 2"
    )
    assert naming.suggest("Wire", None, ["Wire", "Wire 2"], pr) == "Wire 3"
    assert naming.suggest("Iron Ingot", "Grass Fields", []) == "iron ingot factory"
    assert naming.suggest("Wire", None, ["Wire Factory"]) == "wire factory 2"
    assert naming.suggest("Wire", None, ["wire factory", "wire-factory-2"]) == "wire factory 3"
    with pytest.raises(ValueError):
        naming.suggest("Wire", None, [], "fancy")


# ------------------------------------------------------------------ naming


def test_naming_a_candidate_writes_the_store_and_drops_it_from_detection(empty, store_dir):
    body, row = _first(empty)
    reply = _name(empty, body, row)
    assert reply.status_code == 200, reply.text
    assert reply.json()["machines"] == row["machines"]
    assert reply.json()["version"] == 1
    stored = Path(reply.json()["stored_in"])
    assert stored.parent == store_dir
    assert [x["name"] for x in _stored(store_dir)["labels"]] == [row["suggested_name"]]

    after = _detect(empty)
    assert after["named"] == 1 and after["version"] == 1
    assert row["index"] not in [r["index"] for r in after["candidates"]]
    health = empty.get("/api/factories/health").json()
    assert [r["name"] for r in health["factories"]] == [row["suggested_name"]]


def test_the_page_and_the_tool_write_the_same_label(
    empty, store_dir, projection, game, monkeypatch
):
    body, row = _first(empty)
    _name(empty, body, row, name="steel factory")
    from_page = _stored(store_dir)["labels"][0]
    for f in store_dir.glob("*.json"):
        f.unlink()

    from satisfactory_mcp.interfaces.mcp.tools import factories as tools

    monkeypatch.setattr(
        tools, "_state", lambda *a, **k: WorldState(projection=projection, game=game)
    )
    tools.name_factory("steel factory", [row["selector"]])
    from_tool = _stored(store_dir)["labels"][0]
    assert from_page == from_tool


def test_a_taken_name_is_refused_rather_than_re_anchored(empty, store_dir):
    body, row = _first(empty)
    assert _name(empty, body, row, name="Steel").status_code == 200
    second = body["candidates"][1]
    reply = _name(empty, body, second, name="steel", version=1)
    assert reply.status_code == 409
    assert "already has a factory" in reply.json()["error"]
    assert _name(empty, body, second, name="   ", version=1).status_code == 409
    stored = _stored(store_dir)["labels"]
    assert len(stored) == 1 and len(stored[0]["anchors"]) == row["machines"]


def test_a_name_for_another_save_is_refused(empty, store_dir):
    body, row = _first(empty)
    reply = _name(empty, dict(body, token="sav:000000000000"), row)
    assert reply.status_code == 409
    assert not list(store_dir.glob("*.json"))


def test_a_write_against_a_stale_version_is_refused(empty, store_dir):
    body, row = _first(empty)
    assert _name(empty, body, row, name="first").status_code == 200
    reply = _name(empty, body, body["candidates"][1], name="second")
    assert reply.status_code == 409
    assert "changed elsewhere" in reply.json()["error"]
    assert [x["name"] for x in _stored(store_dir)["labels"]] == ["first"]


def test_an_unknown_proposal_is_a_404(empty):
    body, row = _first(empty)
    assert _name(empty, body, dict(row, index=999), name="x").status_code == 404


def test_forget_deletes_one_label_by_its_exact_name(empty, store_dir):
    body, row = _first(empty)
    _name(empty, body, row, name="Iron Works")
    version = _version(empty)
    assert empty.delete(f"/api/labels/Iron?version={version}", headers=ORIGIN).status_code == 409
    reply = empty.delete(f"/api/labels/Iron Works?version={version}", headers=ORIGIN)
    assert reply.status_code == 200
    assert reply.json()["machines"] == row["machines"]
    assert _stored(store_dir)["labels"] == []


# ------------------------------------------------------------------ rename


def _rename(client, name, to, version):
    return client.patch(f"/api/labels/{name}", json={"to": to, "version": version}, headers=ORIGIN)


def _plan_for(factory: str) -> None:
    log = PlanLog("X2faPVKjX06VaRzClNv5KQ")
    chat = Actor("chat")
    log.create(
        "north plan", {"target_item": "Steel Pipe"}, plan_id="p1", factory=factory, actor=chat
    )
    log.create("other plan", {"target_item": "Wire"}, plan_id="p2", factory="elsewhere", actor=chat)


def _plan_factories() -> dict[str, str]:
    return {s.name: s.factory for s in PlanLog("X2faPVKjX06VaRzClNv5KQ").heads()}


def test_rename_keeps_the_machines_and_moves_the_plans(empty, store_dir):
    body, row = _first(empty)
    _name(empty, body, row, name="old name")
    _plan_for("old name")
    reply = _rename(empty, "old name", "  new name ", _version(empty))
    assert reply.status_code == 200, reply.text
    assert reply.json()["name"] == "new name" and reply.json()["plans"] == ["north plan"]
    label = _stored(store_dir)["labels"][0]
    assert label["name"] == "new name" and len(label["anchors"]) == row["machines"]
    assert _plan_factories() == {"north plan": "new name", "other plan": "elsewhere"}
    log = PlanLog("X2faPVKjX06VaRzClNv5KQ")
    moved = log.commits(log.find("north plan").key)[-1]
    assert moved.actor.kind == "page", "a page rename must say the page moved the plan"


def test_rename_refuses_a_taken_blank_or_missing_name(empty):
    body, row = _first(empty)
    _name(empty, body, row, name="alpha")
    _name(empty, body, body["candidates"][1], name="beta", version=1)
    version = _version(empty)
    assert _rename(empty, "alpha", "BETA", version).status_code == 409
    assert _rename(empty, "alpha", "  ", version).status_code == 409
    assert _rename(empty, "gamma", "delta", version).status_code == 409
    assert _rename(empty, "alpha", "delta", version - 1).status_code == 409


def test_the_page_and_the_tool_rename_to_the_same_files(
    empty, store_dir, projection, game, monkeypatch
):
    from satisfactory_mcp.interfaces.mcp.tools import factories as tools

    monkeypatch.setattr(
        tools, "_state", lambda *a, **k: WorldState(projection=projection, game=game)
    )
    _body, row = _first(empty)
    results = []
    for via in ("page", "tool"):
        for f in store_dir.glob("*.json"):
            f.unlink()
        shutil.rmtree(PlanLog.dir_for("X2faPVKjX06VaRzClNv5KQ"), ignore_errors=True)
        tools.name_factory("old name", [row["selector"]])
        _plan_for("old name")
        if via == "page":
            assert _rename(empty, "old name", "new name", _version(empty)).status_code == 200
        else:
            assert "renamed factory" in tools.rename_factory("old name", "new name")
        labels = _stored(store_dir)
        results.append((labels, _plan_factories()))
    assert results[0] == results[1]


def test_the_user_data_override_moves_labels_and_plans(tmp_path, monkeypatch):
    monkeypatch.setenv("SATISFACTORY_USER_DATA", str(tmp_path))
    assert config.user_dir() == tmp_path
    assert config.labels_dir.__wrapped__() == tmp_path / "labels"
    assert config.plans_dir.__wrapped__() == tmp_path / "plans"
