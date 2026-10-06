"""``/api/world/conduits``: belt and pipe runs near a place, by network, or one by id."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.support.web import client_over, failing_state_loader


def test_conduits_near_me_page_runs_with_their_lines(client):
    body = client.get("/api/world/conduits", params={"limit": 20}).json()
    assert body["view"] == "runs" and body["where"] == "you"
    assert len(body["runs"]) == 20 and body["total"] > 20
    assert body["belts"] + body["pipes"] == body["total"]
    run = body["runs"][0]
    assert run["id"].startswith(("chain:", "pipe:"))
    assert run["lines_m"] and len(run["lines_m"][0][0]) == 2
    assert run["distance_m"] <= 250
    assert [r["length_m"] for r in body["runs"]] == sorted(
        (r["length_m"] for r in body["runs"]), reverse=True
    )
    later = client.get("/api/world/conduits", params={"limit": 20, "offset": 20}).json()
    assert later["offset"] == 20 and later["runs"][0]["id"] not in {r["id"] for r in body["runs"]}


def test_conduits_networks_view_and_network_filter(client):
    nets = client.get("/api/world/conduits", params={"view": "networks"}).json()
    assert nets["runs"] == [] and nets["networks"]
    net = next(n for n in nets["networks"] if n["network"] is not None)
    runs = client.get(
        "/api/world/conduits", params={"network": net["network"], "limit": 500}
    ).json()
    assert runs["total"] == net["pieces"]
    assert {r["network"] for r in runs["runs"]} == {net["network"]}


def test_conduits_run_picks_one(client):
    body = client.get("/api/world/conduits", params={"run": "chain:7"}).json()
    assert [r["id"] for r in body["runs"]] == ["chain:7"]


@pytest.mark.parametrize(
    "params, code",
    [
        ({"conduit_kind": "rope"}, 400),
        ({"view": "grid"}, 400),
        ({"view": "networks", "conduit_kind": "belt"}, 400),
        ({"near": "nowhere at all"}, 400),
        ({"run": "chain:99999999"}, 400),
        ({"radius_m": 2001}, 422),
        ({"limit": 0}, 422),
    ],
)
def test_conduits_refusals(client, params, code):
    assert client.get("/api/world/conduits", params=params).status_code == code


def test_conduits_need_a_save(game):
    with client_over(failing_state_loader, game) as c:
        assert c.get("/api/world/conduits").status_code == 404
