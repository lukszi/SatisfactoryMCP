"""``/api/progress/milestones``: the dashboard's Progress section.

``importorskip`` at module scope: ``fastapi`` lives in the optional ``web`` extra. Both
loaders are injected by the ``client`` fixture, so nothing here reads a ``.sav``.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.progression.ladder import SchematicLadder


def _ladder(state):
    return SchematicLadder(game=state.game, unlocks=state.unlocks, inventory=state.inventory)


def test_every_milestone_is_a_row_with_the_ladders_status(client, state):
    body = client.get("/api/progress/milestones").json()
    rungs = {r.schematic.cls: r for r in _ladder(state).rungs("EST_Milestone")}
    assert {m["cls"] for m in body["milestones"]} == set(rungs)
    for row in body["milestones"]:
        rung = rungs[row["cls"]]
        assert row["status"] == rung.status
        assert row["tier"] == rung.schematic.tier
        assert [s["item"] for s in row["short"]] == [m.item for m in rung.missing]
        assert row["blocked_by"] == list(rung.blocked_by)


def test_rows_run_by_tier_then_name(client):
    rows = client.get("/api/progress/milestones").json()["milestones"]
    keys = [(r["tier"], r["name"]) for r in rows]
    assert keys == sorted(keys)


def test_the_tier_tally_agrees_with_the_summary(client, state):
    body = client.get("/api/progress/milestones").json()
    prog = state.progression()
    assert {str(t["tier"]): f"{t['done']}/{t['total']}" for t in body["tiers"]} == {
        str(t): v for t, v in prog["milestones_by_tier"].items()
    }
    assert body["highest_complete_tier"] == prog["highest_complete_tier"]
    assert body["game_phase"] == prog["game_phase"]


def test_ready_means_nothing_is_short_and_short_means_a_positive_gap(client):
    for row in client.get("/api/progress/milestones").json()["milestones"]:
        if row["status"] == "READY":
            assert not row["short"]
        assert all(s["amount"] > 0 for s in row["short"])


def test_an_unreadable_save_is_an_error_not_an_empty_ladder(client, monkeypatch):
    def boom(save=None, world=None):
        raise RuntimeError("sidecar produced no output")

    monkeypatch.setattr(client.app.state, "load_state", boom)
    r = client.get("/api/progress/milestones")
    assert r.status_code == 404
    assert "could not read save" in r.json()["error"]


PROGRESS_ROUTES = ("mam", "phase", "shards", "sloops", "harddrives")


def test_mam_rows_are_the_ladder_with_the_research_state_on_top(client, state):
    body = client.get("/api/progress/mam").json()
    rungs = {r.schematic.cls: r for r in _ladder(state).rungs("EST_MAM")}
    assert {r["cls"] for r in body["research"]} == set(rungs)
    for row in body["research"]:
        rung = rungs[row["cls"]]
        if rung.done:
            assert row["status"] == "DONE"
        elif row["cls"] in state.research.ongoing:
            assert row["status"] == "RUNNING"
        elif state.research.tree_locked(row["cls"]):
            assert row["status"] == "TREE SHUT"
        else:
            assert row["status"] == rung.status
        assert [s["item"] for s in row["short"]] == [m.item for m in rung.missing]
    assert body["knows_trees"] == state.research.knows_trees


def test_mam_capabilities_agree_with_the_research_gate(client, state):
    for cap in client.get("/api/progress/mam").json()["capabilities"]:
        assert cap["researched"] == (state.research_gate(cap["capability"]) is None)


def test_the_phase_view_joins_the_target_row_to_stock(client, state):
    body = client.get("/api/progress/phase").json()
    req = state.phase_requirements()
    stock = state.stock()
    assert body["target_phase"] == (req["target_phase"] or None)
    assert [p["trust"] for p in body["phases"]] == [r["stale"] for r in req["phases"]]
    target = [p for p in body["phases"] if p["phase"] == req["target_phase"]]
    for p in target:
        for item in p["outstanding"]:
            assert item["have"] == stock.get(item["item"], 0.0)
            assert item["short"] == round(max(0.0, item["amount"] - item["have"]), 1)
        assert body["deliverable"] == all(i["short"] <= 0 for i in p["outstanding"])


def test_shards_are_the_budget_the_tool_reads(client, state):
    body = client.get("/api/progress/shards").json()
    budget = state.shard_budget()
    for key in ("free", "craftable", "potential", "committed", "owned"):
        assert body[key] == budget[key]
    assert [h["instance"] for h in body["holders"]] == [h["instance"] for h in budget["holders"]]
    assert body["idle"] == sum(h["idle"] for h in budget["holders"])


def test_sloops_are_the_budget_the_tool_reads(client, state):
    body = client.get("/api/progress/sloops").json()
    budget = state.sloop_budget()
    for key in ("free", "committed", "owned", "mercer_spheres"):
        assert body[key] == budget[key]
    assert sum(h["sloops"] for h in body["holders"]) == budget["committed"]
    assert body["amplifier_researched"] == (state.research_gate("production_boost") is None)


def test_every_pending_drive_is_listed_with_both_options(client, state):
    body = client.get("/api/progress/harddrives").json()
    offers = state.hard_drive_offers
    assert [d["hard_drive_id"] for d in body["drives"]] == [o.hard_drive_id for o in offers]
    for drive, offer in zip(body["drives"], offers, strict=True):
        assert [o["schematic"] for o in drive["options"]] == [o["schematic"] for o in offer.options]
        assert drive["rerolls_left"] == offer.rerolls_left
    assert body["spare"] == state.spare_hard_drives()


@pytest.mark.parametrize("route", PROGRESS_ROUTES)
def test_every_progress_route_refuses_an_unreadable_save(client, monkeypatch, route):
    def boom(save=None, world=None):
        raise RuntimeError("sidecar produced no output")

    monkeypatch.setattr(client.app.state, "load_state", boom)
    r = client.get(f"/api/progress/{route}")
    assert r.status_code == 404
    assert "could not read save" in r.json()["error"]


def test_capabilities_say_when_their_tree_is_still_shut(client, state, projection, game):
    """With spoilers off the page names no capability whose MAM tree is unopened; the
    route has to say which ones those are, for the MAM page and the sloops page alike."""
    import copy

    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    body = client.get("/api/progress/mam").json()
    assert all(not c["tree_shut"] for c in body["capabilities"])
    assert client.get("/api/progress/sloops").json()["amplifier_tree_shut"] is False

    shut = copy.deepcopy(projection)
    trees = shut["research"]["unlocked_trees"]
    shut["research"]["unlocked_trees"] = [t for t in trees if t != "BPD_ResearchTree_AlienTech_C"]
    closed = WorldState(projection=shut, game=game)
    app = create_app(state_loader=lambda save=None, world=None: closed, game_loader=lambda: game)
    with TestClient(app) as c:
        caps = c.get("/api/progress/mam").json()["capabilities"]
        sloops = c.get("/api/progress/sloops").json()
    assert caps and all(cap["tree_shut"] for cap in caps)
    assert sloops["amplifier_tree_shut"] is True


def _closed_trees(projection, game):
    import copy

    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    shut = copy.deepcopy(projection)
    trees = shut["research"]["unlocked_trees"]
    shut["research"]["unlocked_trees"] = [t for t in trees if t != "BPD_ResearchTree_AlienTech_C"]
    closed = WorldState(projection=shut, game=game)
    app = create_app(state_loader=lambda save=None, world=None: closed, game_loader=lambda: game)
    return TestClient(app)


def _phase(state, monkeypatch, phase):
    prog = state.progression()
    monkeypatch.setattr(state, "progression", lambda: {**prog, "game_phase": phase})


def test_milestones_past_the_reached_tier_are_spoilers_and_spoilers_0_drops_them(
    client, state, monkeypatch
):
    _phase(state, monkeypatch, None)
    full = client.get("/api/progress/milestones").json()
    top = max(m["tier"] for m in full["milestones"] if m["status"] == "DONE")
    assert any(m["spoiler"] for m in full["milestones"]), "the fixture must reach past a tier"
    for m in full["milestones"]:
        assert m["spoiler"] == (m["tier"] > top)
    for t in full["tiers"]:
        assert t["spoiler"] == (t["tier"] > top)
    assert client.get("/api/progress/milestones", params={"spoilers": 1}).json() == full
    hidden = client.get("/api/progress/milestones", params={"spoilers": 0}).json()
    assert hidden["milestones"] == [m for m in full["milestones"] if not m["spoiler"]]
    assert hidden["tiers"] == [t for t in full["tiers"] if not t["spoiler"]]
    assert hidden["highest_complete_tier"] == full["highest_complete_tier"]


def test_mam_nodes_and_capabilities_in_a_shut_tree_are_spoilers(client, projection, game):
    opened = client.get("/api/progress/mam").json()
    assert not any(r["spoiler"] for r in opened["research"])
    with _closed_trees(projection, game) as c:
        full = c.get("/api/progress/mam").json()
        hidden = c.get("/api/progress/mam", params={"spoilers": 0}).json()
        sloops = c.get("/api/progress/sloops").json()
    assert any(r["spoiler"] for r in full["research"])
    for r in full["research"]:
        assert r["spoiler"] == (r["status"] == "TREE SHUT")
    for cap in full["capabilities"]:
        assert cap["spoiler"] == cap["tree_shut"]
    assert hidden["research"] == [r for r in full["research"] if not r["spoiler"]]
    assert hidden["capabilities"] == [x for x in full["capabilities"] if not x["spoiler"]]
    assert sloops["amplifier_tree_shut"] is True
    assert sloops["amplifier_spoiler"] == (not sloops["amplifier_researched"])


def test_an_unresearched_amplifier_in_a_shut_tree_loses_its_name_with_spoilers_0(
    projection, game, monkeypatch
):
    with _closed_trees(projection, game) as c:
        closed = c.app.state.load_state()
        gate = {"schematic_name": "Production Amplifier", "cost": []}
        monkeypatch.setattr(closed, "research_gate", lambda name: gate)
        full = c.get("/api/progress/sloops").json()
        quiet = c.get("/api/progress/sloops", params={"spoilers": 0}).json()
    assert full["amplifier_spoiler"] is True
    assert full["amplifier_research"] == "Production Amplifier"
    assert quiet["amplifier_research"] is None and quiet["amplifier_cost"] == []


def test_phases_past_the_target_are_spoilers(client, state, monkeypatch):
    req = state.phase_requirements()
    later = dict(req["phases"][-1], phase="GP_Project_Assembly_Phase_9")
    monkeypatch.setattr(
        state, "phase_requirements", lambda: {**req, "phases": [*req["phases"], later]}
    )
    full = client.get("/api/progress/phase").json()
    assert [p["spoiler"] for p in full["phases"]] == [False] * len(req["phases"]) + [True]
    hidden = client.get("/api/progress/phase", params={"spoilers": 0}).json()
    assert hidden["phases"] == full["phases"][:-1]
    assert hidden["deliverable"] == full["deliverable"]


def test_tiers_the_delivered_phase_opens_are_not_spoilers(client, state, monkeypatch):
    _phase(state, monkeypatch, "GP_Project_Assembly_Phase_3")
    body = client.get("/api/progress/milestones").json()
    for m in body["milestones"]:
        assert m["spoiler"] == (m["tier"] > 8)
        assert m["opens_at"] == (4 if m["tier"] > 8 and m["status"] != "DONE" else None)
    assert not any(t["spoiler"] for t in body["tiers"] if t["tier"] <= 8)


def test_a_save_with_no_phase_locks_no_tier(client, state, monkeypatch):
    _phase(state, monkeypatch, None)
    body = client.get("/api/progress/milestones").json()
    assert all(m["opens_at"] is None for m in body["milestones"])


def test_a_save_with_no_target_phase_hides_every_phase(client, state, monkeypatch):
    req = state.phase_requirements()
    monkeypatch.setattr(state, "phase_requirements", lambda: {**req, "target_phase": ""})
    full = client.get("/api/progress/phase").json()
    assert full["phases"] and all(p["spoiler"] for p in full["phases"])
    assert client.get("/api/progress/phase", params={"spoilers": 0}).json()["phases"] == []
