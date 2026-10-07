"""Notice that a save, a note or a plan changed, tell every browser, parse the save.

Two trees, because two things move under a player who is using both halves at once: the game
writes ``.sav`` files, and this project writes factory labels and stored plans beside them.
They are published as two SSE event names so the page can refetch the half that moved.

Polling, not a filesystem watch: Satisfactory rewrites an autosave in place and the write is
not atomic, so an inotify-style event fires mid-write and a reader gets a torn file, whereas
a poll of the modification times can only observe the mtime the finished write stamped.
Fan-out is one ``asyncio.Queue`` per subscriber, because a shared queue means the first
browser to read an event is the only one that sees it. The queues are bounded and drop
rather than block: a browser that has stopped reading has gone away, and these are edge
triggers.

The plan logs, the activity journal and the settings file are tailed by ``tail.LogTail``
on a faster tick of their own.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterable
from pathlib import Path

from .... import config
from ....core.saveio.projection import load_projection
from .events import KIND_NOTES, KIND_SAVE, WatchEvent
from .tail import LogTail

__all__ = ["POLL_SECONDS", "QUEUE_MAX", "TAIL_SECONDS", "SaveWatcher"]

log = logging.getLogger(__name__)

#: Slow enough to be free, fast enough that a manual save shows up before the player
#: has alt-tabbed back to the browser.
POLL_SECONDS = 3.0

#: The plan-log and journal tail: a stat per file, for ~1 s from a tool returning to the page.
TAIL_SECONDS = 0.5

#: Per-subscriber backlog. Plan and activity events carry data rather than a trigger, so a
#: burst of commits has to fit.
QUEUE_MAX = 32


def _warm_newest() -> None:
    """Resolve and parse the newest save, discarding everything including the failures.

    A failure here has to be invisible: the request that follows resolves the save itself
    and parses it itself, so the only thing a pre-warm can cost is the parse it saved.
    ``debug``, not ``warning``, because the ordinary reason this raises is a save the game
    had not finished writing when the poll saw it, which the next poll fixes.
    """
    try:
        load_projection()
    except Exception:
        log.debug("pre-warming the newest save failed", exc_info=True)


def _newest_file_event(kind: str, roots: Iterable[tuple[Path, str]]) -> WatchEvent | None:
    """The newest file matching any ``(root, pattern)``, as an event of ``kind``."""
    newest: tuple[float, str] | None = None
    for root, pattern in roots:
        try:
            for path in root.glob(pattern):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, path.name)
        except OSError:
            continue
    if newest is None:
        return None
    return WatchEvent(kind=kind, filename=newest[1], mtime=newest[0])


class SaveWatcher:
    """Polls the watched trees and fans changes out to per-subscriber queues."""

    def __init__(
        self,
        root: Path | None = None,
        notes: Iterable[Path] | None = None,
        interval: float = POLL_SECONDS,
        prewarm: bool = False,
        tail: bool = False,
        tail_interval: float = TAIL_SECONDS,
    ) -> None:
        #: ``None`` means "ask config every scan", so a test that repoints
        #: ``config.saves_root`` is obeyed without rebuilding the watcher.
        self._root = root
        #: The same, for the label and plan directories.
        self._notes = None if notes is None else tuple(notes)
        self.interval = interval
        #: Off unless asked for, because a watcher pointed at a directory of made-up
        #: ``.sav`` files -- which is every watcher in the suite -- would spawn a parser
        #: subprocess for one on its first poll.
        self.prewarm = prewarm
        #: Off unless asked for, as ``prewarm`` is: only the served instance tails the
        #: reader's own plan and activity directories.
        self.tail = tail
        self.tail_interval = tail_interval
        self.log_tail = LogTail()
        self._warming: threading.Thread | None = None
        self._subscribers: set[asyncio.Queue[WatchEvent]] = set()
        self._dropped: set[asyncio.Queue[WatchEvent]] = set()
        self._task: asyncio.Task[None] | None = None
        self._tail_task: asyncio.Task[None] | None = None
        #: The newest event of each kind, replayed to every new subscriber.
        self.latest: dict[str, WatchEvent] = {}
        #: Polls that raised in a row, zeroed by any poll that gets through: "the watcher is
        #: broken now", never "the watcher hiccuped once in March".
        self.consecutive_failures = 0

    # ---- subscription ---------------------------------------------------

    def subscribe(self) -> asyncio.Queue[WatchEvent]:
        q: asyncio.Queue[WatchEvent] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WatchEvent]) -> None:
        self._subscribers.discard(q)
        self._dropped.discard(q)

    def was_dropped(self, q: asyncio.Queue[WatchEvent]) -> bool:
        """Whether ``q`` overflowed and was dropped: its stream ends so the browser resyncs."""
        return q in self._dropped

    def publish(self, event: WatchEvent) -> None:
        """Hold ``event`` as its kind's newest and fan it out, for a publisher outside the poll."""
        self.latest[event.kind] = event
        self._publish(event)

    def _publish(self, event: WatchEvent) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(q)
                self._dropped.add(q)

    # ---- the poll -------------------------------------------------------

    def root(self) -> Path:
        return self._root if self._root is not None else config.saves_root()

    def notes_roots(self) -> tuple[tuple[Path, str], ...]:
        """Labels anywhere in their tree; plans only as the legacy top-level file."""
        if self._notes is not None:
            return tuple((root, "**/*.json") for root in self._notes)
        return ((config.labels_dir(), "**/*.json"), (config.plans_dir(), "*.json"))

    def scan(self) -> list[WatchEvent]:
        """The newest file in each watched tree, for the trees that have one.

        Blocking, so it is called through ``asyncio.to_thread``: a disk walk on the event
        loop stalls the requests it is serving.

        Newest-mtime, which is what makes a rewritten label a change. A DELETED note is
        noticed only when it was the newest file in its tree.
        """
        found = [
            _newest_file_event(KIND_SAVE, ((self.root(), "**/*.sav"),)),
            _newest_file_event(KIND_NOTES, self.notes_roots()),
        ]
        return [event for event in found if event is not None]

    def _warm(self) -> None:
        """Parse the save that just landed, so the reads after it do not have to.

        Its own daemon thread, never the poll loop and never a request: this blocks for the
        ~4 s the parser subprocess takes, and the loop it is started from is serving the
        page. Nothing joins the thread and nothing reads its return value -- the projection
        cache is the whole result, and a request that arrives while it is still parsing
        joins the same flight rather than starting a second one, so the pre-warm is only
        ever early and never extra work.

        One at a time. A pre-warm still running when the next save lands is parsing a file
        that is now one autosave out of date, but starting a second parse beside it puts
        two 4 s subprocesses on the machine running the game, and the next poll -- three
        seconds later, on a tree that has not moved since -- would start a third.
        """
        if self._warming is not None and self._warming.is_alive():
            return
        self._warming = threading.Thread(target=_warm_newest, name="save-prewarm", daemon=True)
        self._warming.start()

    async def poll_once(self) -> list[WatchEvent]:
        """One scan; publishes and returns the events whose tree actually moved."""
        news: list[WatchEvent] = []
        for event in await asyncio.to_thread(self.scan):
            if event == self.latest.get(event.kind):
                continue
            self.latest[event.kind] = event
            self._publish(event)
            news.append(event)
            if self.prewarm and event.kind == KIND_SAVE:
                self._warm()
        return news

    # ---- the tail -------------------------------------------------------

    async def tail_once(self) -> list[WatchEvent]:
        news = await asyncio.to_thread(self.log_tail.scan)
        for event in news:
            self.latest[event.kind] = event
            self._publish(event)
        return news

    async def _tail_run(self) -> None:
        failures = 0
        while True:
            try:
                await self.tail_once()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                if failures == 1 or failures % 100 == 0:
                    log.warning("plan tail failed (%d in a row)", failures, exc_info=True)
            await asyncio.sleep(self.tail_interval)

    async def _run(self) -> None:
        # The first scan establishes the baseline and publishes, which is what gives a
        # browser that connected before the first poll something to draw.
        #
        # The catch-all must stay: a save directory that vanished mid-poll is no reason to
        # stop watching. It must also stay LOUD -- ``poll_once`` fans out as well as scans,
        # and a bug in the fan-out raises on every poll, which without a log is a server
        # that spins for its whole life while every browser sits on a page that never
        # updates again. Throttled to the first failure and every twentieth after it,
        # because a line every 3 s is a log nobody can read.
        while True:
            try:
                await self.poll_once()
                self.consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.consecutive_failures += 1
                if self.consecutive_failures == 1 or self.consecutive_failures % 20 == 0:
                    log.warning(
                        "save watcher poll failed (%d in a row); still polling every %.0fs",
                        self.consecutive_failures,
                        self.interval,
                        exc_info=True,
                    )
            await asyncio.sleep(self.interval)

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="save-watcher")
        if self.tail and self._tail_task is None:
            self._tail_task = asyncio.create_task(self._tail_run(), name="plan-tail")

    async def stop(self) -> None:
        tasks = [t for t in (self._task, self._tail_task) if t is not None]
        self._task = self._tail_task = None
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
