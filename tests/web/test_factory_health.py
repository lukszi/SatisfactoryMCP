"""``/api/factories/health``: one row per named factory, worst first, as ``assess`` sees it."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.factories.health import ACTIONABLE, OK, STATES, assess
from tests.support.web import failing_state_loader


def test_every_named_factory_gets_a_health_row(labelled_client, labelled):
    body = labelled_client.get("/api/factories/health").json()
    assert body["states"] == list(STATES)
    assert body["actionable_states"] == list(ACTIONABLE)
    assert body["ok_states"] == [s for s in STATES if s in OK]
    assert set(body["ok_states"]) == OK
    assert body["factories"]
    assert {r["name"] for r in body["factories"]} == {x.name for x in labelled.labels.labels}


def test_a_health_row_matches_what_assess_says_about_that_factory(labelled_client, labelled):
    state = labelled
    body = labelled_client.get("/api/factories/health").json()
    assert body["factories"]
    alive = set(state.graph.machines())
    by_name = {x.name: x for x in state.labels.labels}
    for row in body["factories"]:
        standing = [m for m in by_name[row["name"]].anchors if m in alive]
        report = assess(row["name"], standing, state.game, state.projection, state.graph)
        assert row["alive"] == len(standing)
        assert row["machines"] == len(report.machines)
        assert {s["state"]: s["count"] for s in row["states"]} == {
            s: n for s, n in report.by_state.items() if n
        }
        assert row["unwired"] == len(report.unwired)
        assert row["actionable"] == sum(report.by_state[s] for s in ACTIONABLE)
        assert len(row["worst"]) <= 8
        for issue in row["worst"]:
            assert issue["state"] in STATES
            if issue["x_m"] is not None:
                assert abs(issue["x_m"]) < 5000, "metres, not centimetres"


def test_factories_are_ordered_worst_first(labelled_client):
    rows = labelled_client.get("/api/factories/health").json()["factories"]
    assert len(rows) > 1
    todo = [r["actionable"] for r in rows]
    assert todo == sorted(todo, reverse=True)


def test_the_sweep_counts_blocked_machines_as_todo_like_the_route(
    labelled_client, labelled, use_world
):
    """The MCP sweep's ``todo`` and the route's ``actionable`` are one number per factory."""
    from satisfactory_mcp.interfaces.mcp.tools import factories as tool

    rows = labelled_client.get("/api/factories/health").json()["factories"]
    assert rows
    use_world(labelled)
    out = tool.factory_health(factory="all", limit=500)
    todo = {}
    for line in out.splitlines():
        cells = line.split("\t")
        if len(cells) == 11 and cells[0] != "factory":
            todo[cells[0]] = int(cells[-1] or 0)
    assert todo == {r["name"]: r["actionable"] for r in rows}


def test_an_unreadable_save_is_an_error_not_an_empty_panel(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    for path in ("/api/factories/health", "/api/power/circuits"):
        r = client.get(path)
        assert r.status_code == 404
        assert "could not read save" in r.json()["error"]


def test_worst_actionable_holds_only_machines_needing_action(labelled_client):
    rows = labelled_client.get("/api/factories/health").json()["factories"]
    assert rows
    for row in rows:
        assert all(i["state"] in ACTIONABLE for i in row["worst_actionable"])
        assert len(row["worst_actionable"]) == min(8, row["actionable"])
