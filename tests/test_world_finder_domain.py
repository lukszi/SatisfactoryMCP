"""The World finders' domain: one function per question, shared by a tool and a route.

Over the committed fixture world (no ``.sav``) and the committed node table, plus small
hand-built projections where a rule is about shape.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.gamedata.search import resolve_item
from satisfactory_mcp.domain.collectibles import service
from satisfactory_mcp.domain.spatial import geo, maplink, regions, surroundings
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.nodes import search as node_search
from satisfactory_mcp.domain.world import conduit_search
from satisfactory_mcp.domain.world.state import WorldState

IRON = "Desc_OreIron_C"


def _find(st, game, **kw):
    return node_search.find_nodes(st, game, resolve_resource=lambda q: resolve_item(game, q), **kw)


# ------------------------------------------------------------------ find_nodes


def test_a_resource_filter_keeps_only_that_resource_and_totals_add_up(state, game):
    found = _find(state, game, resource="Iron Ore", view="nodes")
    assert found.rows and {r["resource"] for r in found.rows} == {IRON}
    assert found.total == pytest.approx(sum(r["rate"] for r in found.rows))
    assert found.free == pytest.approx(nodes_mod.capacity(found.rows, only_free=True))
    assert found.unit == "/min"
    assert [(-r["rate"], r["instance"]) for r in found.rows] == sorted(
        (-r["rate"], r["instance"]) for r in found.rows
    )


def test_purity_and_kind_narrow_the_selection(state, game):
    found = _find(state, game, purity="pure", kind="node", view="nodes")
    assert found.rows
    assert {r["purity"] for r in found.rows} == {"pure"}
    assert {r["kind"] for r in found.rows} == {"node"}
    assert found.selectors == ["purity:pure", "kind:node"]


def test_status_splits_the_selection_into_free_and_tapped(state, game):
    every = _find(state, game, resource=IRON, view="nodes")
    free = _find(state, game, resource=IRON, view="nodes", status="free")
    tapped = _find(state, game, resource=IRON, view="nodes", status="tapped")
    assert free.rows and tapped.rows
    assert all(not r["tapped"] for r in free.rows)
    assert all(r["tapped"] for r in tapped.rows)
    assert len(free.rows) + len(tapped.rows) == len(every.rows)


def test_a_region_source_selects_by_where_the_node_stands(state, game):
    found = _find(state, game, sources=["region:Grass Fields"], view="nodes")
    rm = regions.load_regions()
    assert found.rows
    assert {rm.label_for_node(r).name for r in found.rows} == {"Grass Fields"}
    assert found.description.startswith("Grass Fields")


def test_near_adds_a_distance_and_nearest_sorts_by_it(state, game):
    found = _find(state, game, resource=IRON, view="nearest", near="me")
    here = state.player_position()
    distances = [r["distance_m"] for r in found.rows]
    assert distances == sorted(distances)
    first = found.rows[0]
    assert first["distance_m"] == pytest.approx(geo.distance_m((first["x"], first["y"]), here[:2]))
    assert found.where == "you"


def test_nearest_without_a_place_and_an_unknown_place_are_refused(state, game):
    assert _find(state, game, view="nearest").error
    assert "does not name a place" in _find(state, game, view="nodes", near="nowhere").error


def test_every_selector_failing_selects_nothing_rather_than_the_map(state, game):
    found = _find(state, game, sources=["region:Nowhere"], view="nodes")
    assert found.unselected and not found.rows
    assert "unknown region" in found.errors[0]


def test_locked_capacity_is_named_and_counted(state, game):
    crude = _find(state, game, resource="Crude Oil", view="nodes")
    locked = [r for r in crude.rows if node_search.status_of(r) == "locked"]
    assert locked, "the reference world has locked crude satellites"
    assert crude.locked_rate == pytest.approx(sum(r["rate"] for r in locked))
    assert any("excluded from free" in n for n in crude.notes)
    assert crude.total == pytest.approx(sum(r["rate"] for r in crude.rows))


def test_without_a_save_everything_reads_free(game):
    found = _find(None, game, resource=IRON, view="nodes")
    assert {node_search.status_of(r) for r in found.rows} == {"free"}
    assert "no save read" in found.notes[-1]


def test_a_geyser_search_answers_rather_than_raising(state, game):
    found = _find(state, game, kind="geyser", view="nodes")
    assert found.rows and found.unit == "/min"


def test_a_fluid_search_reports_its_elevation_span_and_water_its_block(state, game):
    crude = _find(state, game, resource="Crude Oil", view="fields")
    low, high = crude.elevation
    assert low < high
    water = _find(state, game, resource="Water", view="fields")
    assert water.water is not None and set(water.water) >= {"bodies", "pumps", "sea_level_m"}
    assert water.notes[0] == node_search.WATER_NOTE


# ---------------------------------------------------------------------- fields


def test_fields_cluster_members_within_the_link_distance(state, game):
    found = _find(state, game, resource="Copper Ore", view="fields")
    assert sum(f.size for f in found.fields) == len(found.rows)
    for f in found.fields:
        assert f.key == "field:" + min(m["instance"].rsplit(".", 1)[-1] for m in f.members)
        assert f.selector.startswith("near:")
        x0, y0, x1, y1 = f.bbox
        assert all(x0 <= m["x"] <= x1 and y0 <= m["y"] <= y1 for m in f.members)
        assert f.free <= f.total
        assert f.spoiler == (not any(m["reachable"] for m in f.members))


def test_a_field_distance_is_to_its_nearest_member(state, game):
    found = _find(state, game, resource="Copper Ore", view="fields", near="me")
    here = state.player_position()[:2]
    f = found.fields[0]
    assert f.distance_m == pytest.approx(
        min(geo.distance_m((m["x"], m["y"]), here) for m in f.members)
    )


# ------------------------------------------------------------------------ rank


def test_rank_orders_untapped_reachable_fields_best_first(state, game):
    ranked = node_search.rank(state, game, IRON, None)
    assert ranked.scored
    scores = [s.score for s in ranked.scored]
    assert scores == sorted(scores, reverse=True)
    view = node_search.site_view(ranked.scored[0])
    assert view["selector"].startswith("near:") and view["untapped"] > 0


def test_rank_with_a_failed_selector_selects_nothing(state, game):
    assert node_search.rank(state, game, IRON, ["region:Nowhere"]).unselected


# ----------------------------------------------------------------------- place


def test_here_lists_nodes_nearest_first_and_the_nearest_building(state, game):
    found = surroundings.player_surroundings(state, game, 500.0)
    assert found.player is not None and found.label is not None
    d = [n["distance_m"] for n in found.nodes]
    assert d == sorted(d) and all(v <= 500.0 for v in d)
    assert found.nearest_building is not None
    assert found.pawns == len(state.players)


def test_here_with_no_pawn_has_no_position(game):
    st = WorldState(projection={"players": []}, game=game)
    found = surroundings.player_surroundings(st, game, 500.0)
    assert found.player is None and found.nodes == []


def test_describe_reports_fields_and_pickups_within_500m(state, game):
    x, y = 2000 * 100.0, -2400 * 100.0
    found = surroundings.describe_point(state, game, x, y, 200.0)
    assert len(found.nearest) == 5
    assert found.fields and len(found.fields) <= 3
    assert all(f.distance_m <= surroundings.FIELD_REACH_M for f in found.fields)
    assert found.fields_total >= len(found.fields)
    assert all(len(f.resources) == 1 for f in found.fields), "fields are per resource"
    assert found.pickups and len(found.pickups) <= 5
    assert all(p["distance_m"] <= surroundings.PICKUP_REACH_M for p in found.pickups)
    assert found.pickups_total >= len(found.pickups)
    assert all(not p["collected"] for p in found.pickups)
    assert {"label", "spoiler"} <= set(found.pickups[0])
    assert found.conduits == {"belt": 0, "pipe": 0}


def test_describe_without_a_save_keeps_the_map_and_drops_the_save(game):
    found = surroundings.describe_point(None, game, 200000.0, -240000.0, 200.0)
    assert found.nearest and found.conduits is None
    assert found.pickups == [] and found.pickups_total is None


# -------------------------------------------------------------------- conduits


def test_search_near_lists_runs_longest_first(state):
    found = conduit_search.search(state, "-1216,-1127", 300.0)
    assert found.hits
    lengths = [r.length_m for r in found.hits]
    assert lengths == sorted(lengths, reverse=True)
    assert all(r.dist_m(*found.origin) <= 300.0 for r in found.hits)
    assert len(found.belts) + len(found.pipes) == len(found.hits)


def test_search_kind_and_to_narrow_the_runs(state):
    pipes = conduit_search.search(state, "-1216,-1127", 300.0, kind="pipe")
    assert pipes.hits and all(r.kind == "pipe" for r in pipes.hits)
    both = conduit_search.search(state, "-1216,-1127", 300.0, to="-1200,-1100", to_radius_m=100.0)
    assert all(r.dist_m(*both.second) <= 100.0 for r in both.hits)
    assert both.where_to == "-1200,-1100"


def test_search_network_lists_every_pipe_of_that_network(state):
    net = next(r.network for r in state.conduit_runs if r.kind == "pipe" and r.network is not None)
    found = conduit_search.search(state, "me", 1.0, network=net)
    expected = [r for r in state.conduit_runs if r.kind == "pipe" and r.network == net]
    assert {r.ident for r in found.hits} == {r.ident for r in expected}


def test_search_run_picks_one_run_and_refuses_an_unknown_one(state):
    assert [r.ident for r in conduit_search.search(state, "me", 1.0, run="chain:7").hits] == [
        "chain:7"
    ]
    assert conduit_search.search(state, "me", 1.0, run="chain:999999").error


def test_a_network_touching_both_areas_is_reported_as_bridged(game):
    projection = {
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 33, "fluid": "Desc_Water_C"}],
            "segments": [
                [0, 0, [[0, 0, 0], [3000, 0, 0]], -1, None],
                [0, 0, [[3000, 0, 0], [3000, 40000, 0]], -1, None],
            ],
        },
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    found = conduit_search.search(st, "0,0", 20.0, to="30,400", to_radius_m=20.0)
    assert not found.hits
    assert found.bridged and "pipe network 33" in found.bridged[0]


def test_networks_sum_every_pipe_once(state):
    views = conduit_search.networks(state, (0.0, 0.0))
    pipes = [r for r in state.conduit_runs if r.kind == "pipe"]
    assert sum(v.pieces for v in views) == len(pipes)
    lengths = [v.length_m for v in views]
    assert lengths == sorted(lengths, reverse=True)


# --------------------------------------------------------------------- regions


def test_region_rows_count_one_resource_and_drop_empty_regions(state, game):
    table = nodes_mod.load_nodes()
    rows = regions.region_rows(table, IRON)
    assert rows and all(r["nodes"] > 0 for r in rows)
    assert sum(r["nodes"] for r in rows) <= len(table.by_resource(IRON))
    assert [r["nodes"] for r in rows] == sorted((r["nodes"] for r in rows), reverse=True)
    one = regions.region_rows(table, IRON, table.by_resource(IRON)[:1])
    assert sum(r["nodes"] for r in one) <= 1


# ------------------------------------------------------------------- table_age


def _skewed_meta() -> dict:
    return {
        "cross_validation": {
            "positions": {
                "pin": {
                    "build": "saveVersion 7",
                    "rounding_floor_cm": 0.5,
                    "max_position_delta_cm": 0.4,
                },
                "later": {
                    "build": "buildVersion 900",
                    "rounding_floor_cm": 0.5,
                    "max_position_delta_cm": 12.4,
                    "rows_past_the_rounding_floor": [
                        {"instance": "L:P.NodeA", "delta_cm": 12.4, "dz_cm": -12.4},
                        {"instance": "L:P.NodeB", "delta_cm": 3.0, "dz_cm": 3.0},
                    ],
                    "purity_mismatches": [],
                    "resource_mismatches": [],
                },
            }
        }
    }


def test_table_age_is_silent_on_a_current_table_and_scoped_on_a_stale_one():
    table = nodes_mod.NodeTable(nodes=[], meta=_skewed_meta())
    assert nodes_mod.table_age({"save_version": 7}, table, None) is None
    age = nodes_mod.table_age({"save_version": 8}, table, ["L:P.NodeA"])
    assert age["behind"] and age["table"] == "nodes"
    assert age["moved"] == 1 and age["unjoinable"] == 0
    assert age["notes"] and "NodeA" in age["notes"][0]
    assert nodes_mod.table_age({"save_version": 8}, table, ["L:P.Other"])["moved"] == 0
    assert nodes_mod.drifted(nodes_mod.skew_from_meta(table.meta, {"save_version": 8}), None) == {
        "NodeA",
        "NodeB",
    }


# ------------------------------------------------------------------ collectibles


def test_census_rows_carry_label_and_spoiler(state):
    rows = service.census_rows(state)
    found = set(service.found(state))
    assert rows and found
    for r in rows:
        assert r["label"] == service.label(r["category"])
        assert r["spoiler"] == (
            r["category"] not in found and r["category"] not in service.NEVER_SPOILER
        )
        assert (r["category"] in found) == (r["collected"] > 0)


def test_pods_and_loot_caches_are_never_spoilers():
    assert not service.is_spoiler("crashed_drop_pod", set())
    assert not service.is_spoiler("loot_cache", set())
    assert service.is_spoiler("somersloop", set())
    assert not service.is_spoiler("somersloop", {"somersloop"})
    assert service.label("power_slug_blue") == "blue power slugs"
    assert service.label("some_new_thing") == "some new thing"


def test_collectible_table_age_follows_the_save_build(state, game):
    age = service.table_age(state)
    assert age is not None and age["table"] == "collectibles"
    assert age["behind"] is False and age["gap"] is None
    assert age["observed_from"] and age["observed_matches"] is True
    newer = WorldState(
        projection={**state.projection, "header": {**state.header, "build_version": 999999}},
        game=game,
    )
    later = service.table_age(newer)
    assert later["behind"] and "-> 999999" in later["gap"] and later["notes"]
    other = WorldState(
        projection={**state.projection, "header": {**state.header, "session_name": "Other"}},
        game=game,
    )
    assert service.table_age(other)["observed_matches"] is False


# --------------------------------------------------------------------- maplink


def test_show_ref_spells_each_place_kind():
    assert maplink.show_ref(node="Persistent_Level:PersistentLevel.BP_Node1") == "node:BP_Node1"
    assert maplink.show_ref(run="Chain:7") == "chain:7"
    assert maplink.show_ref(label="steel") == "label:steel"
    assert maplink.show_ref() == ""


def test_choices_cover_the_whole_table(game):
    got = node_search.choices(game)
    table = nodes_mod.load_nodes()
    assert sum(r["nodes"] for r in got["resources"]) == len(table)
    assert set(got["kinds"]) == {n["kind"] for n in table.nodes}


def test_node_rate_without_game_data_is_zero():
    node = nodes_mod.load_nodes().nodes[0]
    assert nodes_mod.node_rate(node, None) == 0.0


def test_hub_is_a_place(state):
    from satisfactory_mcp.domain.spatial.places import resolve_place

    (x, y), where = resolve_place(state, "HUB")
    assert where == "the HUB"
    assert (round(x / 100), round(y / 100)) == (-411, -1443)
