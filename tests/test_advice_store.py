"""The hidden-advisory store (docs/advisors_contract.md §4): dismiss, snooze on play time,
resurfacing, revs, the lock, and the files it refuses. Every write lands in a temporary dir."""

from __future__ import annotations

import json
import threading
import time

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.schema import NewerSchema
from satisfactory_mcp.domain.advice import rules, store

WORLD = "W1"
BY = {"kind": "page", "client": "", "pid": 1}


@pytest.fixture(autouse=True)
def advice_dir(tmp_path, monkeypatch):
    root = tmp_path / "advice"
    monkeypatch.setattr(config, "advice_dir", lambda: root)
    return root


def _adv(members=("M_1", "M_2"), kind="starved", weight=None, subject="tor factory"):
    return rules.Advisory(
        key=rules.key_for(kind, "factory", subject),
        id="",
        kind=kind,
        severity=rules.SEVERITY[kind],
        subject_kind="factory",
        subject=subject,
        text="t",
        tool_text="t",
        weight=float(len(members) if weight is None else weight),
        members=tuple(members),
        spots=(),
        bbox_m=None,
        lines=(),
        next_call="",
        seed=None,
        reveal=(),
        plan=None,
        source="test",
    )


def _split(adv, play_s=0.0):
    return store.split([adv], store.read(WORLD), play_s)


def test_a_missing_file_reads_empty_and_is_not_created(advice_dir):
    assert store.read(WORLD) == {"schema": 1, "version": 0, "hidden": {}}
    assert not advice_dir.exists()


def test_dismiss_hides_until_a_new_machine_joins():
    adv = _adv()
    entry = store.hide(WORLD, adv, "dismiss", play_s=10.0, by=BY, rev=0)
    assert entry["state"] == "dismissed" and entry["rev"] == 1 and entry["ids"] == ["M_1", "M_2"]
    active, hidden = _split(adv)
    assert active == [] and hidden[0][0] == adv
    active, hidden = _split(_adv(("M_1",)))
    assert active == [] and len(hidden) == 1
    worse = _adv(("M_1", "M_2", "M_3"))
    active, hidden = _split(worse)
    assert active == [(worse, True, 1)] and hidden == []


def test_a_snooze_runs_on_play_time():
    adv = _adv()
    entry = store.hide(WORLD, adv, "snooze", hours=1, play_s=1000.0, by=BY)
    assert entry["until_play_s"] == 1000.0 + 3600.0
    assert _split(adv, 1000.0 + 3599.0)[0] == []
    assert _split(adv, 1000.0 + 3600.0)[0] == [(adv, False, 1)]


@pytest.mark.parametrize("hours", [0.25, 25, True, None, "1"])
def test_a_snooze_outside_half_an_hour_to_a_day_is_refused(hours):
    with pytest.raises(store.AdviceError):
        store.hide(WORLD, _adv(), "snooze", hours=hours, play_s=0.0, by=BY)
    assert store.read(WORLD)["version"] == 0


def test_past_the_id_cap_the_weight_has_to_grow_by_half():
    many = [f"M_{n}" for n in range(store.IDS_CAP + 1)]
    store.hide(WORLD, _adv(many), "dismiss", play_s=0.0, by=BY)
    assert store.read(WORLD)["hidden"][_adv().key]["ids"] is None
    assert _split(_adv(many + ["X"] * 10, weight=len(many) + 10))[0] == []
    grown = _adv(many, weight=1.5 * len(many))
    assert _split(grown)[0] == [(grown, True, 1)]


def test_a_row_with_no_members_resurfaces_on_weight():
    adv = _adv((), kind="headroom", weight=100.0)
    store.hide(WORLD, adv, "dismiss", play_s=0.0, by=BY)
    assert _split(_adv((), kind="headroom", weight=140.0))[0] == []
    assert _split(_adv((), kind="headroom", weight=150.0))[0][0][1] is True


def test_a_stale_rev_is_refused_and_writes_nothing():
    adv = _adv()
    store.hide(WORLD, adv, "dismiss", play_s=0.0, by=BY, rev=0)
    with pytest.raises(store.AdviceStale) as hit:
        store.hide(WORLD, adv, "snooze", hours=1, play_s=0.0, by=BY, rev=0)
    assert hit.value.entry["rev"] == 1
    with pytest.raises(store.AdviceStale):
        store.restore(WORLD, adv.key, 7)
    assert store.read(WORLD)["version"] == 1
    store.restore(WORLD, adv.key, 1)
    assert store.read(WORLD)["hidden"] == {}
    with pytest.raises(store.AdviceMissing):
        store.restore(WORLD, adv.key)


def test_no_rev_is_last_writer_wins():
    adv = _adv()
    store.hide(WORLD, adv, "dismiss", play_s=0.0, by=BY)
    entry = store.hide(WORLD, adv, "snooze", hours=4, play_s=0.0, by={"kind": "chat"})
    assert entry["rev"] == 2 and entry["by"] == {"kind": "chat"}


def test_two_writers_both_land_under_the_lock():
    advs = [_adv(subject=f"f{n}") for n in range(8)]
    threads = [
        threading.Thread(
            target=store.hide, args=(WORLD, a, "dismiss"), kwargs={"play_s": 0.0, "by": BY}
        )
        for a in advs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = store.read(WORLD)
    assert data["version"] == 8 and len(data["hidden"]) == 8


def test_a_newer_schema_is_refused_and_left_alone(advice_dir):
    advice_dir.mkdir()
    path = store.path_for(WORLD)
    path.write_text(json.dumps({"schema": 2, "version": 3, "hidden": {}}), encoding="utf-8")
    with pytest.raises(NewerSchema):
        store.read(WORLD)
    with pytest.raises(NewerSchema):
        store.hide(WORLD, _adv(), "dismiss", play_s=0.0, by=BY)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 2


def test_a_torn_file_reads_empty(advice_dir):
    advice_dir.mkdir()
    store.path_for(WORLD).write_text('{"schema": 1, "hidd', encoding="utf-8")
    assert store.read(WORLD) == {"schema": 1, "version": 0, "hidden": {}}


def test_entries_long_out_of_the_list_are_pruned_on_write(monkeypatch):
    old = _adv(subject="gone")
    store.hide(WORLD, old, "dismiss", play_s=0.0, by=BY)
    later = time.time() + store.PRUNE_S + 60
    monkeypatch.setattr(store.time, "time", lambda: later)
    store.hide(WORLD, _adv(), "dismiss", play_s=0.0, by=BY, firing=[_adv().key])
    assert list(store.read(WORLD)["hidden"]) == [_adv().key]
