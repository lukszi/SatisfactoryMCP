"""``/api/factories/candidates`` and ``/api/labels``: detect, name, rename and forget from the page."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp import config
from satisfactory_mcp.domain.factories import candidates, fed, flowgraph, naming
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.web import PAGE_ORIGIN

EVERYTHING = {"fed_only": False, "min_machines": 1}


@pytest.fixture
def store_dir():
    return config.labels_dir()


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
    return client.post("/api/labels", json=payload, headers=PAGE_ORIGIN)


def _flags(reply) -> dict:
    body = reply.json()
    return {k: body[k] for k in ("stale", "name_taken", "pin")}


def _stored(store_dir) -> dict:
    return json.loads(next(store_dir.glob("*.json")).read_text())


def _version(client) -> int:
    return client.get("/api/factories/health").json()["labels_version"]


# ------------------------------------------------------------------ detection


def test_an_empty_store_offers_every_proposal_with_a_name(fresh_state_client, projection, game):
    body = _detect(fresh_state_client)
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


def test_the_filters_say_what_they_hid(fresh_state_client):
    everything = _detect(fresh_state_client)["candidates"]
    body = _detect(fresh_state_client, min_machines=2, fed_only=True)
    small = [r for r in everything if r["machines"] < 2]
    unfed = [r for r in everything if r["machines"] >= 2 and r["fed"] == fed.NOT_FED]
    assert body["hidden"] == {"small": len(small), "not_fed": len(unfed)}
    assert len(body["candidates"]) == len(everything) - len(small) - len(unfed)
    assert all(r["fed"] != fed.NOT_FED and r["machines"] >= 2 for r in body["candidates"])


def test_a_bad_filter_or_style_is_refused_and_names_the_known_styles(fresh_state_client):
    reply = fresh_state_client.get("/api/factories/candidates", params={"style": "fancy"})
    assert reply.status_code == 400
    assert all(style in reply.json()["error"] for style in naming.STYLES)
    assert (
        fresh_state_client.get("/api/factories/candidates", params={"min_machines": 0}).status_code
        == 400
    )


# ------------------------------------------------------------------ flows


def test_every_made_item_has_one_role_and_products_say_where_they_go(fresh_state_client):
    for row in _detect(fresh_state_client)["candidates"]:
        made = [
            f["name"] for k in ("products", "intermediates", "sunk", "unrouted") for f in row[k]
        ]
        assert len(made) == len(set(made)), row["selector"]
        for f in row["products"]:
            assert {"storage", "export"} & set(f["to"]), (row["selector"], f)
        for f in row["sunk"]:
            assert "sink" in f["to"] and not {"storage", "export"} & set(f["to"])


def test_a_named_suggestion_leads_with_a_product(fresh_state_client):
    for row in _detect(fresh_state_client)["candidates"]:
        if row["confident"] and row["products"]:
            heads = [f["name"].lower() for f in row["products"]]
            assert any(row["suggested_name"].startswith(h) for h in heads) or row["buildings"]


def _cand(**buildings):
    return candidates.Candidate(machines=["a", "b"], source="p", buildings=Counter(buildings))


def test_no_product_falls_back_to_what_is_made_and_says_it_guessed(game):
    fg = flowgraph.FlowGraph(produced={"Iron Ingot": 60.0}, roles={"Iron Ingot": "intermediate"})
    assert naming.lead(fg, _cand(), game, set()) == ("Iron Ingot", False)
    assert naming.lead(flowgraph.FlowGraph(), _cand(Build_SmelterMk1_C=2), game, set())[1] is False


def test_a_guess_prefers_a_made_item_to_an_ore(game):
    fg = flowgraph.FlowGraph(
        produced={"Coal": 360.0, "Steel Ingot": 270.0},
        roles={"Coal": "unrouted", "Steel Ingot": "unrouted"},
    )
    assert naming.lead(fg, _cand(), game, {"Coal"}) == ("Steel Ingot", False)
    only_ore = flowgraph.FlowGraph(produced={"Water": 360.0}, roles={"Water": "unrouted"})
    assert naming.lead(only_ore, _cand(), game, {"Water"}) == ("Water", False)


def test_detect_numbers_like_the_map_when_the_filters_hide_clusters(
    fresh_state_client, monkeypatch
):
    everything = {
        r["index"]: r["suggested_name"] for r in _detect(fresh_state_client)["candidates"]
    }
    filtered = _detect(fresh_state_client, fed_only=True, min_machines=3)["candidates"]
    assert filtered and len(filtered) < len(everything)
    mapped = {
        p["index"]: p["label"] for p in fresh_state_client.get("/api/factories").json()["proposals"]
    }
    for row in filtered:
        assert row["suggested_name"] == everything[row["index"]] == mapped[row["index"]]

    one_answer = naming.proposal_names
    monkeypatch.setattr(
        naming,
        "proposal_names",
        lambda *a, **k: {i: f"shared {i}" for i in one_answer(*a, **k)},
    )
    for row in _detect(fresh_state_client, fed_only=True, min_machines=3)["candidates"]:
        assert row["suggested_name"] == f"shared {row['index']}"


def test_a_made_product_outranks_an_extracted_one(game):
    fg = flowgraph.FlowGraph(
        produced={"Iron Ore": 480.0, "Computer": 6.0},
        roles={"Iron Ore": "product", "Computer": "product"},
    )
    assert naming.lead(fg, _cand(), game, {"Iron Ore"}) == ("Computer", True)


# ------------------------------------------------------------------ graph


def _graph(client, **params):
    return client.get("/api/factories/graph", params=params)


def test_a_candidate_graph_groups_by_recipe_and_ends_at_terminals(fresh_state_client):
    body, row = _first(fresh_state_client)
    reply = _graph(fresh_state_client, candidate=row["selector"], token=body["token"])
    assert reply.status_code == 200, reply.text
    graph = reply.json()
    groups = [n for n in graph["nodes"] if n["kind"] == "group"]
    assert sum(n["machines"] for n in groups) == row["machines"]
    assert len(groups) < row["machines"], "grouped by recipe, not per machine"
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    for n in groups:
        assert n["running"] + n["blocked"] + n["stopped"] == n["machines"]
        assert sum(n["states"].values()) == n["machines"]
        assert n["blocked"] == n["states"].get("blocked", 0)
    if row["products"]:
        assert {"storage", "export"} & ids


def test_a_named_factory_draws_the_same_graph_as_its_candidate(fresh_state_client):
    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="graph me")
    named = _graph(fresh_state_client, factory="graph me").json()
    unnamed = _graph(fresh_state_client, candidate=row["selector"], token=body["token"]).json()
    assert named["edges"] == unnamed["edges"]
    groups = [[n for n in g["nodes"] if n["kind"] == "group"] for g in (named, unnamed)]
    assert groups[0] == groups[1]
    inputs = [n["detail"] for n in named["nodes"] if n["kind"] == "input"]
    assert all(d == "enters the factory" for d in inputs)


def test_the_graph_route_refuses_what_it_cannot_answer(fresh_state_client):
    _body, row = _first(fresh_state_client)
    assert _graph(fresh_state_client).status_code == 400
    assert _graph(fresh_state_client, factory="nope").status_code == 404
    assert _graph(fresh_state_client, candidate=row["selector"]).status_code == 400
    stale = _graph(fresh_state_client, candidate=row["selector"], token="sav:000000000000")
    assert stale.status_code == 409


# ------------------------------------------------------------------ styles


def test_short_is_the_default_and_is_lowercase_with_no_region(fresh_state_client):
    body = fresh_state_client.get("/api/factories/candidates").json()
    assert body["style"] == naming.DEFAULT_STYLE == "short"
    for row in body["candidates"]:
        name = row["suggested_name"]
        assert name == name.lower() and " factory" in name
        assert not row["region"] or row["region"].lower() not in name


def test_product_region_names_the_region(fresh_state_client):
    body = _detect(fresh_state_client, style="product, region", **EVERYTHING)
    for row in body["candidates"]:
        if row["region"]:
            assert f", {row['region']}" in row["suggested_name"]


def test_a_suggestion_steps_past_a_taken_name_in_either_style():
    pr = "product, region"
    assert naming.suggest("Wire", "Spire Coast", [], pr) == "Wire, Spire Coast"
    assert naming.suggest("Wire", "Spire Coast", ["wire, spire coast"], pr) == (
        "Wire 2, Spire Coast"
    )
    assert naming.suggest("Wire", None, ["Wire", "Wire 2"], pr) == "Wire 3"
    assert naming.suggest("Iron Ingot", "Grass Fields", []) == "iron ingot factory"
    assert naming.suggest("Wire", None, ["Wire Factory"]) == "wire factory 2"
    assert naming.suggest("Wire", None, ["wire factory", "wire-factory-2"]) == "wire factory 3"
    with pytest.raises(ValueError):
        naming.suggest("Wire", None, [], "fancy")


# ------------------------------------------------------------------ naming


def test_naming_a_candidate_writes_the_store_and_drops_it_from_detection(
    fresh_state_client, store_dir
):
    body, row = _first(fresh_state_client)
    reply = _name(fresh_state_client, body, row)
    assert reply.status_code == 200, reply.text
    assert reply.json()["machines"] == row["machines"]
    assert reply.json()["version"] == 1
    stored = Path(reply.json()["stored_in"])
    assert stored.parent == store_dir
    assert [x["name"] for x in _stored(store_dir)["labels"]] == [row["suggested_name"]]

    after = _detect(fresh_state_client)
    assert after["named"] == 1 and after["version"] == 1
    assert row["index"] not in [r["index"] for r in after["candidates"]]
    health = fresh_state_client.get("/api/factories/health").json()
    assert [r["name"] for r in health["factories"]] == [row["suggested_name"]]


def test_the_page_and_the_tool_write_the_same_label(
    fresh_state_client, store_dir, projection, game, monkeypatch
):
    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="steel factory")
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


def test_a_taken_name_is_refused_rather_than_re_anchored(fresh_state_client, store_dir):
    body, row = _first(fresh_state_client)
    assert _name(fresh_state_client, body, row, name="Steel").status_code == 200
    second = body["candidates"][1]
    reply = _name(fresh_state_client, body, second, name="steel", version=1)
    assert reply.status_code == 409
    assert "already has a factory" in reply.json()["error"]
    assert _flags(reply) == {"stale": False, "name_taken": True, "pin": False}
    assert _name(fresh_state_client, body, second, name="   ", version=1).status_code == 400
    stored = _stored(store_dir)["labels"]
    assert len(stored) == 1 and len(stored[0]["anchors"]) == row["machines"]


def test_a_name_for_another_save_is_refused(fresh_state_client, store_dir):
    body, row = _first(fresh_state_client)
    reply = _name(fresh_state_client, dict(body, token="sav:000000000000"), row)
    assert reply.status_code == 409
    assert _flags(reply) == {"stale": False, "name_taken": False, "pin": True}
    assert not list(store_dir.glob("*.json"))


def test_a_write_against_a_stale_version_is_refused(fresh_state_client, store_dir):
    body, row = _first(fresh_state_client)
    assert _name(fresh_state_client, body, row, name="first").status_code == 200
    reply = _name(fresh_state_client, body, body["candidates"][1], name="second")
    assert reply.status_code == 409
    assert _flags(reply) == {"stale": True, "name_taken": False, "pin": False}
    assert [x["name"] for x in _stored(store_dir)["labels"]] == ["first"]


def test_an_unknown_proposal_is_a_404(fresh_state_client):
    body, row = _first(fresh_state_client)
    assert _name(fresh_state_client, body, dict(row, index=999), name="x").status_code == 404


def test_the_label_routes_declare_every_refusal_they_send(fresh_state_client):
    paths = fresh_state_client.get("/openapi.json").json()["paths"]
    for path, method in (
        ("/api/labels", "post"),
        ("/api/labels/{name}", "patch"),
        ("/api/labels/{name}", "delete"),
    ):
        declared = paths[path][method]["responses"]
        assert {"400", "404", "409"} <= set(declared), (method, path)


def test_forget_deletes_one_label_by_its_exact_name(fresh_state_client, store_dir):
    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="Iron Works")
    version = _version(fresh_state_client)
    assert (
        fresh_state_client.delete(
            f"/api/labels/Iron?version={version}", headers=PAGE_ORIGIN
        ).status_code
        == 404
    )
    reply = fresh_state_client.delete(
        f"/api/labels/Iron Works?version={version}", headers=PAGE_ORIGIN
    )
    assert reply.status_code == 200
    assert reply.json()["machines"] == row["machines"]
    assert _stored(store_dir)["labels"] == []


# ------------------------------------------------------------------ rename


def _rename(client, name, to, version):
    return client.patch(
        f"/api/labels/{name}", json={"to": to, "version": version}, headers=PAGE_ORIGIN
    )


def _plan_for(factory: str) -> None:
    log = PlanLog("X2faPVKjX06VaRzClNv5KQ")
    chat = Actor("chat")
    log.create(
        "north plan", {"target_item": "Steel Pipe"}, plan_id="p1", factory=factory, actor=chat
    )
    log.create("other plan", {"target_item": "Wire"}, plan_id="p2", factory="elsewhere", actor=chat)


def _plan_factories() -> dict[str, str]:
    return {s.name: s.factory for s in PlanLog("X2faPVKjX06VaRzClNv5KQ").heads()}


def test_rename_keeps_the_machines_and_moves_the_plans(fresh_state_client, store_dir):
    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="old name")
    _plan_for("old name")
    reply = _rename(fresh_state_client, "old name", "  new name ", _version(fresh_state_client))
    assert reply.status_code == 200, reply.text
    assert reply.json()["name"] == "new name" and reply.json()["plans"] == ["north plan"]
    label = _stored(store_dir)["labels"][0]
    assert label["name"] == "new name" and len(label["anchors"]) == row["machines"]
    assert _plan_factories() == {"north plan": "new name", "other plan": "elsewhere"}
    log = PlanLog("X2faPVKjX06VaRzClNv5KQ")
    moved = log.commits(log.find("north plan").key)[-1]
    assert moved.actor.kind == "page", "a page rename must say the page moved the plan"


def test_rename_refuses_a_taken_blank_or_missing_name(fresh_state_client):
    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="alpha")
    _name(fresh_state_client, body, body["candidates"][1], name="beta", version=1)
    version = _version(fresh_state_client)
    taken = _rename(fresh_state_client, "alpha", "BETA", version)
    assert taken.status_code == 409 and _flags(taken)["name_taken"]
    assert _rename(fresh_state_client, "alpha", "  ", version).status_code == 400
    assert _rename(fresh_state_client, "gamma", "delta", version).status_code == 404
    stale = _rename(fresh_state_client, "alpha", "delta", version - 1)
    assert stale.status_code == 409 and _flags(stale)["stale"]


def test_a_name_with_a_slash_or_over_sixty_characters_is_refused(fresh_state_client, store_dir):
    body, row = _first(fresh_state_client)
    for bad in ("iron/steel", "x" * 61):
        reply = _name(fresh_state_client, body, row, name=bad)
        assert reply.status_code == 400, bad
    assert not list(store_dir.glob("*.json"))
    assert _name(fresh_state_client, body, row, name="x" * 60).status_code == 200
    version = _version(fresh_state_client)
    assert _rename(fresh_state_client, "x" * 60, "a/b", version).status_code == 400
    assert _rename(fresh_state_client, "x" * 60, "y" * 61, version).status_code == 400


def test_a_stored_name_with_a_slash_can_still_be_renamed_and_forgotten(
    fresh_state_client, store_dir
):
    """A label named before '/' was refused is still addressable: the name is the rest of the
    path, so its encoded slash no longer splits it into a route that does not exist."""
    from satisfactory_mcp.domain.factories.labels import LabelStore

    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="plain")
    with LabelStore.editing("X2faPVKjX06VaRzClNv5KQ") as store:
        store.labels[0].name = "iron/steel"
    renamed = _rename(
        fresh_state_client, "iron%2Fsteel", "iron and steel", _version(fresh_state_client)
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["was"] == "iron/steel"
    with LabelStore.editing("X2faPVKjX06VaRzClNv5KQ") as store:
        store.labels[0].name = "a/b/c"
    gone = fresh_state_client.delete(
        f"/api/labels/a%2Fb%2Fc?version={_version(fresh_state_client)}", headers=PAGE_ORIGIN
    )
    assert gone.status_code == 200, gone.text
    assert _stored(store_dir)["labels"] == []


def test_a_plan_that_cannot_follow_a_rename_is_reported_and_the_rename_stands(
    fresh_state_client, store_dir, monkeypatch
):
    from satisfactory_mcp.core.filelock import LockTimeout

    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="old name")
    log = PlanLog("X2faPVKjX06VaRzClNv5KQ")
    for plan in ("first plan", "second plan"):
        log.create(plan, {"target_item": "Wire"}, factory="old name", actor=Actor("chat"))
    real = PlanLog.push_at_head
    calls = []

    def flaky(self, key, ops, **kw):
        calls.append(key)
        if len(calls) == 2:
            raise LockTimeout("plans held elsewhere")
        return real(self, key, ops, **kw)

    monkeypatch.setattr(PlanLog, "push_at_head", flaky)
    reply = _rename(fresh_state_client, "old name", "new name", _version(fresh_state_client))
    assert reply.status_code == 200, reply.text
    out = reply.json()
    assert out["plans"] == ["first plan"] and out["plans_stuck"] == ["second plan"]
    assert "held elsewhere" in out["stuck_reason"]
    assert _stored(store_dir)["labels"][0]["name"] == "new name"
    assert _plan_factories() == {"first plan": "new name", "second plan": "old name"}


def test_the_page_and_the_tool_rename_to_the_same_files(
    fresh_state_client, store_dir, projection, game, monkeypatch
):
    from satisfactory_mcp.interfaces.mcp.tools import factories as tools

    monkeypatch.setattr(
        tools, "_state", lambda *a, **k: WorldState(projection=projection, game=game)
    )
    _body, row = _first(fresh_state_client)
    results = []
    for via in ("page", "tool"):
        for f in store_dir.glob("*.json"):
            f.unlink()
        shutil.rmtree(PlanLog.dir_for("X2faPVKjX06VaRzClNv5KQ"), ignore_errors=True)
        tools.name_factory("old name", [row["selector"]])
        _plan_for("old name")
        if via == "page":
            assert (
                _rename(
                    fresh_state_client, "old name", "new name", _version(fresh_state_client)
                ).status_code
                == 200
            )
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


def test_a_rename_is_journalled_with_was_and_to(
    fresh_state_client, store_dir, projection, game, monkeypatch
):
    from satisfactory_mcp.domain.session import journal
    from satisfactory_mcp.interfaces.mcp.tools import factories as tools

    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})
    journal.set_writer("web")
    monkeypatch.setattr(
        tools, "_state", lambda *a, **k: WorldState(projection=projection, game=game)
    )
    body, row = _first(fresh_state_client)
    _name(fresh_state_client, body, row, name="old name")
    assert (
        _rename(
            fresh_state_client, "old name", "mid name", _version(fresh_state_client)
        ).status_code
        == 200
    )
    assert "renamed factory" in tools.rename_factory("mid name", "new name")
    entries = [e for e in journal.read("X2faPVKjX06VaRzClNv5KQ") if e["kind"] == "label.rename"]
    assert [(e["actor"]["kind"], e["args"]) for e in entries] == [
        ("page", {"was": "old name", "to": "mid name"}),
        ("chat", {"was": "mid name", "to": "new name"}),
    ]
