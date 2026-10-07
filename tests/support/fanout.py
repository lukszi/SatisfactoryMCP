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
    """How many children one whole-folder test runs at once: a third of the logical CPUs, so
    the three can run side by side; at least 2.

    Never below 2, so the parallel path is the one every machine exercises.
    """
    override = os.environ.get(WIDTH_ENV)
    if override:
        return max(1, int(override))
    return max(2, (os.cpu_count() or 4) // 3)


def heads_of_shares(first: list[T], rest: list[T], workers: int) -> list[T]:
    """``first`` and ``rest`` in one order that puts each of ``first`` at the head of a
    different worker's opening share, round-robin when there are more of them than workers.

    ``--dist worksteal`` opens by dealing each worker, in turn, an equal run of what is left,
    and steals from the tail of a queue: this mirrors that split.
    """
    shares: list[list[T]] = [[] for _ in range(max(1, workers))]
    for index, item in enumerate(first):
        shares[index % len(shares)].append(item)
    remaining = len(first) + len(rest)
    taken = 0
    ordered: list[T] = []
    for index, share in enumerate(shares):
        size = remaining // (len(shares) - index)
        fill = max(0, size - len(share))
        share.extend(rest[taken : taken + fill])
        taken += fill
        remaining -= len(share)
        ordered.extend(share)
    return ordered + rest[taken:]


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
