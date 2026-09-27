"""``/api/storage``: every container and fluid buffer, and what is inside each one.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``,
or by building its own app around a hand-written projection -- so nothing in this file
spawns the sidecar, reads a ``.sav`` or needs the save directory to exist.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app


def test_storage_is_every_container_and_buffer_with_what_is_in_it(client, state):
    """The shape, and the join that is the whole point of the endpoint.

    A container's contents are ITS OWN -- the projection joins each ``StorageInventory``
    component to the actor that owns it -- so this is the first payload able to answer "where
    is the steel" rather than only "how much steel". Checked against the projection row by row
    so that a re-ordering or an off-by-one join is visible here rather than on the map.
    """
    body = client.get("/api/storage").json()
    raw = state.projection["storage"]
    assert body["count"] == len(raw) == len(body["storage"]) == 151
    assert body["filled"] == 125, "the containers the player has actually put something in"
    assert body["items_total"] > 100_000

    for row, source in zip(body["storage"], raw, strict=True):
        assert row["cls"] == source["cls"]
        assert row["instance_leaf"] == source["instance"].rsplit(".", 1)[-1]
        assert row["kind"] in ("solid", "fluid")
        assert row["x_m"] == pytest.approx(round(source["pos"][0] / 100.0, 1))
        assert row["yaw"] == pytest.approx(round(source["yaw"], 1))
        # No display name is ever an engine id, here or anywhere on this surface.
        assert row["name"] and not row["name"].startswith("Build_")


def test_a_solid_container_names_its_whole_contents_and_leaves_nothing_out(client):
    """Items resolved to display names, biggest first, and the WHOLE box.

    There used to be a six-kind truncation here, and the busiest container -- a catch-all
    box holding 12 kinds -- is why the removal is worth pinning against the real world: a
    regression that reintroduced a cap would show on exactly that box, as a ``more`` above 0
    and a list shorter than ``item_kinds``. The popup's inventory grid was measured holding
    38 kinds without overflow, so nothing a 48-slot container can hold needs counting instead
    of showing.
    """
    rows = [r for r in client.get("/api/storage").json()["storage"] if r["kind"] == "solid"]
    assert len(rows) == 146
    for r in rows:
        assert set(r) >= {"items", "more", "item_kinds", "total", "slots"}
        assert "stored_m3" not in r and "fluid" not in r, "a box has no fluid level"
        assert len(r["items"]) == r["item_kinds"], "the whole box, not the biggest six"
        assert r["more"] == 0, "nothing is ever left off, and every row says so"
        counts = [i["count"] for i in r["items"]]
        assert counts == sorted(counts, reverse=True), "biggest first"
        for item in r["items"]:
            assert item["cls"].startswith("Desc_")
            assert item["name"] and not item["name"].startswith("Desc_")
        assert r["total"] == sum(counts), "items is the whole box, so the sum IS the total"
    assert max(r["item_kinds"] for r in rows) == 12, (
        "the catch-all box the old cap existed for -- the row the removal is measured on"
    )


def test_a_fluid_buffer_reports_a_level_against_the_capacity_that_makes_it_a_reading(client):
    """``stored_m3`` alone is a number; ``fill`` is the answer.

    The capacity comes off the docs dump -- ``mStorageCapacity``, 400 on a Fluid Buffer and
    2,400 on an Industrial one -- because the save records only the level. Without it a popup
    saying "1,730.6 m3" leaves the reader to know how big the tank is.
    """
    rows = [r for r in client.get("/api/storage").json()["storage"] if r["kind"] == "fluid"]
    assert len(rows) == 5
    for r in rows:
        assert set(r) >= {"fluid", "fluid_name", "stored_m3", "capacity_m3", "fill"}
        assert "items" not in r and "slots" not in r, "a tank has no slots"
        assert r["capacity_m3"] in (400.0, 2400.0)
        assert r["fill"] == pytest.approx(r["stored_m3"] / r["capacity_m3"], abs=1e-4)
        assert 0.0 <= r["fill"] <= 1.0
        assert r["fluid_name"] and not r["fluid_name"].startswith("Desc_")
    assert max(r["fill"] for r in rows) > 0.9, "one of them is nearly full"


def test_a_storage_row_carries_the_footprint_it_is_drawn_at_or_says_it_cannot(client):
    """Same contract as the machines: a measured footprint, or null and the client's fallback.

    Two of the eight classes here are absent from the docs dump entirely -- the HUB's built-in
    container and the Blueprint Designer's -- so they get null rather than a number invented
    server-side, which would arrive indistinguishable from a measurement. The Dimensional Depot
    uploader sits in the dump under a native of its own, and is measured.
    """
    rows = client.get("/api/storage").json()["storage"]
    measured = {r["cls"] for r in rows if r["w_m"] is not None}
    unmeasured = {r["cls"] for r in rows if r["w_m"] is None}
    assert {"Build_StorageContainerMk1_C", "Build_CentralStorage_C"} <= measured
    assert unmeasured == {
        "Build_StorageBlueprint_C",
        "Build_StorageIntegrated_C",
    }
    for r in rows:
        assert (r["w_m"] is None) == (r["l_m"] is None), "half a footprint is not a footprint"
        if r["w_m"] is not None:
            assert 0 < r["w_m"] < 100 and 0 < r["l_m"] < 100


def test_a_world_with_nothing_in_store_answers_with_an_empty_payload(game):
    """A young save has built no container, and that is not an error -- the belts' rule."""
    for projection in ({}, {"storage": []}, {"storage": None}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            assert c.get("/api/storage").json() == {
                "storage": [],
                "count": 0,
                "filled": 0,
                "items_total": 0,
            }


def test_a_malformed_storage_row_costs_that_row_and_not_the_warehouse(game):
    """Raw projection data, read guarded field by field -- the structures rule, again."""
    projection = {
        "storage": [
            {
                "cls": "Build_StorageContainerMk1_C",
                "instance": "x.Build_StorageContainerMk1_C_1",
                "pos": [100, 200, 300],
                "yaw": 90.0,
                "items": [["Desc_IronPlate_C", 4800], "not an entry", ["Desc_Cement_C"]],
                "slots": 24,
            },
            {"cls": "Build_StorageContainerMk1_C", "instance": "i", "pos": None, "yaw": None},
            "not a row",
            {
                "cls": "Build_PipeStorageTank_C",
                "instance": "t",
                "pos": [0, 0, 0],
                "yaw": 0.0,
                "fluid": None,
                "stored_m3": None,
            },
        ]
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/storage").json()
    assert body["count"] == 3
    first = body["storage"][0]
    assert first["items"] == [{"cls": "Desc_IronPlate_C", "name": "Iron Plate", "count": 4800}]
    assert (first["item_kinds"], first["more"], first["total"]) == (1, 0, 4800)
    assert first["x_m"] == 1.0
    # A row with no position at all is still sent: the projection knows the container exists,
    # and a client that skips it on x is making that call for itself.
    assert body["storage"][1]["x_m"] is None
    assert body["storage"][1]["items"] == []
    # An unreadable fluid level is null, and `fill` refuses rather than dividing by it.
    assert body["storage"][2]["stored_m3"] is None
    assert body["storage"][2]["fill"] is None
    assert body["storage"][2]["capacity_m3"] == 400.0


def test_storage_takes_the_save_and_world_parameters_and_404s_on_an_unreadable_one(game):
    """The ``?save`` / ``?world`` contract every endpoint here shares.

    Both halves matter: the parameters have to REACH the loader -- an endpoint that quietly
    ignored ``?world`` would serve the default world under another world's name -- and a
    loader that refuses has to come back as a 404 with a message rather than as a 500.
    """
    asked: list[tuple] = []

    def loader(save=None, world=None):
        asked.append((save, world))
        if world == "nope":
            raise RuntimeError("no world matching 'nope'")
        return WorldState(projection={"storage": []}, game=game)

    app = create_app(state_loader=loader, game_loader=lambda: game)
    with TestClient(app) as c:
        assert c.get("/api/storage?world=Han%20Solo&save=x.sav").status_code == 200
        bad = c.get("/api/storage?world=nope")
    assert asked[0] == ("x.sav", "Han Solo"), "the query never reached the loader"
    assert bad.status_code == 404
    assert "no world matching" in bad.json()["error"]
