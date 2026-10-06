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

__all__ = ["Child", "creation_time", "kill_tree", "launch"]

WINDOWS = os.name == "nt"
STILL_ACTIVE = 259
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
TH32CS_SNAPPROCESS = 0x2


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


def _open_process(pid: int):
    if not WINDOWS or pid <= 0:
        return None
    handle = _kernel().OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return handle or None


def _creation_filetime(handle) -> int | None:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel().GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _descendants(pid: int) -> list[int]:
    """Every process below ``pid``, from one Toolhelp snapshot."""
    kernel = _kernel()
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    parents: dict[int, list[int]] = {}
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel.Process32First(snap, ctypes.byref(entry))
        while more:
            parents.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            more = kernel.Process32Next(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    found, todo = [], list(parents.get(pid, []))
    while todo:
        child = todo.pop()
        if child in found or child == pid:
            continue
        found.append(child)
        todo.extend(parents.get(child, []))
    return found


def _peak_working_set(handle) -> int | None:
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)


def creation_time(pid: int) -> int | None:
    """When ``pid`` was created, as an opaque number; ``None`` when there is no such process."""
    if WINDOWS:
        handle = _open_process(pid)
        if handle is None:
            return None
        try:
            return _creation_filetime(handle)
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
        self.handle = _open_process(pid)
        self.created = _creation_filetime(self.handle) if self.handle else creation_time(pid)

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
        """The largest peak working set in the process tree: a venv's ``python.exe`` is a
        launcher, and the interpreter doing the work is its child."""
        if self.handle is None:
            return None
        peaks = [_peak_working_set(self.handle)]
        for pid in _descendants(self.pid):
            handle = _open_process(pid)
            if handle is not None:
                peaks.append(_peak_working_set(handle))
                _kernel().CloseHandle(handle)
        known = [p for p in peaks if p is not None]
        return max(known) if known else None

    def close(self) -> None:
        if self.handle is not None:
            _kernel().CloseHandle(self.handle)
            self.handle = None


def launch(command: list[str], log, cwd: str, env: dict[str, str]) -> Child:
    """Start a generator at below-normal priority, its output into ``log``, detached from Ctrl+C."""
    flags = (
        (CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW)
        if WINDOWS
        else 0
    )
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
