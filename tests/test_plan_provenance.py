"""A stored plan's FIELD: what its selectors resolved to, and what a recall says when
that changes.

The gap this closes, in one sentence: ``plan_id`` proves the world did not move, and
proves nothing at all about whether ``sources: ["region:Spire Coast"]`` still means the
same 51 nodes. It did not -- re-deriving the region layer from the game's own
``FGMapAreaTexture`` took that name to 18 -- and every recall since re-planned over a
different sixth of the map without a word.

Most of this file runs with no game install and no save. That is deliberate: the check is
pure geometry over the committed node table, so a synthetic table of five nodes is enough
to shift the ground under a plan and watch it say so. Only the two tests that need the
reader's own plan file or the whole tool are marked ``integration``.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.planning.stored import provenance as prov
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.planning.stored.recall import PLAN_DEFAULTS, recall_plan
from satisfactory_mcp.domain.planning.stored.store import Plan, PlanStore
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.nodes import table as node_table


def _node(name: str, x_m: float, y_m: float, resource: str = "Desc_OreIron_C") -> dict:
    """One node row in the shape the table publishes -- coordinates in CENTIMETRES."""
    return {
        "instance": f"Persistent_Level:PersistentLevel.{name}",
        "resource": resource,
        "purity": "normal",
        "kind": "node",
        "x": x_m * 100,
        "y": y_m * 100,
        "z": 0.0,
    }


#: A field of five, laid out so that a single box can be drawn around a subset of them.
FIELD = [
    _node("BP_ResourceNode1", 0, 0),
    _node("BP_ResourceNode2", 100, 100),
    _node("BP_ResourceNode3", 200, 200),
    _node("BP_ResourceNode4", 900, 900),
    _node("BP_ResourceNode5", 1000, 1000, resource="Desc_OreCopper_C"),
]

BOX = "bbox:-10,-10,250,250"  # holds nodes 1..3


class _World:
    """Just enough state for a selector to resolve against: no save, no docs."""

    def __init__(self, plans=None):
        self.plans = plans
        self.game = object()

    def player_position(self):
        return None


@pytest.fixture
def table(monkeypatch):
    """The node table, swappable mid-test -- which is how the map moves under a plan."""

    def use(nodes):
        # Both names: callers outside the package bind the re-export, those inside the table's.
        for owner in (nodes_mod, node_table):
            monkeypatch.setattr(owner, "load_nodes", lambda: nodes_mod.NodeTable(nodes, {}))

    use(FIELD)
    return use


# ------------------------------------------------------------------- recording


def test_a_selector_records_the_nodes_it_resolved_to(table):
    field = prov.record(_World().game, _World(), [BOX])
    (entry,) = field["selectors"]
    assert entry["selector"] == BOX
    assert entry["count"] == 3
    assert entry["nodes"] == ["BP_ResourceNode1", "BP_ResourceNode2", "BP_ResourceNode3"]
    assert entry["hash"]
    assert entry["bbox"] == [0.0, 0.0, 200.0, 200.0], "metres, and the form bbox: takes"


def test_each_selector_is_recorded_separately_with_the_filters_folded_in(table):
    """Which selector moved is the actionable half. One total for a two-selector spec
    would say the field changed and leave the reader to work out which name did it."""
    field = prov.record(_World().game, _World(), [BOX, "near:1000,1000@50", "kind:node"])
    assert [e["selector"] for e in field["selectors"]] == [BOX, "near:1000,1000@50"]
    assert [e["count"] for e in field["selectors"]] == [3, 1]


def test_a_whole_map_plan_records_an_empty_field(table):
    """Empty is not missing. "no selector to check" and "saved before there was a check"
    are different answers, and only one of them is a reason to warn."""
    field = prov.record(_World().game, _World(), None)
    assert field["selectors"] == []
    assert prov.recorded(Plan(name="p", provenance=field))
    assert prov.notes(_World().game, _World(), Plan(name="p", provenance=field)) == []


def test_a_field_too_large_to_name_is_still_counted_and_hashed(table, monkeypatch):
    monkeypatch.setattr(prov, "LEAF_CAP", 2)
    (entry,) = prov.record(_World().game, _World(), [BOX])["selectors"]
    assert entry["count"] == 3
    assert entry["nodes"] == [], "past the cap the names are dropped, not the record"
    assert entry["hash"]


# ----------------------------------------------------------------------- drift


def _saved_plan(sources=(BOX,)) -> Plan:
    world = _World()
    return Plan(
        name="spire",
        args={"objective": "max_mw", "sources": list(sources)},
        provenance=prov.record(world.game, world, list(sources)),
    )


def test_an_unchanged_field_says_nothing(table):
    plan = _saved_plan()
    assert prov.compare(_World().game, _World(), plan) == []
    assert prov.notes(_World().game, _World(), plan) == []


def test_a_shrunken_selector_is_drift_and_names_what_vanished(table):
    plan = _saved_plan()
    table([n for n in FIELD if n["instance"].endswith(("Node1", "Node4", "Node5"))])

    (drift,) = prov.compare(_World().game, _World(), plan)
    assert (drift.then, drift.now) == (3, 1)
    assert drift.gone == ("BP_ResourceNode2", "BP_ResourceNode3")
    assert drift.appeared == ()
    assert drift.bbox_selector == "bbox:0,0,200,200"


def test_a_set_swapped_without_changing_size_is_still_drift(table):
    """The count alone would call this unchanged. A plan whose three nodes are three
    DIFFERENT nodes is planning somewhere else, which is the whole point of the hash."""
    plan = _saved_plan()
    table([_node(f"BP_ResourceNodeNew{x}", x, x) for x in (10, 20, 30)])

    (drift,) = prov.compare(_World().game, _World(), plan)
    assert (drift.then, drift.now) == (3, 3)
    assert drift.gone and drift.appeared


def test_the_note_is_loud_and_says_how_to_pin_the_field(table):
    plan = _saved_plan()
    table([n for n in FIELD if not n["instance"].endswith("Node3")])

    notes = prov.notes(_World().game, _World(), plan)
    assert "planning over a DIFFERENT field" in notes[0]
    assert "the plan did not change, its selector did" in notes[0]
    assert notes[1].startswith(f"{BOX!r}: 3 node(s) when saved, 2 now.")
    assert "gone: BP_ResourceNode3. appeared: none." in notes[1]
    assert "The saved nodes sat in bbox:0,0,200,200 (metres)." in notes[1]
    assert "pass that box as sources=" in notes[-1]
    assert "re-run with save_as='spire'" in notes[-1]


def test_a_drift_too_large_to_name_reports_counts_rather_than_guessing(table, monkeypatch):
    monkeypatch.setattr(prov, "LEAF_CAP", 2)
    plan = _saved_plan()
    table([n for n in FIELD if not n["instance"].endswith("Node3")])

    (drift,) = prov.compare(_World().game, _World(), plan)
    assert not drift.named and drift.gone == () and drift.appeared == ()
    assert "Too many to name" in prov.notes(_World().game, _World(), plan)[1]


# ------------------------------------------------------------------- degrading


def test_a_plan_with_no_record_says_so_and_states_the_field_it_finds_now(table):
    """Not a refusal and not a guess: the plan still recalls, and the note hands over the
    one thing that settles it -- the box the selector covers today."""
    old = Plan(name="spire-coast-full", args={"sources": [BOX]})
    assert not prov.recorded(old)

    notes = prov.notes(_World().game, _World(), old)
    assert "records no resolved node set" in notes[0]
    assert "CANNOT be checked" in notes[0]
    assert "save_as='spire-coast-full'" in notes[0]
    assert "resolves to 3 node(s) HERE AND NOW in bbox:0,0,200,200" in notes[1]


def test_a_selector_matching_nothing_is_not_offered_as_a_box(table):
    """There is no rectangle to hand over, so the note must not point at one."""
    old = Plan(name="p", args={"sources": ["bbox:5000,5000,6000,6000"]})
    notes = prov.notes(_World().game, _World(), old)
    assert notes[1].endswith(
        "0 node(s) HERE AND NOW -- it selects nothing in this world, "
        "so there is no field to compare."
    )


def test_a_whole_map_plan_with_no_record_stays_quiet(table):
    """There is no selector that could have changed meaning, so there is nothing to say."""
    assert prov.notes(_World().game, _World(), Plan(name="p", args={"objective": "max_mw"})) == []


# ---------------------------------------------------------------------- recall


class _Plans:
    def __init__(self, *plans):
        self.plans = list(plans)

    def find(self, name):
        return next((p for p in self.plans if p.name == name), None)


def test_recalling_a_drifted_plan_carries_the_note(table):
    plan = _saved_plan()
    table([n for n in FIELD if not n["instance"].endswith("Node3")])

    st = _World(plans=_Plans(plan))
    _kwargs, name, notes = recall_plan(st, "spire", dict(PLAN_DEFAULTS))
    assert name == "spire"
    assert any("planning over a DIFFERENT field" in n for n in notes)


def test_overriding_sources_suppresses_the_field_note(table):
    """This call is not planning over the stored field, so a note about it would describe
    a plan nobody asked for -- and would sit above numbers it does not apply to."""
    plan = _saved_plan()
    table([n for n in FIELD if not n["instance"].endswith("Node3")])

    st = _World(plans=_Plans(plan))
    _kwargs, _name, notes = recall_plan(st, "spire", {**PLAN_DEFAULTS, "sources": ["all"]})
    assert not any("DIFFERENT field" in n for n in notes)


def test_a_state_that_cannot_resolve_selectors_claims_nothing(table):
    """A world with no game data attached cannot re-resolve, so it must not report the
    field as unchanged either. Silence, not a verdict."""

    class _NoGame:
        plans = _Plans(_saved_plan())

    _kwargs, _name, notes = recall_plan(_NoGame(), "spire", dict(PLAN_DEFAULTS))
    assert notes == ['recalled plan "spire" v0'], "only the version, no field verdict"


# ----------------------------------------------------------------- persistence


def test_the_record_round_trips_through_disk(tmp_path, monkeypatch, table):
    from satisfactory_mcp.domain.planning.stored import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    world = _World()
    PlanLog("TESTWORLD").create(
        "p",
        {"sources": [BOX]},
        plan_id="id1",
        provenance=prov.record(world.game, world, [BOX]),
        actor=Actor("chat"),
    )

    again = PlanLog("TESTWORLD").view().plans[0]
    assert prov.recorded(again)
    assert again.provenance["selectors"][0]["count"] == 3
    assert prov.compare(world.game, world, again) == []


def test_a_plan_file_written_before_this_existed_still_loads(tmp_path, monkeypatch):
    """The exact file shape on the owner's machine: no `provenance` key anywhere."""
    from satisfactory_mcp.domain.planning.stored import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    (tmp_path / "OLD.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "world_id": "OLD",
                "session_name": "Han Solo",
                "plans": [
                    {
                        "name": "spire-coast-full",
                        "args": {"objective": "max_mw", "sources": ["region:Spire Coast"]},
                        "notes": "",
                        "plan_id": "e2f8b236",
                        "factory": "",
                        "created": "Han Solo_260726-212757.sav",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (plan,) = PlanStore.load("OLD").plans
    assert plan.provenance == {}
    assert not prov.recorded(plan)
    assert prov.compare(object(), _World(), plan) == [], "no record means no verdict"


def test_saving_over_a_plan_rewrites_its_record_with_the_arguments(tmp_path, monkeypatch, table):
    """A record describing the PREVIOUS sources would be checked against the new ones and
    report drift that is really an edit."""
    from satisfactory_mcp.domain.planning.stored import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    log = PlanLog("TESTWORLD")
    world = _World()
    key = log.create(
        "p",
        {"sources": [BOX]},
        plan_id="id1",
        provenance=prov.record(world.game, world, [BOX]),
        actor=Actor("chat"),
    ).key
    wide = ["bbox:-10,-10,2000,2000"]
    log.push_args(
        key,
        1,
        {"sources": wide},
        actor=Actor("chat"),
        stamp=lambda _s: {"plan_id": "id2", "provenance": prov.record(world.game, world, wide)},
    )

    (plan,) = log.view().plans
    assert [e["selector"] for e in plan.provenance["selectors"]] == wide
    assert prov.compare(world.game, world, plan) == []


# --------------------------------------------------------- the reader's own plan


@pytest.mark.integration
def test_the_saved_reference_plan_degrades_rather_than_crashing(planned):
    """The plan that found this gap. It predates the record, so the answer is "cannot be
    checked" plus the box its selector covers TODAY -- never a crash, and never a shrug."""
    stored = planned.plans.find("spire-coast-full")
    assert not prov.recorded(stored)

    notes = prov.notes(planned.game, planned, stored)
    assert "records no resolved node set" in notes[0]
    assert any("resolves to" in n and "bbox:" in n for n in notes[1:])


@pytest.mark.integration
def test_a_plan_saved_now_records_its_field_and_recalls_silently(tmp_path, monkeypatch, live):
    from satisfactory_mcp import server as srv
    from satisfactory_mcp.domain.planning.stored import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    out = srv.plan_factory(
        sources=["near:1475,-2098@300"], exports=["MW"], save_as="probe", limit=2
    )
    assert "Field recorded: near:1475,-2098@300=" in out

    stored = PlanLog(live.plans.world_id).view().find("probe")
    assert prov.recorded(stored)
    assert prov.notes(live.game, live, stored) == [], "an unmoved field is silent"
