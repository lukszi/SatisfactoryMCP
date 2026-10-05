"""``/api/plans``: the pad each stored plan claims, and the two halves that must agree.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Plans are written through ``PlanLog`` into a temporary plans directory, so nothing here
touches the reader's own.
"""

from __future__ import annotations

import math

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.domain.planning.siting import Siting
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app

#: One measured pad, turned off the axes so that every rotation bug shows.
SITED = Siting(
    x_m=-1200.5,
    y_m=340.25,
    z_m=118.0,
    yaw_deg=37.0,
    width_m=96.0,
    depth_m=64.0,
    source="given",
    origin_label="Coal Powerplant",
    when="2026-08-05",
)

#: The square ``plan_layout`` budgets when the caller measured nothing: an estimate, and the
#: payload has to keep saying so.
ESTIMATED = Siting(x_m=0.0, y_m=0.0, yaw_deg=0.0, width_m=48.0, depth_m=48.0, source="layout")


WORLD = "X2faPVKjX06VaRzClNv5KQ"
CHAT = Actor("chat", "claude-code", 1)


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    return tmp_path / "plans"


def _client(projection, game):
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    return TestClient(app)


@pytest.fixture
def planned(plans_dir, projection, game):
    """The API over the fixture world, holding three plans: sited, never sited, estimated."""
    log = PlanLog(WORLD)
    log.create("Aluminium", {}, siting=SITED.to_dict(), factory="North Smelter", actor=CHAT)
    log.create("Sketch", {}, actor=CHAT)
    log.create("Guess", {}, siting=ESTIMATED.to_dict(), actor=CHAT)
    with _client(projection, game) as c:
        yield c


def _corners(row: dict) -> list[tuple[float, float]]:
    """The four corners the PAGE would draw, by ``footprintCorners`` in frontend/src/map.ts.

    Reimplemented here rather than approximated, because the whole point of this endpoint is
    that the client's forward rotation and ``Siting.contains_cm``'s inverse are one rectangle.
    """
    a = math.radians(row["yaw_deg"])
    w, d = row["width_m"] / 2, row["depth_m"] / 2
    return [
        (
            row["x_m"] + dx * math.cos(a) - dy * math.sin(a),
            row["y_m"] + dx * math.sin(a) + dy * math.cos(a),
        )
        for dx, dy in ((-w, -d), (w, -d), (w, d), (-w, d))
    ]


def test_a_sited_plan_is_sent_as_the_rectangle_it_recorded(planned):
    body = planned.get("/api/plans").json()
    assert body["stored"] == 3, "every stored plan is counted, sited or not"
    rows = {row["name"]: row for row in body["plans"]}
    assert set(rows) == {"Aluminium", "Guess"}
    row = rows["Aluminium"]
    assert set(row) == {
        "key",
        "name",
        "x_m",
        "y_m",
        "z_m",
        "z_source",
        "terrain_line",
        "yaw_deg",
        "width_m",
        "depth_m",
        "source",
        "origin_label",
        "factory",
    }
    # METRES, unconverted: a siting is a number the player typed, so the centimetre rule the
    # rest of this surface lives by would divide a correct answer by 100.
    assert row["x_m"] == SITED.x_m and row["y_m"] == SITED.y_m
    assert row["z_m"] == SITED.z_m
    assert (row["width_m"], row["depth_m"]) == (SITED.width_m, SITED.depth_m)
    assert row["yaw_deg"] == SITED.yaw_deg
    assert row["origin_label"] == "Coal Powerplant"
    assert row["factory"] == "North Smelter"
    assert row["key"] == PlanLog(WORLD).find("Aluminium").key


def test_the_index_lists_every_live_plan_at_its_head(planned):
    log = PlanLog(WORLD)
    sketch = log.find("Sketch").key
    log.push(
        sketch,
        1,
        [
            {"op": "set", "field": "objective", "value": "min_machines"},
            {"op": "add", "field": "exports", "member": "Wire"},
            {"op": "put", "field": "export_minimums", "item": "Wire", "value": 30},
        ],
        actor=Actor("page"),
    )
    log.push(log.find("Guess").key, 1, [{"op": "forget"}], actor=CHAT)
    index = {row["name"]: row for row in planned.get("/api/plans").json()["index"]}
    assert set(index) == {"Aluminium", "Sketch"}, "a forgotten plan is not listed"
    row = index["Sketch"]
    assert row["key"] == sketch and row["rev"] == 2
    assert (row["objective"], row["exports"], row["rates"]) == (
        "min_machines",
        ["Wire"],
        {"Wire": 30.0},
    )
    assert row["sited"] is False and index["Aluminium"]["sited"] is True
    assert row["last"]["rev"] == 2 and row["last"]["actor"]["display"] == "page"
    assert row["last"]["text"].startswith("v2 page: objective max_mw→min_machines")


def test_an_estimated_footprint_says_so_and_a_measured_one_says_so(planned):
    """``source`` is the difference between a pad and a guess, and it is the only thing on
    the card that stops a layout-budgeted square being read to the metre."""
    rows = {row["name"]: row for row in planned.get("/api/plans").json()["plans"]}
    assert rows["Aluminium"]["source"] == "given"
    assert rows["Guess"]["source"] == "layout"
    # An origin that resolved from nothing nameable keeps an empty label rather than a
    # stand-in: the card drops the row instead of inventing a provenance.
    assert rows["Guess"]["origin_label"] == ""


def test_a_stored_plan_with_no_siting_is_counted_and_not_drawn(planned):
    """``Sketch`` is a real plan and has nowhere to be. It must not arrive as a rectangle at
    the world centre, which is what a zero footprint would draw."""
    body = planned.get("/api/plans").json()
    assert "Sketch" not in {row["name"] for row in body["plans"]}
    assert body["stored"] - len(body["plans"]) == 1


def test_the_drawn_rectangle_is_the_one_the_domain_counts_machines_inside(planned):
    """The two halves have never met, and this is where they do.

    ``siting.contains_cm`` is written as the explicit inverse of the frontend's
    ``footprintCorners``. If either rotation flips a sign, a plan's pad on the map and the
    pad ``site_plan``'s survey counts machines inside stop being the same rectangle -- and
    both keep working, quietly, about different ground.
    """
    rows = {row["name"]: row for row in planned.get("/api/plans").json()["plans"]}
    row = rows["Aluminium"]
    for x_m, y_m in _corners(row):
        assert SITED.contains_cm(x_m * 100, y_m * 100), (x_m, y_m)
    # A metre outside each corner, straight out along the diagonal from the centre: the
    # rectangle has to END somewhere, or "contains" is a claim about the whole plane.
    for x_m, y_m in _corners(row):
        dx, dy = x_m - row["x_m"], y_m - row["y_m"]
        scale = 1 + 1 / math.hypot(dx, dy)
        assert not SITED.contains_cm(
            (row["x_m"] + dx * scale) * 100, (row["y_m"] + dy * scale) * 100
        )
    # And the turn is real: an axis-aligned box of the same size would hold this point, the
    # rotated one does not.
    assert not SITED.contains_cm((row["x_m"] + 47) * 100, (row["y_m"] + 31) * 100)


def test_a_world_with_no_plans_answers_with_an_empty_layer_and_not_an_error(
    plans_dir, projection, game
):
    """A player who has never run ``site_plan`` is the ordinary case. ``stored: 0`` is what
    tells that apart from "three plans, none of them sited"."""
    with _client(projection, game) as c:
        assert c.get("/api/plans").json() == {"plans": [], "stored": 0, "index": []}


def test_a_hand_edited_siting_that_will_not_parse_costs_one_row(plans_dir, projection, game):
    """``siting.parse`` answers ``None`` for a record it cannot read, and this layer must
    treat that as "not sited" rather than as a 500 over the whole map."""
    log = PlanLog(WORLD)
    log.create("Broken", {}, siting={"origin_m": ["north", "east"]}, actor=CHAT)
    log.create("Aluminium", {}, siting=SITED.to_dict(), actor=CHAT)
    with _client(projection, game) as c:
        body = c.get("/api/plans").json()
    assert [row["name"] for row in body["plans"]] == ["Aluminium"]
    assert body["stored"] == 2


def test_progress_is_one_row_per_live_plan_and_cached_per_version(planned):
    from satisfactory_mcp.interfaces.web.routers import plans as plans_router

    reply = planned.get("/api/plan/built")
    assert reply.status_code == 200, reply.text
    rows = reply.json()["rows"]
    index = planned.get("/api/plans").json()["index"]
    assert [r["key"] for r in rows] == [r["key"] for r in index]
    for row in rows:
        assert row["figure"] in ("?", "–") or " / " in row["figure"]
        assert row["built"] is None or 0 <= row["built"] <= row["total"]
    cached = len(plans_router._BUILT)
    planned.get("/api/plan/built")
    assert len(plans_router._BUILT) == cached
