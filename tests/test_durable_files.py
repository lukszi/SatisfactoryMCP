"""The two files nothing can regenerate, and the three tables everything can.

Opposite failures, one commit, because they are the same mistake about who owns a file.

**The player's own words have to survive a crash.** The plan log and ``LabelStore`` write
JSON the reader typed -- factory names and saved plans -- and both used
``Path.write_text``, which truncates and then writes. Die in between and the file on disk is
empty, which is not recoverable from the save, from the docs dump or from anywhere else on
the machine. ``core.atomic`` writes a sibling and renames it over the top, so the original
survives every failure short of the rename itself.

**The generated tables have to NOT survive a regeneration.** ``load_nodes``,
``load_regions`` and ``load_collectibles`` were ``lru_cache(maxsize=1)``, which caches on
nothing: run the generator against a new game build and the running server keeps answering
from the table it read at startup, while the generator prints the new counts to the same
terminal. They are keyed on ``(path, mtime_ns)`` now, exactly as ``spatial.heightfield``
already was.

Every test here writes to ``tmp_path`` and none needs a save or a game install.
"""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core import atomic
from satisfactory_mcp.core.saveio import projection as projection_mod
from satisfactory_mcp.domain.collectibles import table as collectibles_table
from satisfactory_mcp.domain.factories.labels import LabelStore
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial import regions as regions_mod

# ------------------------------------------------------------------ atomic write


def test_the_bytes_are_the_ones_write_text_would_have_produced(tmp_path):
    """A drop-in, or the switch is a format change wearing a durability fix's clothes.

    Newline translation included: ``Path.write_text`` opens with the platform default, so on
    Windows a ``\\n`` in the JSON becomes ``\\r\\n`` on disk, and a file written by the old
    code and by this one have to be byte for byte the same.
    """
    text = '{\n "schema": 1,\n "plans": []\n}'
    direct = tmp_path / "direct.json"
    direct.write_text(text, encoding="utf-8")
    replaced = atomic.write_text(tmp_path / "replaced.json", text)
    assert replaced.read_bytes() == direct.read_bytes()


def test_a_crash_at_the_rename_leaves_the_ORIGINAL_intact(tmp_path, monkeypatch):
    """The whole point, simulated at the one instruction that is allowed to fail.

    ``os.replace`` is the last step and everything before it happens off to the side, so
    raising there is exactly "the process died with the new content written and not yet
    installed". The old file must be untouched -- not empty, not half the new document.
    """
    path = tmp_path / "labels.json"
    path.write_text('{"labels": ["the old one"]}', encoding="utf-8")
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("simulated crash between the write and the rename")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        atomic.write_text(path, '{"labels": ["the new one"]}')

    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["labels"] == ["the old one"]


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """Litter is not harmless here: these directories are the reader's, not the cache's."""
    path = tmp_path / "plans.json"
    path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(atomic.os, "replace", lambda src, dst: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        atomic.write_text(path, "{}")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plans.json"]


def test_a_first_write_to_a_missing_file_still_lands(tmp_path):
    """The ordinary case: there is nothing to protect and it must work anyway."""
    path = atomic.write_text(tmp_path / "new.json", '{"ok": true}')
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.json"]


# ------------------------------------------------- the same guarantee, for the cache

#: ``write_bytes`` exists for the projection cache, which this module's own docstring used to
#: name as the file that did NOT need it. What changed is not the file, it is how many
#: processes write it: the cache directory is shared by the web server, by every CLI
#: invocation and by the eight ``pytest-xdist`` workers the suite now runs on, all of which
#: resolve the same newest save and miss the same key at the same moment. A plain
#: ``Path.write_bytes`` is create-then-fill, so the window that used to need a crash to be
#: observed is now simply a concurrent reader.


def test_the_cache_write_is_bytes_and_not_text(tmp_path):
    """The reason ``write_bytes`` is not ``write_text`` with an encode in front of it.

    What goes through here is a pickle, and a pickle is full of bytes that a text mode would
    take an interest in -- ``0x0a`` above all, which on Windows would come back as ``0x0d
    0x0a`` and turn a cache entry into an ``UnpicklingError``. This pins the one property
    that matters: what comes back is what went in, byte for byte, newlines and all.
    """
    payload = pickle.dumps({"segments": [1, 2, 3], "note": "a\nb\r\nc"})
    assert b"\n" in payload, "the fixture stopped testing the thing it was chosen to test"
    path = atomic.write_bytes(tmp_path / "save-abc.pkl", payload)
    assert path.read_bytes() == payload
    assert pickle.loads(path.read_bytes())["note"] == "a\nb\r\nc"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["save-abc.pkl"]


def test_a_reader_never_sees_a_partial_cache_entry(tmp_path, monkeypatch):
    """The concurrency property, tested where it is decidable: nothing is at the name yet.

    A real torn read needs two processes and a lucky schedule, which is not a test. What IS
    testable is the invariant that makes the torn read impossible -- the target name never
    holds anything but a complete file, because the content is built under a different name
    and moved. So: fail at the rename, having written a whole payload, and demand that the
    destination does not exist at all. Under ``Path.write_bytes`` it would exist and be
    short.
    """
    target = tmp_path / "save-def.pkl"

    def boom(src, dst):
        # The temp is complete on disk at this instant -- that is what makes the assertion
        # below meaningful rather than vacuous.
        assert Path(src).stat().st_size > 0
        raise OSError("the rename lost the race")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError, match="lost the race"):
        atomic.write_bytes(target, pickle.dumps(list(range(10_000))))

    assert not target.exists(), "a half-written cache entry was published under its real name"
    assert list(tmp_path.iterdir()) == [], "the failed write left its temp behind"


def test_two_writers_in_one_process_do_not_share_a_temp(tmp_path, monkeypatch):
    """Why the temp name carries a counter and not only the process id.

    The pid separates two servers; it does not separate two threads of one server, nor the
    pooled whole-folder passes in the integration suite, which run in one process and can
    have several writes to one directory in flight. Two temps for one target had the same
    name before the counter, so one writer's ``os.replace`` would move the other's
    half-finished file into place -- the exact failure this module exists to prevent,
    reintroduced by the fix for it.
    """
    target = tmp_path / "save-ghi.pkl"
    seen: list[Path] = []
    real_replace = atomic.os.replace

    def record(src, dst):
        seen.append(Path(src))
        return real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", record)
    atomic.write_bytes(target, b"first")
    atomic.write_bytes(target, b"second")

    assert len(seen) == 2
    assert seen[0] != seen[1], f"both writes used the temp {seen[0].name}"
    assert target.read_bytes() == b"second"


def test_pruning_survives_a_file_another_pruner_already_deleted(tmp_path, monkeypatch):
    """``prune_cache`` runs concurrently with itself, so every syscall in it is best-effort.

    The cache directory sits at exactly ``keep`` entries in normal use and every writer
    prunes, so two prunes overlapping is the ordinary case rather than the unlucky one. The
    ``unlink`` was already guarded. The ``stat`` inside the sort key was not, and a file that
    vanished between the glob and the sort raised ``FileNotFoundError`` straight out of
    ``sorted`` -- reaching the ``cache_prune`` tool, which calls this directly and has no
    outer guard to swallow it.

    Simulated at the one instruction that can lose the race, because that is what a rival
    pruner looks like from in here: the glob has already listed the file and it is gone by
    the time its mtime is asked for.
    """
    monkeypatch.setattr(config, "cache_dir", lambda: tmp_path)
    for i in range(5):
        (tmp_path / f"save-{i}.pkl").write_bytes(b"x")

    doomed = tmp_path / "save-2.pkl"
    real_stat = Path.stat

    def vanishing(self, *args, **kwargs):
        if self == doomed:
            doomed.unlink(missing_ok=True)  # a rival pruner got there first
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanishing)
    removed = projection_mod.prune_cache(keep=2)

    monkeypatch.undo()
    survivors = sorted(p.name for p in tmp_path.glob("save-*.pkl"))
    assert len(survivors) == 2, survivors
    assert "save-2.pkl" not in survivors
    assert removed >= 2


@pytest.mark.parametrize(
    ("store_dir", "make"),
    [("labels_dir", lambda: LabelStore(world_id="W", session_name="s"))],
)
def test_the_two_stores_survive_a_crash_mid_save(tmp_path, monkeypatch, store_dir, make):
    """Through the real ``save()``, not through the helper it calls.

    Both stores are tested the same way and in one test, because the property is the same
    property and stating it twice by hand invites the second copy to be forgotten when a
    third store is added.
    """
    monkeypatch.setattr(config, store_dir, lambda: tmp_path)
    store = make()
    path = store.save()
    first = path.read_bytes()
    assert json.loads(path.read_text(encoding="utf-8"))["world_id"] == "W"

    monkeypatch.setattr(atomic.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("no")))
    store.session_name = "changed"
    with pytest.raises(OSError):
        store.save()
    assert path.read_bytes() == first, "a crashed save must not cost what was already stored"
    assert [p.name for p in tmp_path.iterdir()] == [path.name, path.name + ".lock"]


def test_the_plan_log_survives_a_crash_mid_snapshot(tmp_path, monkeypatch):
    """The log line is the record and a snapshot only a shortcut, so a snapshot that dies
    in the rename costs neither the commit nor the snapshot already on disk."""
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path)
    log = PlanLog("W")
    key = log.create("p", {}, actor=Actor("page")).key
    first = (log.root / key / "snap" / "1.json").read_bytes()

    monkeypatch.setattr(atomic.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("no")))
    for n in range(49):
        log.push(
            key, n + 1, [{"op": "set", "field": "sloops", "value": n + 1}], actor=Actor("page")
        )
    monkeypatch.undo()

    assert log.head_rev(key) == 50 and log.state(key).args.sloops == 49
    assert [p.name for p in (log.root / key / "snap").iterdir()] == ["1.json"]
    assert (log.root / key / "snap" / "1.json").read_bytes() == first


# --------------------------------------------------------------- table reloading


def _write(path: Path, payload: dict, mtime_ns: int) -> None:
    """Write a table and stamp its mtime, so a test does not have to wait for a clock tick."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


_NODES_A = {"nodes": [{"instance": "a", "resource": "Iron", "x": 0, "y": 0}], "_meta": {"v": 1}}
_NODES_B = {
    "nodes": [
        {"instance": "a", "resource": "Iron", "x": 0, "y": 0},
        {"instance": "b", "resource": "Copper", "x": 1, "y": 1},
    ],
    "_meta": {"v": 2},
}

_REGIONS_A = {
    "region_grid": ["ab"],
    "confidence_grid": ["ll"],
    "legend": {"a": "Alpha", "b": "Beta"},
    "regions": {"Alpha": {"centroid": [0, 0]}},
    "grid_meta": {"x0": 0, "y0": 0, "cell": 256, "nx": 2, "ny": 1},
    "_meta": {"accuracy_m": 256},
}

_COLLECTIBLES_A = {
    "collectibles": [
        {"cell": "c", "instance": "x.BP_Crystal_C_1", "category": "slug", "class": "BP_Crystal_C"}
    ],
    "_meta": {},
}


def test_a_regenerated_node_table_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    """The failure this replaces: the generator prints new counts, the server quotes old ones."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    nodes_mod._TABLE.clear()
    path = tmp_path / "resource_nodes.json"

    _write(path, _NODES_A, 1_000_000_000)
    first = nodes_mod.load_nodes()
    assert len(first) == 1
    assert nodes_mod.load_nodes() is first, "an unchanged file must still cost one dict lookup"

    _write(path, _NODES_B, 2_000_000_000)
    second = nodes_mod.load_nodes()
    assert second is not first
    assert len(second) == 2
    assert second.meta == {"v": 2}
    # One entry, like the maxsize=1 it replaces: the old table is dead, not retained.
    assert len(nodes_mod._TABLE) == 1


def test_a_regenerated_region_map_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    regions_mod._MAP.clear()
    path = tmp_path / "region_names.json"

    _write(path, _REGIONS_A, 1_000_000_000)
    first = regions_mod.load_regions()
    assert first.label_for(0, 0).name == "Alpha"
    assert regions_mod.load_regions() is first

    renamed = {**_REGIONS_A, "legend": {"a": "Renamed", "b": "Beta"}}
    _write(path, renamed, 2_000_000_000)
    assert regions_mod.load_regions().label_for(0, 0).name == "Renamed"
    assert len(regions_mod._MAP) == 1


def test_a_regenerated_collectible_table_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE

    _write(path, _COLLECTIBLES_A, 1_000_000_000)
    first = collectibles_table.load_collectibles()
    assert len(first) == 1
    assert collectibles_table.load_collectibles() is first

    grown = {
        **_COLLECTIBLES_A,
        "collectibles": [
            *_COLLECTIBLES_A["collectibles"],
            {"cell": "c", "instance": "x.BP_WAT_2", "category": "sloop", "class": "BP_WAT_C"},
        ],
    }
    _write(path, grown, 2_000_000_000)
    assert len(collectibles_table.load_collectibles()) == 2
    assert len(collectibles_table._TABLE) == 1


def test_the_miss_behaviour_of_all_three_loaders_is_unchanged(tmp_path, monkeypatch):
    """Keying on the mtime must not change what happens when there is no file to stat.

    The three disagree on purpose and the disagreement is the point: the node table and the
    region map are committed, so their absence is a broken checkout and says which generator
    to run; the collectible table is untracked, so its absence is the ordinary state of a
    fresh clone and every caller degrades to the save-only census.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "empty")
    nodes_mod._TABLE.clear()
    regions_mod._MAP.clear()
    collectibles_table._TABLE.clear()

    with pytest.raises(FileNotFoundError, match="gen_resource_nodes"):
        nodes_mod.load_nodes()
    with pytest.raises(FileNotFoundError, match="gen_region_names"):
        regions_mod.load_regions()
    assert collectibles_table.load_collectibles() is None


def test_an_unreadable_collectible_table_is_not_cached_as_a_refusal(tmp_path, monkeypatch):
    """``None`` is a state a reader fixes, so the next call has to look again.

    Caching it would key an ABSENCE on an mtime, which is a thing that does not exist: the
    reader runs the generator, the file appears, and a cached no would go on being the
    answer until the process ended.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE

    path.write_text("{ not json", encoding="utf-8")
    assert collectibles_table.load_collectibles() is None

    _write(path, _COLLECTIBLES_A, 3_000_000_000)
    assert len(collectibles_table.load_collectibles()) == 1


def test_a_corrupt_collectible_table_can_be_told_from_a_missing_one(tmp_path, monkeypatch):
    """ "You never ran the generator" and "what it wrote is broken" are different answers.

    Both arrived as ``None``, so a reader who HAD run the generator was told the table did
    not exist and went looking for a run that had already happened. The degrading callers
    still degrade -- that is the default, and it is right for them -- but ``strict`` exists
    so a caller that wants to say which one it is can.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE

    # Not there at all: still ``None``, strict or not. Absence is not a fault.
    assert collectibles_table.load_collectibles(strict=True) is None

    for broken in ("{ not json", "[]", '{"collectibles": []}'):
        path.write_text(broken, encoding="utf-8")
        assert collectibles_table.load_collectibles() is None, broken
        with pytest.raises(collectibles_table.CollectiblesUnreadable, match="exists but"):
            collectibles_table.load_collectibles(strict=True)

    _write(path, _COLLECTIBLES_A, 4_000_000_000)
    assert len(collectibles_table.load_collectibles(strict=True)) == 1
