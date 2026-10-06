"""Planner P5, site drag (docs/planner-p5_contract.md): the ``site`` op's checks and words,
snapping, fit to built, the ground height hook, and the preview the page and chat share.
"""

from __future__ import annotations

import json
import time

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import siting
from satisfactory_mcp.domain.planning.layout.trunks import Trunk, TrunkMember
from satisfactory_mcp.domain.planning.siting import preview as site_preview
from satisfactory_mcp.domain.planning.stored import planlog
from satisfactory_mcp.domain.planning.stored.planlog import Actor, InvalidOp, PlanLog, describe_op
from satisfactory_mcp.domain.spatial import geo
from tests.support.reference_world import FIVE_RIP_ARGS

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)
#: Where the fixture world's RIP machines stand (the "copper setup" centroid) and open sea.
BUILT_SPOT = (-238.0, -1466.0)
SEA = (3000.0, 3000.0)


def _value(x=100.0, y=-200.0, **over) -> dict:
    out = {"origin_m": [x, y, None], "yaw_deg": 0.0, "footprint_m": [96.0, 64.0]}
    out.update(over)
    return out


@pytest.fixture
def plans():
    return PlanLog("TESTWORLD")


@pytest.fixture(autouse=True)
def _no_ground():
    siting.set_ground_z(None)
    yield
    siting.set_ground_z(None)


# ------------------------------------------------------------------ the op's check


def test_check_canonicalises_and_normalises_yaw():
    got = siting.normalise_record(
        _value(yaw_deg=-30.0, origin_label="map", footprint_source="given")
    )
    assert got == {
        "schema": 1,
        "origin_m": [100.0, -200.0, None],
        "yaw_deg": 330.0,
        "footprint_m": [96.0, 64.0],
        "footprint_source": "given",
        "origin_label": "map",
        "when": "",
    }
    assert siting.normalise_record(_value(yaw_deg=720.0))["yaw_deg"] == 0.0
    assert siting.normalise_record({"origin_m": [1.0, 2.0]})["footprint_m"] == [0.0, 0.0]


@pytest.mark.parametrize(
    ("value", "words"),
    [
        (_value(x=5000.0), "the site is outside the map (x must be -3,247…4,253 m)"),
        (_value(y=-3800.0), "the site is outside the map (y must be -3,750…3,750 m)"),
        (_value(footprint_m=[4.0, 64.0]), "site footprint must be 8…2,000 m each way, not 4 × 64"),
        (_value(footprint_m=[96.0, 3000.0]), "site footprint must be 8…2,000 m each way"),
        (_value(footprint_source="guess"), "footprint_source is one of given, layout, default"),
        ({"origin_m": [1.0]}, "origin_m must be [x, y] or [x, y, z]"),
        ({"origin_m": ["a", 1.0]}, "site x must be a finite number"),
        ({"origin_m": [float("nan"), 1.0]}, "site x must be a finite number"),
        (_value(yaw_deg=float("inf")), "site yaw_deg must be a finite number"),
        (_value(schema=2), "written by a newer version (schema 2"),
        (_value(origin_label="x" * 81), "origin_label is text of at most 80"),
        ("here", "site takes a siting object or null"),
    ],
)
def test_check_refuses_in_words(value, words):
    with pytest.raises(ValueError, match=None) as caught:
        siting.normalise_record(value)
    assert words in str(caught.value)


def test_the_map_square_is_the_tile_router_one():
    pytest.importorskip("fastapi")
    from satisfactory_mcp.interfaces.web.routers.assets.tiles import DEFAULT_MAP_BOUNDS_M as b

    assert geo.MAP_SQUARE_M == (b["x_min_m"], b["y_min_m"], b["x_max_m"], b["y_max_m"])


def test_a_refused_site_op_writes_nothing(plans):
    made = plans.create("p", {}, actor=PAGE)
    with pytest.raises(InvalidOp, match="outside the map"):
        plans.push(made.key, 1, [{"op": "site", "value": _value(x=9000.0)}], actor=PAGE)
    with pytest.raises(InvalidOp, match="footprint"):
        plans.push(made.key, 1, [{"op": "site", "value": _value(footprint_m=[1, 1])}], actor=CHAT)
    assert plans.head_rev(made.key) == 1


def test_a_drop_outside_the_playable_box_is_allowed(plans):
    made = plans.create("p", {}, actor=PAGE)
    x0 = geo.MAP_SQUARE_M[0] + 10.0
    assert x0 < geo.CONTENT_BBOX[0] / 100
    pushed = plans.push(made.key, 1, [{"op": "site", "value": _value(x=x0)}], actor=PAGE)
    assert pushed.rev == 2 and pushed.state.siting["origin_m"][0] == x0


# ------------------------------------------------------------------ words, undo, conflict


def test_describe_op_says_where_how_far_and_how_much_it_turned():
    was = siting.normalise_record(_value(1476.0, -2098.0))
    west = siting.normalise_record(_value(-27.0, -2098.0))
    assert describe_op({"op": "site", "was": was, "value": west}) == "site moved 1,503 m west"
    turned = siting.normalise_record(_value(1476.0 + 150, -2098.0 - 150, yaw_deg=30.0))
    assert (
        describe_op({"op": "site", "was": was, "value": turned})
        == "site moved 212 m north-east, turned 30°"
    )
    bigger = siting.normalise_record(_value(1476.0, -2098.0, footprint_m=[200.0, 120.0]))
    assert describe_op({"op": "site", "was": was, "value": bigger}) == "site resized to 200×120 m"
    assert describe_op({"op": "site", "was": was, "value": None}) == "site cleared"
    first = describe_op({"op": "site", "was": None, "value": was})
    assert first.startswith("site set at 1,476, -2,098")


def test_undo_takes_a_move_back_in_one_step(plans):
    made = plans.create("p", {}, actor=PAGE)
    plans.push(made.key, 1, [{"op": "site", "value": _value(10.0, 10.0)}], actor=PAGE)
    moved = plans.push(made.key, 2, [{"op": "site", "value": _value(500.0, 10.0)}], actor=PAGE)
    assert moved.state.siting["origin_m"][0] == 500.0
    assert "site moved 490 m east" in planlog.describe_commit(plans.commits(made.key)[-1])
    back = plans.undo(made.key, 3, 3, actor=PAGE)
    assert back.state.siting["origin_m"][0] == 10.0 and back.rev == 4


def test_a_chat_move_during_a_drag_is_a_conflict_in_words(plans):
    made = plans.create("p", {}, actor=PAGE)
    plans.push(made.key, 1, [{"op": "site", "value": _value(10.0, 10.0)}], actor=CHAT)
    with pytest.raises(planlog.Outdated) as caught:
        plans.push(made.key, 1, [{"op": "site", "value": _value(10.0, 200.0)}], actor=PAGE)
    (conflict,) = caught.value.conflicts
    assert conflict.text().startswith("site: you set the site at 10, 200")
    assert "Claude Code set the site at 10, 10" in conflict.text()


def test_a_drop_on_the_same_spot_is_no_version(plans):
    made = plans.create("p", {}, actor=PAGE)
    plans.push(made.key, 1, [{"op": "site", "value": _value()}], actor=PAGE)
    again = plans.push(made.key, 2, [{"op": "site", "value": _value()}], actor=PAGE)
    assert again.noop and plans.head_rev(made.key) == 2


# ------------------------------------------------------------------ snap, fit, ground


def test_snap_fine_rounds_the_centre_and_turns_in_15_degree_steps():
    assert siting.snap(100.4, -200.6, 37.0, 96.0, 64.0, "fine") == (100.0, -201.0, 30.0)
    assert siting.snap(0.0, 0.0, 353.0, 96.0, 64.0, "fine") == (0.0, 0.0, 0.0)


def test_snap_grid8_puts_the_west_and_north_edges_on_the_world_grid():
    x, y, yaw = siting.snap(103.0, -197.0, 8.0, 100.0, 60.0, "grid8")
    assert ((x - 50.0) % 8, (y - 30.0) % 8, yaw) == (0.0, 0.0, 0.0)
    assert abs(x - 103.0) <= 4 and abs(y + 197.0) <= 4


def test_snap_grid8_turns_in_90_degree_steps():
    assert siting.yaw_step("grid8") == 90.0 and siting.yaw_step("fine") == 15.0
    turns = [siting.snap(0.0, 0.0, a, 96.0, 64.0, "grid8")[2] for a in (30, 44, 46, 120, 300, 316)]
    assert turns == [0.0, 0.0, 90.0, 90.0, 270.0, 0.0]


def test_snap_grid8_a_quarter_turn_lines_the_turned_edges_up():
    """At 90° the pad's extent along x is its depth, so that edge goes on the grid."""
    x, y, yaw = siting.snap(103.0, -197.0, 85.0, 100.0, 60.0, "grid8")
    assert yaw == 90.0 and ((x - 30.0) % 8, (y - 50.0) % 8) == (0.0, 0.0)


def test_fit_to_bbox_covers_the_machines_with_a_margin():
    got = siting.fit_to_bbox([100.0, -50.0, 180.5, 10.0], "oil setup", when="2026-10-05")
    assert got["origin_m"] == [140.25, -20.0, None]
    assert got["footprint_m"] == [97.0, 76.0] and got["yaw_deg"] == 0.0
    assert got["footprint_source"] == "given" and got["origin_label"] == "built “oil setup”"
    sit = siting.Siting.from_record(got)
    assert sit.contains_cm(100.0 * 100, -50.0 * 100) and sit.contains_cm(180.5 * 100, 10.0 * 100)


def test_a_ground_height_provider_fills_z_and_a_failing_one_is_ignored(plans):
    asked = []

    def ground(x, y, yaw, w, d):
        asked.append((x, y, yaw, w, d))
        return 12.345

    siting.set_ground_z(ground)
    assert siting.normalise_record(_value())["origin_m"] == [100.0, -200.0, 12.35]
    assert asked == [(100.0, -200.0, 0.0, 96.0, 64.0)]
    assert siting.normalise_record(_value(origin_m=[1.0, 2.0, 7.0]))["origin_m"][2] == 7.0
    siting.set_ground_z(lambda *a: 1 / 0)
    assert siting.normalise_record(_value())["origin_m"][2] is None


def test_trunk_legs_to_the_site_move_while_the_chain_does_not():
    members = [
        TrunkMember("a", 0.0, 0.0, 5000.0, "pure", 300.0),
        TrunkMember("b", 30000.0, 40000.0, 1000.0, "pure", 300.0),
    ]
    t = Trunk("Desc_LiquidOil_C", "Crude Oil", "pipe", 600.0, members)
    assert t.run_m == pytest.approx(501.6, abs=0.01)
    assert t.to_site_m((30000.0, 50000.0)) == pytest.approx(100.0)
    assert t.to_site_m((30000.0, 140000.0)) == pytest.approx(1000.0)
    assert t.lift_to_site_m(None) is None and t.lift_to_site_m(4.0) == 6.0


# ------------------------------------------------------------------ the preview


@pytest.fixture
def world(labelled):
    return labelled


def _plan(st, at=None, args=None, factory=""):
    log = PlanLog(st.world_id)
    made = log.create("rip", args or FIVE_RIP_ARGS, actor=PAGE, factory=factory)
    if at is not None:
        log.push(
            made.key, 1, [{"op": "site", "value": _value(*at, footprint_m=[200, 200])}], actor=PAGE
        )
    return log.state(made.key)


def _pad(x, y, side=200.0, yaw=0.0):
    return siting.Siting(x, y, None, yaw, side, side, "given")


def _files(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_preview_moves_the_built_line_and_writes_nothing(world):
    state = _plan(world, at=BUILT_SPOT)
    before = _files(config.plans_dir())
    sess = site_preview.open_session(world.game, world, state)
    assert sess.built_now["built"] and sess.built_now["mode"] == "auto"
    home = site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT))
    away = site_preview.preview(world.game, world, sess, _pad(*SEA))
    assert home["built"]["built"] == sess.built_now["built"] and home["loses"] is None
    assert away["built"]["built"] == 0 and away["now"] == sess.built_now
    loss = away["loses"]
    assert loss == {
        "now": sess.built_now["built"],
        "here": 0,
        "total": sess.built_now["total"],
        "text": f"{sess.built_now['built']} of {sess.built_now['total']} built here → 0 at the new spot",
    }
    assert home["on_pad"] > 0 and away["on_pad"] == 0
    assert _files(config.plans_dir()) == before


def test_a_picked_factory_does_not_follow_the_pad(world):
    state = _plan(world, at=BUILT_SPOT, factory="tier 1&2")
    sess = site_preview.open_session(world.game, world, state)
    away = site_preview.preview(world.game, world, sess, _pad(*SEA))
    assert away["built"]["mode"] == "picked" and away["loses"] is None
    assert away["built"]["figure"] == sess.built_now["figure"]
    assert "the pad does not change the count" in away["built"]["where"]


def test_outside_the_map_only_where_is_filled(world):
    sess = site_preview.open_session(world.game, world, _plan(world, at=BUILT_SPOT))
    out = site_preview.preview(world.game, world, sess, _pad(4200.0, 0.0))
    assert out["in_map"] is False and out["built"]["mode"] == "" and out["trunks"] == []
    lines = site_preview.preview_lines(out)
    assert lines[-1] == "outside the map: a site here is refused"


def test_the_first_reply_carries_nodes_and_the_playable_box(world):
    sess = site_preview.open_session(world.game, world, _plan(world, at=BUILT_SPOT))
    first = site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT), include_static=True)
    step = site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT))
    assert first["content_bbox_m"] == [v / 100 for v in geo.CONTENT_BBOX]
    assert isinstance(first["nodes"], list) and step["nodes"] is None
    assert step["content_bbox_m"] is None
    assert len(json.dumps(step)) < 16_000


def test_without_a_terrain_field_the_card_says_so_and_z_is_pending(world):
    sess = site_preview.open_session(world.game, world, _plan(world, at=BUILT_SPOT))
    out = site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT), terrain=None)
    assert out["terrain"] is None and out["terrain_note"] == "no terrain field on this machine"
    assert out["z_m"] is None and out["z_note"] == "terrain height: pending"
    siting.set_ground_z(lambda x, y, yaw, w, d: 42.0)
    out = site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT))
    assert out["z_m"] == 42.0 and out["z_note"] == ""


def test_fit_to_built_offers_a_pad_that_covers_the_candidate(world):
    sess = site_preview.open_session(world.game, world, _plan(world, at=BUILT_SPOT))
    out = site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT, side=40.0))
    assert out["fits"], "a 40 m pad does not cover the factory it stands in"
    fit = out["fits"][0]
    assert siting.normalise_record(fit["value"]) == fit["value"]
    covered = site_preview.preview(world.game, world, sess, siting.Siting.from_record(fit["value"]))
    assert fit["name"] not in [f["name"] for f in covered["fits"]]


def test_a_first_placement_names_its_new_basis_and_starts_at_its_near_centre(world):
    args = dict(FIVE_RIP_ARGS, sources=["near:-238,-1466@400"])
    state = _plan(world, args=args)
    sess = site_preview.open_session(world.game, world, state)
    start = site_preview.initial_siting(world.game, world, sess)
    assert (start.x_m, start.y_m) == BUILT_SPOT and start.source == "layout"
    out = site_preview.preview(world.game, world, sess, start)
    assert out["sited"] is False
    assert out["basis"].startswith("counted on its pad from now (was: ")


def test_another_plans_pad_is_flagged_as_an_overlap(world):
    log = PlanLog(world.world_id)
    other = log.create("other", FIVE_RIP_ARGS, actor=PAGE)
    log.push(
        other.key, 1, [{"op": "site", "value": _value(*SEA, footprint_m=[100, 100])}], actor=PAGE
    )
    sess = site_preview.open_session(world.game, world, _plan(world, at=BUILT_SPOT))
    near = site_preview.preview(world.game, world, sess, _pad(SEA[0] + 120, SEA[1], yaw=45.0))
    far = site_preview.preview(world.game, world, sess, _pad(SEA[0] + 400, SEA[1]))
    assert near["overlaps"] == ["other"] and far["overlaps"] == []


def test_a_preview_step_fits_the_budget(world):
    sess = site_preview.open_session(world.game, world, _plan(world, at=BUILT_SPOT))
    site_preview.preview(world.game, world, sess, _pad(*BUILT_SPOT))
    took = []
    for i in range(20):
        t = time.perf_counter()
        site_preview.preview(world.game, world, sess, _pad(BUILT_SPOT[0] + 10 * i, BUILT_SPOT[1]))
        took.append(time.perf_counter() - t)
    took.sort()
    assert took[18] < 0.06, took
