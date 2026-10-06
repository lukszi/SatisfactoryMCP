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

#: Bumped whenever the projection's shape changes, and part of the disk cache key below, so
#: every pickle written by an older schema misses rather than being served without its new
#: fields. A CORRECTING bump matters more than an additive one: an old pickle then disagrees
#: with this code rather than merely being thinner than it.
#: 12 added placement yaw and belt splines.
#: 13 added fluid pipe splines and the belt attachments -- the splitters and mergers a run
#: passes through.
#: 14 added a pipe segment's own actor index, which joins the drawn pipe to the connection
#: graph in ``graph["material"]`` and is what lets flow direction be inferred.
#: 15 added the spline tangents to both route keys, so a curved belt or a pipe elbow draws as
#: the curve it was built as, and a ``storage`` key -- the containers and fluid buffers, with
#: what is in each one.
#: 16 CORRECTS: ``inventories`` had bucketed eight containers' contents as unspendable machine
#: buffers, and a placement whose rotation would not read had claimed to be axis-aligned
#: instead of saying nothing.
#: 17 added ``power`` -- the poles, and the endpoints of every wire between them. Geometry
#: only: the connectivity has been in ``graph["power"]`` since schema 11, so ``wires[i]`` is
#: the span of ``graph["power"][i]`` rather than a second copy of who is wired to whom.
#: 18 added ``crates`` -- the death and dismantle crates lying on the ground, and what is in
#: each one. Its own key rather than more ``storage`` rows, because a container is
#: infrastructure the player built and a crate is a situation the player got into.
#: 19 CORRECTS the key 16 did: a crate's contents had counted into ``inventories["machine"]``,
#: filed with the smelter buffers as material that cannot be spent, and they are recoverable
#: stock, so they move to their own ``inventories["crate"]`` bucket.
#: 20 added a fourth column to a belt segment, the index of its own actor in
#: ``graph["actors"]`` -- the join schema 14 gave a pipe, now given to the conveyor beside it,
#: which is what lets a contracted run be NAMED rather than only described by its far end.
#: 21 CORRECTS what the parser could see at all. Three savparse fixes land here: large built
#: saves parse to the end rather than dying on an `Item` struct; every foundation, wall and
#: beam arrives, because the lightweight record's trailing int32 is a COUNT of type-specific
#: data blocks and not the constant 0 it was read as, which had left the walk 116 bytes short
#: on the first beam; and a conveyor chain stamped `_RepSizeNoCull`, a class the anniversary
#: build added, is decoded instead of dropped. Every pickle written before this describes less
#: world than the save holds -- fewer structures, and a missing belt run -- so they must miss.
#: 22 CORRECTS a false problem: a save whose older levels still carry the previous build's
#: changelist had reported every read as possibly incomplete. Nothing in the world changes.
SCHEMA_VERSION = 22

#: The in-process projection memo, and the single-flight around its misses. An autosave is a
#: new cache key for a file every reader resolves to at once, so without the flight the map
#: page's eleven layers spawn eleven parser sidecars for the same bytes.
_MEM = Singleflight(maxsize=3)

#: Directory scans, keyed on a fingerprint of the tree they describe. See ``scan_saves``.
#: Small because one save root is the whole workload: the spare entries are there so that a
#: rewrite does not immediately drop the scan a reader is still resolving names against.
_SCANS = Singleflight(maxsize=4)

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
        return max(self.saves, key=lambda s: s["mtime_ns"])

    @property
    def max_play_duration_s(self) -> int:
        return max((s.get("play_duration_s") or 0) for s in self.saves)

    def manual_saves(self) -> list[dict]:
        return [s for s in self.saves if "autosave" not in s["filename"].lower()]


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


def _because(tail: str) -> str:
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
        raise SaveError(f"sidecar produced no output (exit {proc.returncode}){_because(tail)}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SaveError(f"sidecar emitted invalid JSON: {out[:200]}{_because(tail)}") from exc

    # The child's own refusal, the one failure it is designed to have: `main` writes
    # `{"error": ..., "detail": ...}` and exits non-zero for an unreadable save, and for an
    # unexpected exception it ALSO writes the traceback to stderr, which is why the tail
    # rides along -- without it an `AttributeError` arrives with no message at all.
    if isinstance(payload, dict) and "error" in payload:
        raise SaveError(f"{payload['error']}: {payload.get('detail', '')}{_because(tail)}")

    # A non-zero exit with clean JSON on stdout means the child died AFTER writing a payload
    # -- a crash in the interpreter's own shutdown, a MemoryError past the final `json.dump`,
    # a kill from outside -- so the payload is of unknown completeness. Serving it would cache
    # a half-read world under a key that says it is the whole one.
    if proc.returncode != 0:
        raise SaveError(
            f"sidecar exited {proc.returncode} after writing a payload, so what it wrote "
            f"cannot be trusted{_because(tail)}"
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
                            st = entry.stat()
                            found.append((entry.path, st.st_mtime_ns, st.st_size))
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
    r = Path(root) if root else config.saves_root()
    if not r.exists():
        return {"root": str(r), "saves": [], "unsupported": [], "missing_root": True}
    fingerprint = _tree_fingerprint(r)
    key = (str(r), fingerprint)

    def build() -> dict:
        return _run_sidecar(["--list", str(r)])

    if _unsettled(fingerprint):
        return _SCANS.call(key, build)
    return _SCANS.get(key, build)


def list_worlds(root: str | Path | None = None) -> tuple[list[World], list[dict]]:
    """Group saves into worlds by ``save_identifier``.

    Verified stable across all 28 parseable saves in the reference directory.
    Returns (worlds newest-first, unsupported files with reasons).
    """
    scan = scan_saves(root)
    worlds: dict[str, World] = {}
    for s in scan.get("saves", ()):
        wid = s.get("save_identifier") or f"session:{s.get('session_name')}"
        w = worlds.get(wid)
        if w is None:
            w = worlds[wid] = World(world_id=wid, session_name=s.get("session_name") or "?")
        w.saves.append(s)
    ordered = sorted(worlds.values(), key=lambda w: w.newest["mtime_ns"], reverse=True)
    return ordered, list(scan.get("unsupported", ()))


def _resolve_filename(p: Path) -> dict:
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
    needle = p.name.casefold()
    matches = [s for s in scan.get("saves", ()) if str(s.get("filename", "")).casefold() == needle]
    if matches:
        return max(matches, key=lambda s: s["mtime_ns"])
    raise SaveError(
        f"save not found: {p} -- not a path on disk, and no file named {p.name!r} among "
        f"the {len(scan.get('saves') or [])} readable save(s) under {scan.get('root')}"
    )


def _ambiguous_world(name: str, matches: list[World]) -> str:
    """The refusal for a display name two worlds share. Listing beats guessing:
    picking the newest silently reads the WRONG factory with full confidence."""
    lines = [
        f"world name {name!r} matches {len(matches)} worlds -- refusing to pick one. "
        "Pass the world_id instead (world= accepts it):"
    ]
    for w in matches:
        newest = w.newest
        lines.append(
            f"  world={w.world_id!r}: {len(w.saves)} save(s), newest "
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
        p = Path(path)
        if p.is_file():
            return _run_sidecar([str(p), "--header-only"])["header"]
        return _resolve_filename(p)

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
        chosen = next((w for w in worlds if w.world_id.casefold() == needle), None)
        if chosen is None:
            named = [w for w in worlds if w.session_name.casefold() == needle]
            if len(named) > 1:
                raise SaveError(_ambiguous_world(world, named))
            if named:
                chosen = named[0]
        if chosen is None:
            names = ", ".join(f"{w.session_name!r}" for w in worlds)
            raise SaveError(f"no world matching {world!r}; known worlds: {names}")
    else:
        chosen = worlds[0]

    pool = chosen.manual_saves() if prefer_manual else chosen.saves
    if not pool:
        pool = chosen.saves
    return max(pool, key=lambda s: s["mtime_ns"])


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


def _parse(header: dict, key: str) -> dict:
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
        return _parse(header, key)

    return _MEM.get(key, build, refresh=refresh)


def prune_cache(keep: int = 12) -> int:
    """Drop all but the ``keep`` most recently used cached projections.

    Every filesystem call here is best-effort, including the ``stat`` inside the sort key,
    because another process is deleting the same files: the directory is shared by the
    server, the CLI and a test worker per core, all of which prune on every write, and it
    sits at exactly ``keep`` entries in normal use, so a prune racing a prune is the ordinary
    case. A file whose ``stat`` fails sorts as if infinitely old, so the loop below tries to
    delete it and finds it already gone.
    """

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return float("-inf")

    try:
        files = sorted(config.cache_dir().glob("save-*.pkl"), key=_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed
