"""The watcher parses the save it just noticed, and everything that must not cost.

The parse is ~4 s and the watcher sees a save land three seconds before anyone asks about
it, so doing it there is most of the wait gone. The whole risk is in the word "there": this
runs inside the poll loop that serves the page, beside a request path that must not wait for
it, and it feeds a cache with an eviction policy of its own. Each of those is one test here.
"""

from __future__ import annotations

import asyncio
import pickle
import threading
import time

import pytest

from satisfactory_mcp.core.saveio import projection as proj
from satisfactory_mcp.interfaces.web import watch as watch_mod
from satisfactory_mcp.interfaces.web.watch import KIND_SAVE, SaveWatcher


async def _until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


@pytest.fixture
def saves(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()
    (root / "Han Solo_autosave_0.sav").write_bytes(b"not really a save")
    return root


@pytest.fixture
def warmed(monkeypatch):
    """Count what the pre-warm asked for, without parsing anything."""
    calls: list[float] = []

    def fake_load():
        calls.append(time.monotonic())

    monkeypatch.setattr(watch_mod, "load_projection", fake_load)
    return calls


def test_a_new_save_is_parsed_without_anybody_asking(saves, warmed):
    async def go():
        watcher = SaveWatcher(root=saves, notes=(), prewarm=True)
        await watcher.poll_once()
        await _until(lambda: warmed)

    asyncio.run(go())
    assert len(warmed) == 1, "the save the watcher just published was not pre-warmed"


def test_a_watcher_that_was_not_asked_to_pre_warm_does_not(saves, warmed):
    """The default, and the reason it is the default: every watcher in the suite points at
    a directory of invented ``.sav`` files, and pre-warming one spawns a parser subprocess
    that can only fail."""

    async def go():
        watcher = SaveWatcher(root=saves, notes=())
        await watcher.poll_once()
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert warmed == []


def test_a_tree_that_did_not_move_is_not_parsed_again(saves, warmed):
    """``poll_once`` publishes on the edge only, and the pre-warm has to hang off the same
    edge -- a parse per poll would be a 4 s subprocess every three seconds for ever."""

    async def go():
        watcher = SaveWatcher(root=saves, notes=(), prewarm=True)
        await watcher.poll_once()
        await _until(lambda: warmed)
        for _ in range(3):
            await watcher.poll_once()
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert len(warmed) == 1, "an unchanged save tree was parsed again"


def test_a_note_moving_does_not_start_a_parse(tmp_path, warmed):
    """Factory labels and stored plans are the other watched tree, and this project writes
    them itself. Nothing about a renamed factory changes the save, so a parse on that edge
    would be 4 s of subprocess for every name the player types."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "coal power.json").write_text("{}", encoding="utf-8")

    async def go():
        watcher = SaveWatcher(root=tmp_path / "gone", notes=(notes,), prewarm=True)
        found = await watcher.poll_once()
        assert [event.kind for event in found] == ["notes"]
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert warmed == []


def test_the_poll_does_not_wait_for_the_parse(saves, monkeypatch):
    """The pre-warm blocks for seconds and the loop it hangs off is serving the page.

    A pre-warm awaited rather than detached would stall ``poll_once``, and ``poll_once`` is
    what publishes the SSE event -- so the browser would learn about the save only after the
    parse it was supposed to be spared had finished.
    """
    releasing = threading.Event()
    monkeypatch.setattr(watch_mod, "load_projection", releasing.wait)

    async def go():
        watcher = SaveWatcher(root=saves, notes=(), prewarm=True)
        started = time.perf_counter()
        try:
            await watcher.poll_once()
            return time.perf_counter() - started
        finally:
            releasing.set()

    assert asyncio.run(go()) < 1.0, "the poll waited for the parse it started"


def test_a_failed_pre_warm_is_invisible(saves, monkeypatch):
    """A save the game had not finished writing is the ordinary reason this raises, and the
    request behind it resolves and parses the save itself. So the poll survives, the failure
    count stays at zero, and the SSE event still goes out."""

    def explode():
        raise proj.SaveError("the sidecar could not read it")

    monkeypatch.setattr(watch_mod, "load_projection", explode)

    async def go():
        watcher = SaveWatcher(root=saves, notes=(), prewarm=True)
        subscriber = watcher.subscribe()
        published = await watcher.poll_once()
        await _until(lambda: watcher._warming is not None and not watcher._warming.is_alive())
        return published, watcher.consecutive_failures, subscriber.qsize()

    published, failures, queued = asyncio.run(go())
    assert [event.kind for event in published] == [KIND_SAVE]
    assert failures == 0
    assert queued == 1, "a failed pre-warm swallowed the event the browser was waiting for"


def test_two_saves_in_a_row_do_not_run_two_parses_at_once(saves, monkeypatch):
    """Autosaves are five minutes apart and a parse is four seconds, so an overlap means
    something is already wrong -- and two 4 s subprocesses on the machine running the game
    is the wrong way to respond to it. The second edge is dropped; the save it would have
    warmed is the one the next request resolves anyway."""
    releasing = threading.Event()
    entered = threading.Semaphore(0)

    def blocking_load():
        entered.release()
        releasing.wait()

    monkeypatch.setattr(watch_mod, "load_projection", blocking_load)

    async def go():
        watcher = SaveWatcher(root=saves, notes=(), prewarm=True)
        await watcher.poll_once()
        await asyncio.to_thread(entered.acquire)
        (saves / "Han Solo_autosave_1.sav").write_bytes(b"a second save")
        await watcher.poll_once()
        await asyncio.sleep(0.05)
        try:
            return threading.active_count(), entered.acquire(blocking=False)
        finally:
            releasing.set()

    _threads, second_entry = asyncio.run(go())
    assert not second_entry, "a second parse started beside the one still running"


# ---- what the extra parses do to the cache ------------------------------


def test_a_save_being_read_survives_the_autosaves_nobody_asked_about(tmp_path, monkeypatch):
    """The eviction the pre-warm makes possible, and the reason ``_read_disk_cache`` touches.

    ``prune_cache`` keeps twelve pickles by modification time. Pre-warming writes one per
    autosave whether or not a browser is open, so twelve of them is about an hour -- and
    ordered by WRITE time that hour evicts the projection an LLM session has been reading
    all along, whose pickle was written once when the session started and never since.
    Ordered by USE it survives, which is what a twelve-entry cache is for.
    """
    monkeypatch.setattr(proj.config, "cache_dir", lambda: tmp_path)
    pinned = tmp_path / "save-pinnedkey.pkl"
    pinned.write_bytes(pickle.dumps({"header": {"filename": "Han Solo_020826-195005.sav"}}))

    for n in range(20):
        (tmp_path / f"save-autosave{n:02d}.pkl").write_bytes(pickle.dumps({"n": n}))
        # What every reader of the pinned save does, and all it does: a memo miss in a
        # second process reads the pickle rather than parsing.
        assert proj._read_disk_cache("pinnedkey") is not None
        proj.prune_cache()

    assert pinned.is_file(), "the save being read was evicted by autosaves nobody read"
    assert len(list(tmp_path.glob("save-*.pkl"))) == 12
