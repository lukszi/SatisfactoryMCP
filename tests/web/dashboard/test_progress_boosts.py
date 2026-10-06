"""``/api/progress/shards`` and ``/api/progress/sloops``: the boost budgets the tools read."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.support.research import alien_tree_shut_client


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


def test_an_unresearched_amplifier_in_a_shut_tree_loses_its_name_with_spoilers_0(
    projection, game, monkeypatch
):
    with alien_tree_shut_client(projection, game) as c:
        closed = c.app.state.load_state()
        gate = {"schematic_name": "Production Amplifier", "cost": []}
        monkeypatch.setattr(closed, "research_gate", lambda name: gate)
        full = c.get("/api/progress/sloops").json()
        quiet = c.get("/api/progress/sloops", params={"spoilers": 0}).json()
    assert full["amplifier_spoiler"] is True
    assert full["amplifier_research"] == "Production Amplifier"
    assert quiet["amplifier_research"] is None and quiet["amplifier_cost"] == []
