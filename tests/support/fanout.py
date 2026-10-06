"""Fanning a whole-folder test out over the machine while keeping its results in order.

The three ``whole_folder`` tests parse every save on the disk; docs/DEVELOPING.md ("Test
suite") records the measurements behind the width and the ordering.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Executor, Future
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: Overrides ``fanout_width`` so widths can be compared without editing code.
WIDTH_ENV = "SATISFACTORY_TEST_FANOUT"


def fanout_width() -> int:
    """How many children one whole-folder test runs at once: half the logical CPUs, at least 2.

    Never below 2, so the parallel path is the one every machine exercises.
    """
    override = os.environ.get(WIDTH_ENV)
    if override:
        return max(1, int(override))
    return max(2, (os.cpu_count() or 4) // 2)


def in_order(
    executor: Executor,
    items: Iterable[T],
    work: Callable[[T], R],
    *,
    width: int,
) -> Iterator[tuple[T, R]]:
    """Yield ``(item, work(item))`` in the order of ``items``, ``width`` calls in flight.

    Results come back in submission order, so assertions and early stops see what a serial
    loop would. Abandoning the generator cancels whatever has not started.
    """
    pending: deque[tuple[T, Future[R]]] = deque()
    source = iter(items)
    try:
        for submitted in source:
            pending.append((submitted, executor.submit(work, submitted)))
            if len(pending) < width:
                continue
            item, future = pending.popleft()
            yield item, future.result()
        while pending:
            item, future = pending.popleft()
            yield item, future.result()
    finally:
        for _item, future in pending:
            future.cancel()
