"""Planner P2: versions, restore, duplicate, result deltas and list status, domain and routes.

docs/plan_management.md is what these pin. Every write lands in temporary plans, activity and
ui directories; the fixture world is read and never written.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.stored import manage
from satisfactory_mcp.domain.planning.stored.planlog import Actor, NameTaken, PlanLog
from satisfactory_mcp.domain.session import journal

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 4242)
ORIGIN = {"origin": "http://testserver"}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
RIP = "Reinforced Iron Plate"
RIP_ARGS = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name in ("plans_dir", "labels_dir", "activity_dir", "ui_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    monkeypatch.setattr(journal, "_writer", "")
    return tmp_path


@pytest.fixture
def log(dirs):
    return PlanLog("W")


@pytest.fixture
def client(dirs, projection, game):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def _rate(value):
    return {"op": "put", "field": "export_minimums", "item": RIP, "value": value}


def _create(client, name="rip 5", args=None):
    reply = client.post("/api/plans", json={"name": name, "args": args or RIP_ARGS}, headers=ORIGIN)
    assert reply.status_code == 201, reply.text
    return reply.json()["key"]


def _push(client, key, base_rev, *ops):
    reply = client.post(
        f"/api/plans/{key}/ops", json={"base_rev": base_rev, "ops": list(ops)}, headers=ORIGIN
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


# ------------------------------------------------------------------ domain


def test_a_duplicate_is_a_new_plan_at_v1_equal_to_the_version_copied(log):
    key = log.create("north", RIP_ARGS, actor=CHAT, notes="n", factory="F").key
    log.push(key, 1, [_rate(15)], actor=PAGE)
    copy = manage.duplicate(log, key, actor=PAGE, rev=1)
    assert copy.key != key and copy.rev == 1
    state = log.state(copy.key)
    assert state.name == "north (copy)"
    assert state.args.export_minimums == {RIP: 5.0}
    assert (state.notes, state.factory) == ("n", "F")
    assert log.commits(copy.key)[0].note == "duplicated from 'north' v1"
    assert log.state(key).rev == 2, "the source is untouched"
    again = manage.duplicate(log, key, actor=PAGE)
    assert log.state(again.key).name == "north (copy 2)"
    assert log.state(again.key).args.export_minimums == {RIP: 15.0}


def test_a_duplicate_under_a_taken_name_is_refused(log):
    key = log.create("north", RIP_ARGS, actor=CHAT).key
    log.create("south", RIP_ARGS, actor=CHAT)
    with pytest.raises(NameTaken):
        manage.duplicate(log, key, actor=PAGE, name="SOUTH")


def test_versions_are_newest_first_and_say_what_undid_and_restored_what(log):
    key = log.create("north", RIP_ARGS, actor=CHAT).key
    log.push(key, 1, [_rate(15)], actor=PAGE)
    log.undo(key, 2, 2, actor=PAGE)
    log.restore_to(key, 3, 2, actor=PAGE)
    rows = manage.versions(log, key)
    assert [r["commit"].rev for r in rows] == [4, 3, 2, 1]
    by_rev = {r["commit"].rev: r for r in rows}
    assert by_rev[2]["undone_by"] == 3
    assert by_rev[4]["restores"] == 2 and by_rev[3]["restores"] is None
    assert log.state(key).args.export_minimums == {RIP: 15.0}


def _summary(machines, rows, mw_draw=10.0, mw_net=-10.0, inputs=(), feasible=True):
    return {
        "feasible": feasible,
        "machines": machines,
        "mw_draw": mw_draw,
        "mw_net": mw_net,
        "rows": [{"building": b, "machines": n} for b, n in rows],
        "inputs": [{"item": i, "per_min": r} for i, r in inputs],
    }


def test_a_result_delta_names_machines_power_and_inputs():
    before = _summary(5, [("Assembler", 2), ("Constructor", 3)], 20, -20, [("Iron Ore", 60)])
    after = _summary(7, [("Assembler", 4), ("Constructor", 3)], 60, -60, [("Iron Ore", 120)])
    delta = manage.result_delta(before, after)
    assert delta["comparable"] and delta["machines"] == 2 and delta["mw_draw"] == 40
    assert delta["buildings"] == [{"name": "Assembler", "before": 2, "after": 4, "delta": 2}]
    assert delta["inputs"][0]["name"] == "Iron Ore" and delta["inputs"][0]["delta"] == 60
    assert delta["text"] == "+2 Assembler · +40 MW draw · Iron Ore +60/min"
    same = manage.result_delta(before, before)
    assert same["text"] == "no change in the result" and same["buildings"] == []


def test_a_delta_across_an_unsolvable_version_says_only_that():
    ok = _summary(5, [("Assembler", 2)])
    bad = _summary(0, [], feasible=False)
    assert manage.result_delta(ok, bad)["text"] == "no longer solvable"
    assert manage.result_delta(bad, ok)["text"] == "solvable again"
    assert manage.result_delta(ok, bad)["comparable"] is False
    assert manage.result_delta(ok, bad)["machines"] == 0


def _row(rid, recipe, machines, clock):
    return {"id": rid, "recipe": recipe, "building": "B", "machines": machines, "clock": clock}


def test_row_changes_join_on_id_and_sort_added_changed_removed():
    before = {
        "feasible": True,
        "machines": 0,
        "mw_draw": 0,
        "mw_net": 0,
        "rows": [
            _row("R_a", "Zeta", 2, 1.0),
            _row("R_b", "Beta", 1, 0.5),
            _row("R_c", "Gamma", 1, 0.5),
            _row("label:x", "Miner", 1, 0.25),
        ],
    }
    after = {
        **before,
        "rows": [
            _row("R_a", "Zeta", 2, 1.0005),
            _row("R_b", "Beta", 2, 0.5),
            _row("R_c", "Gamma", 1, 0.502),
            _row("R_d", "Alpha", 3, 0.9),
        ],
    }
    rows = manage.result_delta(before, after)["rows"]
    assert [(r["id"], r["change"]) for r in rows] == [
        ("R_d", "added"),
        ("R_b", "changed"),
        ("R_c", "changed"),
        ("label:x", "removed"),
    ]
    assert rows[0] == {
        "id": "R_d",
        "label": "Alpha",
        "change": "added",
        "machines_before": 0,
        "machines_after": 3,
        "clock_before": 0.0,
        "clock_after": 0.9,
    }
    assert manage.result_delta(before, {**after, "feasible": False})["rows"] == []


# ------------------------------------------------------------------ routes


def test_restore_is_a_new_version_equal_to_the_old_one(client):
    key = _create(client)
    _push(client, key, 1, _rate(15))
    _push(client, key, 2, {"op": "set", "field": "sloops", "value": 2})
    reply = client.post(f"/api/plans/{key}/restore", json={"base_rev": 3, "rev": 1}, headers=ORIGIN)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["rev"] == 4
    assert body["state"]["args"]["export_minimums"] == {RIP: 5.0}
    assert body["state"]["args"]["sloops"] == 0
    assert PlanLog(WORLD).commits(key)[-1].note == "restore v1"
    versions = client.get(f"/api/plans/{key}/versions").json()
    assert versions["head"] == 4 and versions["versions"][0]["restores"] == 1
    assert [v["rev"] for v in versions["versions"]] == [4, 3, 2, 1]


def test_restore_over_an_edit_made_since_the_base_is_outdated(client):
    key = _create(client)
    _push(client, key, 1, _rate(15))
    PlanLog(WORLD).push(key, 2, [_rate(20)], actor=CHAT)
    reply = client.post(f"/api/plans/{key}/restore", json={"base_rev": 2, "rev": 1}, headers=ORIGIN)
    assert reply.status_code == 409
    assert reply.json()["outdated"] is True and reply.json()["head"] == 3
    assert PlanLog(WORLD).head_rev(key) == 3


def test_duplicate_route_makes_a_stamped_copy_and_404s_a_missing_version(client):
    key = _create(client)
    reply = client.post(f"/api/plans/{key}/duplicate", json={}, headers=ORIGIN)
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert body["state"]["name"] == "rip 5 (copy)" and body["rev"] == 1
    assert body["state"]["plan_id"]
    assert (
        client.post(f"/api/plans/{key}/duplicate", json={"rev": 9}, headers=ORIGIN).status_code
        == 404
    )
    taken = client.post(f"/api/plans/{key}/duplicate", json={"name": "rip 5"}, headers=ORIGIN)
    assert taken.status_code == 409 and taken.json()["name_taken"] is True
    foreign = client.post(
        f"/api/plans/{key}/duplicate", json={}, headers={"origin": "http://evil.example"}
    )
    assert foreign.status_code == 403


def test_the_delta_route_re_solves_both_versions(client):
    key = _create(client)
    _push(client, key, 1, _rate(15))
    body = client.get(f"/api/plan/delta?key={key}&from_rev=1").json()
    assert body["from_rev"] == 1 and body["to_rev"] == 2 and body["comparable"] is True
    assert body["machines"] > 0 and body["text"]
    names = [b["name"] for b in body["buildings"]]
    assert names, "tripling the rate must add machines somewhere"
    assert client.get(f"/api/plan/delta?key={key}&from_rev=7").status_code == 404
    assert client.get("/api/plan/delta?key=0000beef&from_rev=1").status_code == 404


def test_a_solve_names_its_raw_inputs(client):
    body = client.post("/api/plan/solve", json={"args": RIP_ARGS}, headers=ORIGIN).json()
    assert body["feasible"] and body["inputs"]
    assert all(r["per_min"] > 0 for r in body["inputs"])


def test_the_plans_index_carries_a_status(client):
    _create(client)
    row = client.get("/api/plans").json()["index"][0]
    assert row["status"] == [] and row["recorded"] is True
