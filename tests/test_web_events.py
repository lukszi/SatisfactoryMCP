"""``/api/events``: the SSE stream, driven through the handler rather than the client.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

No ``client`` fixture here, and that is the point: this endpoint reads no world at all, so
what each test needs is an app whose watcher is pointed at empty temporary directories --
the reader's real save root and real label directory would make these tests about timing
rather than about shape, and a machine that happens to have named one factory would put an
``event: notes`` where the keepalive test expects silence.
``test_watch.py`` covers the machinery underneath; this file covers what reaches the wire.
"""

from __future__ import annotations

import asyncio
import json

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import Request

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.interfaces.web.app import create_app
from satisfactory_mcp.interfaces.web.routers import events as web_events

# --------------------------------------------------------------------- events


def _first_sse_chunk(app) -> bytes:
    """Open the event stream, take one chunk, hang up.

    Driven through the endpoint rather than through ``TestClient``: an SSE response is
    an endless generator, and TestClient's portal deadlocks on teardown waiting for one
    to finish. This exercises the real generator -- the real watcher, the real ping
    constant, the real unsubscribe on close -- and terminates.
    """

    async def pull() -> bytes:
        await app.state.watcher.start()
        try:
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/events",
                    "headers": [],
                    "query_string": b"",
                    "app": app,
                }
            )
            response = await web_events.events(request)
            assert response.media_type == "text/event-stream"
            async for chunk in response.body_iterator:
                return chunk  # closes the generator, which unsubscribes
            raise AssertionError("the stream ended without sending anything")
        finally:
            await app.state.watcher.stop()

    return asyncio.run(pull())


def _empty_roots(monkeypatch, tmp_path) -> None:
    """Point every watched and tailed tree at empty temporary directories.

    All of them, not just the save root: the labels, plans and activity directories live in
    the reader's own app-data folder, and any file in one would make the watcher publish
    before the test had written anything.
    """
    for name in ("saves_root", "labels_dir", "plans_dir", "activity_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)


def test_events_keep_the_stream_alive_with_a_ping_comment(game, tmp_path, monkeypatch):
    """Empty watched directories produce no events, so the keepalive is what arrives."""
    _empty_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(web_events, "PING_SECONDS", 0.05)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    assert _first_sse_chunk(app) == b": ping\n\n"


def test_a_written_save_becomes_a_save_event(game, tmp_path, monkeypatch):
    """The watcher's whole job: a new mtime under the save root reaches the browser."""
    _empty_roots(monkeypatch, tmp_path)
    saves = config.saves_root()
    (saves / "nested").mkdir()
    (saves / "nested" / "Han Solo_autosave_0.sav").write_bytes(b"not really a save")
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)

    async def watch_once():
        found = await app.state.watcher.poll_once()
        assert len(found) == 1, found
        return found[0]

    event = asyncio.run(watch_once())
    assert event.filename == "Han Solo_autosave_0.sav"
    assert event.mtime > 0

    # A stream that opens after the change still learns about it: the watcher replays
    # its latest event to a new subscriber, so a browser started mid-session draws the
    # current world instead of waiting for the next autosave.
    chunk = _first_sse_chunk(app).decode()
    assert chunk.startswith("event: save\ndata: ")
    # ``save_token`` is null here and that is the branch worth pinning: the file written
    # above is seventeen bytes of "not really a save", so the header read that would name
    # the world state fails -- and the event still says a write happened, which is the half
    # the page acts on. A stream that dropped the event over an unreadable file would leave
    # the page presenting stale data as live.
    assert json.loads(chunk.split("data: ", 1)[1]) == {
        "filename": "Han Solo_autosave_0.sav",
        "mtime": event.mtime,
        "save_token": None,
    }


def test_a_named_factory_becomes_a_notes_event(game, tmp_path, monkeypatch):
    """The other tree, and the reason it exists.

    ``name_factory`` and ``site_plan`` write here and never to a ``.sav``, so the one moment
    the two halves are used together -- name it, then look at the map -- produced no event at
    all while the watcher globbed ``*.sav``. It arrives under its own event name, because
    what a browser refetches for a label is not what it refetches for an autosave.
    """
    _empty_roots(monkeypatch, tmp_path)
    (config.labels_dir() / "coal power.json").write_text("{}", encoding="utf-8")
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)

    async def watch_once():
        found = await app.state.watcher.poll_once()
        assert len(found) == 1, found
        return found[0]

    event = asyncio.run(watch_once())
    assert event.filename == "coal power.json"

    chunk = _first_sse_chunk(app).decode()
    assert chunk.startswith("event: notes\ndata: ")
    assert json.loads(chunk.split("data: ", 1)[1]) == {
        "filename": "coal power.json",
        "mtime": event.mtime,
    }


def test_a_plan_commit_becomes_a_plans_event_carrying_its_summary(game, tmp_path, monkeypatch):
    """The chat-to-page direction: a commit any process appends reaches the browser with the
    words the page shows, so the page need not fetch just to say what happened."""
    _empty_roots(monkeypatch, tmp_path)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    log = PlanLog("W")
    key = log.create("north hmf", {}, actor=Actor("page")).key
    asyncio.run(app.state.watcher.tail_once())
    chat = Actor("chat", "claude-code", 1)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=chat)
    asyncio.run(app.state.watcher.tail_once())

    chunk = _first_sse_chunk(app).decode()
    assert chunk.startswith("event: plans\ndata: ")
    data = json.loads(chunk.split("data: ", 1)[1])
    assert data["key"] == key and data["name"] == "north hmf"
    assert (data["from_rev"], data["rev"]) == (1, 2)
    assert data["text"] == "v2 Claude Code: sloops 0→4"
    assert data["actors"] == [{**chat.to_dict(), "display": "Claude Code"}]
