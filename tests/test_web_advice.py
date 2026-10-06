"""``/api/advice`` (docs/advisors_contract.md §5): the rows, hide, restore, refusals and the
journal entry per write. Every write lands in temporary advice, labels and activity dirs."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.advice import rules
from satisfactory_mcp.domain.session import journal
from satisfactory_mcp.interfaces.web.app import create_app

ORIGIN = {"origin": "http://testserver"}
EVIL = {"origin": "http://evil.example"}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
STALE_TOKEN = "sav:000000000000"


@pytest.fixture
def client(tmp_path, monkeypatch, labelled, game):
    for name in ("plans_dir", "activity_dir", "ui_dir", "advice_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    monkeypatch.setattr(rules, "_PLANS", {})
    journal.set_writer("web")
    app = create_app(state_loader=lambda save=None, world=None: labelled, game_loader=lambda: game)
    with TestClient(app) as c:
        yield c


def _journal():
    return [e for e in journal.read(WORLD) if e["kind"].startswith("advice.")]


def _row(client, kind="starved", subject="speedwire factory"):
    body = client.get("/api/advice").json()
    return next(r for r in body["active"] if r["kind"] == kind and r["subject"] == subject)


def test_the_list_sends_every_firing_row_ranked(client):
    body = client.get("/api/advice").json()
    assert body["version"] == 0 and body["hidden"] == [] and body["play_s"] > 0
    assert body["save_token"].startswith("sav:")
    kinds = [r["kind"] for r in body["active"]]
    assert kinds[0] == "unconnected" and kinds[-1] == "pickups"
    row = _row(client)
    assert row["id"].startswith("adv:") and row["tone"] == "blocked" and row["state"] == "active"
    assert row["machines"][0]["instance"] and row["reveal"] == ["machines"]
    assert row["rev"] == 0 and row["back"] is False and row["until_play_s"] is None
    assert {r["tone"] for r in body["active"]} <= {"blocked", "mid", "muted"}


def test_a_snooze_hides_it_and_journals_once(client):
    row = _row(client)
    reply = client.post(
        "/api/advice/hidden",
        json={"key": row["key"], "mode": "snooze", "hours": 1, "rev": 0},
        headers=ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    hidden = reply.json()
    assert hidden["state"] == "snoozed" and hidden["rev"] == 1 and hidden["by"] == "page"
    assert hidden["until_play_s"] == pytest.approx(hidden_play(client) + 3600.0)
    body = client.get("/api/advice").json()
    assert row["key"] not in {r["key"] for r in body["active"]}
    assert [r["id"] for r in body["hidden"]] == [row["id"]] and body["version"] == 1
    [entry] = _journal()
    assert entry["kind"] == "advice.hide" and entry["actor"]["kind"] == "page"
    assert entry["args"] == {"id": row["id"], "key": row["key"], "mode": "snooze"}
    assert entry["text"] == f"snoozed {row['id']} starved “speedwire factory” for 1 h of play"


def hidden_play(client) -> float:
    return client.get("/api/advice").json()["play_s"]


def test_restore_shows_it_again_and_journals(client):
    row = _row(client)
    client.post(
        "/api/advice/hidden", json={"key": row["key"], "mode": "dismiss", "rev": 0}, headers=ORIGIN
    )
    reply = client.request(
        "DELETE", f"/api/advice/hidden/{row['id']}", json={"rev": 1}, headers=ORIGIN
    )
    assert reply.status_code == 200 and reply.json() == {"ok": True, "id": row["id"]}
    assert _row(client)["rev"] == 0
    assert [e["kind"] for e in _journal()] == ["advice.hide", "advice.restore"]
    assert _journal()[-1]["text"] == f"restored {row['id']}"


def test_a_stale_rev_is_a_409_with_the_row_as_it_stands(client):
    row = _row(client)
    client.post(
        "/api/advice/hidden", json={"key": row["key"], "mode": "dismiss", "rev": 0}, headers=ORIGIN
    )
    again = client.post(
        "/api/advice/hidden", json={"key": row["key"], "mode": "dismiss", "rev": 0}, headers=ORIGIN
    )
    assert again.status_code == 409
    body = again.json()
    assert body["stale"] is True and body["row"]["state"] == "dismissed" and body["row"]["rev"] == 1
    gone = client.request(
        "DELETE", f"/api/advice/hidden/{row['id']}", json={"rev": 5}, headers=ORIGIN
    )
    assert gone.status_code == 409 and len(_journal()) == 1


def test_bad_bodies_are_refused_and_nothing_is_written(client):
    row = _row(client)
    assert (
        client.post(
            "/api/advice/hidden", json={"key": "nope|world", "mode": "dismiss"}, headers=ORIGIN
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/advice/hidden",
            json={"key": row["key"], "mode": "snooze", "hours": 99},
            headers=ORIGIN,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/advice/hidden", json={"key": row["key"], "mode": "forget"}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        client.request(
            "DELETE", f"/api/advice/hidden/{row['id']}", json={"rev": 0}, headers=ORIGIN
        ).status_code
        == 404
    )
    assert _journal() == [] and client.get("/api/advice").json()["version"] == 0


def test_the_guard_refuses_a_foreign_origin(client):
    row = _row(client)
    body = {"key": row["key"], "mode": "dismiss"}
    assert client.post("/api/advice/hidden", json=body, headers=EVIL).status_code == 403
    assert client.post("/api/advice/hidden", json=body).status_code == 403
    assert (
        client.request(
            "DELETE", f"/api/advice/hidden/{row['id']}", json={"rev": 1}, headers=EVIL
        ).status_code
        == 403
    )


def test_a_stale_save_token_is_refused(client):
    reply = client.get("/api/advice", params={"as_of": STALE_TOKEN})
    assert reply.status_code == 409 and reply.json()["stale"] is True


def test_a_newer_store_is_a_503_naming_the_advisories(client):
    path = config.advice_dir() / f"{WORLD}.json"
    path.write_text('{"schema": 2, "version": 1, "hidden": {}}', encoding="utf-8")
    reply = client.get("/api/advice")
    assert reply.status_code == 503 and reply.json()["newer_schema"] is True
    assert "hidden advisories" in reply.json()["error"]


def test_the_shapes_reach_the_published_schema(client):
    names = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("AdviceRow", "AdviceResponse", "AdviceHideBody", "AdviceStaleResponse"):
        assert name in names
