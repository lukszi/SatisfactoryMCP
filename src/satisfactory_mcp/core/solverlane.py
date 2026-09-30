"""Every HiGHS solve runs on a few daemon threads that never exit. See docs/DEVELOPING.md."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, TypeVar

__all__ = ["LANES", "run"]

T = TypeVar("T")

LANES = 4

_jobs: queue.SimpleQueue[tuple[Future[Any], Callable[[], Any]]] = queue.SimpleQueue()
_lock = threading.Lock()
_lanes: list[threading.Thread] = []
_here = threading.local()


def _lane() -> None:
    _here.lane = True
    while True:
        future, job = _jobs.get()
        if not future.set_running_or_notify_cancel():
            continue
        try:
            future.set_result(job())
        except BaseException as exc:
            future.set_exception(exc)


def run(job: Callable[[], T]) -> T:
    """``job()``'s answer, computed on a solver lane; the caller's thread only waits."""
    if getattr(_here, "lane", False):
        return job()
    with _lock:
        if len(_lanes) < LANES:
            lane = threading.Thread(target=_lane, name=f"solver-lane-{len(_lanes)}", daemon=True)
            lane.start()
            _lanes.append(lane)
    future: Future[T] = Future()
    _jobs.put((future, job))
    return future.result()
