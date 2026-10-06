"""The payback horizon and overclock-last: priced per save, stored per plan, read out per stop.

docs/planner-payback-horizon_contract.md is what these pin.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain import settings
from satisfactory_mcp.domain.planning.readout import payback
from satisfactory_mcp.domain.planning.solver import prices
from satisfactory_mcp.domain.planning.solver.model import PAYBACK_STOPS, Scenario
from satisfactory_mcp.domain.planning.solver.optimize import solve
from satisfactory_mcp.domain.planning.solver.overclock import best_clock
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario
from satisfactory_mcp.domain.planning.stored.planlog import (
    Actor,
    InvalidOp,
    Outdated,
    PlanArgs,
    PlanLog,
    describe_op,
    inverse,
    legacy_hours,
)
from satisfactory_mcp.domain.planning.stored.recall import PLAN_DEFAULTS, overrides_of
from tests.support.reference_world import FIVE_RIP_ARGS, FIXTURE_WORLD
from tests.support.web import PAGE_ORIGIN, create_plan, push_ops, set_op

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)
PLASTIC = "Desc_Plastic_C"
FUEL = "Desc_LiquidFuel_C"
REFINERY = "Build_OilRefinery_C"


@pytest.fixture(scope="module")
def priced(game, projection):
    from satisfactory_mcp.domain.world.state import WorldState

    return prices.prices_for(WorldState(projection=projection, game=game), False)


def _plastic(game, hours=0.0, price=0.0, points=None, **extra):
    return solve(
        Scenario(
            game=game,
            recipes=["Recipe_Plastic_C", "Recipe_ResidualFuel_C"],
            objective="max_item",
            target_item=PLASTIC,
            raw_caps={"Desc_LiquidOil_C": 300.0, "Desc_Water_C": 1e6},
            exports=(PLASTIC, FUEL),
            grid_import_mw=1e5,
            payback_hours=hours,
            power_price=price,
            build_points=points or {},
            **extra,
        )
    )


def _draw(sol) -> float:
    return sum(-p["mw"] for p in sol.processes if p["mw"] < 0)


# ------------------------------------------------------------------ prices


def test_the_grid_mix_is_the_mw_weighted_running_price(priced):
    assert priced.price == pytest.approx((2550 * 36 + 5000 * 360) / 7550, abs=0.1)
    assert [(m["source"], m["mw"], m["price"]) for m in priced.mix] == [
        ("Fuel", 5000.0, 360.0),
        ("Coal", 2550.0, 36.0),
    ]


@pytest.mark.parametrize(
    ("generator", "fuel", "points"),
    [
        ("Build_GeneratorCoal_C", "Desc_Coal_C", 36.0),
        ("Build_GeneratorFuel_C", "Desc_LiquidFuel_C", 360.0),
        ("Build_GeneratorFuel_C", "Desc_LiquidTurboFuel_C", 405.0),
        ("Build_GeneratorNuclear_C", "Desc_NuclearFuelRod_C", 208.6),
    ],
)
def test_each_fuel_is_priced_at_its_own_sink_points(game, generator, fuel, points):
    assert prices.fuel_price(game, generator, fuel) == pytest.approx(points, abs=0.1)


def test_geothermal_runs_free_and_biomass_follows_its_setting(game):
    geo = next(c for c, b in game.buildings.items() if b.variable_power_factor)
    burner = "Build_GeneratorBiomass_Automated_C"
    projection = {
        "generators": [
            {"cls": geo, "instance": "L.geo_1"},
            {"cls": burner, "instance": "L.burner_1", "fuel": "Desc_Biofuel_C"},
        ]
    }
    price, mix = prices.grid_mix(game, projection, None, biomass=False)
    assert price == 0.0 and [m["source"] for m in mix] == ["Geothermal"]
    price, mix = prices.grid_mix(game, projection, None, biomass=True)
    assert {m["source"] for m in mix} == {"Geothermal", "Solid Biofuel"} and price > 0


def test_a_machine_is_priced_by_its_save_priced_materials_and_its_floor(priced, game):
    assert round(priced.points[REFINERY]) == 9064
    assert round(priced.points["Build_Blender_C"]) == 146162
    smelter = game.buildings["Build_SmelterMk1_C"]
    assert priced.points["Build_SmelterMk1_C"] >= smelter.footprint.area_m2 * 15.6


def test_scarcity_tiers_snap_to_factors_of_two_and_hold_inside_the_band(game):
    first = game.buildings["Build_ConstructorMk1_C"].build_cost[0]
    line = ("Build_ConstructorMk1_C", first.item)
    tiers = prices.material_tiers(game, {}, {}, world="")
    assert set(tiers.values()) <= set(prices.TIERS) and tiers[line] == 4.0
    plenty = {first.item: 20 * first.amount * 16}
    assert prices.material_tiers(game, plenty, {}, world="w")[line] == 0.25
    near = {first.item: 20 * first.amount * 6}
    assert prices.material_tiers(game, near, {}, world="w")[line] == 0.25
    assert prices.material_tiers(game, near, {}, world="")[line] == 0.5
    made = {first.item: 20 * first.amount * 16 / 60}
    assert prices.material_tiers(game, {}, made, world="")[line] == 0.25


# ------------------------------------------------------------------ the solve


def test_zero_hours_is_todays_build(game, priced):
    plain = _plastic(game)
    zero = _plastic(game, 0.0, priced.price, priced.points)
    assert zero.processes == plain.processes and zero.exports == plain.exports
    assert zero.machines_total == 12 and _draw(zero) == pytest.approx(347.1, abs=0.1)
    assert [p["clock"] for p in zero.processes if p["label"] == "Plastic"] == [1.0]


def test_machine_counts_never_fall_as_the_horizon_rises(game, priced):
    counts = [_plastic(game, h, priced.price, priced.points).machines_total for h in PAYBACK_STOPS]
    draws = [_draw(_plastic(game, h, priced.price, priced.points)) for h in PAYBACK_STOPS]
    assert counts == sorted(counts) and counts[0] == 12 and counts[-1] > 100
    assert draws == sorted(draws, reverse=True)


def test_the_flows_never_move_only_machines_and_draw(game, priced):
    plain, spread = _plastic(game), _plastic(game, 10.0, priced.price, priced.points)
    assert plain.exports == spread.exports and plain.raw_used == spread.raw_used
    sc = Scenario(
        game=game,
        recipes=[],
        payback_hours=10.0,
        power_price=priced.price,
        build_points=priced.points,
    )
    best = best_clock(sc, game.buildings[REFINERY], 30.0)
    assert 0.4 < best < 0.55
    for p in spread.processes:
        assert p["clock"] <= best + 1e-9


def test_every_stop_is_read_out_from_one_solve(game, priced):
    for hours in (0.0, 10.0):
        sol = _plastic(game, hours, priced.price, priced.points)
        here = next(r for r in sol.payback_curve if r["hours"] == hours)
        assert here["machines"] == sol.machines_total
        assert here["draw_mw"] == pytest.approx(_draw(sol), abs=0.05)
        assert [r["hours"] for r in sol.payback_curve] == [*PAYBACK_STOPS, 0.0]
        assert sol.payback_curve[-1]["plain"]


def test_the_view_prices_the_extra_machines_and_says_what_next(game, priced):
    sol = _plastic(game, 5.0, priced.price, priced.points)
    sc = Scenario(
        game=game,
        recipes=[],
        payback_hours=5.0,
        power_price=priced.price,
        build_points=priced.points,
    )
    view = payback.view(
        game,
        sol,
        sc,
        round(sol.machines_total),
        _draw(sol),
        inherited=False,
        default_hours=0.0,
        price_source="grid mix",
        mix=priced.mix,
        overclock_inherited=True,
        shards={"free": 19, "craftable": 411},
    )
    zero, now = view["stops"][0], next(s for s in view["stops"] if s["hours"] == 5.0)
    assert view["splits"] and zero["extra_machines"] == 0 and zero["cost"] == []
    assert now["extra_machines"] > 0 and now["saved_mw"] > 0
    assert now["average_payback_h"] < 5.0
    motors = next(c for c in now["cost"] if c["item"] == "Motor")
    per = next(f.amount for f in game.buildings[REFINERY].build_cost if f.item == "Desc_Motor_C")
    assert motors["amount"] == now["extra_machines"] * per
    lines = payback.trade_text(view)
    assert lines[0].startswith("payback 5 h at grid mix 251 pts/MWh")
    assert "the last pays back within 5 h" in lines[0] and "; 10 h would change +" in lines[0]


def _view(game, priced, sol, hours, overclock=False):
    sc = Scenario(
        game=game,
        recipes=[],
        payback_hours=hours,
        power_price=priced.price,
        build_points=priced.points,
        overclock_last=overclock,
    )
    return payback.view(
        game,
        sol,
        sc,
        round(sol.machines_total),
        _draw(sol),
        inherited=True,
        default_hours=0.0,
        price_source="grid mix",
        mix=priced.mix,
        overclock_inherited=False,
        shards=None,
    )


def test_every_stop_compares_with_the_plain_build(game, priced):
    off = _view(game, priced, _plastic(game, 0.0, priced.price, priced.points), 0.0)
    on = _view(
        game,
        priced,
        _plastic(game, 0.0, priced.price, priced.points, overclock_last=True),
        0.0,
        overclock=True,
    )
    assert off["stops"][0]["extra_machines"] == 0 and on["stops"][0]["extra_machines"] == -1
    assert on["stops"][-1]["extra_machines"] == off["stops"][-1]["extra_machines"]
    assert payback.trade_text(off)[0].startswith("payback 0 h (default) at grid mix")


def test_required_and_banned_hold_at_every_horizon(game, state, monkeypatch):
    required, banned = "Alternate: Caterium Computer", "Alternate: Crystal Computer"
    for hours in (0.0, 100.0):
        req = build_scenario(
            game,
            state,
            objective="min_power",
            exports=["Computer"],
            export_minimums={"Computer": 10},
            required=[required],
            exclude_recipes=[banned],
            payback_hours=hours,
        )
        sol = solve(req.scenario)
        used = {game.recipes[p["recipe"]].name for p in sol.processes if p.get("recipe")}
        assert sol.ok and required in used and banned not in used, (hours, used)
        assert "Computer" not in used


# ------------------------------------------------------------------ overclock last


def test_overclock_last_carries_the_fraction_on_one_machine(game, priced):
    sol = _plastic(game, 0.0, priced.price, priced.points, overclock_last=True)
    rows = {p["label"]: p for p in sol.processes}
    assert rows["Plastic"]["machines"] == 10 and "last_clock" not in rows["Plastic"]
    fuel = rows["Residual Fuel"]
    assert fuel["machines"] == 1 and fuel["last_clock"] == pytest.approx(5 / 3, abs=1e-4)
    assert sol.overclock["shards"] == 2 and sol.overclock["machines_saved"] == 1
    assert fuel["mw"] == pytest.approx(-58.94, abs=0.05)


@pytest.mark.parametrize("units", [1.2, 1.5, 1.999, 4.51])
def test_the_last_machine_never_needs_more_than_two_shards(game, units):
    from satisfactory_mcp.domain.planning.solver.model import Process
    from satisfactory_mcp.domain.planning.solver.overclock import _Row

    proc = Process("r:x", "recipe", "x", {}, -30.0, -30.0, 1.321929, REFINERY, "x")
    machines, top, shards = _Row(proc, units, game.buildings[REFINERY]).last(0.5)
    assert machines == int(units) and top < 2.0 and shards <= 2
    assert top == pytest.approx(1 + units - int(units))


def test_overclock_last_spends_only_the_shards_in_hand(game, priced):
    kept = _plastic(game, 0.0, priced.price, priced.points, overclock_last=True, overclock_shards=1)
    assert all("last_clock" not in p for p in kept.processes)
    assert kept.overclock["without"] and kept.machines_total == 12


def test_overclock_last_is_weighed_against_the_horizon(game, priced):
    long = _plastic(game, 100.0, priced.price, priced.points, overclock_last=True)
    assert all("last_clock" not in p for p in long.processes)
    assert long.overclock["rows"] == []


def test_overclock_last_off_still_says_what_it_would_save(game, priced):
    sol = _plastic(game, 0.0, priced.price, priced.points)
    assert all("last_clock" not in p for p in sol.processes)
    assert not sol.overclock["on"] and sol.overclock["machines_saved"] == 1


def test_the_bill_counts_shards_on_the_last_machine_only(game, priced):
    from types import SimpleNamespace

    from satisfactory_mcp.domain.planning.readout.slice import slice_of

    sol = _plastic(game, 0.0, priced.price, priced.points, overclock_last=True)
    bill = slice_of(
        SimpleNamespace(
            solution=sol, request=SimpleNamespace(scenario=SimpleNamespace(belt_ipm=780.0))
        ),
        game,
    )
    assert bill.shards == 2 and bill.shard_rows[0].machines == 1


# ------------------------------------------------------------------ the plan log


@pytest.fixture
def plans():
    return PlanLog("W")


@pytest.fixture
def plan(plans):
    return plans.create("oil", {"objective": "min_machines"}, actor=CHAT).key


def test_a_new_plan_inherits_and_inheriting_is_not_a_kwarg(plans, plan):
    state = plans.state(plan)
    assert state.args.payback_hours is None and state.args.overclock_last is None
    assert not {"payback_hours", "overclock_last", "power_price"} & set(state.kwargs())


def test_the_horizon_is_a_solve_argument(plans, plan):
    pushed = plans.push(plan, 1, [set_op("payback_hours", 10)], actor=PAGE)
    assert pushed.state.kwargs()["payback_hours"] == 10.0
    assert pushed.applied[0] == {**set_op("payback_hours", 10.0), "was": None}
    back = plans.push(plan, 2, [set_op("payback_hours", "default")], actor=PAGE)
    assert back.state.args.payback_hours is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payback_hours", -1),
        ("payback_hours", 101),
        ("payback_hours", "ten"),
        ("payback_hours", True),
        ("payback_hours", float("nan")),
        ("overclock_last", 1),
        ("power_price", -5),
    ],
)
def test_only_valid_values_are_stored(plans, plan, field, value):
    with pytest.raises(InvalidOp, match=field):
        plans.push(plan, 1, [set_op(field, value)], actor=PAGE)
    assert plans.head_rev(plan) == 1


def test_the_horizon_conflicts_only_with_itself(plans, plan):
    plans.push(plan, 1, [set_op("payback_hours", 20)], actor=CHAT)
    with pytest.raises(Outdated) as caught:
        plans.push(plan, 1, [set_op("payback_hours", 5)], actor=PAGE)
    assert caught.value.conflicts[0].key == "payback_hours"
    assert plans.push(plan, 1, [set_op("payback_hours", 20)], actor=PAGE).noop
    merged = plans.push(plan, 1, [set_op("overclock_last", True)], actor=PAGE)
    assert merged.state.args.payback_hours == 20 and merged.state.args.overclock_last is True


def test_the_horizon_undoes_to_its_previous_value(plans, plan):
    plans.push(plan, 1, [set_op("payback_hours", 5)], actor=PAGE)
    plans.push(plan, 2, [set_op("payback_hours", 50)], actor=PAGE)
    assert inverse(plans.commits(plan)[2].ops) == [set_op("payback_hours", 5.0)]
    assert plans.undo(plan, 3, 3, actor=PAGE).state.args.payback_hours == 5.0


def test_the_settings_in_words():
    assert describe_op({**set_op("payback_hours", 10.0), "was": None}) == "payback 10 h"
    assert describe_op({**set_op("payback_hours", None), "was": 5.0}) == "payback: shared default"
    assert describe_op(set_op("overclock_last", True)) == "overclock last machine: on"
    assert describe_op(set_op("power_price", None)) == "power price: grid mix"


def test_recalling_with_zero_or_default_overrides():
    assert PLAN_DEFAULTS["payback_hours"] is None
    assert overrides_of({"payback_hours": 0}) == {"payback_hours": 0}
    assert overrides_of({"payback_hours": "default"}) == {"payback_hours": "default"}
    assert overrides_of({"payback_hours": None}) == {}


# ------------------------------------------------------------------ migration


@pytest.mark.parametrize(("step", "hours"), [(0, None), (1, 4.0), (2, 7.0), (3, 11.0), (4, 17.0)])
def test_a_stored_priority_step_reads_as_its_horizon(step, hours):
    assert legacy_hours(step) == hours
    assert PlanArgs.from_dict({"power_priority": step}).payback_hours == hours


def test_old_priority_ops_replay_and_undo_as_horizons(plans, plan):
    ops = plans._ops(plan)
    lines = ops.read_text(encoding="utf-8")
    old = {
        "rev": 2,
        "base_rev": 1,
        "ts": 1.0,
        "actor": PAGE.to_dict(),
        "sav": "",
        "ops": [{"op": "set", "field": "power_priority", "value": 2, "was": 0}],
        "merged_over": [],
        "undoes": None,
        "note": "",
    }
    ops.write_text(lines + json.dumps(old) + "\n", encoding="utf-8")
    assert plans.state(plan).args.payback_hours == 7.0
    assert plans.commits(plan)[1].text() == "v2 page: payback 7 h"
    assert plans.undo(plan, 2, 2, actor=PAGE).state.args.payback_hours is None
    assert '"power_priority"' in ops.read_text(encoding="utf-8").splitlines()[1]


def test_an_old_snapshot_with_a_step_reads_as_its_horizon():
    from satisfactory_mcp.domain.planning.stored.planlog import PlanState

    state = PlanState.from_dict({"name": "x", "args": {"power_priority": 3}}, key="k", rev=7)
    assert state.args.payback_hours == 11.0


# ------------------------------------------------------------------ the shared default


def test_a_plan_that_inherits_follows_the_shared_default(game, state):
    plain = build_scenario(game, state, exports=["Computer"], export_minimums={"Computer": 10})
    settings.write({"payback_hours": 10.0, "overclock_last": True}, PAGE)
    moved = build_scenario(game, state, exports=["Computer"], export_minimums={"Computer": 10})
    pinned = build_scenario(
        game, state, exports=["Computer"], export_minimums={"Computer": 10}, payback_hours=0
    )
    assert plain.scenario.payback_hours == 0 and moved.scenario.payback_hours == 10.0
    assert moved.payback["inherited"] and not pinned.payback["inherited"]
    assert moved.scenario.overclock_last and moved.scenario.overclock_shards == 430
    assert pinned.scenario.payback_hours == 0 and plain.plan_id != moved.plan_id


def test_zero_hours_keeps_every_plan_id(game, state):
    a = build_scenario(game, state, exports=["Computer"], export_minimums={"Computer": 10})
    b = build_scenario(
        game,
        state,
        exports=["Computer"],
        export_minimums={"Computer": 10},
        payback_hours=0,
        power_price=999,
    )
    assert a.plan_id == b.plan_id


# ------------------------------------------------------------------ chat and page

PLASTIC20 = {"objective": "min_power", "exports": ["Plastic"], "export_minimums": {"Plastic": 200}}


@pytest.fixture
def fresh_state_factory(monkeypatch, projection, game):
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import planning

    def fresh(*_a, **_k):
        return WorldState(projection=projection, game=game)

    monkeypatch.setattr(planning, "_state", fresh)
    return fresh


def test_chat_sets_reads_and_resets_the_horizon(fresh_state_factory):
    from satisfactory_mcp import server as srv

    made = srv.plan_factory(save_as="oil", payback_hours=10, limit=2, **PLASTIC20)
    assert 'saved as "oil" v1' in made and "payback 10 h at grid mix 251 pts/MWh" in made, made
    assert "the last pays back within 10 h" in made and "20 h would change" in made
    log = PlanLog(FIXTURE_WORLD)
    key = log.find("oil").key
    assert log.state(key).args.payback_hours == 10.0
    assert "payback 10 h" in srv.plan_factory(plan="oil", limit=2)
    reset = srv.plan_factory(
        plan="oil", save_as="oil", base_rev=1, payback_hours="default", limit=2
    )
    assert "is now v2" in reset, reset
    assert log.state(key).args.payback_hours is None
    assert srv.plan_factory(payback_hours=500, limit=2, **FIVE_RIP_ARGS).startswith(
        "! payback_hours must be 0 to 100 hours"
    )


def test_chat_overclock_and_price_override(fresh_state_factory):
    from satisfactory_mcp import server as srv

    told = srv.plan_factory(overclock_last=True, limit=5, **PLASTIC20)
    assert "overclock last machine:" in told and "(19 in hand + 411 craftable)" in told
    assert "last " in told
    priced = srv.plan_factory(payback_hours=10, power_price=36, limit=2, **PLASTIC20)
    assert "36 pts/MWh set on the plan" in priced
    laid = srv.plan_layout(payback_hours=10, limit=2, **PLASTIC20)
    assert "payback 10 h" in laid


def _solve(client, body: dict):
    return client.post("/api/plan/solve", json=body, headers=PAGE_ORIGIN)


def test_the_page_reads_the_stops_and_pushes_the_horizon(fresh_state_client):
    created = create_plan(fresh_state_client, "oil", PLASTIC20)
    key = created["key"]
    assert created["state"]["args"]["payback_hours"] is None
    plain = _solve(fresh_state_client, {"key": key}).json()
    power = plain["power"]
    assert power["hours"] == 0 and power["inherited"] and power["price_source"] == "grid mix"
    assert [s["hours"] for s in power["stops"]] == list(PAYBACK_STOPS)
    assert power["stops"][0]["machines"] == plain["machines"]
    assert power["overclock"]["shards_free"] == 19
    pushed = push_ops(fresh_state_client, key, 1, set_op("payback_hours", 10))
    assert pushed.json()["state"]["args"]["payback_hours"] == 10
    spread = _solve(fresh_state_client, {"key": key}).json()
    ten = next(s for s in power["stops"] if s["hours"] == 10)
    assert spread["power"]["hours"] == 10 and not spread["power"]["inherited"]
    assert ten["extra_machines"] > 0 and ten["saved_mw"] > 0
    assert spread["machines"] > plain["machines"] and spread["mw_draw"] < plain["mw_draw"]
    assert spread["plan_id"] != plain["plan_id"]
    assert _solve(fresh_state_client, {"args": {"payback_hours": 900}}).status_code == 400


def test_the_shared_default_reaches_the_page(fresh_state_client):
    key = create_plan(fresh_state_client, "oil", PLASTIC20)["key"]
    moved = fresh_state_client.patch(
        "/api/settings", json={"values": {"payback_hours": 20}}, headers=PAGE_ORIGIN
    )
    assert moved.status_code == 200, moved.text
    solved = _solve(fresh_state_client, {"key": key}).json()
    assert solved["power"]["hours"] == 20 and solved["power"]["inherited"]
    assert solved["power"]["default_hours"] == 20
