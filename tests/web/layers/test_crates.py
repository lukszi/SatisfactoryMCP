"""``/api/crates``: the death and dismantle crates on the ground, and what is in each one."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.world.state import WorldState
from tests.support.web import client_over


def test_crates_are_every_crate_on_the_ground_with_what_is_in_it(client, state):
    """The shape, and the join that is the whole point of the endpoint.

    Checked against the projection row by row so that a re-ordering or an off-by-one join is
    visible here rather than on the map. The reference world holds two, which is small enough
    that the pair can be pinned exactly -- and the pair is the interesting one, because one
    crate knows what kind it is and the other cannot.
    """
    body = client.get("/api/crates").json()
    raw = state.projection["crates"]
    assert body["count"] == len(raw) == len(body["crates"]) == 2
    assert body["deaths"] == 0, "nobody has died in this world since the last crate expired"
    assert body["items_total"] == 48

    for row, source in zip(body["crates"], raw, strict=True):
        assert row["cls"] == source["cls"] == "BP_Crate_C"
        assert row["instance_leaf"] == source["instance"].rsplit(".", 1)[-1]
        assert row["kind"] == source["kind"]
        assert row["x_m"] == pytest.approx(round(source["pos"][0] / 100.0, 1))
        assert row["yaw"] == pytest.approx(round(source["yaw"], 1))
        assert row["slots"] == source["slots"]
        # No display name is ever an engine id, here or anywhere on this surface.
        for item in row["items"]:
            assert item["cls"].startswith("Desc_")
            assert item["name"] and not item["name"].startswith("Desc_")


def test_a_crate_says_which_kind_it_is_and_the_third_kind_is_an_answer(client):
    """``none`` is ``CT_None``, the game's own value, and it gets a sentence of its own.

    The reference world holds one dismantle crate and one crate that predates the property
    entirely, which is exactly the pair that makes the field worth sending: a client drawing
    a skull for a death and a wrench for a dismantle needs a third glyph, and a reader who
    meets ``none`` needs to be told it is not a parse failure.
    """
    rows = client.get("/api/crates").json()["crates"]
    assert [r["kind"] for r in rows] == ["dismantle", "none"], "sorted by kind, then instance"
    for r in rows:
        assert r["kind_text"], "every kind this build knows about is glossed"
    assert "predates" in rows[1]["kind_text"]
    assert rows[0]["items"][0] == {"cls": "Desc_IronPlate_C", "name": "Iron Plate", "count": 15}
    assert rows[1]["items"] == [
        {"cls": "Desc_Coal_C", "name": "Coal", "count": 17},
        {"cls": "Desc_Cement_C", "name": "Concrete", "count": 7},
    ]


def test_a_full_death_crate_is_sent_whole_and_says_nothing_was_left_out(game):
    """All 38 kinds shown, and ``more`` says 0 -- the cap is gone and this pins its absence.

    There used to be a twelve-kind truncation here, justified as "what a popup can show
    without scrolling"; the popup renders an inventory grid now, measured to hold exactly
    this crate -- the fullest one on this machine, 38 kinds in 55 slots -- without overflow,
    so the whole list goes out. ``more`` stays in the row at its honest value, because the
    client's "+N more" tile keys on it and a server that starts truncating again owes it a
    real count rather than a removed field.
    """
    items = [[f"Desc_Thing{i:02d}_C", 100 - i] for i in range(38)]
    body = _crates_body({"crates": [_crate(items=items, slots=55)]}, game)
    row = body["crates"][0]
    assert len(row["items"]) == 38, "the whole crate, not the biggest twelve"
    assert row["item_kinds"] == 38
    assert row["more"] == 0, "nothing left off, and the row says so rather than dropping the field"
    assert row["total"] == sum(100 - i for i in range(38))
    assert row["slots"] == 55
    counts = [i["count"] for i in row["items"]]
    assert counts == sorted(counts, reverse=True), "biggest first, as the projection sorted them"
    assert body["deaths"] == 1


def test_a_crate_has_no_footprint_and_does_not_pretend_to(client):
    """The refusal ``/api/storage`` makes for the four classes the dump cannot size.

    A crate is not a buildable, so the docs dump carries no clearance for it and none is
    invented here. Asserted as an ABSENCE rather than as a null, because a ``"w_m": null`` on
    every row would invite a client to print it.
    """
    for row in client.get("/api/crates").json()["crates"]:
        assert "w_m" not in row and "l_m" not in row


def test_a_world_where_nobody_has_died_answers_with_an_empty_payload(game):
    """A save with no crates in it is not an error -- the storage rule, and the belts' rule."""
    for projection in ({}, {"crates": []}, {"crates": None}):
        with client_over(WorldState(projection=projection, game=game), game) as c:
            assert c.get("/api/crates").json() == {
                "crates": [],
                "count": 0,
                "deaths": 0,
                "items_total": 0,
            }


def test_a_malformed_crate_row_costs_that_row_and_not_the_others(game):
    """Raw projection data, read guarded field by field -- the structures rule, again.

    The last row is the forward-compatible case and the one worth having: a projection cut by
    a later extractor that learned a fourth ``EFGCrateType`` value is still served, with the
    word it used and no gloss, rather than 500ing on a key this build has not heard of.
    """
    projection = {
        "crates": [
            _crate(items=[["Desc_IronPlate_C", 15], "not an entry", ["Desc_Cement_C"]], slots=2),
            {"cls": "BP_Crate_C", "instance": "i", "pos": None, "yaw": None, "kind": "dismantle"},
            "not a row",
            {**_crate(), "kind": "CT_SomethingNew"},
        ]
    }
    body = _crates_body(projection, game)
    assert body["count"] == 3
    first = body["crates"][0]
    assert first["items"] == [{"cls": "Desc_IronPlate_C", "name": "Iron Plate", "count": 15}]
    assert (first["item_kinds"], first["more"], first["total"]) == (1, 0, 15)
    assert first["x_m"] == 1.0
    # A row with no position at all is still sent: the projection knows the crate exists, and
    # a client that skips it on x is making that call for itself.
    assert body["crates"][1]["x_m"] is None
    assert body["crates"][1]["items"] == []
    assert body["crates"][2]["kind"] == "CT_SomethingNew"
    assert body["crates"][2]["kind_text"] is None


def test_crates_takes_the_save_and_world_parameters_and_404s_on_an_unreadable_one(game):
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
        return WorldState(projection={"crates": []}, game=game)

    with client_over(loader, game) as c:
        assert c.get("/api/crates?world=Han%20Solo&save=x.sav").status_code == 200
        bad = c.get("/api/crates?world=nope")
    assert asked[0] == ("x.sav", "Han Solo"), "the query never reached the loader"
    assert bad.status_code == 404
    assert "no world matching" in bad.json()["error"]


def _crate(items=None, slots=1, kind="death") -> dict:
    return {
        "cls": "BP_Crate_C",
        "instance": "x.BP_Crate_C_1",
        "pos": [100, 200, 300],
        "yaw": -90.0,
        "kind": kind,
        "items": items if items is not None else [["Desc_Wire_C", 3]],
        "slots": slots,
    }


def _crates_body(projection: dict, game) -> dict:
    with client_over(WorldState(projection=projection, game=game), game) as c:
        return c.get("/api/crates").json()
