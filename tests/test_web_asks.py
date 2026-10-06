"""``/api/asks`` (docs/planner-p4_contract.md §5 and §7): every route, status and journal entry.

Every write lands in temporary asks, plans, activity and ui directories.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.session import asks, journal
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app

ORIGIN = {"origin": "http://testserver"}
EVIL = {"origin": "http://evil.example"}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
RIP = "Reinforced Iron Plate"
ABOUT = {"kind": "stage", "label": "stage 1", "ref": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch, projection, game):
    for name in ("plans_dir", "labels_dir", "activity_dir", "ui_dir", "pins_dir", "asks_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    journal.set_writer("web")
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def _ask(client, text="is stage 1 safe to switch on?", about=None):
    return client.post("/api/asks", json={"text": text, "about": about or ABOUT}, headers=ORIGIN)


def _journal():
    return [e for e in journal.read(WORLD) if e["kind"].startswith("ask.")]


def _plan(client):
    body = {"name": "rip", "args": {"objective": "min_machines", "exports": [RIP]}}
    reply = client.post("/api/plans", json=body, headers=ORIGIN)
    assert reply.status_code == 201, reply.text
    return reply.json()["key"]


def test_the_list_starts_empty(client):
    assert client.get("/api/asks").json() == {"version": 0, "asks": []}


def test_create_is_a_201_row_and_one_journal_entry(client):
    key = _plan(client)
    reply = _ask(client, about={**ABOUT, "plan": key, "rev": 1})
    assert reply.status_code == 201, reply.text
    row = reply.json()
    assert row["id"] == "ask:1" and row["state"] == "open" and row["plan_name"] == "rip"
    assert row["copy"] == "ask:1 is stage 1 safe to switch on?"
    assert row["about"] == {**ABOUT, "plan": key, "rev": 1}
    [entry] = _journal()
    assert entry["kind"] == "ask.add" and entry["args"] == {"n": 1}
    assert entry["plan"] == key and entry["rev"] == 1 and entry["actor"]["kind"] == "page"
    assert entry["text"] == "queued ask:1 “is stage 1 safe to switch on?”"
    listed = client.get("/api/asks").json()
    assert listed["version"] == 1 and [a["id"] for a in listed["asks"]] == ["ask:1"]


@pytest.mark.parametrize(
    "text, about",
    [("", ABOUT), ("x" * 201, ABOUT), ("q", {"kind": "nope", "label": "x", "ref": ""})],
)
def test_bad_asks_are_a_400_and_nothing_is_written(client, text, about):
    reply = _ask(client, text, about)
    assert reply.status_code == 400 and reply.json()["error"]
    assert client.get("/api/asks").json()["version"] == 0 and _journal() == []


def test_an_unknown_plan_in_about_is_a_404(client):
    reply = _ask(client, about={**ABOUT, "plan": "0000beef"})
    assert reply.status_code == 404 and reply.json() == {
        "error": "no plan “0000beef” in this world"
    }


def test_the_200th_live_ask_is_the_last(client, monkeypatch):
    monkeypatch.setattr(asks, "MAX_LIVE", 1)
    assert _ask(client).status_code == 201
    reply = _ask(client)
    assert reply.status_code == 400 and "already has 1 asks" in reply.json()["error"]


def test_delete_needs_the_current_rev_and_409s_with_the_fresh_row(client):
    _ask(client)
    asks.mark_seen(WORLD, [1], "Claude Code")
    stale = client.request("DELETE", "/api/asks/1", json={"rev": 1}, headers=ORIGIN)
    assert stale.status_code == 409
    body = stale.json()
    assert body["error"] == "ask:1 changed since you read it" and body["stale"] is True
    assert body["ask"]["rev"] == 2 and body["ask"]["state"] == "seen"
    assert body["ask"]["seen_by"] == "Claude Code"
    done = client.request("DELETE", "/api/asks/1", json={"rev": 2}, headers=ORIGIN)
    assert done.status_code == 200 and done.json() == {"ok": True, "n": 1}
    assert client.get("/api/asks").json()["asks"] == []
    again = client.request("DELETE", "/api/asks/1", json={"rev": 3}, headers=ORIGIN)
    assert again.status_code == 404 and again.json() == {"error": "ask:1 was deleted"}
    missing = client.request("DELETE", "/api/asks/5", json={"rev": 1}, headers=ORIGIN)
    assert missing.status_code == 404
    assert [e["kind"] for e in _journal()] == ["ask.add", "ask.drop"]
    assert _journal()[-1]["text"] == "deleted ask:1"


def test_the_guard_refuses_foreign_writes_and_hosts(client):
    assert (
        client.post("/api/asks", json={"text": "q", "about": ABOUT}, headers=EVIL).status_code
        == 403
    )
    _ask(client)
    assert client.request("DELETE", "/api/asks/1", json={"rev": 1}, headers=EVIL).status_code == 403
    assert client.post("/api/asks", json={"text": "q", "about": ABOUT}).status_code == 403
    assert client.get("/api/asks", headers={"host": "evil.example"}).status_code in (400, 403)
    assert client.request(
        "DELETE", "/api/asks/1", json={"rev": 1}, headers={**ORIGIN, "host": "evil.example"}
    ).status_code in (400, 403)
    assert client.get("/api/asks").json()["asks"][0]["rev"] == 1


def test_a_newer_schema_is_a_503(client):
    asks.path_for(WORLD).parent.mkdir(parents=True, exist_ok=True)
    asks.path_for(WORLD).write_text(json.dumps({"schema": 9, "asks": []}), encoding="utf-8")
    for reply in (
        client.get("/api/asks"),
        _ask(client),
        client.request("DELETE", "/api/asks/1", json={"rev": 1}, headers=ORIGIN),
    ):
        assert reply.status_code == 503 and reply.json()["newer_schema"] is True


def test_an_unreadable_save_is_a_404(client):
    client.app.state.load_state = lambda save=None, world=None: (_ for _ in ()).throw(OSError("x"))
    assert client.get("/api/asks").status_code == 404
    assert _ask(client).status_code == 404
