"""The web app over stub loaders, and the plan-route calls the web tests repeat.

``fastapi`` is imported inside ``client_over``: it is an optional extra, and ``conftest``
imports this module for every test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from satisfactory_mcp.domain.planning import journal
from tests.support.reference_world import FIVE_RIP_ARGS

#: The page's own origin; a write without it is refused as cross-site.
PAGE_ORIGIN = {"origin": "http://testserver"}


def failing_state_loader(save=None, world=None):
    """A state loader that fails the way the real one does when the sidecar produces nothing."""
    raise RuntimeError("sidecar produced no output")


@contextmanager
def client_over(state_or_loader: Any, game: Any) -> Iterator[Any]:
    """A ``TestClient`` over the app with both loaders stubbed; no save is ever read.

    ``state_or_loader`` is a world served as is, a callable used as the state loader, or
    ``None`` for an app with no world at all. Skips when the ``web`` extra is not installed.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    if callable(state_or_loader):
        state_loader: Callable = state_or_loader
    else:

        def state_loader(save=None, world=None):
            return state_or_loader

    app = create_app(state_loader=state_loader, game_loader=lambda: game)
    with TestClient(app) as client:
        yield client


def create_plan(client, name: str = "rip 5", args: dict = FIVE_RIP_ARGS) -> dict:
    """Create a plan from the page and return the reply body."""
    reply = client.post("/api/plans", json={"name": name, "args": args}, headers=PAGE_ORIGIN)
    assert reply.status_code == 201, reply.text
    return reply.json()


def push_ops(client, key: str, base_rev: int, *ops: dict, expect: int = 200):
    """Push ``ops`` onto plan ``key`` from the page and return the reply."""
    reply = client.post(
        f"/api/plans/{key}/ops", json={"base_rev": base_rev, "ops": list(ops)}, headers=PAGE_ORIGIN
    )
    assert reply.status_code == expect, reply.text
    return reply


def set_op(field: str, value: Any) -> dict:
    return {"op": "set", "field": field, "value": value}


def put_op(field: str, item: str, value: Any) -> dict:
    return {"op": "put", "field": field, "item": item, "value": value}


def journal_entries(world: str, prefix: str) -> list[dict]:
    """The activity journal's entries for ``world`` whose kind starts with ``prefix``."""
    return [entry for entry in journal.read(world) if entry["kind"].startswith(prefix)]
