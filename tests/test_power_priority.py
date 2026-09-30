"""The power-priority setting: stored per plan, merged like any scalar, read out by the solve.

docs/planner-power-priority_contract.md is what these pin.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import power_priority as pp
from satisfactory_mcp.domain.planning.optimize import (
    POWER_PRIORITY_CLOCKS,
    Scenario,
    solve,
    spread_count,
)
from satisfactory_mcp.domain.planning.planlog import (
    Actor,
    InvalidOp,
    Outdated,
    PlanLog,
    describe_op,
    inverse,
)
from satisfactory_mcp.domain.planning.recall import PLAN_DEFAULTS, overrides_of

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)
PLASTIC = "Desc_Plastic_C"
FUEL = "Desc_LiquidFuel_C"


def _set(value):
    return {"op": "set", "field": "power_priority", "value": value}


# ------------------------------------------------------------------ the split


def test_step_zero_is_the_plain_build():
    assert spread_count(11.667, 12, 1.0) == 12
    assert spread_count(0.3, 1, 1.0) == 1


@pytest.mark.parametrize(
    ("units", "cap", "machines"),
    [(10.0, 0.75, 14), (10.0, 0.5, 20), (10.0, 1 / 3, 30), (10.0, 0.25, 40), (1.6667, 0.5, 4)],
)
def test_a_step_caps_every_machine_at_its_clock(units, cap, machines):
    n = spread_count(units, 10 if units == 10.0 else 2, cap)
    assert n == machines
    assert units / n <= cap + 1e-9


def test_a_row_already_below_the_cap_is_not_split():
    assert spread_count(0.3, 1, 0.5) == 1
    assert spread_count(0.2, 1, 0.25) == 1


def test_no_machine_is_split_below_the_minimum_clock():
    assert spread_count(0.03, 1, 0.01, min_clock=0.01) == 3
    assert spread_count(0.03, 1, 0.001, min_clock=0.01) == 3


def test_a_step_never_removes_machines():
    assert spread_count(3.0, 6, 1.0) == 6


def test_the_steps_stop_at_a_quarter_clock():
    assert POWER_PRIORITY_CLOCKS[0] == 1.0 and POWER_PRIORITY_CLOCKS[-1] == 0.25
    assert list(POWER_PRIORITY_CLOCKS) == sorted(POWER_PRIORITY_CLOCKS, reverse=True)


def test_a_scenario_refuses_a_step_that_does_not_exist(game):
    with pytest.raises(ValueError, match="power_priority must be 0 to 4"):
        Scenario(game=game, recipes=[], power_priority=5)


# ------------------------------------------------------------------ the plan log


@pytest.fixture
def plans(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    return PlanLog("W")


@pytest.fixture
def plan(plans):
    return plans.create("oil", {"objective": "min_machines"}, actor=CHAT).key


def test_a_new_plan_is_at_step_zero_and_zero_is_not_a_kwarg(plans, plan):
    state = plans.state(plan)
    assert state.args.power_priority == 0
    assert "power_priority" not in state.kwargs()


def test_the_step_is_a_solve_argument(plans, plan):
    pushed = plans.push(plan, 1, [_set(2)], actor=PAGE)
    assert pushed.state.args.power_priority == 2
    assert pushed.state.kwargs()["power_priority"] == 2
    assert pushed.applied[0] == {**_set(2), "was": 0}


@pytest.mark.parametrize("value", [-1, 5, 1.5, "2", True, None])
def test_only_a_whole_step_from_zero_to_four_is_stored(plans, plan, value):
    with pytest.raises(InvalidOp, match="power_priority"):
        plans.push(plan, 1, [_set(value)], actor=PAGE)
    assert plans.head_rev(plan) == 1


def test_the_step_conflicts_only_with_itself(plans, plan):
    plans.push(plan, 1, [_set(3)], actor=CHAT)
    with pytest.raises(Outdated) as caught:
        plans.push(plan, 1, [_set(1)], actor=PAGE)
    assert caught.value.conflicts[0].key == "power_priority"
    assert plans.push(plan, 1, [_set(3)], actor=PAGE).noop
    merged = plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    assert merged.state.args.power_priority == 3 and merged.state.args.sloops == 2


def test_the_step_undoes_to_its_previous_value(plans, plan):
    plans.push(plan, 1, [_set(2)], actor=PAGE)
    plans.push(plan, 2, [_set(4)], actor=PAGE)
    assert inverse(plans.commits(plan)[2].ops) == [_set(2)]
    assert plans.undo(plan, 3, 3, actor=PAGE).state.args.power_priority == 2


def test_the_step_in_words():
    assert describe_op({**_set(2), "was": 0}) == "power priority 2: machines at most 50%"
    assert describe_op({**_set(0), "was": 2}) == "power priority off: machines at full clock"


def test_recalling_with_zero_resets_a_stored_step():
    assert PLAN_DEFAULTS["power_priority"] is None
    assert overrides_of({"power_priority": 0}) == {"power_priority": 0}
    assert overrides_of({"power_priority": None}) == {}


# ------------------------------------------------------------------ the solve


def _plastic(game, step):
    return solve(
        Scenario(
            game=game,
            recipes=["Recipe_Plastic_C", "Recipe_ResidualFuel_C"],
            objective="max_item",
            target_item=PLASTIC,
            raw_caps={"Desc_LiquidOil_C": 300.0, "Desc_Water_C": 1e6},
            exports=(PLASTIC, FUEL),
            grid_import_mw=1e5,
            power_priority=step,
        )
    )


def test_the_split_changes_machines_and_draw_never_the_flows(game):
    plain, spread = _plastic(game, 0), _plastic(game, 2)
    assert plain.exports == spread.exports and plain.raw_used == spread.raw_used
    assert spread.machines_total > plain.machines_total
    draw = [sum(-p["mw"] for p in s.processes if p["mw"] < 0) for s in (plain, spread)]
    assert draw[1] < draw[0]
    for p in spread.processes:
        assert p["clock"] <= 0.5 + 1e-9
        assert p["machines"] * p["clock"] == pytest.approx(p["machine_equivalents"], abs=1e-3)


def test_every_step_is_read_out_from_one_solve(game):
    readouts = [_plastic(game, step) for step in range(len(POWER_PRIORITY_CLOCKS))]
    ladder = readouts[0].power_steps
    assert [r["step"] for r in ladder] == list(range(len(POWER_PRIORITY_CLOCKS)))
    for sol, rung in zip(readouts, ladder, strict=True):
        assert rung["machines"] == sol.machines_total
        drawn = sum(-p["mw"] for p in sol.processes if p["mw"] < 0)
        assert rung["draw_mw"] == pytest.approx(drawn, abs=0.05)
        assert sol.power_steps == ladder


def test_the_ladder_prices_the_extra_machines(game):
    sol = _plastic(game, 1)
    view = pp.ladder(game, sol, 1, round(sol.machines_total), 0.0)
    plain, quarter = view["steps"][0], view["steps"][-1]
    assert view["splits"] and plain["extra_machines"] == 0 and plain["cost"] == []
    assert quarter["extra_machines"] > 0 and quarter["saved_mw"] > 0
    refinery = game.buildings["Build_OilRefinery_C"]
    motors = next(c for c in quarter["cost"] if c["item"] == "Motor")
    assert motors["amount"] == quarter["extra_machines"] * next(
        f.amount for f in refinery.build_cost if f.item == "Desc_Motor_C"
    )
    assert quarter["foundations"] == quarter["extra_machines"] * refinery.footprint.foundations
    assert view["steps"][1]["machines"] == round(sol.machines_total)
    assert "MW saved for" in pp.trade_text(view)


# ------------------------------------------------------------------ chat and page

RIP = "Reinforced Iron Plate"
RIP5 = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def world(tmp_path, monkeypatch, projection, game):
    from satisfactory_mcp.domain.planning import journal
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import planning

    for name in ("plans_dir", "activity_dir", "pins_dir", "labels_dir", "ui_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})

    def fresh(*_a, **_k):
        return WorldState(projection=projection, game=game)

    monkeypatch.setattr(planning, "_state", fresh)
    return fresh


def test_chat_sets_reads_and_resets_the_step(world):
    from satisfactory_mcp import server as srv

    made = srv.plan_factory(save_as="rip", power_priority=2, limit=2, **RIP5)
    assert 'saved as "rip" v1' in made and "power priority 2 (machines at most 50%)" in made
    log = PlanLog(WORLD)
    key = log.find("rip").key
    assert log.state(key).args.power_priority == 2
    recalled = srv.plan_factory(plan="rip", limit=2)
    assert "power priority 2" in recalled
    reset = srv.plan_factory(plan="rip", save_as="rip", base_rev=1, power_priority=0, limit=2)
    assert "is now v2" in reset, reset
    assert log.state(key).args.power_priority == 0
    assert srv.plan_factory(power_priority=7, limit=2, **RIP5).startswith(
        "! power_priority must be 0 to 4"
    )


@pytest.fixture
def client(world, projection, game):
    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def test_the_page_reads_the_ladder_and_pushes_the_step(client):
    created = client.post("/api/plans", json={"name": "rip", "args": RIP5}, headers=ORIGIN)
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    assert created.json()["state"]["args"]["power_priority"] == 0
    plain = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
    power = plain["power"]
    assert power["step"] == 0 and len(power["steps"]) == len(POWER_PRIORITY_CLOCKS)
    assert power["steps"][0]["machines"] == plain["machines"]
    assert power["steps"][0]["mw_draw"] == plain["mw_draw"]
    pushed = client.post(
        f"/api/plans/{key}/ops", json={"base_rev": 1, "ops": [_set(4)]}, headers=ORIGIN
    )
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["state"]["args"]["power_priority"] == 4
    spread = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
    assert spread["power"]["step"] == 4
    assert spread["machines"] == power["steps"][4]["machines"]
    assert spread["mw_draw"] == pytest.approx(power["steps"][4]["mw_draw"], abs=0.02)
    assert spread["plan_id"] != plain["plan_id"]
    bad = client.post("/api/plan/solve", json={"args": {"power_priority": 9}}, headers=ORIGIN)
    assert bad.status_code == 400
