"""What a pool of workers may take: the memory free now, and a numpy without a BLAS pool.

docs/map/renders.md sections 17 and 40, and docs/map/light-and-crowns.md section 29 ("Strips,
memory and workers").
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager

__all__ = ["ONE_BLAS_THREAD", "free_ram_bytes", "one_blas_thread"]

#: The environment a worker process imports numpy and scipy under. Each loads its own
#: OpenBLAS, and each OpenBLAS commits about 0.8 GB of thread buffers on load without it.
ONE_BLAS_THREAD = {"OPENBLAS_NUM_THREADS": "1"}


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong)
        for name in ("total", "avail", "page", "page_avail", "virt", "virt_avail", "ext")
    ]


def free_ram_bytes() -> int | None:
    """Memory free for new work now, in bytes, or None where the platform does not say.

    On Windows the lesser of the free physical memory and the commit still available: an
    array commits its whole size when it is made, so a full commit refuses it with RAM to
    spare. Elsewhere ``MemAvailable``, else the free pages.
    """
    if sys.platform == "win32":
        status = _MemoryStatus(length=ctypes.sizeof(_MemoryStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(min(status.avail, status.page_avail))
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as info:
            for line in info:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None


@contextmanager
def one_blas_thread() -> Generator[None, None, None]:
    """``ONE_BLAS_THREAD`` for the processes started inside; the values before come back.

    This process keeps the BLAS pool it loaded with.
    """
    before = {name: os.environ.get(name) for name in ONE_BLAS_THREAD}
    os.environ.update(ONE_BLAS_THREAD)
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
