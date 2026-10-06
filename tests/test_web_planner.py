"""The planner routes: plans read at a version, pushed by ops or args, undone, solved, focused.

docs/planner_slice_contract.md §9 and §11 is what these pin. Every write lands in temporary
plans, activity and ui directories; the fixture world is read and never written.
"""

from __future__ import annotations

import json
import os

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.stored.planlog import KINDS, Actor, PlanLog
from satisfactory_mcp.domain.session import focus, journal
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app

ORIGIN = {"origin": "http://testserver"}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
CHAT = Actor("chat", "claude-code", 4242)
RIP = "Reinforced Iron Plate"
HMF_ARGS = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name in ("plans_dir", "labels_dir", "activity_dir", "ui_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    monkeypatch.setattr(journal, "_writer", "")
    return tmp_path


@pytest.fixture
def client(dirs, projection, game):
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def _create(client, name="rip 5", args=None):
    reply = client.post("/api/plans", json={"name": name, "args": args or HMF_ARGS}, headers=ORIGIN)
    assert reply.status_code == 201, reply.text
    return reply.json()


def _push(client, key, base_rev, *ops):
    return client.post(
        f"/api/plans/{key}/ops", json={"base_rev": base_rev, "ops": list(ops)}, headers=ORIGIN
    )


def _rate(value):
    return {"op": "put", "field": "export_minimums", "item": RIP, "value": value}


# ------------------------------------------------------------------ create and read


def test_a_created_plan_is_v1_with_every_field_and_a_stamp(client):
    body = _create(client)
    state = body["state"]
    assert body["rev"] == 1 and state["rev"] == 1 and state["head"] == 1
    assert list(state["args"]) == list(KINDS)
    assert state["args"]["export_minimums"] == {RIP: 5.0}
    assert state["args"]["allow_sinks"] is True and state["args"]["water_extractors"] is None
    assert state["plan_id"], "a page-created plan must be stamped, or list_plans says it moved"
    assert state["siting"] is None
    assert state["text"] == "v1 page: created"
    commit = PlanLog(WORLD).commits(body["key"])[0]
    assert commit.actor.kind == "page" and commit.actor.pid == os.getpid()


def test_a_taken_name_is_a_409_that_says_so(client):
    _create(client, "north")
    reply = client.post("/api/plans", json={"name": "NORTH", "args": {}}, headers=ORIGIN)
    assert reply.status_code == 409
    assert reply.json()["name_taken"] is True


def test_invalid_args_are_a_400_and_nothing_is_created(client):
    reply = client.post(
        "/api/plans", json={"name": "x", "args": {"objective": "most"}}, headers=ORIGIN
    )
    assert reply.status_code == 400
    assert PlanLog(WORLD).keys() == []


def test_a_plan_reads_at_any_version_and_404s_past_its_head(client):
    key = _create(client)["key"]
    assert _push(client, key, 1, _rate(15)).status_code == 200
    old = client.get(f"/api/plans/{key}?rev=1").json()
    head = client.get(f"/api/plans/{key}").json()
    assert old["args"]["export_minimums"] == {RIP: 5.0} and old["head"] == 2
    assert head["args"]["export_minimums"] == {RIP: 15.0} and head["rev"] == 2
    assert head["text"] == f"v2 page: rate {RIP} 5→15/min"
    assert client.get(f"/api/plans/{key}?rev=3").status_code == 404
    missing = client.get("/api/plans/0000beef")
    assert missing.status_code == 404
    assert missing.json() == {"error": "no plan “0000beef” in this world"}
    assert client.get("/api/plans/..").status_code == 404
    ops = client.get("/api/plans/0000beef/ops")
    assert ops.status_code == 404 and ops.json() == {"error": "no plan “0000beef” in this world"}


def test_the_ops_route_lists_commits_after_since(client):
    key = _create(client)["key"]
    _push(client, key, 1, {"op": "set", "field": "sloops", "value": 2})
    body = client.get(f"/api/plans/{key}/ops?since=1").json()
    assert body["head"] == 2 and [c["rev"] for c in body["commits"]] == [2]
    commit = body["commits"][0]
    assert commit["actor"]["display"] == "page"
    assert commit["ops"][0] == {"op": "set", "field": "sloops", "value": 2, "was": 0}
    assert commit["text"] == "v2 page: sloops 0→2"


# ------------------------------------------------------------------ push and merge


def test_a_page_edit_over_a_chat_edit_to_another_key_merges(client):
    key = _create(client)["key"]
    PlanLog(WORLD).push(key, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=CHAT)
    reply = _push(client, key, 1, _rate(15))
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["rev"] == 3 and body["merged_over"] == [2]
    assert [c["actor"]["display"] for c in body["others"]] == ["Claude Code"]
    assert body["state"]["args"]["sloops"] == 4
    assert body["state"]["args"]["export_minimums"] == {RIP: 15.0}
    assert "merged onto v2" in body["text"]


def test_the_same_key_is_a_409_with_the_head_and_nothing_applied(client, monkeypatch):
    journal.set_writer("web")
    key = _create(client)["key"]
    PlanLog(WORLD).push(key, 1, [_rate(15)], actor=CHAT)
    reply = _push(client, key, 1, _rate(12))
    assert reply.status_code == 409
    body = reply.json()
    assert body["outdated"] is True and body["head"] == 2 and body["base_rev"] == 1
    assert body["state"]["args"]["export_minimums"] == {RIP: 15.0}
    [conflict] = body["conflicts"]
    assert conflict["theirs_actor"]["display"] == "Claude Code" and conflict["theirs_rev"] == 2
    assert conflict["text"] == f"rate {RIP}: you 12, Claude Code set 15 in v2"
    assert [c["rev"] for c in body["since"]] == [2]
    assert PlanLog(WORLD).head_rev(key) == 2
    [entry] = journal.read(WORLD)
    assert entry["kind"] == "plan.rejected" and entry["plan"] == key and entry["rev"] == 2
    assert entry["actor"]["kind"] == "page"


def test_a_no_op_push_appends_nothing(client):
    key = _create(client)["key"]
    body = _push(client, key, 1, _rate(5)).json()
    assert body["noop"] is True and body["rev"] == 1
    assert PlanLog(WORLD).head_rev(key) == 1


def test_a_bad_op_is_a_400_and_a_forgotten_plan_a_410(client):
    key = _create(client)["key"]
    assert _push(client, key, 1, {"op": "set", "field": "sloops", "value": -1}).status_code == 400
    assert _push(client, key, 5, _rate(1)).status_code == 400
    PlanLog(WORLD).push(key, 1, [{"op": "forget"}], actor=CHAT)
    assert _push(client, key, 2, _rate(1)).status_code == 410


def test_a_write_from_another_origin_is_refused_by_the_guard(client):
    key = _create(client)["key"]
    reply = client.post(
        f"/api/plans/{key}/ops",
        json={"base_rev": 1, "ops": [_rate(1)]},
        headers={"origin": "http://evil.example"},
    )
    assert reply.status_code == 403
    assert PlanLog(WORLD).head_rev(key) == 1


def test_args_from_a_chat_solve_apply_as_one_commit_naming_the_entry(client):
    key = _create(client)["key"]
    args = {**HMF_ARGS, "export_minimums": {RIP: 20}, "banned": ["Recipe_Alternate_Screw_2_C"]}
    reply = client.post(
        f"/api/plans/{key}/args",
        json={"base_rev": 1, "args": args, "from_entry": "chat-1:7"},
        headers=ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    commit = PlanLog(WORLD).commits(key, since=1)[0]
    assert commit.note == "applied chat solve chat-1:7"
    assert reply.json()["state"]["args"]["banned"] == ["Recipe_Alternate_Screw_2_C"]


# ------------------------------------------------------------------ undo


def test_undo_is_an_inverse_commit_and_twice_is_already_undone(client):
    key = _create(client)["key"]
    _push(client, key, 1, _rate(15))
    reply = client.post(f"/api/plans/{key}/undo", json={"base_rev": 2, "rev": 2}, headers=ORIGIN)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["rev"] == 3 and body["state"]["args"]["export_minimums"] == {RIP: 5.0}
    assert body["state"]["text"].startswith("v3 page: undo v2")
    again = client.post(f"/api/plans/{key}/undo", json={"base_rev": 3, "rev": 2}, headers=ORIGIN)
    assert again.status_code == 409
    assert again.json() == {"error": "v2 was already undone by v3", "already_undone": True, "by": 3}
    redo = client.post(f"/api/plans/{key}/undo", json={"base_rev": 3, "rev": 3}, headers=ORIGIN)
    assert redo.json()["state"]["args"]["export_minimums"] == {RIP: 15.0}


def test_undoing_what_chat_changed_again_since_is_outdated(client):
    key = _create(client)["key"]
    _push(client, key, 1, _rate(15))
    PlanLog(WORLD).push(key, 2, [_rate(30)], actor=CHAT)
    reply = client.post(f"/api/plans/{key}/undo", json={"base_rev": 3, "rev": 2}, headers=ORIGIN)
    assert reply.status_code == 409 and reply.json()["outdated"] is True


# ------------------------------------------------------------------ solve


def test_a_stored_version_and_its_args_solve_to_the_same_answer(client):
    created = _create(client)
    by_key = client.post("/api/plan/solve", json={"key": created["key"]}, headers=ORIGIN)
    by_args = client.post("/api/plan/solve", json={"args": HMF_ARGS}, headers=ORIGIN)
    assert by_key.status_code == 200, by_key.text
    a, b = by_key.json(), by_args.json()
    assert a == b
    assert a["feasible"] is True and a["plan_id"] == created["state"]["plan_id"]
    assert a["exports"] == [{"item": RIP, "per_min": 5.0}]
    assert a["mw_net"] == pytest.approx(a["mw_generated"] - a["mw_draw"], abs=0.02)
    assert a["machines"] >= len(a["rows"]) > 0 and a["token"].startswith("sav:")
    keys = [(r["building"], r["recipe"]) for r in a["rows"]]
    assert keys == sorted(keys)
    top = next(r for r in a["rows"] if r["item"] == RIP)
    assert top["outputs"][0] == {"item": RIP, "per_min": 5.0} and top["mw"] < 0


def test_a_required_recipe_that_is_not_a_recipe_is_named_as_a_blocker(client):
    args = {**HMF_ARGS, "required": ["Recipe_NoSuchThing_C"]}
    body = client.post("/api/plan/solve", json={"args": args}, headers=ORIGIN).json()
    assert any("Recipe_NoSuchThing_C" in b for b in body["blockers"])


def test_a_required_recipe_in_force_is_flagged_on_its_row(client, game):
    args = {**HMF_ARGS, "required": ["Recipe_IronPlate_C"]}
    body = client.post("/api/plan/solve", json={"args": args}, headers=ORIGIN).json()
    if not body["feasible"]:
        pytest.skip("the fixture save cannot run this plan")
    rows = [r for r in body["rows"] if r["recipe_id"] == "Recipe_IronPlate_C"]
    assert rows and all(r["required"] for r in rows)


def test_solve_takes_exactly_one_of_args_and_key(client):
    for payload in ({}, {"args": HMF_ARGS, "key": "0000beef"}):
        assert client.post("/api/plan/solve", json=payload, headers=ORIGIN).status_code == 400
    gone = client.post("/api/plan/solve", json={"key": "0000beef"}, headers=ORIGIN)
    assert gone.status_code == 404 and gone.json() == {"error": "no plan “0000beef” in this world"}
    bad = client.post("/api/plan/solve", json={"args": {"sloops": "x"}}, headers=ORIGIN)
    assert bad.status_code == 400


def test_a_plan_carries_display_names_for_the_ids_it_holds(client, game):
    args = {
        **HMF_ARGS,
        "banned": ["Recipe_Alternate_Screw_C", "Recycled"],
        "sources": ["node:BP_ResourceNode26_99", "region:Spire Coast"],
    }
    names = _create(client, args=args)["state"]["names"]
    assert names == {
        "Recipe_Alternate_Screw_C": game.recipes["Recipe_Alternate_Screw_C"].name,
        "node:BP_ResourceNode26_99": "Crude Oil",
    }


def test_an_infeasible_solve_names_its_cause_in_player_words(client):
    args = {**HMF_ARGS, "export_minimums": {RIP: 10_000_000}}
    body = client.post("/api/plan/solve", json={"args": args}, headers=ORIGIN).json()
    assert body["feasible"] is False and body["cause"]
    assert "HiGHS" not in body["cause"] and "INFEASIBLE" not in body["cause"]
    typo = client.post(
        "/api/plan/solve", json={"args": {**HMF_ARGS, "exports": ["Plait"]}}, headers=ORIGIN
    ).json()
    assert typo["cause"] == "an export is not an item: no item matches 'Plait'"


def test_plans_from_a_newer_schema_are_a_503_that_names_no_path(client, dirs):
    key = _create(client)["key"]
    marker = PlanLog.dir_for(WORLD) / "migrated.json"
    marker.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    for reply in (client.get("/api/plans"), client.get(f"/api/plans/{key}")):
        assert reply.status_code == 503, reply.text
        body = reply.json()
        assert body["newer_schema"] is True and "newer version" in body["error"]
        assert str(dirs) not in body["error"] and "migrated.json" not in body["error"]


def test_labels_from_a_newer_schema_are_a_503_that_says_so(client):
    path = config.labels_dir() / f"{WORLD}.json"
    path.write_text(json.dumps({"schema": 99, "labels": []}), encoding="utf-8")
    reply = client.get("/api/factories")
    assert reply.status_code == 503, reply.text
    assert reply.json()["error"].startswith("the factory names were saved by a newer version")


# ------------------------------------------------------------------ focus and activity


def test_focus_is_written_with_a_heartbeat_for_ui_context_to_read(client):
    reply = client.put(
        "/api/ui/focus",
        json={
            "view": "planner",
            "dash": "planner/0000beef",
            "plan": "0000beef",
            "rev": 3,
            "tab": "workbench",
            "selection": {"kind": "process", "label": "Constructor · Iron Plate", "ref": "R"},
            "follow": "toasts",
            "sav": "sav:000000000000",
        },
        headers=ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    stored = focus.read(WORLD)
    assert stored["heartbeat"] == reply.json()["heartbeat"]
    assert stored["plan"] == "0000beef" and stored["follow"] == "toasts"
    assert focus.is_open(stored)
    bad = client.put("/api/ui/focus", json={"view": "kitchen"}, headers=ORIGIN)
    assert bad.status_code == 400


def test_activity_merges_commits_and_journal_entries_by_time(client):
    journal.set_writer("web")
    key = _create(client)["key"]
    journal.append(WORLD, "plan.solve", actor=CHAT, args={"sloops": 2}, text="solved it")
    _push(client, key, 1, _rate(15))
    body = client.get("/api/activity").json()
    kinds = [(e["source"], e["kind"]) for e in body["entries"]]
    assert kinds == [("plan", "commit"), ("journal", "plan.solve"), ("plan", "commit")]
    solve = body["entries"][1]
    assert solve["actor"]["display"] == "Claude Code" and solve["args"] == {"sloops": 2}
    assert body["entries"][2]["name"] == "rip 5" and body["entries"][2]["rev"] == 2
    since = body["entries"][1]["ts"]
    later = client.get(f"/api/activity?since={since}").json()["entries"]
    assert [e["rev"] for e in later] == [2]
    assert len(client.get("/api/activity?limit=1").json()["entries"]) == 1


def test_repeat_looks_at_one_item_collapse_to_the_newest(client):
    journal.set_writer("web")
    key = _create(client)["key"]
    look = {"view": "alternates", "item": "Desc_IronPlateReinforced_C"}
    for n in range(60):
        journal.append(WORLD, "plan.view", actor=CHAT, plan=key, args=look, text=f"look {n}")
    other = {**look, "item": "Desc_IronPlate_C"}
    journal.append(WORLD, "plan.view", actor=CHAT, plan=key, args=other, text="plate")
    rows = client.get("/api/activity?limit=50").json()["entries"]
    assert [(r["kind"], r["text"]) for r in rows] == [
        ("commit", rows[0]["text"]),
        ("plan.view", "look 59"),
        ("plan.view", "plate"),
    ]
    assert [r["count"] for r in rows] == [1, 60, 1]


def test_a_run_of_finder_calls_is_one_row_with_a_count_and_the_latest_params(client):
    journal.set_writer("web")
    key = _create(client)["key"]
    for n in range(70):
        params = {"resource": "Desc_OreCopper_C", "at": "hub", "within_m": str(500 + n)}
        journal.append(
            WORLD,
            "world.find",
            actor=CHAT,
            args={"view": "rank", "params": params},
            text=f"find {n}",
        )
    journal.append(
        WORLD, "world.find", actor=Actor("chat", "other", 7), args={"view": ""}, text="elsewhere"
    )
    rows = client.get("/api/activity?limit=50").json()["entries"]
    assert [(r["kind"], r["text"], r["count"]) for r in rows] == [
        ("commit", rows[0]["text"], 1),
        ("world.find", "find 69", 70),
        ("world.find", "elsewhere", 1),
    ]
    assert rows[1]["args"]["params"]["within_m"] == "569"
    assert rows[0]["plan"] == key


# ------------------------------------------------------------------ graph, deltas, alternates


def test_the_solve_carries_the_production_graph_with_ids_and_depths(client, game):
    body = client.post("/api/plan/solve", json={"args": HMF_ARGS}, headers=ORIGIN).json()
    rows, graph = body["rows"], body["graph"]
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == len(ids)
    assert all(r["id"] == (r["recipe_id"] or r["id"]) for r in rows)
    assert all(r["id"].startswith("label:") for r in rows if r["recipe_id"] is None)
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds == sorted(kinds, key=["input", "process", "export"].index)
    procs = [n for n in graph["nodes"] if n["kind"] == "process"]
    assert [n["id"] for n in procs] == ids and [n["row"] for n in procs] == ids
    assert [n["rank"] for n in procs] == [r["depth"] + 1 for r in rows]
    [export] = [n for n in graph["nodes"] if n["kind"] == "export"]
    assert export["id"] == f"ex:{RIP}" and export["item"] == "Desc_IronPlateReinforced_C"
    assert export["rank"] == max(n["rank"] for n in procs) + 1
    assert export["detail"] == "exported 5/min"
    top = next(n for n in procs if n["item"] == "Desc_IronPlateReinforced_C")
    row = next(r for r in rows if r["id"] == top["id"])
    assert top["detail"].startswith(f"{row['building']} ×{row['machines']} · ")
    assert top["detail"].endswith("%") and " MW · " in top["detail"]
    names = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in names and e["target"] in names for e in graph["edges"])
    into = sum(e["per_min"] for e in graph["edges"] if e["target"] == export["id"])
    assert into == pytest.approx(5.0, abs=1e-3)
    miners = [r for r in rows if r["id"].startswith("label:")]
    assert miners and all(r["depth"] == 0 for r in miners)
    assert len(json.dumps(graph)) < 8000


def test_an_infeasible_solve_has_an_empty_graph(client):
    args = {**HMF_ARGS, "export_minimums": {RIP: 10_000_000}}
    body = client.post("/api/plan/solve", json={"args": args}, headers=ORIGIN).json()
    assert body["feasible"] is False and body["graph"] == {"nodes": [], "edges": []}


def test_the_delta_names_rows_added_changed_and_removed(client):
    key = _create(client)["key"]
    alt = "Recipe_Alternate_ReinforcedIronPlate_1_C"
    _push(client, key, 1, {"op": "add", "field": "banned", "member": alt}, _rate(10))
    body = client.get(f"/api/plan/delta?key={key}&from_rev=1").json()
    changes = {r["id"]: r for r in body["rows"]}
    assert changes[alt]["change"] == "removed" and changes[alt]["machines_after"] == 0
    added = [r for r in body["rows"] if r["change"] == "added"]
    assert added and all(r["machines_before"] == 0 for r in added)
    order = [r["change"] for r in body["rows"]]
    assert order == sorted(order, key=["added", "changed", "removed"].index)
    same = client.get(f"/api/plan/delta?key={key}&from_rev=2&to_rev=2").json()
    assert same["rows"] == []


def test_alternates_for_a_stored_plan(client):
    key = _create(client)["key"]
    reply = client.post(
        "/api/plan/alternates",
        json={"key": key, "item": "Desc_IronPlateReinforced_C"},
        headers=ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["key"] == key and body["rev"] == 1 and body["name"] == RIP
    assert body["text"].startswith(f"recipes for {RIP} in “rip 5” v1: ")
    assert {o["status"] for o in body["options"]} >= {"in use", "available"}
    hidden = client.post(
        "/api/plan/alternates?spoilers=0",
        json={"key": key, "item": RIP},
        headers=ORIGIN,
    ).json()
    assert all(o["status"] != "locked" for o in hidden["options"])
    assert hidden["hidden"] == len(body["options"]) - len(hidden["options"])
    by_rev = client.post(
        "/api/plan/alternates", json={"key": key, "rev": 1, "item": RIP}, headers=ORIGIN
    )
    assert by_rev.status_code == 200


def _required(member):
    return {"op": "add", "field": "required", "member": member}


def test_a_drawer_require_replaces_every_required_recipe_for_the_item_at_the_head(client):
    bolted = "Recipe_Alternate_ReinforcedIronPlate_1_C"
    stitched = "Recipe_Alternate_ReinforcedIronPlate_2_C"
    key = _create(client)["key"]
    assert _push(client, key, 1, _required(bolted)).status_code == 200
    reply = client.post(
        f"/api/plans/{key}/ops",
        json={
            "base_rev": 2,
            "ops": [_required(stitched)],
            "require_item": "Desc_IronPlateReinforced_C",
        },
        headers=ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["state"]["args"]["required"] == [stitched]
    plain = _push(client, key, 3, _required(bolted))
    assert sorted(plain.json()["state"]["args"]["required"]) == sorted([bolted, stitched])


def test_a_drawer_require_refuses_when_another_was_required_since_its_base(client):
    bolted = "Recipe_Alternate_ReinforcedIronPlate_1_C"
    stitched = "Recipe_Alternate_ReinforcedIronPlate_2_C"
    key = _create(client)["key"]
    assert _push(client, key, 1, _required(bolted)).status_code == 200
    reply = client.post(
        f"/api/plans/{key}/ops",
        json={
            "base_rev": 1,
            "ops": [_required(stitched)],
            "require_item": "Desc_IronPlateReinforced_C",
        },
        headers=ORIGIN,
    )
    assert reply.status_code == 409 and reply.json()["outdated"] is True
    assert client.get(f"/api/plans/{key}").json()["args"]["required"] == [bolted]


def test_alternates_refuse_unknown_plans_revs_and_items(client):
    key = _create(client)["key"]
    for payload in (
        {"key": "0000beef", "item": RIP},
        {"key": "..", "item": RIP},
        {"key": key, "rev": 9, "item": RIP},
        {"key": key, "item": "Plait of nothing"},
    ):
        reply = client.post("/api/plan/alternates", json=payload, headers=ORIGIN)
        assert reply.status_code == 404, payload
    missing = client.post("/api/plan/alternates", json={"key": key}, headers=ORIGIN)
    assert missing.status_code in (400, 422)


# ------------------------------------------------------------------ the schema


def test_the_conflict_bodies_reach_the_published_schema(client):
    schema = client.get("/openapi.json").json()
    push = schema["paths"]["/api/plans/{key}/ops"]["post"]
    assert push["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OutdatedResponse"
    }
    create = schema["paths"]["/api/plans"]["post"]
    assert "201" in create["responses"] and "409" in create["responses"]
    for name in ("PushedResponse", "PlanStateBody", "SolveResponse", "ActivityResponse"):
        assert name in schema["components"]["schemas"], name
    ops = [
        op["operationId"]
        for path in schema["paths"].values()
        for op in path.values()
        if op["operationId"].split("_api_")[0]
        in {"create_plan", "plan_state", "plan_ops", "push_ops", "push_args", "undo_rev"}
    ]
    assert len(ops) == 6
    json.dumps(schema)


# ------------------------------------------------------------------ track (P4)


def _headroom(value):
    return {"op": "set", "field": "headroom_mw", "value": value}


def test_track_is_one_solve_of_the_head_with_rows_and_stages(client):
    key = _create(client)["key"]
    pushed = _push(client, key, 1, _headroom(2000))
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["state"]["headroom_mw"] == 2000.0
    assert pushed.json()["text"] == 'plan "rip 5" is now v2'
    assert client.get(f"/api/plans/{key}").json()["headroom_mw"] == 2000.0
    reply = client.get(f"/api/plan/track?key={key}")
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["key"] == key and body["rev"] == 2 and body["feasible"] is True
    assert (
        body["headroom_mw"] == 2000.0 and body["startup"]["headroom_source"] == "stored on the plan"
    )
    assert body["rows"] and body["stages"] and body["count"] == len(body["stages"])
    assert body["rows"][0]["id"].startswith("job:")
    old = client.get(f"/api/plan/track?key={key}&rev=1").json()
    assert old["rev"] == 1 and old["headroom_mw"] is None
    assert old["startup"]["headroom_source"] == "measured from the save"
    plate = client.get(f"/api/plan/track?key={key}&rev=1&headroom=nameplate").json()
    assert plate["startup"]["headroom_source"] == "nameplate from the save"
    assert client.get(f"/api/plan/track?key={key}&headroom=given").status_code == 422
    assert client.get(f"/api/plan/track?key={key}&biomass=include").status_code == 200
    assert (
        client.get(f"/api/plan/track?key={key}&biomass=include").json()["power"]["biomass"] is True
    )
    assert client.get(f"/api/plan/track?key={key}&biomass=true").status_code == 422
    assert client.get("/api/plan/feeders?biomass=true").status_code == 422


def test_track_refuses_unknown_plans_and_revs(client):
    key = _create(client)["key"]
    for query in ("key=0000beef", "key=..", f"key={key}&rev=4"):
        assert client.get(f"/api/plan/track?{query}").status_code == 404, query


def test_track_is_a_400_when_the_solve_refuses(client, monkeypatch):
    from satisfactory_mcp.domain.planning.progress import track

    key = _create(client)["key"]

    def refuse(*_a, **_k):
        raise ValueError("no such item")

    monkeypatch.setattr(track, "track_view", refuse)
    reply = client.get(f"/api/plan/track?key={key}")
    assert reply.status_code == 400 and reply.json() == {"error": "no such item"}


def test_track_of_an_infeasible_plan_or_an_empty_scope_is_a_200(client):
    key = _create(client, args={**HMF_ARGS, "export_minimums": {RIP: 1e7}})["key"]
    body = client.get(f"/api/plan/track?key={key}").json()
    assert body["feasible"] is False and body["cause"] and body["rows"] == []
    other = _create(client, name="scoped")["key"]
    _push(client, other, 1, {"op": "set", "field": "factory", "value": "nowhere"})
    body = client.get(f"/api/plan/track?key={other}").json()
    assert body["scope"] == "nowhere" and body["scope_error"] == "" and body["rows"]
    assert body["built_at"]["fallback"].startswith("“nowhere” has no machines left")


def test_feeders_answer_on_demand(client):
    reply = client.get("/api/plan/feeders")
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert isinstance(body["feeders"], list) and body["text"]
    assert body["total_mw"] >= 0
    assert all({"name", "instance", "x_m", "y_m", "mw"} <= set(f) for f in body["feeders"])


def test_headroom_is_validated_merged_and_undone_like_any_scalar(client):
    key = _create(client)["key"]
    for bad in (0, -5, 2_000_000, "lots", True):
        assert _push(client, key, 1, _headroom(bad)).status_code == 400, bad
    assert _push(client, key, 1, _headroom(2000)).status_code == 200
    PlanLog(WORLD).push(key, 2, [_headroom(1500)], actor=CHAT)
    clash = _push(client, key, 2, _headroom(3000))
    assert clash.status_code == 409
    assert clash.json()["conflicts"][0]["key"] == "headroom_mw"
    merged = _push(client, key, 2, _rate(15))
    assert merged.status_code == 200 and merged.json()["state"]["headroom_mw"] == 1500.0
    undo = client.post(f"/api/plans/{key}/undo", json={"base_rev": 4, "rev": 3}, headers=ORIGIN)
    assert undo.status_code == 200, undo.text
    assert undo.json()["state"]["headroom_mw"] == 2000.0
    cleared = _push(client, key, 5, _headroom(None))
    assert cleared.json()["state"]["headroom_mw"] is None
    assert cleared.json()["state"]["text"] == "v6 page: startup headroom: save default"


def test_the_track_and_ask_shapes_reach_the_published_schema(client):
    schema = client.get("/openapi.json").json()
    names = schema["components"]["schemas"]
    for name in ("TrackResponse", "TrackRow", "TrackStage", "FeedersResponse", "AskRow"):
        assert name in names, name
    assert "headroom_mw" in names["PlanStateBody"]["properties"]
    drop = schema["paths"]["/api/asks/{n}"]["delete"]
    assert drop["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AskStaleResponse"
    }
    ids = {
        op["operationId"].split("_api_")[0] for p in schema["paths"].values() for op in p.values()
    }
    assert {"plan_track", "plan_feeders", "asks", "create_ask", "drop_ask"} <= ids


def test_the_last_stage_and_the_budget_start_from_the_same_measured_headroom(client):
    key = _create(client)["key"]
    measured = client.get("/api/power/circuits").json()["world"]["measured_headroom_mw"]
    power = client.get(f"/api/plan/track?key={key}").json()["power"]
    assert power["measured_headroom_mw"] == measured
    assert _push(client, key, 1, _headroom(measured)).status_code == 200
    stages = client.get(f"/api/plan/track?key={key}").json()["stages"]
    net = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()["mw_net"]
    assert stages[-1]["available_after"] == pytest.approx(measured + net, abs=0.006)
