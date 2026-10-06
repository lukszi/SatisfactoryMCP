"""``?as_of=`` on every read: the token the page holds either matches or the read is refused."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.pinning import STALE
from tests.support.web import client_over, failing_state_loader

STALE_TOKEN = "sav:000000000000"


def _token(client) -> str:
    return client.get("/api/summary").json()["save_token"]


@pytest.mark.parametrize("path", ["/api/power/circuits", "/api/factories/health", "/api/nodes"])
def test_the_current_token_reads_as_if_unpinned(client, path):
    token = _token(client)
    pinned = client.get(path, params={"as_of": token})
    assert pinned.status_code == 200
    assert pinned.json() == client.get(path).json()


@pytest.mark.parametrize("path", ["/api/power/circuits", "/api/factories/health", "/api/nodes"])
def test_a_stale_token_is_refused_with_the_page_sentence(client, path):
    reply = client.get(path, params={"as_of": STALE_TOKEN})
    assert reply.status_code == 409
    body = reply.json()
    assert body["error"] == STALE
    assert body["stale"] is True
    assert body["pin"].startswith(f"as_of={STALE_TOKEN}")


def test_a_malformed_token_is_refused_too(client):
    reply = client.get("/api/summary", params={"as_of": "not-a-token"})
    assert reply.status_code == 409
    assert "not a save token" in reply.json()["pin"]


def test_no_token_is_no_check(client):
    assert client.get("/api/power/circuits").status_code == 200


def test_writes_are_left_to_their_own_check(client):
    reply = client.post(
        "/api/labels",
        params={"as_of": STALE_TOKEN},
        json={},
        headers={"Origin": "http://testserver"},
    )
    assert reply.status_code != 409 or "stale" not in reply.json()


def test_a_world_that_cannot_be_read_is_left_to_the_route(game):
    with client_over(failing_state_loader, game) as c:
        reply = c.get("/api/power/circuits", params={"as_of": STALE_TOKEN})
    assert reply.status_code == 404
    assert "stale" not in reply.json()
