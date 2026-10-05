"""A generator process the server started, or one an earlier server started and this one adopts.

Windows keeps a child running after its parent exits, so a restarted server finds its job by
pid and checks the process creation time it recorded, which a reused pid cannot match. A
held process handle answers liveness, the exit code and the peak working set; on other
systems the pid alone does, and peak memory is not reported.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

__all__ = ["BELOW_NORMAL", "Child", "creation_time", "kill_tree", "launch"]

WINDOWS = os.name == "nt"
STILL_ACTIVE = 259
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL = 0x00004000


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel


def _open(pid: int):
    if not WINDOWS or pid <= 0:
        return None
    handle = _kernel().OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return handle or None


def _created(handle) -> int | None:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel().GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def creation_time(pid: int) -> int | None:
    """When ``pid`` was created, as an opaque number; ``None`` when there is no such process."""
    if WINDOWS:
        handle = _open(pid)
        if handle is None:
            return None
        try:
            return _created(handle)
        finally:
            _kernel().CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
            return int(stat.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return 0


class Child:
    """One generator process: ours (a ``Popen``) or adopted by pid."""

    def __init__(self, pid: int, popen: subprocess.Popen | None = None) -> None:
        self.pid = pid
        self.popen = popen
        self.handle = _open(pid)
        self.created = _created(self.handle) if self.handle else creation_time(pid)

    @classmethod
    def adopt(cls, pid: int, created: int | None) -> Child | None:
        """The process an earlier server started, if it is still that process."""
        if not pid or creation_time(pid) is None:
            return None
        child = cls(pid)
        if created is not None and child.created != created:
            child.close()
            return None
        return child

    def exit_code(self) -> int | None:
        """``None`` while running."""
        if self.popen is not None:
            return self.popen.poll()
        if self.handle is not None:
            code = wintypes.DWORD()
            if _kernel().GetExitCodeProcess(self.handle, ctypes.byref(code)):
                return None if code.value == STILL_ACTIVE else int(code.value)
            return -1
        return None if creation_time(self.pid) is not None else -1

    def peak_rss(self) -> int | None:
        if self.handle is None:
            return None
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        if not psapi.GetProcessMemoryInfo(self.handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)

    def close(self) -> None:
        if self.handle is not None:
            _kernel().CloseHandle(self.handle)
            self.handle = None


def launch(command: list[str], log, cwd: str, env: dict[str, str]) -> Child:
    """Start a generator at below-normal priority, its output into ``log``, detached from Ctrl+C."""
    flags = (CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL | CREATE_NO_WINDOW) if WINDOWS else 0
    popen = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        start_new_session=not WINDOWS,
    )
    if not WINDOWS:
        try:
            os.setpriority(os.PRIO_PROCESS, popen.pid, 10)
        except (AttributeError, OSError):
            pass
    return Child(popen.pid, popen)


def kill_tree(pid: int) -> None:
    """End a generator and every worker it started; killing the parent alone orphans them."""
    if WINDOWS:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), 9)
    except OSError:
        pass
