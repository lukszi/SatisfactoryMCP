"""``track_view`` (docs/planner-p4_contract.md §3 and §5.2) over the fixture world.

Plans land in a temporary plan log; the fixture world is read and never written.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import track as track_mod
from satisfactory_mcp.domain.planning.commission import partition_id
from satisfactory_mcp.domain.planning.commission_service import build_commission_report
from satisfactory_mcp.domain.planning.diff_service import build_diff_report
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.domain.world.state import WorldState

RIP = "Reinforced Iron Plate"
ARGS = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
PAGE = Actor("page")


@pytest.fixture
def world(tmp_path, monkeypatch, projection, game):
    root = tmp_path / "plans"
    root.mkdir()
    monkeypatch.setattr(config, "plans_dir", lambda: root)
    return WorldState(projection=projection, game=game)


def _plan(st, args=None, **scalars):
    log = PlanLog(st.world_id)
    made = log.create("rip 5", args or ARGS, actor=PAGE)
    ops = [{"op": "set", "field": k, "value": v} for k, v in scalars.items()]
    if ops:
        return log.push(made.key, 1, ops, actor=PAGE).state
    return log.state(made.key)


def test_rows_follow_the_diff_with_stable_ids_and_ranges_kept(world):
    state = _plan(world)
    out = track_mod.track_view(world.game, world, state)
    report = build_diff_report(world.game, world, state.kwargs(), plan=state.key, stored=state)
    assert [r["id"] for r in out["rows"]] == [track_mod.job_id(r.key) for r in report.rep.rows]
    assert all(r["id"].startswith("job:") for r in out["rows"])
    water = next(r for r in out["rows"] if r["building"] == "Water Extractor")
    assert water["id"] == "job:extractor|Build_WaterPump_C|Desc_Water_C|normal"
    assert water["have_min"] is not None and water["build_max"] is not None
    assert water["verb"] in ("ok", "unpause", "setrecipe", "build")
    assert out["key"] == state.key and out["rev"] == state.rev and out["feasible"] is True
    assert out["unpause"] == sum(r["count"] for r in out["rows"] if r["verb"] == "unpause")
    assert out["setrecipe"] == sum(r["count"] for r in out["rows"] if r["verb"] == "setrecipe")
    assert out["actionable"] == sum(1 for r in out["rows"] if r["verb"] != "ok")
    again = track_mod.track_view(world.game, world, state)
    assert [r["id"] for r in again["rows"]] == [r["id"] for r in out["rows"]]


def test_positions_are_metres_and_the_box_covers_them(world):
    state = _plan(world)
    out = track_mod.track_view(world.game, world, state)
    by_leaf = {
        str(r["instance"]).rsplit(".", 1)[-1]: r["pos"]
        for group in ("machines", "extractors", "generators")
        for r in world.projection.get(group, ())
        if r.get("pos")
    }
    acted = [r for r in out["rows"] if r["act"]]
    assert acted, "the fixture plan has an unpause or set-recipe row"
    for row in out["rows"]:
        assert len(row["act"]) <= track_mod.CAP and len(row["targets"]) <= track_mod.CAP
        for m in row["act"]:
            pos = by_leaf[m["instance"]]
            assert m["x_m"] == round(pos[0] / 100, 1) and m["y_m"] == round(pos[1] / 100, 1)
        if row["bbox_m"] is not None:
            x0, y0, x1, y1 = row["bbox_m"]
            assert x0 <= x1 and y0 <= y1
            for m in row["act"]:
                assert x0 <= m["x_m"] <= x1 and y0 <= m["y_m"] <= y1
        if row["act"]:
            assert row["selectors"] == ",".join(f"machine:{m['instance']}" for m in row["act"])


def test_act_and_targets_are_capped(world, monkeypatch):
    monkeypatch.setattr(track_mod, "CAP", 1)
    out = track_mod.track_view(world.game, world, _plan(world))
    assert all(len(r["act"]) <= 1 and len(r["targets"]) <= 1 for r in out["rows"])
    assert all(r["selectors"].count(",") == 0 for r in out["rows"])


def test_stages_name_their_rows_and_the_partition_is_stable(world):
    state = _plan(world, headroom_mw=2000)
    out = track_mod.track_view(world.game, world, state)
    assert out["headroom_mw"] == 2000.0
    assert out["startup"]["headroom_source"] == "stored on the plan"
    assert out["startup"]["headroom_mw"] == 2000.0
    assert out["count"] == len(out["stages"]) >= 1
    assert len(out["partition_id"]) == 10
    indices = {s["index"] for s in out["stages"]}
    ids = {r["id"] for r in out["rows"]}
    for stage in out["stages"]:
        assert stage["state"] and {r["row"] for r in stage["rows"]} <= ids
    assert any(r["stages"] for r in out["rows"])
    assert all(set(r["stages"]) <= indices for r in out["rows"])
    assert out["stage_text"].startswith(("you are in stage", "every stage is built"))
    assert track_mod.track_view(world.game, world, state)["partition_id"] == out["partition_id"]


def test_the_partition_moves_with_the_machine_count_and_the_headroom(world):
    base = track_mod.track_view(world.game, world, _plan(world, headroom_mw=2000))
    log = PlanLog(world.world_id)
    bigger = log.create("rip 15", {**ARGS, "export_minimums": {RIP: 15}}, actor=PAGE)
    bigger = log.push(
        bigger.key, 1, [{"op": "set", "field": "headroom_mw", "value": 2000}], actor=PAGE
    ).state
    assert track_mod.track_view(world.game, world, bigger)["partition_id"] != base["partition_id"]
    starved = log.push(
        bigger.key, 2, [{"op": "set", "field": "headroom_mw", "value": 1}], actor=PAGE
    ).state
    out = track_mod.track_view(world.game, world, starved)
    assert out["partition_id"] == "" and out["count"] == 0 and out["stages"] == []
    assert out["startup"]["ok"] is False and out["startup"]["warnings"]
    assert out["rows"], "no startup order still shows the jobs"


def test_the_default_headroom_is_the_nameplate_from_the_save(world):
    out = track_mod.track_view(world.game, world, _plan(world))
    assert out["headroom_mw"] is None
    assert out["startup"]["headroom_source"] == "nameplate from the save"
    assert out["startup"]["headroom_mw"] == out["power"]["headroom_mw"]


def test_tools_and_track_share_one_partition(world):
    state = _plan(world, headroom_mw=2000)
    out = track_mod.track_view(world.game, world, state)
    report = build_commission_report(world.game, world, state.kwargs(), None, stored=state)
    assert report.head_source == "stored on the plan"
    assert partition_id(report.tracking) == out["partition_id"]


def test_a_factory_with_no_machines_is_a_scope_error(world):
    state = _plan(world, factory="nowhere at all")
    out = track_mod.track_view(world.game, world, state)
    assert out["scope"] == "nowhere at all"
    assert out["scope_error"] == "“nowhere at all” has no machines in this save"
    assert out["rows"] == [] and out["stages"] == [] and out["cost"] == []


def test_an_infeasible_plan_is_a_shape_not_an_error(world):
    state = _plan(world, {**ARGS, "export_minimums": {RIP: 1e7}})
    out = track_mod.track_view(world.game, world, state)
    assert out["feasible"] is False and out["headline"].startswith("INFEASIBLE") and out["cause"]
    assert out["rows"] == [] and out["stages"] == [] and out["partition_id"] == ""


def test_an_empty_solve_says_so(world):
    state = _plan(world, {"objective": "min_machines", "exports": [RIP]})
    out = track_mod.track_view(world.game, world, state)
    assert out["feasible"] is True and out["empty"] is True
    assert out["rows"] == [] and out["stages"] == []


def test_caveats_and_payload(world):
    out = track_mod.track_view(world.game, world, _plan(world))
    assert out["caveats"][0] == track_mod.PAGE_ENERGISED
    ranged = any(r["build_max"] not in (None, r["build"]) for r in out["rows"])
    assert (track_mod.PAGE_RANGE in out["caveats"]) == ranged
    assert out["monitored"] > 0
    assert len(json.dumps(out)) < 64_000


def _page_strings(out):
    yield out["age_note"]
    yield out["drift_note"]
    yield out["scope_note"]
    yield from out["notes"]
    yield from out["caveats"]
    yield from out["startup"]["warnings"]
    yield from (r["note"] for r in out["rows"])
    yield from (s["state"] for s in out["stages"])


def test_the_page_reads_no_ids_codes_or_property_names(world):
    out = track_mod.track_view(world.game, world, _plan(world, headroom_mw=100000))
    assert out["stages"]
    for text in _page_strings(out):
        for banned in ("sav:", "OQ", "mHas", " -- ", "plan_id", "BUILD", "saveVersion", "%-"):
            assert banned not in text, (banned, text)


def test_page_text_rewrites_the_tool_caveats():
    from satisfactory_mcp.domain.planning.commission import (
        ENERGISED_CAVEAT,
        NO_MONITOR,
        RANGE_CAVEAT,
    )

    assert track_mod.page_text(ENERGISED_CAVEAT) == track_mod.PAGE_ENERGISED
    assert track_mod.page_text(RANGE_CAVEAT) == track_mod.PAGE_RANGE
    assert track_mod.page_text(NO_MONITOR) == track_mod.PAGE_NO_MONITOR
    assert track_mod.page_text("a -- b") == "a: b"


def test_feeders_name_what_the_waves_stand_on(world):
    out = track_mod.feeders_view(world.game, world)
    assert out["text"]
    assert all(f["mw"] > 0 and f["name"] and f["instance"] for f in out["feeders"])
    assert all(f["instance"] not in f["name"] for f in out["feeders"])
    generation = world.power_report(biomass=False)["generation_mw"]
    assert out["total_mw"] <= generation + 0.5
    if out["feeders"]:
        assert out["total_mw"] >= out["feeders"][0]["mw"] - 0.5
        assert f"{out['total_mw']:,.0f} MW" in out["text"]
    assert [f["mw"] for f in out["feeders"]] == sorted(
        (f["mw"] for f in out["feeders"]), reverse=True
    )
