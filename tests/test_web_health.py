"""``/api/factories/health`` and ``/api/power/circuits``: the side panel's two payloads.

``importorskip`` at module scope: ``fastapi`` lives in the optional ``web`` extra. Both
loaders are injected by the ``client`` fixture, so nothing here reads a ``.sav``.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.factories.health import ACTIONABLE, STATES, assess


def test_every_named_factory_gets_a_health_row(client, state):
    body = client.get("/api/factories/health").json()
    assert body["states"] == list(STATES)
    assert body["actionable_states"] == list(ACTIONABLE)
    assert {r["name"] for r in body["factories"]} == {x.name for x in state.labels.labels}


def test_a_health_row_matches_what_assess_says_about_that_factory(client, state):
    body = client.get("/api/factories/health").json()
    if not body["factories"]:
        pytest.skip("the fixture world has no named factories")
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


def test_factories_are_ordered_worst_first(client):
    rows = client.get("/api/factories/health").json()["factories"]
    todo = [r["actionable"] for r in rows]
    assert todo == sorted(todo, reverse=True)


def test_the_sweep_counts_blocked_machines_as_todo_like_the_route(client, state, monkeypatch):
    """The MCP sweep's ``todo`` and the route's ``actionable`` are one number per factory."""
    from satisfactory_mcp.interfaces.mcp.tools import factories as tool

    rows = client.get("/api/factories/health").json()["factories"]
    if not rows:
        pytest.skip("the fixture world has no named factories")
    monkeypatch.setattr(tool, "_state", lambda save=None, world=None, as_of=None: state)
    out = tool.factory_health(factory="all", limit=500)
    todo = {}
    for line in out.splitlines():
        cells = line.split("\t")
        if len(cells) == 11 and cells[0] != "factory":
            todo[cells[0]] = int(cells[-1] or 0)
    assert todo == {r["name"]: r["actionable"] for r in rows}


def test_the_world_ledger_is_power_report(client, state):
    body = client.get("/api/power/circuits").json()
    report = state.power_report()
    assert body["world"]["generation_mw"] == pytest.approx(report["generation_mw"], abs=0.1)
    assert body["world"]["measured_draw_mw"] == pytest.approx(report["measured_draw_mw"], abs=0.1)
    assert body["paused"] == report["paused_count"]
    assert len(body["starved"]) == len(report["starved_generators"])


def test_circuits_split_the_generation_without_losing_or_inventing_any(client, state):
    body = client.get("/api/power/circuits").json()
    circuits = body["circuits"]
    assert circuits, "the fixture world is wired"
    assert [c["index"] for c in circuits] == list(range(len(circuits)))
    total = sum(c["ledger"]["generation_mw"] for c in circuits)
    assert total <= body["world"]["generation_mw"] + 0.1 * len(circuits)
    for c in circuits:
        led = c["ledger"]
        assert led["headroom_mw"] == pytest.approx(led["generation_mw"] - led["draw_mw"], abs=0.2)
        if c["bbox_m"] is not None:
            x_min, y_min, x_max, y_max = c["bbox_m"]
            assert x_min <= c["centroid_m"][0] <= x_max
            assert y_min <= c["centroid_m"][1] <= y_max


def test_the_dark_machines_are_the_ones_assess_finds(client, state):
    body = client.get("/api/power/circuits").json()
    report = assess("world", state.graph.machines(), state.game, state.projection, state.graph)
    assert [r["instance"] for r in body["unwired"]] == sorted(report.unwired)
    assert [r["instance"] for r in body["no_generator"]] == sorted(report.no_generator)
    for circuit in body["circuits"]:
        if circuit["ledger"]["generation_mw"] == 0 and circuit["consumers"]:
            assert body["no_generator"], "a consumer circuit with no source is a dark machine"


def test_an_unreadable_save_is_an_error_not_an_empty_panel(client, monkeypatch):
    def boom(save=None, world=None):
        raise RuntimeError("sidecar produced no output")

    monkeypatch.setattr(client.app.state, "load_state", boom)
    for path in ("/api/factories/health", "/api/power/circuits"):
        r = client.get(path)
        assert r.status_code == 404
        assert "could not read save" in r.json()["error"]
