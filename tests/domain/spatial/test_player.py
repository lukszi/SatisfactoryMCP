"""Player position, and the selectors that depend on it."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.select import select_nodes

pytestmark = pytest.mark.integration


def test_position_comes_from_the_pawn_not_the_player_state(state):
    """BP_PlayerState_C sits at the world origin, so reading it would report every
    player at (0, 0). The transform lives on Char_Player_C."""
    here = state.player_position()
    assert here is not None
    x, y, _z = here
    assert (x, y) != (0.0, 0.0)
    # Inside the map's content bounds rather than at some sentinel.
    minx, miny, maxx, maxy = geo.CONTENT_BBOX
    assert minx <= x <= maxx and miny <= y <= maxy


def test_players_only_include_pawns_with_a_transform(state):
    for p in state.players:
        assert p.get("pos") and len(p["pos"]) == 3


def test_missing_pawn_reports_none_rather_than_a_default(state):
    """A save with no pawn must not silently resolve to the map origin, which would
    put 'near me' in the middle of the ocean."""
    stripped = type(state)(projection={**state.projection, "players": []}, game=state.game)
    assert stripped.player_position() is None


def test_near_me_selects_around_the_player(state):
    table = nodes_mod.load_nodes()
    x, y, _ = state.player_position()
    sel = select_nodes(["near:me@500"], table.nodes, st=state)
    assert sel.nodes
    assert not sel.errors
    for n in sel.nodes:
        assert geo.distance_m((n["x"], n["y"]), (x, y)) <= 500


def test_near_me_matches_an_explicit_circle_at_the_same_point(state):
    """near:me is sugar, not a different rule."""
    table = nodes_mod.load_nodes()
    x, y, _ = state.player_position()
    a = select_nodes(["near:me@400"], table.nodes, st=state)
    b = select_nodes([f"near:{x / 100:.4f},{y / 100:.4f}@400"], table.nodes)
    assert {n["instance"] for n in a.nodes} == {n["instance"] for n in b.nodes}


def test_near_me_without_a_position_is_refused_not_silently_widened(state):
    """No pawn means the scope is unknown. Falling back to the whole map would plan
    against the entire world while claiming to plan around the player."""
    table = nodes_mod.load_nodes()
    stripped = type(state)(projection={**state.projection, "players": []}, game=state.game)
    sel = select_nodes(["near:me@500"], table.nodes, st=stripped)
    assert sel.nodes == []
    assert any("player pawn" in e for e in sel.errors)


def test_near_me_needs_a_radius(state):
    table = nodes_mod.load_nodes()
    sel = select_nodes(["near:me"], table.nodes, st=state)
    assert any("radius" in e for e in sel.errors)


def test_supplying_a_player_never_reinterprets_a_direction(state):
    """The regression this pins actually happened: passing the player position as
    `origin` turned "north" from the northern HALF of the map into a 60-degree cone
    from the player, silently changing every plan scoped by direction. The world state
    goes in as `st`, which only resolves the place inside a near: term."""
    table = nodes_mod.load_nodes()
    x, y, _ = state.player_position()
    plain = select_nodes(["north"], table.nodes)
    with_player = select_nodes(["north"], table.nodes, st=state)
    assert {n["instance"] for n in plain.nodes} == {n["instance"] for n in with_player.nodes}
    # An explicit cone origin is still available, and does narrow the result.
    cone = select_nodes(["north"], table.nodes, origin=(x, y))
    assert len(cone.nodes) < len(plain.nodes)
