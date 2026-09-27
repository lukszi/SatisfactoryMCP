"""Two processes editing one world's labels at once: neither loses the other's work."""

from __future__ import annotations

import multiprocessing as mp
import os
import time

import pytest

from satisfactory_mcp.core import filelock
from satisfactory_mcp.domain.factories.labels import LabelStore, StaleStore

ROUNDS = 12


def _writer(root: str, who: str, start) -> None:
    os.environ["SATISFACTORY_USER_DATA"] = root
    start.wait()
    for i in range(ROUNDS):
        with LabelStore.editing("W") as store:
            time.sleep(0.005)
            store.put(f"{who} {i}", [f"Build_{who}_C_{i}"])


def test_two_processes_naming_at_once_keep_every_label(tmp_path):
    ctx = mp.get_context("spawn")
    start = ctx.Barrier(2)
    procs = [ctx.Process(target=_writer, args=(str(tmp_path), who, start)) for who in "AB"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    os.environ["SATISFACTORY_USER_DATA"] = str(tmp_path)
    try:
        from satisfactory_mcp import config

        config.labels_dir.cache_clear()
        store = LabelStore.load("W")
    finally:
        del os.environ["SATISFACTORY_USER_DATA"]
        config.labels_dir.cache_clear()
    names = sorted(x.name for x in store.labels)
    assert names == sorted(f"{w} {i}" for w in "AB" for i in range(ROUNDS))
    assert store.version == 2 * ROUNDS


def test_a_stale_version_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    from satisfactory_mcp import config

    monkeypatch.setattr(config, "labels_dir", lambda: tmp_path)
    with LabelStore.editing("W") as store:
        store.put("one", ["m1"])
    with (
        pytest.raises(StaleStore, match="changed elsewhere"),
        LabelStore.editing("W", expect=0) as s,
    ):
        s.put("two", ["m2"])
    assert [x.name for x in LabelStore.load("W").labels] == ["one"]


def test_a_held_lock_times_out_with_the_file_named(tmp_path):
    target = tmp_path / "W.json"
    with filelock.held(target):
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_try_lock, args=(str(target), q))
        p.start()
        p.join(30)
        said = q.get(timeout=5)
    assert "W.json" in said and "nothing was written" in said


def _try_lock(target: str, q) -> None:
    from pathlib import Path

    try:
        with filelock.held(Path(target), timeout=0.3):
            q.put("got it")
    except filelock.LockTimeout as exc:
        q.put(str(exc))
