"""``/api/floors``: the floor decomposition, over the committed fixture projection.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Both loaders are injected by the ``client`` fixture in ``conftest.py``, so nothing here
spawns the sidecar or reads a ``.sav``. The heightfield is the one other thing this
endpoint reads, and it is replaced too -- see ``blind_floors``.
"""

from __future__ import annotations

import types

import pytest

from tests.support.web import client_over

fastapi = pytest.importorskip("fastapi")


from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web import terrain as web_terrain

# --------------------------------------------------------------------- floors


@pytest.fixture
def blind_floors(client, monkeypatch):
    """The floors endpoint with no terrain field, which is what most machines have.

    Pinned rather than left to the machine: this repository ships no heightfield, so a test
    that passed only where somebody had run the generator would be a test of the generator.
    The one case that needs a field builds its own.

    Patched on ``interfaces.web.terrain`` -- the MODULE -- because that is the seam:
    ``routers/floors.py`` calls ``terrain.field()`` through the module rather than binding
    the function at import time, so a ``setattr`` here is what the endpoint sees.
    """
    monkeypatch.setattr(web_terrain, "field", lambda: None)
    return client


def test_floors_decompose_the_world_into_platforms_and_bands(blind_floors):
    """The endpoint's own shape, over the fixture world's 132 platforms."""
    body = blind_floors.get("/api/floors").json()
    assert body["note"] is None
    assert body["selection"] is None
    assert body["counts"]["platforms"] == 132
    assert body["counts"]["bands"] == 93
    assert len(body["platforms"]) == 132

    tower = next(p for p in body["platforms"] if p["index"] == 1)
    assert tower["cells"] == 335
    assert tower["area_m2"] == 335 * 64.0
    # Metres, one decimal, like every other coordinate on this surface.
    assert tower["centre_m"] == [pytest.approx(-474.0, abs=1.0), pytest.approx(-1489.0, abs=1.0)]
    # The deck heights themselves, one decimal: six storeys 12 m apart, the module this
    # world is actually built on. Not the 4 m the design assumed before it was measured.
    assert [b["top_m"] for b in tower["bands"]] == [-5.8, 30.2, 42.2, 54.2, 66.2, 78.2]
    assert [round(b["top_m"] - tower["bands"][1]["top_m"]) for b in tower["bands"][1:]] == [
        0,
        12,
        24,
        36,
        48,
    ]
    assert [b["ordinal"] for b in tower["bands"]] == [0, 1, 2, 3, 4, 5]
    assert [b["cells"] for b in tower["bands"]] == [111, 175, 175, 117, 218, 132]
    # A band is a level, not a smear -- so the spread of its own members is zero.
    assert all(b["span_m"] == 0.0 for b in tower["bands"])
    # And the decomposition's own premise travels with it, per platform.
    assert tower["clean"] == 1.0
    assert body["rules"]["tile_m"] == 8.0
    assert body["rules"]["riser_m"] == 6.0


def test_a_band_carries_instance_ids_rather_than_a_second_copy_of_the_geometry(blind_floors, state):
    """The shape stage 3 needs, and the one it must not be given.

    The page already holds every machine and splitter from ``/api/machines`` and
    ``/api/belts``. What it cannot derive is which floor each is on, so a band ships ids and
    nothing else -- no positions, no footprints, no yaw. Re-sending those would double a
    payload the client already has in order to filter the copy.
    """
    body = blind_floors.get("/api/floors").json()
    tower = next(p for p in body["platforms"] if p["index"] == 1)
    band = tower["bands"][4]
    assert set(band) == {
        "ordinal",
        "top_m",
        "low_m",
        "high_m",
        "span_m",
        "pieces",
        "cells",
        "area_m2",
        "share",
        "minor",
        "machines",
        "attachments",
        "deck_rows",
        "machine_count",
        "attachment_count",
        "deck_row_count",
    }
    assert band["machine_count"] == len(band["machines"]) > 0
    assert band["attachment_count"] == len(band["attachments"]) > 0
    assert band["deck_row_count"] == len(band["deck_rows"]) > 0

    # Every id resolves against a payload the page already has, which is the whole point.
    known = {
        str(r["instance"]).rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for r in state.projection[key]
    }
    assert set(band["machines"]) <= known
    splitters = {str(r["instance"]).rsplit(".", 1)[-1] for r in state.projection["attachments"]}
    assert set(band["attachments"]) <= splitters
    # No id is on two floors at once.
    everywhere = [
        i for p in body["platforms"] for b in p["bands"] for i in b["machines"] + b["attachments"]
    ]
    assert len(everywhere) == len(set(everywhere))


def test_a_deck_is_listed_by_position_because_concrete_has_no_instance_id(blind_floors):
    """The one join the concrete can carry, checked against the payload it points into.

    A lightweight buildable has no instance name, so ``deck_rows`` indexes
    ``/api/structures`` -- and this is the test that the two walks really are one order.
    Every row named must be a foundation whose top surface is the band's own level, which
    would break the moment either side started or stopped skipping a piece.
    """
    body = blind_floors.get("/api/floors").json()
    pieces = blind_floors.get("/api/structures").json()["structures"]
    tower = next(p for p in body["platforms"] if p["index"] == 1)

    seen: set[int] = set()
    for band in tower["bands"]:
        assert band["deck_rows"], "a band with no deck is not a band"
        for row in band["deck_rows"]:
            piece = pieces[row]
            assert "Foundation" in piece["cls"] or "Platform" in piece["cls"], piece["cls"]
            # z is the piece's CENTRE, so the deck is half a thickness up -- the stage-0
            # correction, read here from the other end of the wire.
            thickness = 4.0 if "8x4" in piece["cls"] else 2.0 if "8x2" in piece["cls"] else 1.0
            assert piece["z_m"] + thickness / 2 == pytest.approx(
                band["top_m"], abs=body["rules"]["band_eps_m"]
            )
        seen |= set(band["deck_rows"])
    # One piece, one floor: overlapping decks would draw a storey twice.
    assert sum(len(b["deck_rows"]) for b in tower["bands"]) == len(seen) == 949


def test_a_mezzanine_is_reported_minor_rather_than_merged_away(blind_floors):
    """Cell area is what tells a six-cell ledge from a 276-cell deck, so it is sent."""
    body = blind_floors.get("/api/floors").json()
    plat = next(p for p in body["platforms"] if p["index"] == 2)
    ledge, deck = plat["bands"]
    assert (ledge["cells"], deck["cells"]) == (6, 276)
    assert ledge["area_m2"] == 384.0
    assert ledge["minor"] is True and deck["minor"] is False
    assert ledge["share"] < body["rules"]["minor_share"] <= deck["share"]


def test_runs_are_grouped_by_what_they_do_to_a_floor(blind_floors, state):
    """Four groups, keyed by the join the belts and pipes payloads already carry."""
    body = blind_floors.get("/api/floors").json()
    runs = body["runs"]
    assert set(runs) == {"same-deck", "connector", "terrain", "mixed"}
    assert sum(len(v) for v in runs.values()) == body["counts"]["runs"] == 2412

    belts = [r for group in runs.values() for r in group if r["kind"] == "belt"]
    assert len(belts) == 1909 < len(state.projection["belts"]["segments"]), (
        "pieces are grouped into chains before anything vertical is asked of them"
    )
    assert {r["key"] for r in belts} == {s[0] for s in state.projection["belts"]["segments"]}
    pipes = [r for group in runs.values() for r in group if r["kind"] == "pipe"]
    assert sorted(r["key"] for r in pipes) == list(
        range(len(state.projection["pipes"]["segments"]))
    )

    # Connectors are how you leave a floor, and they name both ends.
    assert len(runs["connector"]) == 130
    for row in runs["connector"]:
        head, tail = row["ends"]
        assert head is not None and tail is not None
        assert (head["platform"], head["ordinal"]) != (tail["platform"], tail["ordinal"])
        assert head["top_m"] == pytest.approx(round(head["top_m"], 1))
    # A terrain run has no deck at either end, and says so with nulls rather than zeros.
    assert all(row["ends"] == [None, None] for row in runs["terrain"])
    # The riser flag is not the lift flag: a quarter of lift chains never change floor.
    jogs = [r for r in runs["same-deck"] if r["lift"]]
    assert jogs and all(not r["riser"] for r in jogs)
    assert body["violations"] == []


def test_placements_are_only_the_things_that_did_not_land_on_a_floor(blind_floors):
    """On-floor things are listed inside their band, so listing them here too would be the
    same 1,252 rows twice. What is here is the three ways of not being on a floor."""
    body = blind_floors.get("/api/floors").json()
    assert set(body["placements"]) == {"exempt", "terrain", "off-deck"}
    exempt = body["placements"]["exempt"]
    assert {r["cls"] for r in exempt} == {
        "Build_MinerMk1_C",
        "Build_MinerMk2_C",
        "Build_OilPump_C",
        "Build_WaterPump_C",
    }
    # Resolved to a display name like every other class on this surface.
    assert all(row["name"] and not row["name"].startswith("Build_") for row in exempt)
    assert all(row["x_m"] is not None and abs(row["x_m"]) < 5000 for row in exempt)

    # Without a field nothing can be MEASURED onto the ground, and that is stated rather
    # than left to be inferred from an empty list.
    assert body["terrain_measured"] is False
    assert body["placements"]["terrain"] == []
    assert body["placements"]["off-deck"]
    assert all(r["above_terrain_m"] is None for r in body["placements"]["off-deck"])


def test_a_terrain_field_turns_off_deck_into_a_measurement(client, monkeypatch):
    """With a field, "on the ground" stops being a guess -- and the flag says a field ran."""

    class _Flat:
        def at(self, x, y):
            del x, y
            return types.SimpleNamespace(z_m=80.0)

    monkeypatch.setattr(web_terrain, "field", _Flat)
    body = client.get("/api/floors").json()
    assert body["terrain_measured"] is True
    grounded = body["placements"]["terrain"]
    assert grounded, "a flat field at 80 m catches this world's ground-built machines"
    assert all(abs(r["above_terrain_m"]) <= 2.0 for r in grounded)
    assert all(r["above_terrain_m"] is not None for r in body["placements"]["off-deck"])


def test_floors_narrow_to_one_platform_and_to_a_named_factory(blind_floors, state):
    """A floor picker asks about one factory, and the two ways of naming it agree."""
    one = blind_floors.get("/api/floors?platform=1").json()
    assert one["selection"] == "platform 1"
    assert [p["index"] for p in one["platforms"]] == [1]
    assert one["counts"]["bands"] == 6
    assert one["counts"]["runs"] < 2412

    label = state.labels.labels[0].name if state.labels.labels else None
    if label:
        named = blind_floors.get("/api/floors", params={"factory": label}).json()
        assert named["selection"] == f"factory {label!r}"
        assert named["platforms"], f"{label} stands on something"


def test_asking_for_a_platform_or_a_factory_that_is_not_there_is_a_4xx(blind_floors):
    """An empty 200 would draw an empty floor picker and say nothing about why."""
    r = blind_floors.get("/api/floors?platform=9999")
    assert r.status_code == 404
    assert "no platform matches" in r.json()["error"]

    r = blind_floors.get("/api/floors", params={"factory": "no such factory"})
    assert r.status_code == 400
    assert "error" in r.json()
    assert "Named factories" in r.json()["error"]


def test_a_save_too_old_for_floors_says_so_with_a_200(game):
    """Not an error and not an empty list. The world has floors; this file cannot show them.

    Three shapes, because the projection has carried all three, exactly as
    ``/api/structures`` next door is asserted.
    """
    for projection in ({}, {"structures": {}}, {"structures": {"classes": [], "instances": []}}):
        with client_over(WorldState(projection=projection, game=game), game) as c:
            r = c.get("/api/floors")
        assert r.status_code == 200
        body = r.json()
        assert "predates lightweight buildables" in body["note"]
        assert body["platforms"] == []
        assert body["counts"]["bands"] == 0
        assert all(rows == [] for rows in body["runs"].values())
