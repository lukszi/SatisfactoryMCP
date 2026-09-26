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
