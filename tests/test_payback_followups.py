"""The payback follow-ups F1a-F6a: docs/planner-payback-horizon_contract.md §10."""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import payback, prices
from satisfactory_mcp.domain.planning.optimize import (
    PAYBACK_STOPS,
    POWER_GOAL_BUILD_COST_FROM_H,
    Scenario,
    machine_mw,
    solve,
)
from satisfactory_mcp.domain.planning.planlog import (
    PAYBACK_MAX_H,
    Actor,
    InvalidOp,
    Outdated,
    PlanLog,
    describe_op,
    inverse,
    use_recipe_names,
)
from satisfactory_mcp.domain.planning.prices import tiers_path as real_tiers_path
from satisfactory_mcp.domain.planning.scenario import build_scenario, shard_stock
from satisfactory_mcp.domain.world.state import WorldState

CONSTRUCTOR = "Build_ConstructorMk1_C"
REFINERY = "Build_OilRefinery_C"


# ------------------------------------------------------------------ F2a: shared tier memory


@pytest.fixture
def tier_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    monkeypatch.setattr(prices, "tiers_path", real_tiers_path)
    return real_tiers_path("W")


def test_the_tier_memory_lives_beside_the_plan_log(tier_file):
    assert tier_file.parent == PlanLog.dir_for("W")
    assert tier_file.name == "tiers.json"


def test_a_second_process_keeps_the_tier_the_first_one_stored(game, tier_file):
    first = game.buildings[CONSTRUCTOR].build_cost[0]
    line = (CONSTRUCTOR, first.item)
    plenty = {first.item: 20 * first.amount * 16}
    near = {first.item: 20 * first.amount * 6}
    assert prices.material_tiers(game, plenty, {}, world="W")[line] == 0.25
    stored = json.loads(tier_file.read_text(encoding="utf-8"))
    assert stored["schema"] == prices.TIER_SCHEMA
    assert stored["tiers"][f"{CONSTRUCTOR}|{first.item}"] == 0.25
    # Nothing in process memory: the band is read back from the file, as another process
    # reading the same world would.
    assert prices.material_tiers(game, near, {}, world="W")[line] == 0.25
    assert prices.material_tiers(game, near, {}, world="")[line] == 0.5


def test_an_unreadable_tier_file_starts_afresh(game, tier_file):
    first = game.buildings[CONSTRUCTOR].build_cost[0]
    tier_file.parent.mkdir(parents=True)
    tier_file.write_text("{not json", encoding="utf-8")
    tiers = prices.material_tiers(game, {}, {}, world="W")
    assert tiers[(CONSTRUCTOR, first.item)] == 4.0
    assert json.loads(tier_file.read_text(encoding="utf-8"))["schema"] == prices.TIER_SCHEMA


def test_a_tier_file_from_a_newer_version_is_not_overwritten(game, tier_file):
    tier_file.parent.mkdir(parents=True)
    newer = json.dumps({"schema": prices.TIER_SCHEMA + 1, "tiers": {}})
    tier_file.write_text(newer, encoding="utf-8")
    prices.material_tiers(game, {}, {}, world="W")
    assert tier_file.read_text(encoding="utf-8") == newer


# ------------------------------------------------------------------ F1a: power goals priced by the horizon

SPIRE = {
    "objective": "max_mw",
    "sources": ["region:Spire Coast"],
    "exports": ["MW", "Plastic", "Rubber"],
    "export_minimums": {"Plastic": 600.0, "Rubber": 250.0},
    "extractor_clocks": [1.0, 1.5, 2.0, 2.5],
    "exclude_recipes": ["Turbofuel", "Alternate: Compacted Coal", "Coal-Powered Generator"],
}


def test_a_machine_costs_its_points_over_the_horizon_in_mw(game):
    sc = Scenario(game=game, recipes=[], build_points={REFINERY: 9000.0}, power_price=250.0)
    assert machine_mw(sc, REFINERY) == 5.0
    sc.payback_hours = 2.0
    assert machine_mw(sc, REFINERY) == 5.0
    sc.payback_hours = 10.0
    assert machine_mw(sc, REFINERY) == pytest.approx(9000 / 2500)
    assert machine_mw(sc, "Build_Unknown_C") == 5.0
    sc.power_price = 0.0
    assert machine_mw(sc, REFINERY) == 5.0


def test_zero_hours_keeps_the_flat_machine_price(game, state):
    flat = solve(build_scenario(game, state, **SPIRE).scenario)
    priced = solve(build_scenario(game, state, payback_hours=0, power_price=999, **SPIRE).scenario)
    assert flat.processes == priced.processes and flat.net_mw == pytest.approx(28252.5, abs=0.1)
    assert round(flat.machines_total) == 286


def test_a_horizon_prices_the_power_goal_in_build_points(game, state):
    def used(hours):
        sol = solve(build_scenario(game, state, payback_hours=hours, **SPIRE).scenario)
        return sol, {p["label"] for p in sol.processes}

    _plain, before = used(0.0)
    priced, after = used(POWER_GOAL_BUILD_COST_FROM_H)
    # The 32 Blenders cost 146,162 points each (Heavy Modular Frames are scarce here): at 5 h
    # that is ~117 MW a machine, so the packaged route through Refineries replaces them.
    assert "Alternate: Diluted Fuel" in before and "Alternate: Diluted Fuel" not in after
    assert "Alternate: Diluted Packaged Fuel" in after
    assert priced.net_mw == pytest.approx(28447.7, abs=0.1)
    assert round(priced.machines_total) == 545


@pytest.mark.parametrize("hours", [1.0, 2.0])
def test_short_horizons_keep_the_power_goal_and_the_recipes(game, state, hours):
    """Ruling 3b: below 5 h a dismantle refunds the materials, so a power plan does not give
    up MW for cheaper machines; only the clock spread of the horizon applies."""
    plain = solve(build_scenario(game, state, **SPIRE).scenario)
    short = solve(build_scenario(game, state, payback_hours=hours, **SPIRE).scenario)
    assert short.net_mw == pytest.approx(plain.net_mw, abs=0.1)
    assert {p["label"] for p in short.processes} == {p["label"] for p in plain.processes}
    assert round(short.machines_total) == 289


# ------------------------------------------------------------------ F3b: craftable shards count


def test_craftable_shards_count_toward_overclock_last(game, state):
    assert shard_stock(state) == {"free": 19.0, "craftable": 411.0}
    req = build_scenario(game, state, overclock_last=True, exports=["Plastic"])
    assert req.scenario.overclock_shards == 430.0


def test_only_slugs_with_an_unlocked_shard_recipe_are_craftable(game, projection, monkeypatch):
    st = WorldState(projection=projection, game=game)
    every = st.unlocked_recipes("part")
    purple = [r for r in every if r.cls != "Recipe_PowerCrystalShard_3_C"]
    monkeypatch.setattr(st, "unlocked_recipes", lambda kind="part": purple)
    held = {s["item"]: s["held"] for s in st.shard_budget()["slugs"]}
    assert shard_stock(st)["craftable"] == 411.0 - 5 * held["Desc_Crystal_mk3_C"]


def test_the_shard_line_shows_hand_plus_craftable():
    oc = {**payback.no_overclock(True), "shards_free": 19.0, "shards_craftable": 411.0}
    oc["rows"] = [{"label": "Residual Fuel"}]
    oc["shards"], oc["machines_saved"], oc["extra_mw"] = 2, 1, 11.8
    line = payback._overclock_words(oc)[0]
    assert line == (
        "overclock last machine: 1 row(s), 2 shard(s) (19 in hand + 411 craftable): "
        "−1 machines, +11.8 MW"
    )


# ------------------------------------------------------------------ F3 extra: per-row choice

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)
FUEL_ROW = "Recipe_ResidualFuel_C"
PLASTIC_ROW = "Recipe_Plastic_C"
PLASTIC20 = {"objective": "min_power", "exports": ["Plastic"], "export_minimums": {"Plastic": 200}}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture(scope="module")
def priced(game, projection):
    return prices.prices_for(WorldState(projection=projection, game=game), False)


def _plastic(game, priced, hours=0.0, **extra):
    return solve(
        Scenario(
            game=game,
            recipes=[PLASTIC_ROW, FUEL_ROW],
            objective="max_item",
            target_item="Desc_Plastic_C",
            raw_caps={"Desc_LiquidOil_C": 300.0, "Desc_Water_C": 1e6},
            exports=("Desc_Plastic_C", "Desc_LiquidFuel_C"),
            grid_import_mw=1e5,
            payback_hours=hours,
            power_price=priced.price,
            build_points=priced.points,
            **extra,
        )
    )


def _fuel(sol):
    return next(p for p in sol.processes if p["recipe"] == FUEL_ROW)


def test_a_row_set_to_last_overclocks_with_the_switch_off(game, priced):
    sol = _plastic(game, priced, row_overclock={FUEL_ROW: "last"})
    fuel = _fuel(sol)
    assert fuel["machines"] == 1 and fuel["last_clock"] == pytest.approx(5 / 3, abs=1e-4)
    assert fuel["overclock_option"]["pinned"] == "last" and fuel["overclock_option"]["applied"]
    assert sol.overclock["pinned_last"] == 1 and not sol.overclock["on"]


def test_a_row_set_to_last_wins_even_past_its_break_even(game, priced):
    sol = _plastic(game, priced, 100.0, row_overclock={FUEL_ROW: "last"})
    assert _fuel(sol)["machines"] == 1 and "last_clock" in _fuel(sol)


def test_a_row_set_to_spread_never_overclocks(game, priced):
    sol = _plastic(game, priced, overclock_last=True, row_overclock={FUEL_ROW: "spread"})
    fuel = _fuel(sol)
    assert fuel["machines"] == 2 and "last_clock" not in fuel
    option = fuel["overclock_option"]
    assert option["pinned"] == "spread" and not option["applied"]
    assert option["machines"] == 1 and option["spread_machines"] == 2 and option["shards"] == 2
    assert sol.overclock["rows"] == [] and sol.overclock["pinned_spread"] == 1


def test_a_forced_row_still_needs_its_shards(game, priced):
    sol = _plastic(game, priced, overclock_shards=1, row_overclock={FUEL_ROW: "last"})
    assert "last_clock" not in _fuel(sol)
    assert _fuel(sol)["overclock_option"]["without"]


def test_the_plain_build_never_overclocks_a_row(game, priced):
    sol = _plastic(game, priced, row_overclock={FUEL_ROW: "last"})
    plain = sol.payback_curve[-1]
    assert plain["plain"] and plain["shards"] == 0 and plain["machines"] == 12
    assert sol.payback_curve[0]["machines"] == 11


@pytest.fixture
def plans(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    return PlanLog("W")


@pytest.fixture
def plan(plans):
    return plans.create("oil", {"objective": "min_power"}, actor=CHAT).key


def _put(row, choice):
    return {"op": "put", "field": "row_overclock", "item": row, "value": choice}


def _set(field, value):
    return {"op": "set", "field": field, "value": value}


def test_a_row_choice_is_a_plan_op_with_undo(plans, plan):
    pushed = plans.push(plan, 1, [_put(FUEL_ROW, "last")], actor=PAGE)
    assert pushed.state.kwargs()["row_overclock"] == {FUEL_ROW: "last"}
    plans.push(plan, 2, [_put(FUEL_ROW, "spread")], actor=PAGE)
    assert inverse(plans.commits(plan)[2].ops) == [_put(FUEL_ROW, "last")]
    drop = {"op": "del", "field": "row_overclock", "item": FUEL_ROW}
    gone = plans.push(plan, 3, [drop], actor=PAGE)
    assert "row_overclock" not in gone.state.kwargs()
    assert plans.undo(plan, 4, 4, actor=PAGE).state.args.row_overclock == {FUEL_ROW: "spread"}


@pytest.mark.parametrize("value", ["on", 1, True, None])
def test_only_last_or_spread_is_stored(plans, plan, value):
    with pytest.raises(InvalidOp, match="row_overclock"):
        plans.push(plan, 1, [_put(FUEL_ROW, value)], actor=PAGE)


def test_row_choices_merge_per_row(plans, plan):
    plans.push(plan, 1, [_put(FUEL_ROW, "last")], actor=CHAT)
    with pytest.raises(Outdated) as caught:
        plans.push(plan, 1, [_put(FUEL_ROW, "spread")], actor=PAGE)
    assert caught.value.conflicts[0].key == f"row_overclock[{FUEL_ROW}]"
    assert caught.value.conflicts[0].text().startswith("overclock on ")
    assert plans.push(plan, 1, [_put(FUEL_ROW, "last")], actor=PAGE).noop
    merged = plans.push(plan, 1, [_put(PLASTIC_ROW, "spread")], actor=PAGE)
    assert merged.state.args.row_overclock == {FUEL_ROW: "last", PLASTIC_ROW: "spread"}
    both = plans.push(plan, 3, [_set("overclock_last", True)], actor=CHAT)
    assert both.state.args.row_overclock[FUEL_ROW] == "last"


def test_a_row_choice_in_words():
    use_recipe_names(lambda: {FUEL_ROW: "Residual Fuel"})
    try:
        assert describe_op(_put(FUEL_ROW, "last")) == "Residual Fuel: overclock last"
        spread = describe_op(_put(FUEL_ROW, "spread"))
        assert spread == "Residual Fuel: one more underclocked machine"
        dropped = {"op": "del", "field": "row_overclock", "item": FUEL_ROW, "was": "last"}
        assert describe_op(dropped) == "Residual Fuel: follows the plan's overclock setting"
    finally:
        use_recipe_names(None)


@pytest.fixture
def world(tmp_path, monkeypatch, projection, game):
    from satisfactory_mcp.interfaces.mcp.tools import planning

    for name in ("plans_dir", "activity_dir", "pins_dir", "labels_dir", "ui_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)

    def fresh(*_a, **_k):
        return WorldState(projection=projection, game=game)

    monkeypatch.setattr(planning, "_state", fresh)
    return fresh


def test_chat_sets_a_row_choice_and_keeps_the_others(world):
    from satisfactory_mcp import server as srv

    made = srv.plan_factory(
        save_as="oil", row_overclock={"Residual Fuel": "last"}, limit=5, **PLASTIC20
    )
    assert 'saved as "oil" v1' in made, made
    assert "overclock last set on its row: Residual Fuel" in made, made
    log = PlanLog(WORLD)
    key = log.find("oil").key
    assert log.state(key).args.row_overclock == {FUEL_ROW: "last"}
    more = srv.plan_factory(
        plan="oil", save_as="oil", base_rev=1, row_overclock={"Plastic": "spread"}, limit=2
    )
    assert "is now v2" in more, more
    assert log.state(key).args.row_overclock == {FUEL_ROW: "last", PLASTIC_ROW: "spread"}
    back = srv.plan_factory(
        plan="oil", save_as="oil", base_rev=2, row_overclock={"Residual Fuel": "default"}, limit=2
    )
    assert "is now v3" in back, back
    assert log.state(key).args.row_overclock == {PLASTIC_ROW: "spread"}
    refused = srv.plan_factory(row_overclock={"Nothing Like It": "last"}, limit=2, **PLASTIC20)
    assert refused.startswith("! row_overclock: no recipe is called"), refused
    laid = srv.plan_layout(plan="oil", row_overclock={"Residual Fuel": "last"}, limit=2)
    assert "overridden this call: row_overclock" in laid, laid


def test_the_page_pushes_a_row_choice(world, projection, game):
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as client:
        created = client.post("/api/plans", json={"name": "oil", "args": PLASTIC20}, headers=ORIGIN)
        key = created.json()["key"]
        before = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
        row = next(r for r in before["rows"] if r["recipe_id"] == FUEL_ROW)
        assert row["overclock_option"]["applied"] is False and row["last_clock"] is None
        pushed = client.post(
            f"/api/plans/{key}/ops",
            json={"base_rev": 1, "ops": [_put(FUEL_ROW, "last")]},
            headers=ORIGIN,
        )
        assert pushed.status_code == 200, pushed.text
        assert pushed.json()["state"]["args"]["row_overclock"] == {FUEL_ROW: "last"}
        assert pushed.json()["state"]["names"][FUEL_ROW] == "Residual Fuel"
        after = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
        row = next(r for r in after["rows"] if r["recipe_id"] == FUEL_ROW)
        assert row["overclock_option"]["applied"] and row["overclock_option"]["pinned"] == "last"
        assert row["last_clock"] > 1 and after["plan_id"] != before["plan_id"]


# ------------------------------------------------------------------ F5a, F6a


def test_a_release_that_switches_recipes_shows_in_the_two_solves(world, projection, game):
    """The page names the switch from the build list before and after the release (F5a)."""
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    spire = {**SPIRE, "banned": SPIRE["exclude_recipes"]}
    del spire["exclude_recipes"]
    with TestClient(app) as client:
        created = client.post("/api/plans", json={"name": "spire", "args": spire}, headers=ORIGIN)
        key = created.json()["key"]
        before = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
        client.post(
            f"/api/plans/{key}/ops",
            json={"base_rev": 1, "ops": [_set("payback_hours", 5)]},
            headers=ORIGIN,
        )
        after = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
    was = {r["recipe"] for r in before["rows"]}
    now = {r["recipe"] for r in after["rows"]}
    assert now - was == {"Alternate: Diluted Packaged Fuel", "Packaged Water", "Unpackage Fuel"}
    assert was - now == {"Alternate: Diluted Fuel"}


def test_the_horizon_stays_capped_at_100_hours(plans, plan):
    assert PAYBACK_STOPS[-1] == PAYBACK_MAX_H == 100.0
    assert plans.push(plan, 1, [_set("payback_hours", 100)], actor=PAGE).rev == 2
    with pytest.raises(InvalidOp, match="0 to 100 hours"):
        plans.push(plan, 2, [_set("payback_hours", 100.5)], actor=PAGE)
