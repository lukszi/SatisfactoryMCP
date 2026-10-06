"""Why an infeasible plan cannot get what it needs.

Every test here pins the counter-example that motivated the rule. The motivating one
is Nitrogen Gas: a plan needing it returned a bare INFEASIBLE, `explain_byproducts`
correctly said no byproduct was stuck, and the real answer -- nitrogen exists only as
resource-well satellites, and this world has no Pressurizer -- took a hand
cross-reference of a node scan against a recipe to find.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning import supply
from satisfactory_mcp.domain.planning.optimize import solve
from satisfactory_mcp.domain.planning.scenario import build_scenario
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration

NITROGEN = "Desc_NitrogenGas_C"
CRUDE = "Desc_LiquidOil_C"
IRON = "Desc_OreIron_C"


# --------------------------------------------------- naming the building


def _node(resource: str, kind: str) -> dict:
    return next(
        n for n in nodes_mod.load_nodes().nodes if n["resource"] == resource and n["kind"] == kind
    )


def test_a_locked_well_names_both_buildings_it_needs(game, state):
    """The Pressurizer produces nothing itself, so it is in no extractor table -- and
    yet with no Pressurizer on the core every satellite of that well yields zero."""
    node = _node(NITROGEN, "well_sat")
    blocking = nodes_mod.blocking_buildings(node, game, state.unlocked_building_ids)
    assert set(blocking) == {"Build_FrackingExtractor_C", "Build_FrackingSmasher_C"}


def test_nothing_blocks_a_node_this_world_can_already_mine(game, state):
    node = _node(IRON, "node")
    assert nodes_mod.blocking_buildings(node, game, state.unlocked_building_ids) == ()


def test_a_miner_does_not_make_an_oil_node_tappable(game):
    """Sharper than `reachable`, in the direction that matters: reachable asks only
    whether an extractor of the right KIND is unlocked, so an unlocked Miner Mk2 makes
    a crude node read as reachable while nothing on the map can pump it."""
    node = _node(CRUDE, "node")
    unlocked = {"Build_MinerMk1_C", "Build_MinerMk2_C"}
    assert nodes_mod.reachable(node, unlocked) is True
    assert nodes_mod.blocking_buildings(node, game, unlocked) == ("Build_OilPump_C",)


# ------------------------------------------------------------- the probe


def test_a_resource_with_no_node_in_scope_is_named(game, state):
    """Steel needs coal, and a scope selected by `resource:Iron Ore` holds none. The
    probe proves it: free coal, nothing else changed, and the same plan solves."""
    req = build_scenario(
        game,
        state,
        objective="max_item",
        target_item="Steel Ingot",
        exports=["Steel Ingot"],
        export_minimums={"Steel Ingot": 100.0},
        sources=["resource:Iron Ore"],
    )
    assert not solve(req.scenario).ok

    report = supply.diagnose(req, game, state.unlocked_building_ids)
    assert report.solves == 1
    assert report.rescued
    assert [m.item for m in report.missing] == ["Desc_Coal_C"]
    assert "no node in scope" in report.missing[0].reason
    assert "Coal" in "\n".join(supply.describe(report, game))


def test_nitrogen_gas_is_reported_as_locked_behind_the_pressurizer(game, state):
    """The counter-example this module exists for. Nitrogen is on the map -- 45 well
    satellites -- and every one of them is behind a building this world has not
    unlocked, which is a different answer from "there is none"."""
    req = build_scenario(
        game,
        state,
        objective="min_machines",
        exports=["Nitrogen Gas"],
        export_minimums={"Nitrogen Gas": 60.0},
    )
    report = supply.diagnose(req, game, state.unlocked_building_ids)
    missing = {m.item: m for m in report.missing}
    assert NITROGEN in missing
    nitrogen = missing[NITROGEN]
    assert nitrogen.in_scope == 45
    assert "none reachable" in nitrogen.reason
    assert "Resource Well Pressurizer" in nitrogen.needs


def test_a_supplied_resource_is_never_a_candidate(game, state):
    """Iron is extractable in this scope, so it can never be the missing input -- and
    a diagnosis that named it would send the player somewhere pointless."""
    req = build_scenario(game, state, sources=list(REFERENCE_FIELD))
    report = supply.diagnose(req, game, state.unlocked_building_ids)
    assert IRON not in report.candidates


def test_the_probe_says_so_when_supply_is_not_the_cause(game, state):
    """The negative result is worth as much as the positive one: an unreachable target
    rate is not fixed by any amount of ore, and saying "missing raw" here would send
    the player prospecting for no reason."""
    req = build_scenario(
        game,
        state,
        objective="max_item",
        target_item="Steel Ingot",
        exports=["Steel Ingot"],
        export_minimums={"Steel Ingot": 1e7},
        sources=list(REFERENCE_FIELD),
    )
    assert not solve(req.scenario).ok

    report = supply.diagnose(req, game, state.unlocked_building_ids)
    assert not report.rescued
    assert not report.missing
    assert "not a raw-supply problem" in "\n".join(supply.describe(report, game))


def test_an_export_nothing_can_make_is_named_without_a_probe(game, state):
    """The locked half of "must build first: Blender". Cooling System is what the
    rocket plan was really blocked on, and no probe is needed to prove it: an export
    with no producing column cannot be exported at any rate."""
    req = build_scenario(
        game,
        state,
        objective="max_item",
        target_item="Cooling System",
        exports=["Cooling System"],
    )
    report = supply.diagnose(req, game, state.unlocked_building_ids)
    lines = "\n".join(report.unmakeable)
    assert "Cooling System" in lines
    assert "none of them unlocked" in lines
    assert "Cooling System" in "\n".join(supply.describe(report, game))


# ------------------------------------------- unconstrained export columns


def _nitrogen(game) -> str:
    """An item this world can neither craft nor extract: no unlocked recipe, and its
    nodes need a Resource Well Pressurizer that is not built."""
    return next(k for k in game.items if game.item_name(k) == "Nitrogen Gas")


def test_an_export_nothing_produces_is_pinned_to_zero(game, state):
    """The invariant the fix establishes: export columns come from what you ASKED for,
    balance rows came only from what your processes TOUCH, so an export neither produced
    nor raw got a column with no row and the LP could set it freely.

    Verified NOT to discriminate on its own -- min_power has no incentive to raise that
    column, so it reads 0 with or without the fix. The two tests below are the ones that
    fail without it; this one pins the intended behaviour so a later change cannot quietly
    start reporting a non-zero export here.
    """
    from satisfactory_mcp.domain.planning.optimize import solve
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    item = _nitrogen(game)
    req = build_scenario(game, state, objective="min_power", exports=["Nitrogen Gas", "MW"])
    sol = solve(req.scenario)
    assert sol.ok, "a plan that simply cannot export it is still a valid plan"
    assert sol.exports.get(item, 0.0) == 0.0, sol.exports


def test_a_floor_on_an_unmakeable_export_is_infeasible_not_conjured(game, state):
    """The dangerous half. `export_minimums` sets a lower bound on that same
    unconstrained column, so the floor was satisfied out of thin air: min_power with a
    100/min floor SUCCEEDED and reported exports: Nitrogen Gas=100 on a world with no
    recipe and no reachable node for it. A confidently wrong plan beats a bare
    INFEASIBLE for damage."""
    from satisfactory_mcp.domain.planning.optimize import solve
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    req = build_scenario(
        game,
        state,
        objective="min_power",
        exports=["Nitrogen Gas", "MW"],
        export_minimums={"Nitrogen Gas": 100},
    )
    sol = solve(req.scenario)
    assert not sol.ok, f"a floor above an impossible zero must not solve: {sol.exports}"


def test_max_item_on_an_unmakeable_target_is_bounded(game, state):
    """Unbounded, not infeasible: the objective pushed the free column up forever and
    HiGHS returned UNBOUNDED, which surfaced to the user as a bare INFEASIBLE."""
    from satisfactory_mcp.domain.planning.optimize import solve
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    req = build_scenario(
        game, state, objective="max_item", target_item="Nitrogen Gas", exports=["Nitrogen Gas"]
    )
    sol = solve(req.scenario)
    assert sol.ok
    assert sol.objective_value == 0.0


def test_the_reason_is_stated_even_when_the_plan_succeeds(game, state):
    """Zero output is a quiet answer. The same naming the infeasible path does must
    run here, or a pinned-to-zero export looks like an ordinary empty result."""
    from satisfactory_mcp.domain.planning import supply
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    req = build_scenario(game, state, objective="min_power", exports=["Nitrogen Gas", "MW"])
    lines = supply.unmakeable(req, game)
    assert any("Nitrogen Gas" in line for line in lines), lines
    assert any("none of them unlocked" in line for line in lines), lines


def test_power_keeps_its_own_balance_and_is_not_double_rowed(game, state):
    """MW is excluded from the new rows on purpose: it balances on the power row. A
    second row would force generation to zero and every power plan to 0 MW."""
    from satisfactory_mcp.domain.planning.optimize import solve
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    req = build_scenario(
        game, state, objective="max_mw", sources=list(REFERENCE_FIELD), exports=["MW"]
    )
    sol = solve(req.scenario)
    assert sol.ok
    assert sol.net_mw > 0, "exporting power must still be possible"
