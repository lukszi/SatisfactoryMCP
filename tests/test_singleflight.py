"""One computation per key under concurrency, and the two read paths that depend on it.

The defect these pin is invisible to a single-threaded test and expensive in the field: the
map page fetches eleven layers at once, so every autosave used to start eleven parser
sidecars for the same file and eleven copies of every derived view over the result.
"""

from __future__ import annotations

import threading
import time

import pytest

from satisfactory_mcp.core.saveio import projection as proj
from satisfactory_mcp.core.singleflight import Singleflight
from satisfactory_mcp.domain.world import state as world_state


def _fan_out(n: int, fn):
    """Run ``fn(i)`` on ``n`` threads released together, and return what they returned."""
    out: dict[int, object] = {}
    ready = threading.Barrier(n)

    def worker(i: int) -> None:
        ready.wait()
        try:
            out[i] = fn(i)
        except BaseException as exc:
            out[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a waiter was never released"
    return out


def test_concurrent_misses_on_one_key_build_once():
    flight = Singleflight(maxsize=3)
    calls = []

    def build():
        calls.append(1)
        time.sleep(0.2)  # long enough that every other thread is certainly waiting
        return {"answer": 42}

    out = _fan_out(11, lambda _i: flight.get("k", build))

    assert len(calls) == 1, f"{len(calls)} builds for one key -- the flight did not collapse"
    values = list(out.values())
    assert all(v is values[0] for v in values), "waiters got copies, not the leader's object"


def test_different_keys_do_not_wait_on_each_other():
    """The lock covers bookkeeping only. Held across the build, this would take 11x as long."""
    flight = Singleflight(maxsize=16)

    started = time.perf_counter()
    _fan_out(11, lambda i: flight.get(i, lambda: time.sleep(0.3)))
    elapsed = time.perf_counter() - started

    assert elapsed < 1.5, f"{elapsed:.2f}s for 11 independent 0.3s builds -- they serialised"


def test_a_failing_build_reaches_every_waiter_and_leaves_the_key_usable():
    flight = Singleflight(maxsize=3)
    calls: list[int] = []

    def build():
        calls.append(1)
        time.sleep(0.2)
        raise RuntimeError("sidecar died")

    out = _fan_out(6, lambda _i: flight.get("k", build))

    assert len(calls) == 1
    assert all(isinstance(v, RuntimeError) for v in out.values())
    # A failure must not poison the key: the next caller gets its own attempt.
    assert flight.get("k", lambda: "fine") == "fine"


def test_refresh_ignores_a_stored_value():
    flight = Singleflight(maxsize=3)
    assert flight.get("k", lambda: "first") == "first"
    assert flight.get("k", lambda: "second") == "first"
    assert flight.get("k", lambda: "second", refresh=True) == "second"
    assert flight.get("k", lambda: "third") == "second"


def test_call_collapses_the_concurrent_asks_and_stores_nothing():
    flight = Singleflight(maxsize=3)
    calls: list[int] = []

    def build():
        calls.append(1)
        time.sleep(0.2)
        return len(calls)

    _fan_out(11, lambda _i: flight.call("scan", build))
    assert len(calls) == 1

    assert flight.peek("scan") is None, "a freshness-critical read must not be stored"
    assert flight.call("scan", build) == 2, "the next caller must read the world again"


def test_the_memo_is_bounded():
    flight = Singleflight(maxsize=2)
    for k in ("a", "b", "c"):
        flight.get(k, lambda k=k: k)
    assert flight.peek("a") is None, "the oldest entry should have been evicted"
    assert flight.peek("c") == "c"


# ---- the two call sites -------------------------------------------------


@pytest.fixture
def isolated_memos():
    """The module-level memos are process-wide; a test that fills them must empty them."""
    proj._PROJECTION_MEMO.clear()
    world_state._DERIVED.clear()
    yield
    proj._PROJECTION_MEMO.clear()
    world_state._DERIVED.clear()


def test_one_autosave_starts_one_sidecar_not_eleven(monkeypatch, isolated_memos):
    header = {"path": "C:/saves/autosave_0.sav", "mtime_ns": 7, "size": 3, "filename": "a.sav"}
    spawns: list[list[str]] = []

    def fake_sidecar(args, timeout=180.0):
        spawns.append(args)
        time.sleep(0.3)
        return {"schema_version": proj.SCHEMA_VERSION, "header": header}

    monkeypatch.setattr(proj, "resolve_save", lambda *a, **k: header)
    monkeypatch.setattr(proj, "_run_sidecar", fake_sidecar)
    monkeypatch.setattr(proj, "_read_disk_cache", lambda key: None)
    monkeypatch.setattr(proj.atomic, "write_bytes", lambda *a, **k: None)

    out = _fan_out(11, lambda _i: proj.load_projection())

    assert len(spawns) == 1, f"{len(spawns)} sidecars for one save -- that is the stampede"
    assert all(v is out[0] for v in out.values())


def test_concurrent_scans_of_one_directory_run_one_subprocess(monkeypatch, tmp_path):
    """What invalidates a stored scan is ``tests/test_scan_fingerprint.py``'s subject; this
    is only that eleven layers arriving together do not become eleven subprocesses."""
    spawns: list[list[str]] = []
    proj._SCAN_MEMO.clear()

    def fake_sidecar(args, timeout=180.0):
        spawns.append(args)
        time.sleep(0.3)
        return {"root": str(tmp_path), "saves": [], "unsupported": []}

    monkeypatch.setattr(proj, "_run_sidecar", fake_sidecar)
    _fan_out(11, lambda _i: proj.scan_saves(tmp_path))
    assert len(spawns) == 1


def test_states_over_one_projection_share_the_expensive_views(projection, game, isolated_memos):
    first = world_state.WorldState(projection=projection, game=game)
    second = world_state.WorldState(projection=projection, game=game)

    for name in ("graph", "structures", "pipe_flow", "conduit_runs", "proposals"):
        assert getattr(first, name) is getattr(second, name), name


def test_a_different_projection_gets_its_own_views(projection, game, isolated_memos):
    other = {**projection}  # a distinct dict, so a distinct identity
    a = world_state.WorldState(projection=projection, game=game)
    b = world_state.WorldState(projection=other, game=game)

    assert a.graph is not b.graph


def test_the_stores_the_tools_write_through_are_never_shared(projection, game, isolated_memos):
    """``plans`` and ``labels`` are disk-backed and mutated in place -- ``st.plans.put(...)``
    then ``st.plans.save()``. Sharing them would hide one process's rename from the other."""
    a = world_state.WorldState(projection=projection, game=game)
    b = world_state.WorldState(projection=projection, game=game)

    assert a.plans is not b.plans
    assert a.labels is not b.labels
