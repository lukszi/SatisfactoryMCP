"""A save that cannot be read is a 404 that carries the loader's reason, on every route.

Every handler spends ``?save=``/``?world=`` through ``serial._state`` and turns a loader
failure into the same 404. ``/api/summary`` leads because the page opens with it, and its
failure is the one the header has to explain.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.support.web import client_over, failing_state_loader

ROUTES = ["/api/summary", "/api/belts", "/api/pipes", "/api/floors", "/api/structures"]


@pytest.mark.parametrize("path", ROUTES)
def test_a_save_that_cannot_be_read_is_a_404_with_a_reason(game, path):
    with client_over(failing_state_loader, game) as client:
        reply = client.get(path)
    assert reply.status_code == 404
    assert "sidecar produced no output" in reply.json()["error"]
