"""``POST /api/factories/plan``: a plan from a built factory, as the factory page makes it."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.support.web import PAGE_ORIGIN


def _make(client, factory: str):
    return client.post("/api/factories/plan", json={"factory": factory}, headers=PAGE_ORIGIN)


def test_a_plan_is_made_and_reads_back_from_the_plan_routes(labelled_client):
    reply = _make(labelled_client, "tier 1&2")
    assert reply.status_code == 201
    body = reply.json()
    assert (body["name"], body["rev"], body["factory"]) == ("tier 1&2 as built", 1, "tier 1&2")
    assert body["feasible"] and body["machines"] > 0
    assert any(line.startswith("Modular Engine") for line in body["differences"])
    plan = labelled_client.get(f"/api/plans/{body['key']}").json()
    assert plan["factory"] == "tier 1&2"
    assert plan["siting"]["origin_label"] == "built “tier 1&2”"
    assert plan["args"]["objective"] == "min_raw_power"
    assert plan["notes"] == body["notes"]


def test_a_second_press_makes_a_second_plan(labelled_client):
    first = _make(labelled_client, "biofuel setup").json()
    second = _make(labelled_client, "biofuel setup").json()
    assert first["key"] != second["key"]
    assert second["name"] == "biofuel setup as built (2)"
    assert second["differences"] == []


def test_an_unknown_factory_is_a_404(labelled_client):
    reply = _make(labelled_client, "no such factory")
    assert reply.status_code == 404
    assert "no factory named" in reply.json()["error"]


def test_a_write_from_another_site_is_refused(labelled_client):
    reply = labelled_client.post("/api/factories/plan", json={"factory": "tier 1&2"})
    assert reply.status_code == 403
