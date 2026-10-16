"""``/api/map-markers``: the markers the player placed on the in-game map."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.routers.layers.map_markers import srgb_hex


def test_the_markers_come_in_the_saves_order_with_their_place_and_colour(client, state):
    body = client.get("/api/map-markers").json()
    raw = state.projection["map_markers"]
    assert body["count"] == len(body["markers"]) == len(raw) == 6
    for row, source in zip(body["markers"], raw, strict=True):
        assert row["name"] == source["name"]
        assert row["x_m"] == pytest.approx(round(source["pos"][0] / 100.0, 1))
        assert row["y_m"] == pytest.approx(round(source["pos"][1] / 100.0, 1))
        assert row["colour"] and row["colour"].startswith("#") and len(row["colour"]) == 7
        assert row["icon_id"] == source["icon_id"]
        assert row["type"] == "RT_MapMarker"
    assert body["markers"][0]["name"] == "caterium"
    assert body["markers"][0]["compass"] == "CVD_Far"


def test_a_linear_colour_is_sent_as_srgb():
    assert srgb_hex([0.0, 0.0, 0.0, 1.0]) == "#000000"
    assert srgb_hex([1.0, 1.0, 1.0, 0.5]) == "#ffffff"
    # Linear 0.216 is sRGB 0.502: the game's light greys must not arrive dark.
    assert srgb_hex([0.2159, 0.0, 2.0, 1.0]) == "#8000ff"
    assert srgb_hex(None) is None
    assert srgb_hex([0.5]) is None


def test_a_projection_without_markers_answers_with_none(client, state, monkeypatch):
    monkeypatch.delitem(state.projection, "map_markers")
    assert client.get("/api/map-markers").json() == {"markers": [], "count": 0}
