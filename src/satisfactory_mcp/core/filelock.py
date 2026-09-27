"""An exclusive lock across processes, held on a ``<file>.lock`` beside the file it guards.

OS byte-range locking (``msvcrt`` on Windows, ``fcntl`` elsewhere), so a process that dies
releases its lock with it. docs/frontend_vision.md §9.6 says who shares these files.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["LockTimeout", "held"]

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
def held(target: Path, timeout: float = TIMEOUT_S) -> Iterator[None]:
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
