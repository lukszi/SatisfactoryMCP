"""The watcher's own mechanism: fan-out, unsubscribe, the bound, the trees, the failures.

``test_events.py`` already covers what the watcher is FOR -- a written save becomes an
``event: save`` on the wire, and a stream that opens late still gets the replay. What it does
not touch is the machinery underneath, and that machinery is where the interesting failures
are, because every one of them is silent:

  * a shared queue instead of one per subscriber, and the first browser to read an event is
    the only browser that ever sees it. The second tab just stops updating.
  * a subscriber that is never discarded, and every closed stream leaves a queue behind that
    the publisher goes on filling for the life of the process.
  * an unbounded queue, and a browser that stopped reading is a slow leak; a bounded one that
    raises instead of dropping takes the poll loop down with it on the first full queue.
  * a poll that raises forever while the loop keeps sleeping, which looks exactly like a world
    where nobody has saved.

None of the four shows up as an error anywhere. They show up as a page that is quietly stale,
which is the one failure the whole module was written to prevent -- so they are pinned here
rather than left to the endpoint test, which would pass through all four.

No game install, no save file, no event loop of the caller's: every test drives its own
through ``asyncio.run``, which is what the two async tests in ``test_events.py`` do and is
why this suite still needs no ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.session import journal
from satisfactory_mcp.interfaces.web.watch.events import (
    KIND_ACTIVITY,
    KIND_NOTES,
    KIND_PLANS,
    KIND_SAVE,
    WatchEvent,
)
from satisfactory_mcp.interfaces.web.watch.watcher import QUEUE_MAX, SaveWatcher


def _event(n: int) -> WatchEvent:
    return WatchEvent(kind=KIND_SAVE, filename=f"save{n}.sav", mtime=float(1000 + n))


async def _until(predicate, timeout: float = 5.0) -> bool:
    """Wait for a condition rather than for a duration.

    A fixed sleep would either be flaky on a loaded machine or slow on every other one, and
    the thing being waited for here is a background task getting a turn -- which is a
    scheduling question, not a timing one.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


def _drain(q: asyncio.Queue) -> list[str]:
    return [q.get_nowait().filename for _ in range(q.qsize())]


# ------------------------------------------------------------------ fan-out


def test_every_subscriber_gets_every_event(tmp_path):
    """One queue per browser, not one queue shared between them.

    This is the whole reason ``_subscribers`` is a set of queues rather than a single queue
    the endpoint reads: with a shared one, ``get`` hands the event to exactly one waiter, so
    opening the map in a second tab would silently stop the first tab updating. Two
    subscribers, one publish, and both have to hold the same event.
    """

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        first, second = watcher.subscribe(), watcher.subscribe()
        watcher._publish(_event(1))
        return _drain(first), _drain(second)

    first, second = asyncio.run(go())
    assert first == ["save1.sav"], "the first subscriber missed the event"
    assert second == ["save1.sav"], "the second subscriber missed it -- the queue is shared"


def test_unsubscribing_stops_the_publisher_writing_to_that_queue(tmp_path):
    """A closed stream is a queue nobody will ever read again.

    ``/api/events`` unsubscribes in the ``finally`` of its generator, so this is what happens
    on every tab close, every reload and every navigation away. A publisher that went on
    filling the queue would hold the events, the queue and the browser's whole payload alive
    for the lifetime of the server, once per page load.
    """

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        staying, leaving = watcher.subscribe(), watcher.subscribe()
        watcher.unsubscribe(leaving)
        watcher._publish(_event(1))
        return _drain(staying), _drain(leaving), len(watcher._subscribers)

    staying, leaving, remaining = asyncio.run(go())
    assert staying == ["save1.sav"]
    assert leaving == [], "an unsubscribed queue is still being written to"
    assert remaining == 1, "unsubscribe left the subscriber in the set"


def test_unsubscribing_twice_is_not_an_error(tmp_path):
    """``discard``, not ``remove``: the endpoint's ``finally`` can run after an exception
    path that already unsubscribed, and a KeyError there would replace a handled
    disconnection with a traceback in the server log."""

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        q = watcher.subscribe()
        watcher.unsubscribe(q)
        watcher.unsubscribe(q)
        return len(watcher._subscribers)

    assert asyncio.run(go()) == 0


# ------------------------------------------------------------------ the bound


def test_a_full_queue_drops_instead_of_raising(tmp_path):
    """QUEUE_MAX + 1 events into one subscriber: bounded, and the publish does not raise.

    The raise is the half that matters, and it is not hypothetical: ``_publish`` is called
    from ``poll_once``, which is called from ``_run``'s loop. An unhandled ``QueueFull`` there
    would count as a failed poll for every subsequent poll too -- one browser that stopped
    reading would stop the watcher telling ANY browser anything.

    WHICH event is dropped is asserted because the answer is not the one the module docstring
    leads a reader to expect. ``put_nowait`` fills the queue and refuses the overflow, so the
    events kept are the OLDEST QUEUE_MAX and the newest is discarded -- the opposite way round
    from "the newest save is the only one worth telling it about". That is not a bug in
    practice, because these are edge triggers: whichever of them a browser reads, it refetches
    and gets the state as it is now, not as it was when the event was queued. It is pinned
    here so that a change of mind about it has to be a change to this line.
    """

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        q = watcher.subscribe()
        for n in range(QUEUE_MAX + 1):
            watcher._publish(_event(n))  # must not raise
        return q.maxsize, _drain(q)

    maxsize, held = asyncio.run(go())
    assert maxsize == QUEUE_MAX, "the subscriber queue is unbounded"
    assert held == [f"save{n}.sav" for n in range(QUEUE_MAX)]


def test_an_overflowed_subscriber_is_cut_so_its_stream_ends_and_the_page_resyncs(tmp_path):
    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        q = watcher.subscribe()
        for n in range(QUEUE_MAX):
            watcher._publish(_event(n))
        before = watcher.was_dropped(q)
        watcher._publish(_event(QUEUE_MAX))
        _drain(q)
        watcher._publish(_event(QUEUE_MAX + 1))
        after = watcher.was_dropped(q), q.qsize()
        watcher.unsubscribe(q)
        return before, after, watcher.was_dropped(q)

    before, after, forgotten = asyncio.run(go())
    assert before is False
    assert after == (True, 0), "a cut queue kept receiving events"
    assert forgotten is False


def test_one_stalled_subscriber_does_not_starve_the_others(tmp_path):
    """The point of per-subscriber bounds: the drop is local to the browser that caused it."""

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        stalled, healthy = watcher.subscribe(), watcher.subscribe()
        for n in range(QUEUE_MAX + 1):
            watcher._publish(_event(n))
            if healthy.qsize():
                healthy.get_nowait()  # a browser that is keeping up
        return stalled.qsize(), healthy.qsize()

    stalled, healthy = asyncio.run(go())
    assert stalled == QUEUE_MAX, "the stalled subscriber did not fill"
    assert healthy == 0, "a reader that kept up was penalised for the one that did not"


# ------------------------------------------------------------------ the two trees


def test_each_tree_is_remembered_apart_from_the_other(tmp_path):
    """Two trees, two baselines -- not one ``latest`` compared against both.

    With a single baseline every poll would find the other tree's newest file where it left
    its own, so nothing would ever equal the last thing seen and every quiet poll would
    publish two events. A browser told to refetch every 3 s for the life of the session is
    the one failure a poll-based watcher can produce that nobody sees in a log.
    """
    saves, notes = tmp_path / "saves", tmp_path / "notes"
    saves.mkdir()
    notes.mkdir()
    (saves / "Han Solo_autosave_0.sav").write_bytes(b"not really a save")
    (notes / "coal power.json").write_text("{}", encoding="utf-8")

    async def go():
        watcher = SaveWatcher(root=saves, notes=(notes,))
        first = await watcher.poll_once()
        second = await watcher.poll_once()
        return first, second, watcher.latest

    first, second, latest = asyncio.run(go())
    assert {event.kind for event in first} == {KIND_SAVE, KIND_NOTES}
    assert second == [], "a poll over an unchanged disk published something"
    assert latest[KIND_SAVE].filename == "Han Solo_autosave_0.sav"
    assert latest[KIND_NOTES].filename == "coal power.json"


# ------------------------------------------------------------------ the failure count


def test_the_failure_count_says_the_watcher_is_broken_now_and_not_that_it_once_was(tmp_path):
    """``consecutive_failures`` is a level, not a total, and the loop survives the failures.

    The failure mode it exists for is the one that repeats forever: a poll that raises on
    every pass while the loop goes on sleeping for the life of the server, telling nobody,
    with every connected browser sat on a page that never updates again. Silence and "no save
    has changed" look exactly alike from the outside.

    So both halves are asserted -- the count climbs while the poll keeps raising, and it
    returns to zero (rather than staying at its high-water mark) the moment one gets through.
    The recovery half is what makes the number readable at all: a total would mean "this
    server has had a bad day at some point", which is not a thing anyone can act on.
    """

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=(), interval=0.01)
        broken = {"now": True}

        def scan():
            if broken["now"]:
                raise OSError("the save root went away mid-poll")
            return [_event(7)]

        watcher.scan = scan  # type: ignore[method-assign]
        await watcher.start()
        try:
            climbed = await _until(lambda: watcher.consecutive_failures >= 3)
            peak = watcher.consecutive_failures
            broken["now"] = False
            recovered = await _until(lambda: watcher.latest)
            return climbed, peak, recovered, watcher.consecutive_failures, watcher.latest
        finally:
            await watcher.stop()

    climbed, peak, recovered, after, latest = asyncio.run(go())
    assert climbed, f"the failures were not counted (stuck at {peak})"
    assert recovered, "the loop stopped polling after the failures instead of carrying on"
    assert after == 0, f"the count is cumulative rather than consecutive ({after})"
    assert latest == {KIND_SAVE: _event(7)}


def test_stopping_a_watcher_that_never_started_is_a_no_op(tmp_path):
    """``create_app`` builds a watcher whether or not the app's lifespan ever ran, so the
    shutdown path has to tolerate a task that does not exist."""

    async def go():
        watcher = SaveWatcher(root=tmp_path, notes=())
        await watcher.stop()
        await watcher.start()
        await watcher.stop()
        await watcher.stop()
        return watcher._task

    assert asyncio.run(go()) is None


# ------------------------------------------------------------------ the plan and journal tail


@pytest.fixture
def logs(tmp_path):
    """A save root with no saves in it, beside the private plan and activity logs."""
    return tmp_path


def _tail(watcher: SaveWatcher) -> list[WatchEvent]:
    return asyncio.run(watcher.tail_once())


CHAT = Actor("chat", "claude-code", 7)
PAGE = Actor("page", "", 8)


def test_the_first_tail_is_a_baseline_and_announces_nothing(logs):
    PlanLog("W").create("old plan", {}, actor=CHAT)
    watcher = SaveWatcher(root=logs, notes=())
    assert _tail(watcher) == []
    assert _tail(watcher) == []


def test_new_commits_are_one_plans_event_per_plan_per_tick(logs):
    log = PlanLog("W")
    key = log.create("north", {}, actor=CHAT).key
    watcher = SaveWatcher(root=logs, notes=())
    _tail(watcher)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=CHAT)
    log.push(key, 2, [{"op": "rename", "name": "south"}], actor=PAGE)
    [event] = _tail(watcher)
    assert event.kind == KIND_PLANS
    data = event.as_dict()
    assert data["world"] == "W" and data["key"] == key and data["name"] == "south"
    assert (data["from_rev"], data["rev"]) == (1, 3)
    assert [a["display"] for a in data["actors"]] == ["Claude Code", "page"]
    assert data["text"] == 'v3 page: renamed "north"→"south"'
    assert data["forgotten"] is False and data["ts"] == event.mtime
    assert _tail(watcher) == []


def test_a_plan_created_after_the_baseline_is_announced_from_its_create(logs):
    watcher = SaveWatcher(root=logs, notes=())
    _tail(watcher)
    PlanLog("W").create("fresh", {}, actor=PAGE)
    [event] = _tail(watcher)
    assert event.as_dict()["rev"] == 1 and event.as_dict()["from_rev"] == 0


def test_a_torn_tail_waits_for_its_newline(logs):
    log = PlanLog("W")
    key = log.create("north", {}, actor=CHAT).key
    watcher = SaveWatcher(root=logs, notes=())
    _tail(watcher)
    ops = log.root / key / "ops.jsonl"
    with open(ops, "ab") as handle:
        handle.write(b'{"rev": 2, "half')
    assert _tail(watcher) == []
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 1}], actor=CHAT)
    [event] = _tail(watcher)
    assert event.as_dict()["rev"] == 2


def test_each_journal_entry_is_an_activity_event(logs):
    journal.set_writer("chat")
    watcher = SaveWatcher(root=logs, notes=())
    _tail(watcher)
    journal.append("W", "plan.solve", actor=CHAT, args={"sloops": 2}, text="solved")
    journal.append("W", "plan.view", actor=CHAT, plan="a1b2c3d4", rev=3)
    events = _tail(watcher)
    assert [e.kind for e in events] == [KIND_ACTIVITY, KIND_ACTIVITY]
    first, second = (e.as_dict() for e in events)
    assert first["kind"] == "plan.solve" and first["args"] == {"sloops": 2}
    assert first["actor"]["display"] == "Claude Code" and first["world"] == "W"
    assert (second["plan"], second["rev"]) == ("a1b2c3d4", 3)
    assert watcher.latest[KIND_ACTIVITY] == events[-1]


def test_the_tail_publishes_to_subscribers_and_runs_only_when_asked(logs):
    async def go():
        quiet = SaveWatcher(root=logs, notes=(), interval=60)
        await quiet.start()
        tailing = SaveWatcher(root=logs, notes=(), interval=60, tail=True, tail_interval=0.01)
        q = tailing.subscribe()
        await tailing.start()
        try:
            await _until(lambda: tailing.log_tail._offsets is not None)
            PlanLog("W").create("fresh", {}, actor=PAGE)
            await _until(lambda: q.qsize() > 0)
            return quiet._tail_task, q.get_nowait()
        finally:
            await quiet.stop()
            await tailing.stop()

    idle, event = asyncio.run(go())
    assert idle is None
    assert event.kind == KIND_PLANS


def test_plan_logs_and_snapshots_no_longer_fire_notes(tmp_path):
    plans = config.plans_dir()
    (plans / "W" / "a1b2c3d4" / "snap").mkdir(parents=True)
    (plans / "W" / "a1b2c3d4" / "snap" / "1.json").write_text("{}", encoding="utf-8")
    watcher = SaveWatcher(root=tmp_path / "saves")
    assert asyncio.run(watcher.poll_once()) == []
    (plans / "W.json").write_text("{}", encoding="utf-8")
    [event] = asyncio.run(watcher.poll_once())
    assert event.kind == KIND_NOTES and event.filename == "W.json"
