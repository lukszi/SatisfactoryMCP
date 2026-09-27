"""Persisted plans: what is stored, and how a recall merges with overrides.

The design decision worth pinning is that the REQUEST is stored, never the solution. A
solve depends on unlocked recipes, free nodes and built buildings, all of which move as
the game is played, so a stored solution would keep answering about a world that no
longer exists -- silently.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.domain.planning.store import PLAN_ARGS, Plan, PlanStore

pytestmark = pytest.mark.integration

CHAT = Actor("chat")


@pytest.fixture
def store(tmp_path, monkeypatch):
    from satisfactory_mcp.domain.planning import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    return PlanLog("TESTWORLD")


class _FakeState:
    def __init__(self, plans):
        self.plans = plans


def _held(*plans: Plan) -> _FakeState:
    """Plans held in memory, the shape ``recall_plan`` reads through ``st.plans``."""
    return _FakeState(PlanStore(world_id="TESTWORLD", plans=list(plans)))


class _World:
    world_id = "TESTWORLD"

    def __init__(self) -> None:
        self.header: dict = {}


# ---------------------------------------------------------------- storage


def test_a_plan_round_trips_through_disk(store):
    store.create(
        "north oil",
        {"objective": "max_mw", "sources": ["north"], "limit": 25, "save": "x.sav"},
        plan_id="abc123",
        notes="the coast",
        factory="oil setup",
        actor=CHAT,
    )

    again = PlanLog("TESTWORLD").view()
    assert [p.name for p in again.plans] == ["north oil"]
    plan = again.plans[0]
    assert plan.plan_id == "abc123"
    assert plan.notes == "the coast"
    assert plan.factory == "oil setup"


def test_only_solve_shaping_arguments_are_stored(store):
    """`limit` is presentation and `save`/`world` say which file was read, not what was
    asked for. Storing them would make two identical requests compare unequal."""
    store.create(
        "p",
        {"objective": "max_item", "limit": 25, "save": "x.sav", "world": "W", "sources": ["north"]},
        plan_id="x",
        actor=CHAT,
    )
    plan = store.view().find("p")
    assert set(plan.args) <= set(PLAN_ARGS)
    assert "limit" not in plan.args
    assert "save" not in plan.args


def test_defaults_are_not_stored(store):
    """A stored plan should read as the request that was made, not a dump of every knob."""
    store.create(
        "p", {"objective": "max_mw", "sources": None, "exports": []}, plan_id="x", actor=CHAT
    )
    assert store.view().find("p").args == {"objective": "max_mw"}


def test_saving_the_same_name_twice_updates_rather_than_duplicates(store):
    from satisfactory_mcp.interfaces.mcp.tools.planning import _save_request

    _save_request(_World(), "p", {"objective": "max_mw"}, "one", "", "", "", {}, None)
    _save_request(_World(), "P", {"objective": "min_power"}, "two", "", "", "", {}, None)
    (plan,) = store.view().plans
    assert plan.plan_id == "two" and plan.rev == 2
    assert plan.args["objective"] == "min_power"


def test_plans_do_not_live_in_the_cache():
    """cache_prune wipes the cache tree, and a plan the player named is not
    regenerable."""
    from satisfactory_mcp import config

    assert config.plans_dir() != config.cache_dir()
    assert config.cache_dir() not in config.plans_dir().parents
    assert config.plans_dir() != config.labels_dir()


def test_a_file_written_before_siting_and_provenance_still_loads(tmp_path, monkeypatch):
    """Both keys arrived as defaulted fields on purpose, and the discipline only holds if
    a file that predates them still opens. A plan is not regenerable: this is the one
    failure that loses something the player typed."""
    import json

    from satisfactory_mcp.domain.planning import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    (tmp_path / "OLDWORLD.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "world_id": "OLDWORLD",
                "session_name": "before",
                "plans": [
                    {
                        "name": "ancient",
                        "args": {"objective": "min_power", "sources": ["north"]},
                        "notes": "typed by hand",
                        "plan_id": "old123",
                        "factory": "",
                        "created": "some.sav",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = PlanStore.load("OLDWORLD").plans[0]
    assert plan.kwargs() == {"objective": "min_power", "sources": ["north"]}
    assert plan.notes == "typed by hand"
    assert plan.provenance == {} and plan.siting == {}


# ---------------------------------------------------------------- renaming


@pytest.fixture
def live_plans(tmp_path, monkeypatch):
    """One stored plan in a scratch plans dir, reached through the real state loader.

    Written through the store rather than through plan_factory: a rename must not need a
    solve, so the test that proves it must not pay for one either.
    """
    from satisfactory_mcp.domain.planning import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    st = srv._state()
    PlanLog(st.world_id).create(
        "north oil",
        {"objective": "max_mw", "sources": ["north"]},
        plan_id="abc123",
        notes="the coast",
        factory="oil setup",
        actor=CHAT,
    )
    return st


def test_a_plan_can_be_renamed_and_keeps_everything_else(live_plans):
    """A name was the one thing a player picked and the one thing they could not correct:
    the workaround was to save the plan again under a second name and forget the first,
    which pays an LP solve and drops the siting and the field record on the floor."""
    out = srv.rename_plan(name="north oil", to="coast oil")
    assert "renamed plan 'north oil' to 'coast oil'" in out

    again = PlanLog(live_plans.world_id).view()
    assert [p.name for p in again.plans] == ["coast oil"]
    assert again.plans[0].plan_id == "abc123"
    assert again.plans[0].notes == "the coast"
    assert again.plans[0].factory == "oil setup"


def test_a_rename_onto_an_existing_name_is_refused(live_plans):
    """`find` matches case-insensitively, so two plans differing only in case would make
    every later recall ambiguous."""
    PlanLog(live_plans.world_id).create("coast oil", {}, plan_id="x", actor=CHAT)
    assert "already has a plan named" in srv.rename_plan(name="north oil", to="COAST OIL")
    assert PlanLog(live_plans.world_id).find("north oil") is not None


def test_a_rename_of_an_unknown_plan_lists_what_exists(live_plans):
    out = srv.rename_plan(name="nope", to="whatever")
    assert out.startswith("! no saved plan named 'nope'")
    assert "north oil" in out


def test_a_blank_new_name_is_refused(live_plans):
    assert "cannot be blank" in srv.rename_plan(name="north oil", to="   ")


# ------------------------------------------------------------ the detail view


def test_one_plan_reads_back_as_the_request_that_was_stored(live_plans):
    """The only way to read a stored plan's arguments was plan_factory(plan=...), which
    pays a full LP solve and then prints what the request RESOLVED to. The question
    "what did I ask for" is answerable from the file alone."""
    out = srv.list_plans(name="north oil")
    assert "# plan 'north oil'" in out
    assert "plan_id=abc123" in out
    assert "objective\tmax_mw" in out
    assert "sources\tnorth" in out
    assert "nothing was solved here" in out


def test_an_unknown_plan_name_lists_what_exists(live_plans):
    assert srv.list_plans(name="nope").startswith("! no saved plan named 'nope'")


def test_the_list_marks_a_cell_it_had_to_cut(live_plans):
    """A silently clipped source list reads as the whole list, and a plan's sources are
    the argument that decides what it plans over."""
    PlanLog(live_plans.world_id).create(
        "wide",
        {"objective": "max_mw", "sources": [f"node:BP_ResourceNode{i}" for i in range(9)]},
        plan_id="y",
        actor=CHAT,
    )
    row = next(line for line in srv.list_plans().splitlines() if line.startswith("wide\t"))
    assert "~" in row


# ----------------------------------------------------------------- recall


def test_recall_returns_the_stored_arguments():
    st = _held(Plan("p", {"objective": "min_power", "sources": ["north"]}, plan_id="x"))
    kwargs, name, _ = srv._plan_kwargs(st, "p", dict(srv.PLAN_DEFAULTS))
    assert name == "p"
    assert kwargs["objective"] == "min_power"
    assert kwargs["sources"] == ["north"]


def test_a_default_valued_argument_does_not_clobber_the_plan():
    """THE trap. MCP fills defaults in before the tool sees them, so `objective` always
    arrives as "max_mw"; a naive merge would overwrite every recalled plan with it."""
    st = _held(Plan("p", {"objective": "min_power"}, plan_id="x"))
    supplied = dict(srv.PLAN_DEFAULTS)  # exactly what an untouched call looks like
    kwargs, _, notes = srv._plan_kwargs(st, "p", supplied)
    assert kwargs["objective"] == "min_power"
    assert not any("overridden" in n for n in notes)


def test_an_explicit_override_wins_and_says_it_was_not_saved():
    st = _held(Plan("p", {"objective": "min_power", "sources": ["north"]}, plan_id="x"))
    supplied = {**srv.PLAN_DEFAULTS, "sources": ["south"]}
    kwargs, _, notes = srv._plan_kwargs(st, "p", supplied)
    assert kwargs["sources"] == ["south"]
    assert kwargs["objective"] == "min_power", "untouched arguments still come from the plan"
    assert any("overridden" in n and "sources" in n for n in notes)
    assert any("not saved" in n for n in notes)


def test_recalling_an_unknown_plan_lists_what_exists():
    st = _held(Plan("north oil", plan_id="x"))
    with pytest.raises(KeyError, match="north oil"):
        srv._plan_kwargs(st, "nope", dict(srv.PLAN_DEFAULTS))


def test_recall_names_the_version_first():
    st = _held(Plan("p", {"objective": "min_power"}, plan_id="x", rev=14))
    _, _, notes = srv._plan_kwargs(st, "p", dict(srv.PLAN_DEFAULTS))
    assert notes[0] == 'recalled plan "p" v14'


def test_no_plan_name_passes_arguments_straight_through():
    supplied = {**srv.PLAN_DEFAULTS, "objective": "max_item"}
    kwargs, name, notes = srv._plan_kwargs(_held(), None, supplied)
    assert name == "" and notes == []
    assert kwargs["objective"] == "max_item"


def test_plan_defaults_cover_every_stored_argument():
    """A stored argument with no declared default could never be overridden, because
    the override test compares against PLAN_DEFAULTS."""
    assert set(PLAN_ARGS) == set(srv.PLAN_DEFAULTS)


def test_kwargs_filters_out_anything_no_longer_accepted():
    """A plan saved by an older build must not blow up a newer build_scenario call."""
    plan = Plan(name="p", args={"objective": "max_mw", "retired_knob": 7})
    assert plan.kwargs() == {"objective": "max_mw"}


# ----------------------------------------------------------------- scoping


def _diff_index(state, request, scope=None):
    from satisfactory_mcp.domain.planning.diff import _index

    return _index(state, request, scope)


def test_scoping_limits_what_counts_as_already_built(game, state):
    """Unscoped, "you already have 12 of these" counts machines on the far side of the
    map that are busy doing something else -- the wrong answer to "how far along is the
    aluminium setup"."""
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    request = build_scenario(game, state, objective="max_mw", exports=["MW"])
    everything = _diff_index(state, request)
    total = sum(len(v) for v in everything.by_recipe.values())
    assert total > 0

    first = next(iter(everything.by_recipe.values()))[0]
    only_one = _diff_index(state, request, {first["instance"].rsplit(".", 1)[-1]})
    assert sum(len(v) for v in only_one.by_recipe.values()) == 1


def test_an_empty_scope_means_nothing_is_already_built(game, state):
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    request = build_scenario(game, state, objective="max_mw", exports=["MW"])
    index = _diff_index(state, request, set())
    assert not index.by_recipe
    assert not index.idle
    assert not index.by_generator
    assert not index.by_extractor_class


def test_a_node_tapped_by_another_factory_is_neither_reusable_nor_free(game, state):
    """It must drop out of BOTH. Left in `tapped` it would read as already built for
    this plan; moved to `free` it would plan a second miner onto an occupied node."""
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    request = build_scenario(game, state, objective="max_mw", exports=["MW"])
    unscoped = _diff_index(state, request)
    scoped = _diff_index(state, request, set())

    assert sum(len(v) for v in unscoped.tapped.values()) > 0
    assert not scoped.tapped, "no in-scope extractor taps anything"
    before = sum(len(v) for v in unscoped.free.values())
    after = sum(len(v) for v in scoped.free.values())
    assert after == before, "tapped nodes must not become free just because we narrowed"
