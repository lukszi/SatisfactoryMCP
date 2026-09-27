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
