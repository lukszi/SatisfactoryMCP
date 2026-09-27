"""``/api/collectibles``: the same filtering the MCP tool does, over the fixture world.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Both loaders are injected by the ``client`` fixture in ``conftest.py``, so nothing here
spawns the sidecar or reads a ``.sav``.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.interfaces.web.app import create_app

# ---------------------------------------------------------------- collectibles


def test_collectibles_list_remaining_placements(client):
    body = client.get("/api/collectibles", params={"mode": "remaining"}).json()
    assert body["mode"] == "remaining"
    assert body["rows"]
    row = body["rows"][0]
    assert set(row) >= {"category", "name", "x_m", "y_m", "z_m", "collected", "observed"}
    assert all(r["collected"] is False for r in body["rows"])


def test_collectibles_can_be_scoped_to_one_group_and_to_collected(client):
    body = client.get(
        "/api/collectibles", params={"mode": "collected", "group": "power_slug_blue"}
    ).json()
    assert body["group"] == "power_slug_blue"
    assert {r["category"] for r in body["rows"]} == {"power_slug_blue"}
    assert all(r["collected"] for r in body["rows"])


def test_only_a_standing_pod_carries_a_loot_flag(client):
    """The contract ``CollectibleRow`` states, checked against the whole reference table.

    Three states reach the wire and the invariant is what keeps null single-valued: the flag
    is a live pod body's own ``mHasBeenLooted``, so it is there exactly when some save has had
    that pod loaded -- which is what ``observed == "standing"`` says. Anywhere else, including
    every category that has no such property, null means the flag was never read.
    """
    rows = client.get("/api/collectibles", params={"mode": "remaining"}).json()["rows"]
    pods = [r for r in rows if r["category"] == "crashed_drop_pod"]
    assert {r["looted"] for r in pods} == {True, False, None}, "the world has all three"
    assert all(r["looted"] is None for r in rows if r["category"] != "crashed_drop_pod")
    for r in pods:
        assert (r["looted"] is not None) == (r["observed"] == "standing"), r


def test_a_collected_pod_says_nothing_about_what_it_held(client):
    """A dismantled pod is not a looted one, and the save records only that it is gone."""
    rows = client.get(
        "/api/collectibles", params={"mode": "collected", "group": "crashed_drop_pod"}
    ).json()["rows"]
    assert rows
    assert all(r["looted"] is None for r in rows)


def test_an_unknown_view_is_refused_with_the_tools_own_wording(client):
    """The refusal names no parameter, because the two callers spell it differently: this
    wire says `mode=` and the MCP tool says `show=`, and one shared message cannot say both."""
    r = client.get("/api/collectibles", params={"mode": "sideways"})
    assert r.status_code == 400
    assert r.json()["error"].startswith("! unknown view 'sideways'")


def test_remaining_is_refused_when_the_map_table_is_absent(state, game, monkeypatch):
    """No table, no answer -- the same refusal the MCP tool gives, not a shorter list."""
    monkeypatch.setattr(type(state), "collectibles", property(lambda self: None))
    app = create_app(
        state_loader=lambda save=None, world=None: state,
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        r = c.get("/api/collectibles", params={"mode": "remaining"})
    assert r.status_code == 400
    assert "needs the map's own placement table" in r.json()["error"]


def test_every_mode_carries_the_census_with_labels_and_spoilers(client):
    for mode in ("census", "remaining", "collected"):
        body = client.get("/api/collectibles", params={"mode": mode}).json()
        assert body["census"], mode
        assert body["found"]
    census = {c["category"]: c for c in body["census"]}
    assert census["power_slug_blue"]["label"] == "blue power slugs"
    assert census["crashed_drop_pod"]["spoiler"] is False
    for c in census.values():
        assert c["spoiler"] == (
            c["category"] not in body["found"]
            and c["category"]
            not in (
                "crashed_drop_pod",
                "loot_cache",
            )
        )


def test_spoilers_zero_hides_unfound_categories_and_recounts(client):
    every = client.get("/api/collectibles", params={"mode": "remaining"}).json()
    hidden = client.get("/api/collectibles", params={"mode": "remaining", "spoilers": 0}).json()
    spoiled = {c["category"] for c in every["census"] if c["spoiler"]}
    assert every["hidden_spoilers"] == 0
    assert all(r["spoiler"] == (r["category"] in spoiled) for r in every["rows"])
    assert hidden["hidden_spoilers"] == len(spoiled)
    assert not any(r["spoiler"] for r in hidden["rows"])
    assert {c["category"] for c in hidden["census"]}.isdisjoint(spoiled)
    assert sum(hidden["counts"].values()) == len(hidden["rows"])
    if spoiled:
        assert len(hidden["rows"]) < len(every["rows"])


def test_the_table_age_follows_the_save_build(state, game):
    newer = type(state)(
        projection={**state.projection, "header": {**state.header, "build_version": 502094}},
        game=game,
    )
    app = create_app(state_loader=lambda save=None, world=None: newer, game_loader=lambda: game)
    with TestClient(app) as c:
        body_now = c.get("/api/collectibles", params={"mode": "census"}).json()
    stale = body_now["stale"]
    assert stale["table"] == "collectibles" and stale["behind"] is True
    assert stale["gap"] == "buildVersion 495413 -> 502094"
    assert stale["observed_matches"] is True
