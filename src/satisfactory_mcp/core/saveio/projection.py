"""Invoke the sidecar, cache its projection, and group saves into worlds.

This is the ONLY module that knows a save parser exists. Everything downstream
consumes the plain-dict projection, which is why the whole test suite can run from a
committed JSON fixture with no game install.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ... import config
from .. import atomic
from ..singleflight import Singleflight
from ..text import ago, format_local_time

#: The projection's shape, stamped by the extractor and part of the disk cache key, so a pickle
#: from an older schema misses. Each bump is in ``docs/save-projection.md``, "Schema history".
SCHEMA_VERSION = 22

#: The in-process projection memo, and the single-flight around its misses. An autosave is a
#: new cache key for a file every reader resolves to at once, so without the flight the map
#: page's eleven layers spawn eleven parser sidecars for the same bytes.
_PROJECTION_MEMO = Singleflight(maxsize=3)

#: Directory scans, keyed on a fingerprint of the tree they describe. See ``scan_saves``.
#: Small because one save root is the whole workload: the spare entries are there so that a
#: rewrite does not immediately drop the scan a reader is still resolving names against.
_SCAN_MEMO = Singleflight(maxsize=4)

#: .NET ticks at the Unix epoch, for converting saveDateTimeInTicks.
_TICKS_AT_EPOCH = 621_355_968_000_000_000
_TICKS_PER_SECOND = 10_000_000


class SaveError(RuntimeError):
    """The sidecar could not produce a projection."""


@dataclass
class World:
    """One game world, identified by the header's saveIdentifier."""

    world_id: str
    session_name: str
    saves: list[dict] = field(default_factory=list)

    @property
    def newest(self) -> dict:
        return max(self.saves, key=lambda save: save["mtime_ns"])

    @property
    def max_play_duration_s(self) -> int:
        return max((save.get("play_duration_s") or 0) for save in self.saves)

    def manual_saves(self) -> list[dict]:
        return [save for save in self.saves if "autosave" not in save["filename"].lower()]


def ticks_to_epoch_seconds(ticks: int | None) -> float | None:
    if not ticks:
        return None
    return (ticks - _TICKS_AT_EPOCH) / _TICKS_PER_SECOND


def _child_env() -> dict[str, str]:
    """This process's environment, with the source tree put in front on PYTHONPATH.

    Merged over ``os.environ`` rather than replacing it, because the extractor is a normal
    Python program that wants the same PATH, TEMP and console encoding. Only the import root
    is made explicit, so that a checkout always runs its own source no matter what an
    inherited PYTHONPATH says.
    """
    env = dict(os.environ)
    root = config.source_root()
    if root is not None:
        inherited = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{root}{os.pathsep}{inherited}" if inherited else str(root)
    return env


#: How much of the child's stderr is carried, in characters, taken from the END. The end is
#: where a traceback's exception line is and where the last thing the parser complained about
#: is; the beginning is a page of notes about a file that then parsed fine.
STDERR_TAIL_CHARS = 600


def _stderr_clause(tail: str) -> str:
    """The stderr tail as a clause to hang on a message, or nothing at all.

    An empty stderr must add no punctuation: a message ending in ``: `` reads as a truncated
    error rather than as an error with nothing more to say.
    """
    return f" -- sidecar stderr: {tail}" if tail else ""


def _run_sidecar(args: list[str], timeout: float = 180.0) -> dict:
    """Run the extractor in a child process and return its payload, or raise ``SaveError``.

    Both of the child's channels are evidence: stdout is the payload, and stderr is the parser
    saying what it skipped and the interpreter printing a traceback. The four outcomes, in the
    order they are decided:

    * **nothing on stdout** -- the sidecar never got going. Exit code and stderr tail.
    * **stdout carries an ``error`` key** -- the child's own designed refusal, an unreadable
      save or a bad argument. Its own words, plus the stderr tail, which is where the
      traceback for an *unexpected* exception is.
    * **stdout parses and the exit code is non-zero** -- the child died after writing. The
      payload is of unknown completeness and is refused rather than cached.
    * **stdout parses and the exit was clean** -- the payload, with any stderr folded into
      its ``warnings``.
    """
    # ``-m``, not a file path: the child then imports the extractor exactly the way this
    # process was imported, so there is no second copy of the code to drift out of date.
    cmd = [sys.executable, "-m", config.EXTRACTOR_MODULE, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            env=_child_env(),
            # DEVNULL, not inherit. capture_output only redirects stdout/stderr, so
            # without this the sidecar inherits the MCP server's stdin -- which is the
            # client's JSON-RPC pipe. Anything that touches it blocks for ever and can
            # steal bytes from the protocol stream.
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            text=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SaveError(f"sidecar timed out after {timeout}s") from exc
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    tail = proc.stderr.decode("utf-8", errors="replace")[-STDERR_TAIL_CHARS:].strip()
    if not out:
        raise SaveError(
            f"sidecar produced no output (exit {proc.returncode}){_stderr_clause(tail)}"
        )
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SaveError(f"sidecar emitted invalid JSON: {out[:200]}{_stderr_clause(tail)}") from exc

    # The child's own refusal, the one failure it is designed to have: `main` writes
    # `{"error": ..., "detail": ...}` and exits non-zero for an unreadable save, and for an
    # unexpected exception it ALSO writes the traceback to stderr, which is why the tail
    # rides along -- without it an `AttributeError` arrives with no message at all.
    if isinstance(payload, dict) and "error" in payload:
        raise SaveError(f"{payload['error']}: {payload.get('detail', '')}{_stderr_clause(tail)}")

    # A non-zero exit with clean JSON on stdout means the child died AFTER writing a payload
    # -- a crash in the interpreter's own shutdown, a MemoryError past the final `json.dump`,
    # a kill from outside -- so the payload is of unknown completeness. Serving it would cache
    # a half-read world under a key that says it is the whole one.
    if proc.returncode != 0:
        raise SaveError(
            f"sidecar exited {proc.returncode} after writing a payload, so what it wrote "
            f"cannot be trusted{_stderr_clause(tail)}"
        )

    # It worked, and it still had something to say: `extract` sends the parser's own notes to
    # stderr -- what pioneersav skipped, which conveyor chain would not decode -- so that
    # stdout stays parseable, and they are carried into the projection's `warnings` from here.
    # ONE entry rather than one per line: this is the tail of a truncated stream, so its first
    # line is very likely half a line, and splitting it would publish a fragment as though it
    # were a note somebody wrote.
    if tail and isinstance(payload, dict):
        payload.setdefault("warnings", []).append(
            f"the sidecar wrote to stderr and the parse still succeeded; "
            f"last {STDERR_TAIL_CHARS} characters: {tail}"
        )
    return payload


def _tree_fingerprint(root: Path) -> tuple:
    """Every ``.sav`` under ``root`` with its size and modification time, from one walk.

    ``DirEntry.stat`` rather than ``os.stat``: the values come from the directory
    enumeration the walk is already doing, which is what makes this 0.5 ms for 72 files
    against 4 ms of individual stats and 87 ms of sidecar. Measured prompt on NTFS even
    against a handle the writer still holds open -- ``tests/test_scan_fingerprint.py``
    pins that, because a fingerprint that lags is a save the player just wrote and this
    server cannot see.

    Unreadable entries are skipped rather than raising: a scan is how the save tree is
    discovered, so it has to survive one folder it cannot open.
    """
    found: list[tuple] = []
    stack = [str(root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.name.casefold().endswith(".sav"):
                            file_stat = entry.stat()
                            found.append((entry.path, file_stat.st_mtime_ns, file_stat.st_size))
                    except OSError:
                        continue
        except OSError:
            continue
    # Sorted, because directory order is not a property of the tree and two walks of one
    # unchanged tree have to produce the same tuple.
    return tuple(sorted(found))


#: How recently a file may have been written before the fingerprint stops trusting itself.
#: Windows stamps modification times from a system clock that advances in ~15.6 ms steps, so
#: two writes landing in one step are stamped identically -- and a save rewritten in place at
#: an unchanged size is then invisible to ``(name, size, mtime)``. Inside this window the scan
#: is taken again rather than remembered, which is what the sidecar did on every call and is
#: the only way this memo is not a weaker freshness test than the thing it replaced.
_SETTLE_NS = 1_000_000_000


def _unsettled(fingerprint: tuple) -> bool:
    """Whether any file in the tree was stamped too recently to be told apart from its own
    next rewrite. Absolute difference, so a save dated in the future -- copied off another
    machine, or written across a clock adjustment -- settles instead of never being cached."""
    now = time.time_ns()
    return any(abs(now - mtime_ns) < _SETTLE_NS for _path, mtime_ns, _size in fingerprint)


def scan_saves(root: str | Path | None = None) -> dict:
    """Header-only scan of the save tree: one subprocess, ~90 ms, when the tree has moved.

    This is what notices the save the player wrote a moment ago, so it is memoised on a
    fingerprint of the tree rather than on time: the sidecar reads bytes out of each header
    -- session name, play duration, ``save_identifier`` -- and those cannot change without
    the file changing, which the fingerprint sees. The fingerprint IS the memo key, so two
    callers who disagree about the state of the disk can never share an answer, and a caller
    that joins a scan already in flight is one that already agreed with its leader.
    """
    save_root = Path(root) if root else config.saves_root()
    if not save_root.exists():
        return {"root": str(save_root), "saves": [], "unsupported": [], "missing_root": True}
    fingerprint = _tree_fingerprint(save_root)
    key = (str(save_root), fingerprint)

    def build() -> dict:
        return _run_sidecar(["--list", str(save_root)])

    if _unsettled(fingerprint):
        return _SCAN_MEMO.call(key, build)
    return _SCAN_MEMO.get(key, build)


def list_worlds(root: str | Path | None = None) -> tuple[list[World], list[dict]]:
    """Group saves into worlds by ``save_identifier``.

    Verified stable across all 28 parseable saves in the reference directory.
    Returns (worlds newest-first, unsupported files with reasons).
    """
    scan = scan_saves(root)
    worlds: dict[str, World] = {}
    for save in scan.get("saves", ()):
        world_id = save.get("save_identifier") or f"session:{save.get('session_name')}"
        world = worlds.get(world_id)
        if world is None:
            world = worlds[world_id] = World(
                world_id=world_id, session_name=save.get("session_name") or "?"
            )
        world.saves.append(save)
    ordered = sorted(worlds.values(), key=lambda world: world.newest["mtime_ns"], reverse=True)
    return ordered, list(scan.get("unsupported", ()))


def _resolve_filename(save_path: Path) -> dict:
    """A save named the way this server itself names saves: by FILENAME, not by path.

    Every presenter prints ``header["filename"]`` -- the basename -- and the server's working
    directory is nowhere near the save tree, so a name the server prints must resolve here or
    a client handing one straight back is told "save not found" about a file it just read.

    The scan is taken FRESH on every miss, because the name most worth resolving is the manual
    save the player wrote moments ago. The same filename under two account folders resolves to
    the newest copy; ``casefold`` because the filesystems these saves live on do not
    distinguish case and the resolver must not be stricter than the disk.
    """
    scan = scan_saves()
    needle = save_path.name.casefold()
    matches = [
        save for save in scan.get("saves", ()) if str(save.get("filename", "")).casefold() == needle
    ]
    if matches:
        return max(matches, key=lambda save: save["mtime_ns"])
    raise SaveError(
        f"save not found: {save_path} -- not a path on disk, and no file named "
        f"{save_path.name!r} among the {len(scan.get('saves') or [])} readable save(s) under "
        f"{scan.get('root')}"
    )


def _ambiguous_world(name: str, matches: list[World]) -> str:
    """The refusal for a display name two worlds share. Listing beats guessing:
    picking the newest silently reads the WRONG factory with full confidence."""
    lines = [
        (
            f"world name {name!r} matches {len(matches)} worlds -- refusing to pick one. "
            "Pass the world_id instead (world= accepts it):"
        )
    ]
    for world in matches:
        newest = world.newest
        lines.append(
            f"  world={world.world_id!r}: {len(world.saves)} save(s), newest "
            f"{newest.get('filename')} written {format_local_time(newest.get('mtime_ns'))} "
            f"({ago(newest.get('mtime_ns'))}), saveVersion {newest.get('save_version')}"
        )
    return "\n".join(lines)


def resolve_save(
    path: str | Path | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
) -> dict:
    """Pick a save header: explicit path or filename, else newest in the named/only world."""
    if path:
        save_path = Path(path)
        if save_path.is_file():
            return _run_sidecar([str(save_path), "--header-only"])["header"]
        return _resolve_filename(save_path)

    worlds, _ = list_worlds()
    if not worlds:
        raise SaveError(
            f"no readable saves under {config.saves_root()} "
            "(set SATISFACTORY_SAVES if they live elsewhere)"
        )
    chosen = None
    if world:
        needle = world.casefold()
        # The id first, because it is unique by construction (worlds are grouped by
        # it), so it is the handle the ambiguity refusal below can honestly offer.
        chosen = next((known for known in worlds if known.world_id.casefold() == needle), None)
        if chosen is None:
            named = [known for known in worlds if known.session_name.casefold() == needle]
            if len(named) > 1:
                raise SaveError(_ambiguous_world(world, named))
            if named:
                chosen = named[0]
        if chosen is None:
            names = ", ".join(f"{known.session_name!r}" for known in worlds)
            raise SaveError(f"no world matching {world!r}; known worlds: {names}")
    else:
        chosen = worlds[0]

    pool = chosen.manual_saves() if prefer_manual else chosen.saves
    if not pool:
        pool = chosen.saves
    return max(pool, key=lambda save: save["mtime_ns"])


def _cache_key(header: dict) -> str:
    """Identity of a parsed save.

    mtime_ns and size are essential: autosaves are rewritten IN PLACE under the same
    filename every ~5 minutes, so path alone would serve a stale world.
    """
    raw = "|".join(
        [
            header["path"],
            str(header["mtime_ns"]),
            str(header["size"]),
            str(SCHEMA_VERSION),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _read_disk_cache(key: str) -> dict | None:
    disk = config.cache_dir() / f"save-{key}.pkl"
    if not disk.is_file():
        return None
    try:
        payload = pickle.loads(disk.read_bytes())
    except Exception:
        # Corrupt, or a stale pickle format, or a file another process's `prune_cache`
        # deleted between the `is_file` above and the read. The unlink is best-effort
        # for the same reason: on Windows it raises `PermissionError` while any other
        # process holds the file open, and nothing in a read path may die over a cache.
        try:
            disk.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    # Touched on the way out, which is what makes ``prune_cache`` keep the twelve saves
    # most recently READ rather than the twelve most recently written. The watcher parses
    # every autosave now, so twelve writes is about an hour, and without this the save an
    # LLM session has pinned is evicted by autosaves nobody ever asked about. Best-effort:
    # another process may be pruning this very file, and a cache is never a requirement.
    try:
        os.utime(disk)
    except OSError:
        pass
    return payload


def _parse_and_cache(header: dict, key: str) -> dict:
    payload = _run_sidecar([header["path"]])
    if payload.get("schema_version") != SCHEMA_VERSION:
        payload.setdefault("warnings", []).append(
            f"sidecar schema {payload.get('schema_version')} != expected {SCHEMA_VERSION}"
        )
    try:
        # `atomic.write_bytes`, not `Path.write_bytes`: this directory has more than one
        # writer -- the web server, any CLI invocation and, under `pytest-xdist`, a test
        # worker per core -- all resolving the same newest save and missing the same key at
        # the same moment. See `core/atomic.py`.
        atomic.write_bytes(config.cache_dir() / f"save-{key}.pkl", pickle.dumps(payload))
        # Prune on write, because autosaves rotate every ~5 minutes and each one is a new
        # cache key: without this the directory grows by ~500 kB per autosave for ever.
        prune_cache()
    except OSError:
        pass  # cache is an optimisation, never a requirement
    return payload


def load_projection(
    path: str | Path | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
    refresh: bool = False,
) -> dict:
    """Return the projection for a save, using the two-tier cache.

    Parsing costs ~4 s, reading the pickle another process wrote ~15 ms, and a memo hit
    ~1 ms. The ``resolve_save`` above it costs one directory walk while the save tree is
    still, and a sidecar of its own the first time it moves.
    """
    header = resolve_save(path, world, prefer_manual)
    key = _cache_key(header)

    def build() -> dict:
        if not refresh:
            cached = _read_disk_cache(key)
            if cached is not None:
                return cached
        return _parse_and_cache(header, key)

    return _PROJECTION_MEMO.get(key, build, refresh=refresh)


def prune_cache(keep: int = 12) -> int:
    """Drop all but the ``keep`` most recently used cached projections.

    Every filesystem call here is best-effort, including the ``stat`` inside the sort key,
    because another process is deleting the same files: the directory is shared by the
    server, the CLI and a test worker per core, all of which prune on every write, and it
    sits at exactly ``keep`` entries in normal use, so a prune racing a prune is the ordinary
    case. A file whose ``stat`` fails sorts as if infinitely old, so the loop below tries to
    delete it and finds it already gone.
    """

    def _mtime(cache_file: Path) -> float:
        try:
            return cache_file.stat().st_mtime
        except OSError:
            return float("-inf")

    try:
        files = sorted(config.cache_dir().glob("save-*.pkl"), key=_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            pass
    return removed
