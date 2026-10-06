"""``/api/asks`` (docs/planner-p4_contract.md §5 and §7): every route, status and journal entry."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.planning import asks, journal
from tests.support.reference_world import FIXTURE_WORLD, RIP
from tests.support.web import PAGE_ORIGIN, create_plan, journal_entries

EVIL = {"origin": "http://evil.example"}
ABOUT = {"kind": "stage", "label": "stage 1", "ref": "1"}


@pytest.fixture(autouse=True)
def _web_writes():
    journal.set_writer("web")


def _ask(fresh_state_client, text="is stage 1 safe to switch on?", about=None):
    return fresh_state_client.post(
        "/api/asks", json={"text": text, "about": about or ABOUT}, headers=PAGE_ORIGIN
    )


def test_the_list_starts_empty(fresh_state_client):
    assert fresh_state_client.get("/api/asks").json() == {"version": 0, "asks": []}


def test_create_is_a_201_row_and_one_journal_entry(fresh_state_client):
    key = create_plan(fresh_state_client, "rip", {"objective": "min_machines", "exports": [RIP]})[
        "key"
    ]
    reply = _ask(fresh_state_client, about={**ABOUT, "plan": key, "rev": 1})
    assert reply.status_code == 201, reply.text
    row = reply.json()
    assert row["id"] == "ask:1" and row["state"] == "open" and row["plan_name"] == "rip"
    assert row["copy"] == "ask:1 is stage 1 safe to switch on?"
    assert row["about"] == {**ABOUT, "plan": key, "rev": 1}
    [entry] = journal_entries(FIXTURE_WORLD, "ask.")
    assert entry["kind"] == "ask.add" and entry["args"] == {"n": 1}
    assert entry["plan"] == key and entry["rev"] == 1 and entry["actor"]["kind"] == "page"
    assert entry["text"] == "queued ask:1 “is stage 1 safe to switch on?”"
    listed = fresh_state_client.get("/api/asks").json()
    assert listed["version"] == 1 and [a["id"] for a in listed["asks"]] == ["ask:1"]


@pytest.mark.parametrize(
    "text, about",
    [("", ABOUT), ("x" * 201, ABOUT), ("q", {"kind": "nope", "label": "x", "ref": ""})],
)
def test_bad_asks_are_a_400_and_nothing_is_written(fresh_state_client, text, about):
    reply = _ask(fresh_state_client, text, about)
    assert reply.status_code == 400 and reply.json()["error"]
    assert (
        fresh_state_client.get("/api/asks").json()["version"] == 0
        and journal_entries(FIXTURE_WORLD, "ask.") == []
    )


def test_an_unknown_plan_in_about_is_a_404(fresh_state_client):
    reply = _ask(fresh_state_client, about={**ABOUT, "plan": "0000beef"})
    assert reply.status_code == 404 and reply.json() == {
        "error": "no plan “0000beef” in this world"
    }


def test_the_200th_live_ask_is_the_last(fresh_state_client, monkeypatch):
    monkeypatch.setattr(asks, "MAX_LIVE", 1)
    assert _ask(fresh_state_client).status_code == 201
    reply = _ask(fresh_state_client)
    assert reply.status_code == 400 and "already has 1 asks" in reply.json()["error"]


def test_delete_needs_the_current_rev_and_409s_with_the_fresh_row(fresh_state_client):
    _ask(fresh_state_client)
    asks.mark_seen(FIXTURE_WORLD, [1], "Claude Code")
    stale = fresh_state_client.request(
        "DELETE", "/api/asks/1", json={"rev": 1}, headers=PAGE_ORIGIN
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["error"] == "ask:1 changed since you read it" and body["stale"] is True
    assert body["ask"]["rev"] == 2 and body["ask"]["state"] == "seen"
    assert body["ask"]["seen_by"] == "Claude Code"
    done = fresh_state_client.request("DELETE", "/api/asks/1", json={"rev": 2}, headers=PAGE_ORIGIN)
    assert done.status_code == 200 and done.json() == {"ok": True, "n": 1}
    assert fresh_state_client.get("/api/asks").json()["asks"] == []
    again = fresh_state_client.request(
        "DELETE", "/api/asks/1", json={"rev": 3}, headers=PAGE_ORIGIN
    )
    assert again.status_code == 404 and again.json() == {"error": "ask:1 was deleted"}
    missing = fresh_state_client.request(
        "DELETE", "/api/asks/5", json={"rev": 1}, headers=PAGE_ORIGIN
    )
    assert missing.status_code == 404
    assert [e["kind"] for e in journal_entries(FIXTURE_WORLD, "ask.")] == ["ask.add", "ask.drop"]
    assert journal_entries(FIXTURE_WORLD, "ask.")[-1]["text"] == "deleted ask:1"


def test_the_guard_refuses_foreign_writes_and_hosts(fresh_state_client):
    assert (
        fresh_state_client.post(
            "/api/asks", json={"text": "q", "about": ABOUT}, headers=EVIL
        ).status_code
        == 403
    )
    _ask(fresh_state_client)
    assert (
        fresh_state_client.request(
            "DELETE", "/api/asks/1", json={"rev": 1}, headers=EVIL
        ).status_code
        == 403
    )
    assert (
        fresh_state_client.post("/api/asks", json={"text": "q", "about": ABOUT}).status_code == 403
    )
    assert fresh_state_client.get("/api/asks", headers={"host": "evil.example"}).status_code in (
        400,
        403,
    )
    assert fresh_state_client.request(
        "DELETE", "/api/asks/1", json={"rev": 1}, headers={**PAGE_ORIGIN, "host": "evil.example"}
    ).status_code in (400, 403)
    assert fresh_state_client.get("/api/asks").json()["asks"][0]["rev"] == 1


def test_a_newer_schema_is_a_503(fresh_state_client):
    asks.path_for(FIXTURE_WORLD).parent.mkdir(parents=True, exist_ok=True)
    asks.path_for(FIXTURE_WORLD).write_text(json.dumps({"schema": 9, "asks": []}), encoding="utf-8")
    for reply in (
        fresh_state_client.get("/api/asks"),
        _ask(fresh_state_client),
        fresh_state_client.request("DELETE", "/api/asks/1", json={"rev": 1}, headers=PAGE_ORIGIN),
    ):
        assert reply.status_code == 503 and reply.json()["newer_schema"] is True


def test_an_unreadable_save_is_a_404(fresh_state_client):
    fresh_state_client.app.state.load_state = lambda save=None, world=None: (_ for _ in ()).throw(
        OSError("x")
    )
    assert fresh_state_client.get("/api/asks").status_code == 404
    assert _ask(fresh_state_client).status_code == 404
