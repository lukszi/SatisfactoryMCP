"""One computation per key, however many callers ask for it at once.

Two reads in this tree cost seconds and are asked for by eleven callers at a time, because
that is how many layers the map page fetches: the save parse (a subprocess, ~3.7 s) and the
derived views over the projection it returns (~0.8 s). Both are pure functions of their key,
so the second concurrent caller has nothing to add and waits for the first one's answer.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

__all__ = ["Singleflight"]

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass
class _Flight(Generic[V]):
    """One in-progress computation. ``done`` is the happens-before edge: the leader writes
    ``value`` or ``error`` before setting it and no waiter reads either before waiting.
    ``value`` is unset until the leader writes it, and only read once ``error`` is None."""

    done: threading.Event = field(default_factory=threading.Event)
    value: V = field(init=False)
    error: BaseException | None = None


class Singleflight(Generic[K, V]):
    """A bounded LRU memo whose concurrent misses on one key collapse into one call."""

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._values: OrderedDict[K, V] = OrderedDict()
        self._flights: dict[K, _Flight[V]] = {}

    def call(self, key: K, build: Callable[[], V]) -> V:
        """``build()``'s answer, shared with whoever is asking right now and never stored.

        For a read whose freshness is the whole point, where the only waste is doing it
        simultaneously: the joiner gets a snapshot taken microseconds before its own would
        have been, and the next caller reads the world again.
        """
        return self.get(key, build, refresh=True, store=False)

    def get(
        self, key: K, build: Callable[[], V], *, refresh: bool = False, store: bool = True
    ) -> V:
        """``build()``'s answer for ``key``, computed once however many threads ask.

        ``refresh`` ignores a stored value but still JOINS a flight already in progress:
        these keys name immutable inputs, so a computation that started a moment ago is
        computing from the same bytes a fresh one would read.

        Whatever ``build`` raises reaches every caller waiting on that key -- they would
        each have raised it too, and a failing parse is the last thing worth doing eleven
        times.
        """
        # NEVER hold the lock across ``build``. It covers the bookkeeping only, because
        # ``build`` here spawns a subprocess and waits on its pipes, and a lock held across
        # that puts every OTHER key behind this one as well.
        with self._lock:
            if not refresh and key in self._values:
                self._values.move_to_end(key)
                return self._values[key]
            flight = self._flights.get(key)
            leader = flight is None
            if flight is None:
                flight = self._flights[key] = _Flight()

        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value

        try:
            flight.value = build()
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            # In a ``finally`` so that a leader which dies -- including on KeyboardInterrupt
            # -- still releases its waiters instead of parking them for ever.
            with self._lock:
                self._flights.pop(key, None)
                if store and flight.error is None:
                    self._values[key] = flight.value
                    self._values.move_to_end(key)
                    while len(self._values) > self._maxsize:
                        self._values.popitem(last=False)
            flight.done.set()
        return flight.value

    def peek(self, key: K) -> V | None:
        """The stored value or ``None``, without computing or waiting."""
        with self._lock:
            return self._values.get(key)

    def clear(self) -> None:
        """Drop every stored value. Flights in progress are left to finish."""
        with self._lock:
            self._values.clear()
