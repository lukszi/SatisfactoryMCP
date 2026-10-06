"""The plan tools over the op log: versions in reads, base_rev on writes, plan_log (contract §10).

The unit half runs against a stand-in world, so it needs neither a save nor the game install.
The integration half drives plan_factory against the reference world.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.core.filelock import LockTimeout
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.planning.stored.recall import PLAN_DEFAULTS
from satisfactory_mcp.domain.session import journal
from satisfactory_mcp.interfaces.mcp import app
from satisfactory_mcp.interfaces.mcp.tools.planning import _plan_log, _requests, solve, staging
from tests.support.reference_world import FIVE_RIP_ARGS, FIXTURE_WORLD, RIP

PAGE = Actor("page", "", 4242)
WORLD = "TESTWORLD"


class _World:
    world_id = WORLD
    age_note = "test world"

    def __init__(self) -> None:
        self.header: dict = {"session_name": "Test"}
        self.session_name = "Test"

    @property
    def plans(self):
        return PlanLog(WORLD).view()


@pytest.fixture
def log(monkeypatch, use_world):
    use_world(_World)
    monkeypatch.setattr(app, "save_token", lambda st: "sav:test")
    plans = PlanLog(WORLD)
    plans.create("north oil", {"objective": "max_mw", "sources": ["north"]}, actor=PAGE)
    return plans


def _key(log: PlanLog, name: str = "north oil") -> str:
    return log.find(name, include_forgotten=True).key


# ------------------------------------------------------------------ refusals


def test_a_write_without_base_rev_is_refused_and_names_the_version(log):
    out = srv.rename_plan(name="north oil", to="coast oil")
    assert out == (
        '! plan "north oil" exists at v1: read it (list_plans name="north oil") and pass '
        "base_rev=1; nothing renamed"
    )
    assert log.head_rev(_key(log)) == 1
    assert "base_rev=1" in srv.forget_plan(name="north oil")
    assert log.find("north oil") is not None


def test_cheap_refusals_still_come_before_the_version_check(log):
    log.create("coast oil", {}, actor=PAGE)
    assert "already has a plan named" in srv.rename_plan(name="north oil", to="COAST OIL")
    assert "cannot be blank" in srv.rename_plan(name="north oil", to="  ")
    assert srv.rename_plan(name="nope", to="x").startswith("! no saved plan named 'nope'")


# ------------------------------------------------------------------ writes


def test_a_rename_with_base_rev_lands_as_the_next_version(log):
    out = srv.rename_plan(name="north oil", to="coast oil", base_rev=1)
    assert "renamed plan 'north oil' to 'coast oil'" in out
    assert 'plan "coast oil" is now v2' in out
    (commit,) = log.commits(_key(log, "coast oil"), since=1)
    assert commit.actor == Actor("chat", "", os.getpid())
    assert commit.sav == "sav:test"


def test_edits_to_different_keys_merge_and_say_what_others_changed(log):
    key = _key(log)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    out = srv.rename_plan(name="north oil", to="coast oil", base_rev=1)
    assert "merged onto v2 (you were on v1) -> now v3; others changed: v2 sloops 0→4 (page)" in out
    assert log.state(key).args.sloops == 4


def test_the_same_key_changed_elsewhere_is_outdated_and_nothing_lands(log):
    key = _key(log)
    log.push(key, 1, [{"op": "rename", "name": "page name"}], actor=PAGE)
    out = srv.rename_plan(name="page name", to="chat name", base_rev=1)
    assert out.startswith('! outdated: plan "page name" is at v2; you wrote against v1.')
    assert "Nothing was applied." in out
    assert 're-read (list_plans name="page name") and push again with base_rev=2' in out
    assert log.head_rev(key) == 2


def test_a_forget_can_be_undone_through_plan_log(log):
    key = _key(log)
    out = srv.forget_plan(name="north oil", base_rev=1)
    assert out.startswith("forgot plan 'north oil' in v2")
    assert log.find("north oil") is None

    assert "FORGOTTEN" in srv.plan_log(name="north oil")
    back = srv.plan_log(name="north oil", undo=2, base_rev=2)
    assert back.startswith('# undid v2 of plan "north oil": restored')
    assert not log.state(key).forgotten


def test_a_busy_lock_writes_nothing_and_says_so(log, monkeypatch):
    def held(*_a, **_k):
        raise LockTimeout("held")

    monkeypatch.setattr(PlanLog, "push", held)
    assert srv.rename_plan(name="north oil", to="x", base_rev=1) == _plan_log.BUSY


def _over(log, key, base_rev, overrides, whole=None):
    head = log.state(key)
    kwargs = whole if whole is not None else {**PLAN_DEFAULTS, **head.kwargs(), **overrides}
    return solve._save_over(
        _World(), head, base_rev, kwargs, None, solve.PlanMeta("", ""), None, None, overrides
    )


def test_a_recalled_save_diffs_only_what_chat_overrode_against_its_base(log):
    key = _key(log)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    log.push(key, 2, [{"op": "set", "field": "sloops", "value": 3}], actor=PAGE)
    head = log.state(key)
    stale = {**PLAN_DEFAULTS, **head.kwargs(), "water_extractors": 5}
    pushed, tail = solve._save_over(
        _World(), head, 1, stale, None, solve.PlanMeta("", ""), None, None, None
    )
    assert pushed is None and "sloops: you 3, page set 2 in v2" in tail

    pushed, tail = _over(log, key, 1, {"water_extractors": 5})
    assert pushed is not None, tail
    now = log.state(key)
    assert (now.rev, now.args.sloops, now.args.water_extractors) == (4, 3, 5)


def test_a_save_over_a_plan_renamed_since_base_rev_merges_into_it(log):
    key = _key(log)
    log.push(key, 1, [{"op": "rename", "name": "coast oil"}], actor=PAGE)
    assert solve._save_target(_World(), "north oil", None) == (None, "")
    target, refusal = solve._save_target(_World(), "north oil", 1)
    assert refusal == "" and target.key == key
    assert solve._save_target(_World(), key, 2)[0].key == key

    pushed, tail = _over(log, key, 1, {"sloops": 2})
    assert pushed is not None, tail
    now = log.state(key)
    assert (now.name, now.args.sloops) == ("coast oil", 2)
    assert len(log.heads()) == 1


def test_a_stale_name_two_plans_carried_is_refused_not_guessed(log):
    first = _key(log)
    log.push(first, 1, [{"op": "rename", "name": "coast oil"}], actor=PAGE)
    second = log.create("north oil", {}, actor=PAGE).key
    log.push(second, 1, [{"op": "rename", "name": "desert oil"}], actor=PAGE)
    target, refusal = solve._save_target(_World(), "north oil", 1)
    assert target is None
    assert refusal.startswith('! no plan is called "north oil" now, and 2 plans were at v1')
    assert refusal.endswith("Pass save_as=<key>; nothing saved")


# ------------------------------------------------------------------ plan_log


def test_plan_log_lists_versions_newest_first_with_head_and_key(log):
    key = _key(log)
    for n in (1, 2, 3):
        log.push(key, n, [{"op": "set", "field": "sloops", "value": n}], actor=PAGE)
    out = srv.plan_log(name="north oil")
    lines = out.splitlines()
    assert lines[0] == f'# plan "north oil" v4 (key {key})'
    body = [line for line in lines if line.startswith("v")]
    assert [line.split()[0] for line in body] == ["v4", "v3", "v2", "v1"]
    assert body[0].startswith("v4 page: sloops 2→3")
    assert body[-1].startswith("v1 page: created")

    assert [
        x.split()[0]
        for x in srv.plan_log(name="north oil", since=2).splitlines()
        if x.startswith("v")
    ] == ["v4", "v3"]
    assert "(+2 more" in srv.plan_log(name="north oil", limit=2)


def test_undo_needs_base_rev_and_refuses_twice(log):
    key = _key(log)
    log.push(key, 1, [{"op": "add", "field": "banned", "member": "Recipe_X_C"}], actor=PAGE)
    assert "base_rev=2; nothing undone" in srv.plan_log(name="north oil", undo=2)
    out = srv.plan_log(name="north oil", undo=2, base_rev=2)
    assert "−banned" in out and 'plan "north oil" is now v3' in out
    assert "already undone by v3" in srv.plan_log(name="north oil", undo=2, base_rev=3)
    assert "undone in v3" in srv.plan_log(name="north oil")
    assert "cannot be undone" in srv.plan_log(name="north oil", undo=1, base_rev=3)
    assert "not both" in srv.plan_log(name="north oil", undo=2, restore=1, base_rev=3)


def test_undo_of_a_version_changed_again_since_is_outdated(log):
    key = _key(log)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    log.push(key, 2, [{"op": "set", "field": "sloops", "value": 5}], actor=PAGE)
    out = srv.plan_log(name="north oil", undo=2, base_rev=3)
    assert out.startswith("! outdated")
    assert log.head_rev(key) == 3


def test_restore_makes_the_head_equal_an_old_version(log):
    key = _key(log)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    log.push(key, 2, [{"op": "add", "field": "sources", "member": "south"}], actor=PAGE)
    out = srv.plan_log(name="north oil", restore=1, base_rev=3)
    assert out.startswith('# restored v1 of plan "north oil"')
    head = log.state(key)
    assert head.rev == 4
    assert head.args.to_dict() == log.state(key, 1).args.to_dict()


# ------------------------------------------------------------------ reads


def test_list_plans_shows_the_version_and_the_last_change(log):
    key = _key(log)
    log.push(key, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    out = srv.list_plans()
    header = next(line for line in out.splitlines() if line.startswith("name\t"))
    assert header.split("\t")[:3] == ["name", "ver", "last change"]
    row = next(line for line in out.splitlines() if line.startswith("north oil\t"))
    assert row.split("\t")[1] == "v2"
    assert row.split("\t")[2].startswith("page 0s: sloops 0→4")


def test_the_detail_view_heads_with_version_and_key(log):
    key = _key(log)
    assert srv.list_plans(name="north oil").splitlines()[0] == f'# plan "north oil" v1 (key {key})'


# ------------------------------------------------------------------ the MCP seam


def test_the_actor_comes_from_the_initialize_handshake():
    info = SimpleNamespace(name="claude-code")
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(clientInfo=info)))
    assert app.actor(ctx) == Actor("chat", "claude-code", os.getpid())
    assert app.actor(None) == Actor("chat", "", os.getpid())

    class _Outside:
        @property
        def session(self):
            raise ValueError("Context is not available outside of a request")

    assert app.actor(_Outside()).client == ""


def test_no_tool_schema_exposes_the_context():
    for tool in asyncio.run(srv.mcp.list_tools()):
        assert "ctx" not in tool.inputSchema.get("properties", {}), tool.name


def test_the_write_tools_take_base_rev_and_the_server_says_to_pass_it():
    tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    for name in ("plan_factory", "rename_plan", "forget_plan", "site_plan", "plan_log"):
        assert "base_rev" in tools[name].inputSchema["properties"], name
    assert "required" in tools["plan_factory"].inputSchema["properties"]
    assert "base_rev" in srv.mcp.instructions and "ui_context" in srv.mcp.instructions


# ------------------------------------------------------------------ pins in the plan tools


@pytest.fixture
def pinned(monkeypatch, projection, game, use_world):
    from satisfactory_mcp.domain.world.state import WorldState

    def fresh(*_a, **_k):
        return WorldState(projection=projection, game=game)

    use_world(fresh)
    return fresh()


def _iron_field(st):
    from satisfactory_mcp.domain.session import pins
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    iron = [n for n in nodes_mod.load_nodes().nodes if n["resource"] == "Desc_OreIron_C"]
    pin, _ = pins.create(st, "field", {"node": max(iron, key=lambda n: n["y"])["instance"]})
    return pin


def test_save_as_stores_what_source_and_required_pins_stand_for(pinned):
    from satisfactory_mcp.domain.planning.readout import summary
    from satisfactory_mcp.domain.session import pins

    field = _iron_field(pinned)
    made = srv.plan_factory(sources=[field["id"]], save_as="probe", limit=2, **FIVE_RIP_ARGS)
    assert 'saved as "probe" v1' in made, made
    assert f"{field['id']} = node:" in made
    head = PlanLog(FIXTURE_WORLD).find("probe")
    assert head.args.sources == [f"node:{m}" for m in field["ref"]["nodes"]]
    recipe = summary.solve_summary(pinned.game, pinned, head.kwargs())["rows"][0]["recipe_id"]
    process, _ = pins.create(pinned, "process", {"plan": head.key, "recipe": recipe})
    plan_pin, _ = pins.create(pinned, "plan", {"plan": head.key})
    out = srv.plan_factory(
        plan=plan_pin["id"], required=[process["id"]], save_as="probe", base_rev=1, limit=2
    )
    assert "is now v2" in out, out
    assert PlanLog(FIXTURE_WORLD).state(head.key).args.required == [recipe]
    refused = srv.plan_factory(plan="pin:99", limit=2)
    assert refused.startswith("! pin:99 does not exist")
    wrong = srv.plan_factory(sources=[process["id"]], limit=2, **FIVE_RIP_ARGS)
    assert (
        wrong
        == f"! {process['id']} is a process: it cannot stand for resource nodes; nothing solved"
    )


def test_alternates_for_a_plan_add_deltas_and_journal_a_view(pinned):
    srv.plan_factory(
        objective="min_machines", exports=[RIP], export_minimums={RIP: 5}, save_as="rip", limit=2
    )
    journal.set_writer("chat")
    out = srv.alternates_for_item(item=RIP, plan="rip")
    assert out.startswith(f"# recipes for {RIP} in “rip” v1: 4 (")
    assert "in plan" in out and "Δmach" in out and "ΔMW draw" in out and "Δraw" in out
    assert 'plan_factory(plan="rip", required=[...], base_rev=1, save_as="rip")' in out
    (entry,) = journal.read(FIXTURE_WORLD)
    assert (entry["kind"], entry["tool"], entry["rev"]) == ("plan.view", "alternates_for_item", 1)
    assert entry["args"] == {"view": "alternates", "item": "Desc_IronPlateReinforced_C"}
    assert entry["text"] == f"looked at recipes for {RIP}"
    plain = srv.alternates_for_item(item=RIP)
    assert "Δmach" not in plain
    assert srv.alternates_for_item(item=RIP, plan="nope").startswith("! no saved plan named 'nope'")


# ------------------------------------------------------------------ track from chat (P4)


def _rip(monkeypatch, headroom=None):
    monkeypatch.setattr(staging, "_stages_seen", {})
    srv.plan_factory(save_as="rip", limit=2, **FIVE_RIP_ARGS)
    log = PlanLog(FIXTURE_WORLD)
    key = log.find("rip").key
    if headroom is not None:
        log.push(key, 1, [{"op": "set", "field": "headroom_mw", "value": headroom}], actor=PAGE)
    return key


def test_diff_and_commission_journal_a_track_view(pinned, monkeypatch):
    key = _rip(monkeypatch)
    journal.set_writer("chat")
    srv.diff_vs_save(plan="rip", limit=2)
    srv.diff_vs_save(plan="rip", stage=1, limit=2)
    srv.commission_plan(plan="rip", limit=2)
    srv.diff_vs_save(limit=2, **FIVE_RIP_ARGS)
    entries = journal.read(FIXTURE_WORLD)
    assert [(e["tool"], e["plan"]) for e in entries] == [
        ("diff_vs_save", key),
        ("diff_vs_save", key),
        ("commission_plan", key),
    ]
    assert [e["args"] for e in entries] == [
        {"view": "track", "stage": None, "section": "stages"},
        {"view": "track", "stage": 1, "section": "stages"},
        {"view": "track", "stage": None, "section": "startup"},
    ]


def test_both_tools_use_the_stored_headroom(pinned, monkeypatch):
    _rip(monkeypatch, headroom=2000)
    out = srv.commission_plan(plan="rip", limit=2)
    assert "headroom_MW=2,000 (source: stored on the plan)" in out
    given = srv.commission_plan(plan="rip", headroom_mw=1500, limit=2)
    assert "headroom_MW=1,500 (source: given by caller)" in given
    assert "# STAGES" in srv.diff_vs_save(plan="rip", limit=2)


def test_a_second_read_notes_that_the_stages_moved(pinned, monkeypatch):
    key = _rip(monkeypatch, headroom=2000)
    first = srv.diff_vs_save(plan="rip", limit=2)
    assert "the stages changed" not in first
    assert "the stages changed" not in srv.diff_vs_save(plan="rip", limit=2)
    PlanLog(FIXTURE_WORLD).push(
        key, 2, [{"op": "set", "field": "headroom_mw", "value": 1}], actor=PAGE
    )
    moved = srv.commission_plan(plan="rip", limit=2)
    assert (
        "the stages changed since you last read this plan (v2 -> v3): you were in stage 1 of 1, "
        "now no startup order fits the headroom"
    ) in moved
    assert "the stages changed" not in srv.diff_vs_save(plan="rip", limit=2)


def test_a_chat_save_never_touches_the_stored_headroom(pinned, monkeypatch):
    key = _rip(monkeypatch, headroom=2000)
    out = srv.plan_factory(plan="rip", save_as="rip", base_rev=2, sloops=1, limit=2)
    assert "is now v3" in out, out
    head = PlanLog(FIXTURE_WORLD).state(key)
    assert head.headroom_mw == 2000.0 and head.args.sloops == 1
    assert not any(
        op.get("field") == "headroom_mw" for op in PlanLog(FIXTURE_WORLD).commits(key)[-1].ops
    )


# ------------------------------------------------------------------ integration


PROBE = {"sources": ["near:1475,-2098@300"], "exports": ["MW"], "limit": 2}


@pytest.mark.integration
def test_save_as_creates_then_needs_base_rev_then_merges(game):
    made = srv.plan_factory(save_as="probe", **PROBE)
    assert 'saved as "probe" v1' in made

    refused = srv.plan_factory(save_as="probe", sloops=1, **PROBE)
    assert refused.startswith('! plan "probe" exists at v1')
    assert refused.endswith("base_rev=1; nothing saved")

    world = app.load_world().world_id
    key = PlanLog(world).find("probe").key
    PlanLog(world).push(key, 1, [{"op": "set", "field": "notes", "value": "page"}], actor=PAGE)
    saved = srv.plan_factory(save_as="probe", sloops=1, base_rev=1, **PROBE)
    assert saved.rstrip().splitlines()[-1].startswith("merged onto v2 (you were on v1) -> now v3")
    head = PlanLog(world).state(key)
    assert (head.rev, head.args.sloops, head.notes) == (3, 1, "page")
    assert head.plan_id, "the stamp recorded the new head's solve-input hash"


@pytest.mark.integration
def test_recall_and_save_over_one_plan_merges_page_edits_made_since(game):
    srv.plan_factory(save_as="probe", **PROBE)
    world = app.load_world().world_id
    key = PlanLog(world).find("probe").key
    PlanLog(world).push(key, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    PlanLog(world).push(key, 2, [{"op": "set", "field": "sloops", "value": 3}], actor=PAGE)
    PlanLog(world).push(key, 3, [{"op": "rename", "name": "probe two"}], actor=PAGE)
    out = srv.plan_factory(plan="probe", save_as="probe", base_rev=1, water_extractors=5, limit=2)
    assert "merged onto v4 (you were on v1) -> now v5" in out
    head = PlanLog(world).state(key)
    assert (head.name, head.args.sloops, head.args.water_extractors) == ("probe two", 3, 5)
    assert len(PlanLog(world).heads()) == 1


@pytest.mark.integration
def test_a_new_name_ignores_base_rev_and_says_so(game):
    out = srv.plan_factory(save_as="fresh", base_rev=7, **PROBE)
    assert "base_rev=7 was ignored: this is a new plan" in out


@pytest.mark.integration
def test_a_same_key_edit_from_chat_is_outdated(game):
    srv.plan_factory(save_as="probe", **PROBE)
    world = app.load_world().world_id
    key = PlanLog(world).find("probe").key
    PlanLog(world).push(key, 1, [{"op": "set", "field": "sloops", "value": 3}], actor=PAGE)
    out = srv.plan_factory(save_as="probe", sloops=1, base_rev=1, **PROBE)
    assert '! outdated: plan "probe" is at v2; you wrote against v1.' in out
    assert "sloops: you 1, page set 3 in v2" in out
    assert PlanLog(world).head_rev(key) == 2


@pytest.mark.integration
def test_a_recalled_outdated_save_never_tells_chat_to_pass_save_as(game):
    srv.plan_factory(save_as="probe", **PROBE)
    world = app.load_world().world_id
    key = PlanLog(world).find("probe").key
    PlanLog(world).push(
        key, 1, [{"op": "set", "field": "water_extractors", "value": 5}], actor=PAGE
    )
    out = srv.plan_factory(plan="probe", save_as="probe", base_rev=1, water_extractors=3, limit=2)
    assert "! outdated:" in out
    assert "pass save_as to keep it" not in out
    assert "overridden this call: water_extractors (not saved: see below)" in out


@pytest.mark.integration
def test_a_bare_solve_is_journalled_with_its_request(game):
    journal.set_writer("chat")
    srv.plan_factory(objective="min_power", **PROBE)
    (entry,) = journal.read(app.load_world().world_id)
    assert entry["kind"] == "plan.solve" and entry["tool"] == "plan_factory"
    assert entry["args"] == {
        "objective": "min_power",
        "sources": PROBE["sources"],
        "exports": ["MW"],
    }
    assert entry["plan"] is None and entry["text"].endswith("(min_power)")


@pytest.mark.integration
def test_a_recalled_solve_journals_the_plans_pinned_logistics_and_its_rev(game):
    srv.plan_factory(save_as="probe", logistics_items=["Water"], **PROBE)
    journal.set_writer("chat")
    srv.plan_factory(plan="probe", sloops=1, limit=2)
    (entry,) = journal.read(app.load_world().world_id)
    assert (entry["kind"], entry["rev"]) == ("plan.solve", 1)
    assert entry["args"]["logistics_items"] == ["Water"] and entry["args"]["sloops"] == 1


@pytest.mark.integration
def test_a_recalled_plan_prints_its_version_and_journals_a_view(game):
    srv.plan_factory(save_as="probe", **PROBE)
    journal.set_writer("chat")
    out = srv.commission_plan(plan="probe", limit=2)
    assert 'recalled plan "probe" v1' in out
    (entry,) = journal.read(app.load_world().world_id)
    assert (entry["kind"], entry["tool"], entry["rev"]) == ("plan.view", "commission_plan", 1)


@pytest.mark.integration
def test_required_names_are_resolved_or_refused_by_name(game):
    assert "no recipe is called 'Nonsuch'" in srv.plan_factory(required=["Nonsuch"], **PROBE)
    rid = next(r.cls for r in game.recipes.values() if r.name == "Iron Plate")
    ids, refused = _requests.resolve_required(["iron plate", rid])
    assert refused == "" and ids == [rid, rid]


@pytest.mark.integration
def test_site_plan_needs_base_rev(game):
    srv.plan_factory(save_as="probe", **PROBE)
    out = srv.site_plan(plan="probe", at="0,0")
    assert out.endswith("base_rev=1; not sited")
    sited = srv.site_plan(plan="probe", at="0,0", base_rev=1)
    assert 'plan "probe" is now v2' in sited
    assert "is now v3" in srv.site_plan(plan="probe", clear=True, base_rev=2)
