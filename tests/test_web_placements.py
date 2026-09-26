"""``/api/machines`` and ``/api/structures``: everything the player physically placed.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``,
or by building its own app around a hand-written projection -- so nothing in this file
spawns the sidecar, reads a ``.sav`` or needs the save directory to exist.

The two endpoints are tested together because they answer one question in two resolutions:
an actor with a recipe and a clock, and an interned foundation with neither. What both owe
the map is a position, a size and a facing, and the yaw test at the bottom asserts that
across the pair rather than twice.
"""

from __future__ import annotations

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.core.gamedata.footprint import FOUNDATION_M
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app


def test_machines_split_by_kind_and_name_their_buildings(client, state):
    body = client.get("/api/machines").json()
    assert set(body) == {"machines", "extractors", "generators"}
    for kind in body:
        assert len(body[kind]) == len(state.projection[kind])
    row = body["machines"][0]
    assert set(row) == {
        "instance_leaf",
        "cls",
        "name",
        "x_m",
        "y_m",
        "z_m",
        "recipe",
        "recipe_name",
        "clock",
        "paused",
        "state",
        "actionable",
        "uptime",
        "yaw",
        "w_m",
        "l_m",
        "h_m",
    }
    assert row["name"] != row["cls"], "the class id was not joined to a building name"
    assert "." not in row["instance_leaf"]


def test_a_machine_carries_the_height_a_top_down_map_cannot_draw(client):
    """``h_m`` is the third side of the clearance box, and it is the floor view's evidence.

    All three sides come from one ``mClearanceData`` and go null together, so a row with a
    width and no height would mean the extractor had read two thirds of a measurement.
    """
    rows = [row for kind in client.get("/api/machines").json().values() for row in kind]
    for row in rows:
        assert (row["h_m"] is None) == (row["w_m"] is None) == (row["l_m"] is None)
    tall = {row["name"]: row["h_m"] for row in rows if row["h_m"] and row["h_m"] >= 12}
    # The case the floor view exists for: taller than this world's 12 m storey module, so
    # it is physically through the deck above and only the deck below can say so.
    assert tall.get("Refinery") == 15.0, tall


def test_every_machine_carries_the_state_the_health_report_would_give_it(client, state):
    """The map's states and ``factory_health``'s are the SAME assessment, not two.

    Asserted against ``health.assess`` re-run over the same records rather than against a
    table of expected words, because the value of the field is that the map and the tool
    cannot disagree about a machine. Totality is asserted too: a row whose state fell
    through would be a rectangle the page draws with no verdict at all.
    """
    from satisfactory_mcp.domain.factories import health

    body = client.get("/api/machines").json()
    rows = {row["instance_leaf"]: row for kind in body for row in body[kind]}
    report = health.assess("t", list(rows), state.game, state.projection)
    assert len(report.machines) == len(rows), "assess did not see every row the map is sent"
    for m in report.machines:
        assert rows[m.instance]["state"] == m.state
        if m.uptime is None:
            assert rows[m.instance]["uptime"] is None
        else:
            assert rows[m.instance]["uptime"] == pytest.approx(m.uptime, abs=5e-4)
    assert {r["state"] for r in rows.values()} <= set(health.STATES)


def test_actionable_is_the_health_tuple_and_blocked_stays_tellable(client):
    """``actionable`` is ``health.ACTIONABLE`` read on the server, so the map, the dashboard
    and ``factory_health`` share one list. ``blocked`` is in it and is still sent as its own
    ``state``, because the map marks it apart from the empty-box states."""
    from satisfactory_mcp.domain.factories import health

    body = client.get("/api/machines").json()
    counts: dict[str, int] = {}
    for kind in body:
        for row in body[kind]:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
            assert row["actionable"] == (row["state"] in health.ACTIONABLE), row
    assert counts["blocked"] > counts["starved"], counts
    assert "blocked" in health.ACTIONABLE
    # And a paused machine keeps both spellings: the save's own field, and the assessment.
    for kind in body:
        for row in body[kind]:
            assert row["paused"] == (row["state"] == "paused"), row


def test_uptime_is_a_fraction_and_absent_rather_than_zero_where_unmeasured(client):
    """The only measured number in the project. ``null`` is "no productivity monitor on this
    building"; ``0.0`` is "monitored, and it produced nothing" -- and a page that drew those
    the same would report every belt-side buffer as a stopped machine."""
    rows = [row for kind in client.get("/api/machines").json().values() for row in kind]
    measured = [r["uptime"] for r in rows if r["uptime"] is not None]
    assert measured and len(measured) < len(rows), "both branches exist on the fixture world"
    for value in measured:
        assert 0.0 <= value <= 1.0
    assert any(r["uptime"] == 0.0 for r in rows), "a monitored idle machine reads 0, not null"
    # One-way, not an equivalence: a machine can be paused or on a dead node AND unmonitored,
    # and those states are named for the stronger fact rather than for the missing window.
    for row in rows:
        if row["state"] == "unmonitored":
            assert row["uptime"] is None, row


def test_machine_popup_rows_never_degrade_to_engine_ids(client):
    """The popup teaches one vocabulary. A recipe resolves to its docs name; a building
    the docs dump has no entry for (both biomass burners here) still comes back as words
    rather than as ``Build_GeneratorIntegratedBiomass_C``."""
    body = client.get("/api/machines").json()
    rows = [row for kind in body for row in body[kind]]
    with_recipe = [r for r in rows if r["recipe"]]
    assert with_recipe
    for r in with_recipe:
        assert r["recipe_name"], r
        assert not r["recipe_name"].startswith("Recipe_"), r
    for r in rows:
        assert r["name"], r
        assert not str(r["name"]).startswith("Build_"), r


def test_machines_carry_their_own_footprint_so_the_map_can_draw_true_size(client, state):
    """A Manufacturer is not a Constructor, and the map may not draw them the same.

    The numbers are the docs dump's own clearance union, so they are asserted as the
    ordering that makes the drawing worth doing rather than as literals that move with a
    game patch. The null case is asserted too: a building with no clearance data must
    come back as null, because a 6x6 guess sent from here would be indistinguishable
    from a measurement once it reached the page.
    """
    body = client.get("/api/machines").json()
    sizes = {
        row["cls"]: (row["w_m"], row["l_m"])
        for kind in body
        for row in body[kind]
        if row["w_m"] is not None
    }
    assert sizes["Build_ManufacturerMk1_C"] > sizes["Build_ConstructorMk1_C"]
    for w, l in sizes.values():
        assert 1 <= w <= 40 and 1 <= l <= 40, "a footprint in centimetres, or none at all"
    unmeasured = {
        row["cls"]
        for kind in body
        for row in body[kind]
        if row["w_m"] is None or row["l_m"] is None
    }
    assert unmeasured, "the fixture world has buildings the docs dump gives no clearance for"
    for cls in unmeasured:
        building = state.game.buildings.get(cls)
        assert building is None or building.footprint is None, (
            f"{cls} has a footprint and was dropped on the way out"
        )


def test_positions_are_metres_not_centimetres(client, state):
    """The one unit rule, pinned against a known fixture position.

    The save stores centimetres. A regression here is silent -- the map still draws,
    100x out -- so it is checked against the projection's own first machine rather than
    against a magnitude.
    """
    raw = state.projection["machines"][0]
    row = client.get("/api/machines").json()["machines"][0]
    assert row["instance_leaf"] == raw["instance"].rsplit(".", 1)[-1]
    assert row["x_m"] == pytest.approx(round(raw["pos"][0] / 100.0, 1))
    assert row["y_m"] == pytest.approx(round(raw["pos"][1] / 100.0, 1))
    assert row["z_m"] == pytest.approx(round(raw["pos"][2] / 100.0, 1))


def test_structures_are_the_floor_plan_the_player_actually_built(client, state):
    """Every lightweight buildable, un-interned, in metres, with the grid it snaps to."""
    body = client.get("/api/structures").json()
    raw = state.projection["structures"]
    assert body["count"] == len(raw["instances"]) == len(body["structures"])
    assert body["count"] > 8000, "the reference world is a 320-hour base, not a starter camp"
    # The page draws one tile per piece and must not hardcode its edge.
    assert body["tile_m"] == FOUNDATION_M == 8.0

    row = body["structures"][0]
    assert set(row) == {"cls", "x_m", "y_m", "z_m", "yaw"}
    # The class index is resolved here, or the page would have to carry the legend.
    assert row["cls"] == raw["classes"][raw["instances"][0][0]]
    assert row["cls"].startswith("Build_")
    assert {r["cls"] for r in body["structures"]} <= set(raw["classes"])
    # Metres, like every other coordinate on this surface. A regression is silent.
    assert row["x_m"] == pytest.approx(round(raw["instances"][0][1] / 100.0, 1))
    assert row["y_m"] == pytest.approx(round(raw["instances"][0][2] / 100.0, 1))
    assert row["z_m"] == pytest.approx(round(raw["instances"][0][3] / 100.0, 1))
    assert max(abs(r["x_m"]) for r in body["structures"]) < 5000


def test_a_world_with_nothing_built_answers_with_an_empty_floor_plan(game):
    """A young save has no lightweight subsystem at all, and that is not an error.

    Asserted through three shapes, because the projection has carried all three: the key
    absent, the key present but empty, and a row too short to be a transform. Anything
    but a 200 with an empty list here draws a red banner on a save whose only fault is
    that the player has not poured concrete yet.
    """
    for projection in ({}, {"structures": {}}, {"structures": {"classes": [], "instances": []}}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            body = c.get("/api/structures").json()
        assert body == {"structures": [], "count": 0, "tile_m": FOUNDATION_M}


def test_a_malformed_structure_row_costs_one_piece_not_the_endpoint(game):
    """Raw projection data, read guarded field by field -- the same rule elevation uses."""
    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [
                [0, 100, 200, 300, 33.5],
                [0, 100, 200],  # short: no z
                [0, "x", 200, 300],  # unparseable
                [7, 400, 500, 600, "sideways"],  # bad index, and an unreadable yaw
                [0, 700, 800, 900],  # a schema-11 row: no yaw column at all
                "not a row",
            ],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/structures").json()
    assert body["count"] == 3
    assert body["structures"][0] == {
        "cls": "Build_Foundation_8x1_01_C",
        "x_m": 1.0,
        "y_m": 2.0,
        "z_m": 3.0,
        "yaw": 33.5,
    }
    # An index with no class is still a real piece at a real place: it keeps its position
    # and loses only its name, which is the honest half-answer. Same for a yaw that will
    # not parse -- the piece survives, unrotated and saying so.
    assert body["structures"][1] == {"cls": None, "x_m": 4.0, "y_m": 5.0, "z_m": 6.0, "yaw": None}
    # A four-column row is what every projection cut before schema 12 holds. It is not
    # short and it is not broken: it is a piece whose facing was never recorded, and null
    # is the only answer that does not turn that into a claim of "axis-aligned".
    assert body["structures"][2] == {
        "cls": "Build_Foundation_8x1_01_C",
        "x_m": 7.0,
        "y_m": 8.0,
        "z_m": 9.0,
        "yaw": None,
    }


def test_a_save_that_cannot_be_read_has_no_floor_plan_either(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/structures")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]


def test_placements_carry_the_yaw_the_map_has_to_draw_them_at(client, state):
    """Schema 12's rotation, on both surfaces that draw a rectangle.

    The endpoint's docstring used to promise the opposite -- "the quaternion is dropped,
    a client can only draw these axis-aligned" -- so this is asserted as a fact about the
    world rather than as a field being present: an angled slab has to survive the trip, or
    the map goes back to drawing staircases with no test noticing.
    """
    structures = client.get("/api/structures").json()["structures"]
    raw = state.projection["structures"]["instances"]
    assert structures[0]["yaw"] == pytest.approx(round(raw[0][4], 1))
    for row in structures:
        assert row["yaw"] is None or -180.0 <= row["yaw"] <= 180.0, row

    # The whole reason the drawing changes. A world built only on the cardinal grid would
    # let a broken rotation look perfect.
    angled = [r for r in structures if r["yaw"] and round(r["yaw"] % 90.0, 3) not in (0.0, 90.0)]
    assert len(angled) > 1000, "the reference world has several slabs laid at an angle"

    machines = client.get("/api/machines").json()
    rows = [row for kind in machines for row in machines[kind]]
    assert rows and all("yaw" in row for row in rows)
    assert any(row["yaw"] for row in rows), "every machine on this world faces north?"
    for row in rows:
        assert row["yaw"] is None or -180.0 <= row["yaw"] <= 180.0, row


def test_a_projection_from_before_schema_12_says_unknown_rather_than_zero(game):
    """``null``, not ``0.0``. The two draw the same and only one of them is a measurement,
    and an endpoint that filled the gap in with zero would make a world whose facings were
    never recorded indistinguishable from a world built entirely on the cardinal grid."""
    projection = {
        "structures": {"classes": ["Build_Foundation_8x1_01_C"], "instances": [[0, 1, 2, 3]]},
        "machines": [{"cls": "Build_ConstructorMk1_C", "instance": "x.y", "pos": [1, 2, 3]}],
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        assert c.get("/api/structures").json()["structures"][0]["yaw"] is None
        assert c.get("/api/machines").json()["machines"][0]["yaw"] is None
