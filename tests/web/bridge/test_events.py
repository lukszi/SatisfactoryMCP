"""``/api/events``: the SSE stream, driven through the handler rather than the client.

Each app's watcher reads an empty save root, so only what a test writes produces an event.
``watch/test_watcher.py`` covers the machinery underneath; this file covers what reaches the wire.
"""

from __future__ import annotations

import asyncio
import json

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import Request

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.interfaces.web.app import create_app
from satisfactory_mcp.interfaces.web.routers.bridge import events as web_events
from satisfactory_mcp.interfaces.web.watch.events import WatchEvent

# --------------------------------------------------------------------- events


def _first_sse_chunk(app, since: float = 0.0) -> bytes:
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
            response = await web_events.events(request, since=since)
            assert response.media_type == "text/event-stream"
            async for chunk in response.body_iterator:
                return chunk  # closes the generator, which unsubscribes
            raise AssertionError("the stream ended without sending anything")
        finally:
            await app.state.watcher.stop()

    return asyncio.run(pull())


def _empty_save_root(monkeypatch, tmp_path) -> None:
    """Point the watched save root at an empty folder; the stores are private already."""
    root = tmp_path / "saves_root"
    root.mkdir()
    monkeypatch.setattr(config, "saves_root", lambda: root)


def test_events_keep_the_stream_alive_with_a_ping_comment(game, tmp_path, monkeypatch):
    """Empty watched directories produce no events, so the keepalive is what arrives."""
    _empty_save_root(monkeypatch, tmp_path)
    monkeypatch.setattr(web_events, "PING_SECONDS", 0.05)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    assert _first_sse_chunk(app) == b": ping\n\n"


def test_a_written_save_becomes_a_save_event(game, tmp_path, monkeypatch):
    """The watcher's whole job: a new mtime under the save root reaches the browser."""
    _empty_save_root(monkeypatch, tmp_path)
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
    _empty_save_root(monkeypatch, tmp_path)
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


def test_a_stream_whose_queue_overflowed_drains_and_then_ends(game, tmp_path, monkeypatch):
    _empty_save_root(monkeypatch, tmp_path)
    monkeypatch.setattr(web_events, "PING_SECONDS", 5.0)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    watcher = app.state.watcher
    queue = asyncio.Queue(maxsize=2)
    monkeypatch.setattr(watcher, "subscribe", lambda: watcher._subscribers.add(queue) or queue)
    event = WatchEvent("notes", "a.json", 1.0)

    async def pull() -> list[bytes]:
        request = Request(
            {"type": "http", "method": "GET", "path": "/api/events", "headers": [], "app": app}
        )
        response = await web_events.events(request)
        for _ in range(3):
            watcher._publish(event)
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(asyncio.wait_for(pull(), timeout=3.0))
    assert [c.startswith(b"event: notes") for c in chunks] == [True, True]
    assert watcher.was_dropped(queue) is False, "the closed stream did not unsubscribe"


def test_a_plan_commit_becomes_a_plans_event_carrying_its_summary(game, tmp_path, monkeypatch):
    """The chat-to-page direction: a commit any process appends reaches the browser with the
    words the page shows, so the page need not fetch just to say what happened."""
    _empty_save_root(monkeypatch, tmp_path)
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


def _find(path, n: int, ts: float) -> None:
    row = {
        "id": f"{path.stem}:{n}",
        "seq": n,
        "ts": ts,
        "actor": {"kind": "chat", "client": "claude-code", "pid": 7},
        "kind": "world.find",
        "args": {"view": "nodes", "params": {}},
        "text": f"find {n}",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_a_new_chat_journal_read_from_its_start_replays_only_after_since(
    game, tmp_path, monkeypatch
):
    """The page's open time is the cursor: a find chat made before it is history.

    The journal file is new to the tail, so the tail reads it from its start and holds its
    newest entry for the replay, however old that entry is.
    """
    _empty_save_root(monkeypatch, tmp_path)
    monkeypatch.setattr(web_events, "PING_SECONDS", 0.05)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    asyncio.run(app.state.watcher.tail_once())
    _find(config.activity_dir() / "W" / "chat-7.jsonl", 1, 1000.0)
    [held] = asyncio.run(app.state.watcher.tail_once())
    assert held.kind == "activity" and held.mtime == 1000.0

    assert _first_sse_chunk(app, since=1000.5) == b": ping\n\n"
    assert _first_sse_chunk(app, since=1000.0) == b": ping\n\n"
    chunk = _first_sse_chunk(app, since=999.0).decode()
    assert chunk.startswith("event: activity\ndata: ")
    assert json.loads(chunk.split("data: ", 1)[1])["id"] == "chat-7:1"
    assert _first_sse_chunk(app).startswith(b"event: activity\n")


def test_the_live_stream_withholds_entries_stamped_before_since(game, tmp_path, monkeypatch):
    """The tail publishes up to a tick after the write, so an entry written just before the
    page opened can arrive after it subscribed; it is history all the same."""
    _empty_save_root(monkeypatch, tmp_path)
    monkeypatch.setattr(web_events, "PING_SECONDS", 5.0)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    watcher = app.state.watcher
    asyncio.run(watcher.tail_once())
    path = config.activity_dir() / "W" / "chat-7.jsonl"

    async def pull() -> dict:
        request = Request(
            {"type": "http", "method": "GET", "path": "/api/events", "headers": [], "app": app}
        )
        response = await web_events.events(request, since=2000.0)
        stream = response.body_iterator
        _find(path, 1, 1999.5)
        _find(path, 2, 2000.5)
        await watcher.tail_once()
        try:
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=3.0)
        finally:
            await stream.aclose()
        return json.loads(chunk.decode().split("data: ", 1)[1])

    assert asyncio.run(pull())["id"] == "chat-7:2"


def test_since_is_a_documented_query_parameter(game):
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    params = app.openapi()["paths"]["/api/events"]["get"]["parameters"]
    assert [(p["name"], p["in"]) for p in params] == [("since", "query")]
