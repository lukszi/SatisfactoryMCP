"""``/api/progress/{gate,alternates}``: the target phase's bill, the next steps, the alternates."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.core.singleflight import Singleflight
from satisfactory_mcp.domain.progression.roadmap.bill import target_bill
from satisfactory_mcp.domain.progression.roadmap.steps import MAX_STEPS, _order, _Step
from satisfactory_mcp.interfaces.web.routers.dashboard import progress_roadmap
from tests.support.web import failing_state_loader


@pytest.fixture(autouse=True)
def _fresh_answers(monkeypatch):
    monkeypatch.setattr(progress_roadmap, "_GATES", Singleflight(maxsize=8))
    monkeypatch.setattr(progress_roadmap, "_ALTERNATES", Singleflight(maxsize=8))


def test_the_gate_is_the_target_phase_bill_against_stock(client, state):
    body = client.get("/api/progress/gate", params={"horizon_h": 10}).json()
    phase, parts = target_bill(state)
    assert body["phase"] == phase
    assert body["horizon_h"] == 10
    assert [p["item"] for p in body["parts"]] == [p.item for p in parts]
    for row in body["parts"]:
        assert row["short"] == max(0.0, row["owed"] - row["held"])
        assert row["need_per_min"] == pytest.approx(row["short"] / 600, abs=1e-4)
        if row["short"] <= 0:
            assert row["machines"] is None and row["need_per_min"] == 0
        assert row["recipe_unlocked"] or row["unlock"] is not None or row["recipe"] is None
        for inp in row["inputs"]:
            assert inp["needed"] == pytest.approx(inp["per_unit"] * row["short"], rel=1e-3, abs=0.1)


def test_a_longer_horizon_never_needs_a_bigger_chain(client):
    short = client.get("/api/progress/gate", params={"horizon_h": 5}).json()["parts"]
    long = client.get("/api/progress/gate", params={"horizon_h": 50}).json()["parts"]
    for a, b in zip(short, long, strict=True):
        if a["machines"] is not None and b["machines"] is not None:
            assert b["machines"] <= a["machines"]


def test_steps_are_few_and_wait_only_on_earlier_steps(client):
    steps = client.get("/api/progress/gate").json()["steps"]
    assert len(steps) <= MAX_STEPS
    for n, step in enumerate(steps, start=1):
        assert step["kind"] in ("make", "line", "grow", "unlock")
        assert all(0 < a < n for a in step["after"])
        assert step["parts"], "every step opens at least one part"
        if step["kind"] == "unlock":
            assert step["system"] in ("milestone", "MAM")
        else:
            assert step["item"]


def _step(kind: str, parts: int, deps: set | None = None) -> _Step:
    return _Step(kind, kind, deps=deps or set(), parts={f"p{i}" for i in range(parts)})


def test_prerequisites_come_first_and_work_that_can_start_before_unlocks():
    steps = {
        ("unlock", "a"): _step("unlock", 3, {("make", "x")}),
        ("line", "l"): _step("line", 1),
        ("make", "x"): _step("make", 2),
        ("unlock", "b"): _step("unlock", 4),
    }
    assert _order(steps) == [("make", "x"), ("line", "l"), ("unlock", "b"), ("unlock", "a")]


def test_alternates_cut_something_and_name_where_they_come_from(client, state):
    body = client.get("/api/progress/alternates").json()
    have = state.available_recipe_ids
    offered = {
        r.cls: o.hard_drive_id
        for o in state.hard_drive_offers
        for opt in o.options
        for r in opt["recipes"]
    }
    for row in body["alternates"]:
        assert min(row["machines"], row["mw"], row["raw"]) < 0
        if row["source"] == "unlocked":
            assert row["recipe"] in have
        elif row["source"] == "drive":
            assert row["drive"] == offered[row["recipe"]]
        else:
            assert (
                row["source"] == "pool"
                and row["recipe"] not in have
                and row["recipe"] not in offered
            )
    keys = [(r["machines"], r["mw"], r["raw"], r["name"]) for r in body["alternates"]]
    assert keys == sorted(keys)
    if body["base"] is None:
        assert body["alternates"] == []


def test_answers_are_kept_per_save_and_horizon(client, monkeypatch):
    calls: list[float] = []
    real = progress_roadmap.gate_view

    def counted(st, horizon_h):
        calls.append(horizon_h)
        return real(st, horizon_h)

    monkeypatch.setattr(progress_roadmap, "gate_view", counted)
    first = client.get("/api/progress/gate").json()
    assert client.get("/api/progress/gate").json() == first
    client.get("/api/progress/gate", params={"horizon_h": 50})
    assert calls == [20.0, 50.0]


@pytest.mark.parametrize("horizon", [0, -1, 5000])
def test_a_horizon_outside_its_range_is_refused(client, horizon):
    assert client.get("/api/progress/gate", params={"horizon_h": horizon}).status_code == 422


@pytest.mark.parametrize("route", ["gate", "alternates"])
def test_an_unreadable_save_is_an_error(client, monkeypatch, route):
    monkeypatch.setattr(client.app.state, "load_state", failing_state_loader)
    r = client.get(f"/api/progress/{route}")
    assert r.status_code == 404


def test_no_target_phase_means_no_bill_and_no_steps(client, state, monkeypatch):
    req = state.phase_requirements()
    monkeypatch.setattr(state, "phase_requirements", lambda: {**req, "target_phase": ""})
    body = client.get("/api/progress/gate").json()
    assert body["parts"] == [] and body["steps"] == [] and body["solves"] == 0
    alts = client.get("/api/progress/alternates").json()
    assert alts["base"] is None and alts["alternates"] == []
