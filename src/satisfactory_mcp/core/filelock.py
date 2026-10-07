"""An exclusive lock across processes, held on a ``<file>.lock`` beside the file it guards.

OS byte-range locking (``msvcrt`` on Windows, ``fcntl`` elsewhere), so a process that dies
releases its lock with it. docs/frontend_vision.md §9.6 says who shares these files.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from typing_extensions import TypedDict

from . import atomic

__all__ = ["LockTimeout", "VersionedDoc", "held", "update_versioned_json"]

T = TypeVar("T")


class VersionedDoc(TypedDict):
    """A JSON document whose ``version`` counts its writes."""

    version: int


Doc = TypeVar("Doc", bound=VersionedDoc)

TIMEOUT_S = 10.0

POLL_S = 0.02


class LockTimeout(TimeoutError):
    """Another process held the lock for longer than the caller would wait."""


if os.name == "nt":
    import msvcrt

    def _try(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def held(target: Path, timeout: float = TIMEOUT_S) -> Generator[None, None, None]:
    """Hold the lock for ``target`` for the duration of the block, or raise ``LockTimeout``."""
    lock = target.with_name(target.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"{target.name} is being written by another process and stayed "
                        f"locked for {timeout:.0f} s ({lock}); nothing was written"
                    ) from None
                time.sleep(POLL_S)
        try:
            yield
        finally:
            _release(fd)
    finally:
        os.close(fd)


def _next_version(version: object) -> int:
    """``version + 1``, or a ``TypeError`` for a version that is no number."""
    if isinstance(version, (int, float)):
        return int(version) + 1
    raise TypeError(f"a file version is a {type(version).__name__}, not a number")


def update_versioned_json(
    path: Path,
    read: Callable[[], Doc],
    change: Callable[[Doc], tuple[T, bool]],
) -> T:
    """Run ``change(read()) -> (result, dirty)`` under ``path``'s lock; when dirty, bump
    ``version`` and rewrite the file atomically. Returns ``result``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with held(path):
        data = read()
        result, dirty = change(data)
        if dirty:
            data["version"] = _next_version(data["version"])
            atomic.write_text(path, json.dumps(data, ensure_ascii=False))
    return result
