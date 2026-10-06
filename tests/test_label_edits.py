"""Editing a label instead of writing it again: rename, add a machine, drop a machine.

A label holds a materialised SET of machine ids, so membership is set arithmetic and no
exception mechanism is needed to drop one. The property every test here is really pinning
is that an edit touches only what it names -- ``name_factory`` re-anchors, and re-anchoring
to fix one machine is what these tools exist to replace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.factories.labels import NAME_MAX, BadName, LabelError, LabelStore

STEEL = [f"Build_FoundryMk1_C_{100 + i}" for i in range(4)]
STRAY = "Build_ConstructorMk1_C_300"


@dataclass
class _Cand:
    machines: list = field(default_factory=lambda: [STRAY])
    centroid: tuple = (0.0, 0.0)
    buildings: dict = field(default_factory=dict)


def _persist(store: LabelStore) -> None:
    with LabelStore.editing(store.world_id, store.session_name) as fresh:
        fresh.labels = store.labels


@pytest.fixture
def store(tmp_path, monkeypatch):
    from satisfactory_mcp.domain.factories import labels as labels_mod

    monkeypatch.setattr(labels_mod.config, "labels_dir", lambda: tmp_path)
    return LabelStore(world_id="TESTWORLD")


# ------------------------------------------------------------------ rename


def test_a_label_can_be_renamed_and_keeps_its_machines(store):
    label = store.put("steel site", STEEL, notes="ingots")
    assert store.rename(label, "  North Steel  ") == "steel site"
    assert label.name == "North Steel"
    assert label.anchors == sorted(STEEL)
    assert label.notes == "ingots"


def test_a_rename_moves_the_slug_so_the_old_name_stops_resolving(store):
    """``find`` matches a slug as well as a name. A label keeping its old id would go on
    answering to the name it was renamed away from, everywhere a selector is written."""
    label = store.put("steel site", STEEL)
    assert label.id == "steel-site"
    store.rename(label, "Ore Yard")
    assert label.id == "ore-yard"
    assert store.find("steel-site") is None
    assert store.find("Ore Yard") is label


def test_a_rename_onto_an_existing_name_names_the_conflict(store):
    store.put("steel site", STEEL)
    store.put("ore yard", [STRAY])
    with pytest.raises(LabelError, match="already has a factory named 'ore yard'"):
        store.rename(store.find("steel site"), "ORE YARD")
    assert store.find("steel site") is not None


def test_two_names_that_slug_the_same_are_refused_too(store):
    """The names differ case-insensitively and the ids do not, which is the collision
    ``find`` cannot see past."""
    store.put("steel site", STEEL)
    store.put("Ore-Yard", [STRAY])
    with pytest.raises(LabelError, match="both slug to 'ore-yard'"):
        store.rename(store.find("steel site"), "Ore Yard")


def test_a_blank_new_name_is_refused(store):
    store.put("steel site", STEEL)
    with pytest.raises(LabelError, match="cannot be blank"):
        store.rename(store.find("steel site"), "   ")


def test_a_slash_or_an_overlong_name_is_refused_for_a_new_label_and_a_rename(store):
    label = store.put("steel site", STEEL)
    for bad in ("steel/iron", "x" * (NAME_MAX + 1)):
        with pytest.raises(BadName):
            store.rename(label, bad)
        with pytest.raises(BadName):
            store.name(bad, _Cand(), create=True)
        with pytest.raises(BadName):
            store.name(bad, _Cand())
    assert [x.name for x in store.labels] == ["steel site"]


def test_a_label_file_from_a_newer_schema_is_refused(store):
    from satisfactory_mcp.core.schema import NewerSchema
    from satisfactory_mcp.domain.factories import labels as labels_mod

    path = LabelStore.path_for("TESTWORLD")
    path.write_text(json.dumps({"schema": labels_mod.SCHEMA + 1, "labels": []}), encoding="utf-8")
    with pytest.raises(NewerSchema, match="newer version"):
        LabelStore.load("TESTWORLD")


# --------------------------------------------------------------- membership


def test_attaching_adds_only_what_is_new_and_keeps_the_rest(store):
    label = store.put("steel site", STEEL)
    assert store.attach(label, [STEEL[0], STRAY]) == [STRAY]
    assert label.anchors == sorted([*STEEL, STRAY])


def test_detaching_drops_one_machine_and_leaves_the_others_anchored(store):
    label = store.put("steel site", [*STEEL, STRAY])
    assert store.detach(label, [STRAY, "Build_SmelterMk1_C_999"]) == [STRAY]
    assert label.anchors == sorted(STEEL)


def test_dropping_the_last_machine_is_refused_rather_than_deleting_the_label(store):
    """The name and its notes are the only things in the file the player typed, and there
    is nowhere to get them back from. Deleting is ``remove``, and it is said out loud."""
    label = store.put("steel site", [STRAY])
    with pytest.raises(LabelError, match="forget_factory"):
        store.detach(label, [STRAY])
    assert label.anchors == [STRAY]


def test_an_edited_label_round_trips_through_the_file(store):
    label = store.put("steel site", STEEL, notes="ingots", when="2026-08-09")
    label.centroid = (-100_000.0, -120_000.0)
    store.attach(label, [STRAY])
    store.detach(label, [STEEL[0]])
    store.rename(label, "North Steel")
    _persist(store)

    again = LabelStore.load("TESTWORLD")
    assert [x.name for x in again.labels] == ["North Steel"]
    kept = again.labels[0]
    assert kept.id == "north-steel"
    assert kept.anchors == sorted([*STEEL[1:], STRAY])
    assert kept.notes == "ingots"
    assert kept.created == "2026-08-09"
    assert kept.centroid == (-100_000.0, -120_000.0)
    assert again.find("steel-site") is None


# ------------------------------------------------------------- the two tools


@pytest.fixture
def live(tmp_path, monkeypatch):
    """One label over real machines, in a scratch labels dir, through the state loader."""
    from satisfactory_mcp.domain.factories import labels as labels_mod
    from satisfactory_mcp.domain.planning.stored import store as plans_mod

    monkeypatch.setattr(labels_mod.config, "labels_dir", lambda: tmp_path / "labels")
    monkeypatch.setattr(plans_mod.config, "plans_dir", lambda: tmp_path / "plans")
    st = srv._state()
    picked = sorted(st.graph.machines())[:5]
    st.labels.put("north steel", picked[:3], notes="the first three")
    _persist(st.labels)
    return st, picked


def _reload(st) -> LabelStore:
    return LabelStore.load(st.labels.world_id)


@pytest.mark.integration
def test_rename_factory_keeps_the_membership(live):
    st, picked = live
    out = srv.rename_factory(name="north steel", to="coast steel")
    assert "renamed factory 'north steel' to 'coast steel'" in out
    again = _reload(st)
    assert [x.name for x in again.labels] == ["coast steel"]
    assert again.labels[0].anchors == sorted(picked[:3])
    assert again.labels[0].notes == "the first three"
    assert again.find("north steel") is None


@pytest.mark.integration
def test_rename_factory_refuses_a_name_this_world_already_uses(live):
    st, picked = live
    st.labels.put("coast steel", picked[3:])
    _persist(st.labels)
    assert "already has a factory named 'coast steel'" in srv.rename_factory(
        name="north steel", to="COAST STEEL"
    )
    assert _reload(st).find("north steel") is not None


@pytest.mark.integration
def test_rename_factory_lists_what_exists_when_the_name_is_unknown(live):
    out = srv.rename_factory(name="nope", to="whatever")
    assert out.startswith("! no label named 'nope'")
    assert "north steel" in out


@pytest.mark.integration
def test_a_stored_plan_scoped_to_the_factory_follows_the_rename(live):
    """``Plan.factory`` is resolved by name every time a diff or a layout scopes itself to
    one, so a rename that left it behind would point both at nothing."""
    st, _picked = live
    from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog

    log = PlanLog(st.plans.world_id)
    log.create("steel expansion", {}, plan_id="x", factory="north steel", actor=Actor("chat"))
    assert "1 stored plan(s) followed it" in srv.rename_factory(
        name="north steel", to="coast steel"
    )
    assert log.find("steel expansion").factory == "coast steel"


@pytest.mark.integration
def test_amend_factory_adds_one_machine_without_re_anchoring_the_others(live):
    st, picked = live
    out = srv.amend_factory(name="north steel", add=[f"machine:{picked[3]}"])
    assert "3 -> 4 anchor(s), +1 -0" in out
    assert _reload(st).find("north steel").anchors == sorted(picked[:4])


@pytest.mark.integration
def test_amend_factory_drops_one_machine_and_keeps_the_rest(live):
    st, picked = live
    out = srv.amend_factory(name="north steel", drop=[f"machine:{picked[0]}"])
    assert "3 -> 2 anchor(s), +0 -1" in out
    assert _reload(st).find("north steel").anchors == sorted(picked[1:3])


@pytest.mark.integration
def test_amend_factory_refuses_to_drop_the_last_machine(live):
    st, picked = live
    st.labels.put("lone", [picked[4]])
    _persist(st.labels)
    out = srv.amend_factory(name="lone", drop=[f"machine:{picked[4]}"])
    assert out.startswith("! that would leave 'lone' with no machines at all")
    assert "forget_factory" in out
    assert _reload(st).find("lone").anchors == [picked[4]]


@pytest.mark.integration
def test_amend_factory_says_when_the_machines_added_are_already_named(live):
    """The verdict comes from ``covers``, the one predicate for "already named"; the
    per-label counts beside it are attribution, not a second filter."""
    st, picked = live
    st.labels.put("neighbour", picked[3:])
    _persist(st.labels)
    out = srv.amend_factory(name="north steel", add=[f"machine:{picked[3]},{picked[4]}"])
    assert "overlaps 'neighbour' on 2 machine(s)" in out
    assert "2 added machine(s) already have a name -- covers()" in out
    assert len(_reload(st).find("north steel").anchors) == 5


@pytest.mark.integration
def test_a_dead_anchor_survives_an_amend_and_goes_only_when_pruning_is_asked_for(live):
    """Reading an older save must not silently shrink a label, so nothing drops an anchor
    the save is merely missing until it is asked to."""
    st, picked = live
    st.labels.put("north steel", [*picked[:3], "Build_SmelterMk1_C_999999"])
    _persist(st.labels)

    srv.amend_factory(name="north steel", add=[f"machine:{picked[3]}"])
    assert "Build_SmelterMk1_C_999999" in _reload(st).find("north steel").anchors

    out = srv.amend_factory(name="north steel", prune_missing=True)
    assert "1 of them already gone from this save" in out
    assert _reload(st).find("north steel").anchors == sorted(picked[:4])


@pytest.mark.integration
def test_amend_factory_writes_nothing_on_a_dry_run(live):
    st, picked = live
    out = srv.amend_factory(name="north steel", add=[f"machine:{picked[3]}"], dry_run=True)
    assert "would amend" in out
    assert "dry run: nothing written" in out
    assert _reload(st).find("north steel").anchors == sorted(picked[:3])


@pytest.mark.integration
def test_amend_factory_asks_for_something_to_do(live):
    assert srv.amend_factory(name="north steel").startswith("! nothing to amend")
