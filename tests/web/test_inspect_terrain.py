"""``/api/inspect``'s terrain half: which layer of the field answered, and why it is silent.

The field loader is replaced rather than the data directory pointed elsewhere, so these run
the same on a machine that has a real field and on one that never had one.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.interfaces.web import terrain as web_terrain
from tests.support.heightfields import FAKE_SPACING, FAKE_X0, FAKE_Y0, build_field
from tests.support.web import client_over


def test_the_inspect_endpoint_says_which_source_answered(tmp_path, monkeypatch):
    """The popup's whole claim, over the wire: a number, its layer, and that layer's error."""
    field = hf.load_field(build_field(tmp_path))
    monkeypatch.setattr(web_terrain, "field", lambda: field)
    # The synthetic field is pinned at the map's south-west corner, so ask about a point
    # inside it in metres -- which is the unit the endpoint takes and the popup prints.
    x_m, y_m = FAKE_X0 / 100.0, FAKE_Y0 / 100.0

    with client_over(None, None) as client:
        body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()

    e = body["elevation"]
    assert e["terrain_m"] == 12.3
    assert e["terrain_source"] == "landscape"
    assert e["terrain_accuracy_m"] == 0.205
    assert e["terrain_note"] is None
    assert e["terrain_water_m"] is None
    assert e["terrain_water_depth_m"] is None and e["terrain_water_note"] is None
    # And the populations are still there, still separate, still labelled.
    assert "ground_m" in e and "ground_count" in e and "fill_note" in e


def test_the_endpoint_sends_a_water_level_without_a_depth_where_it_has_no_depth(
    tmp_path, monkeypatch
):
    """The ocean over the wire: a surface height, a null depth, and the reason for the null.

    The two water rows are asked about in one test because the contrast is the claim. Over
    1 m terrain the panel gets both numbers; over the fill layer it gets the level, no
    depth, and a sentence naming the layer that cannot supply one. A 0.0 there would read
    as a measurement of nothing, which is the failure ``fill_note`` already argued about.
    """
    field = hf.load_field(build_field(tmp_path))
    monkeypatch.setattr(web_terrain, "field", lambda: field)
    with client_over(None, None) as client:
        asked = {}
        for label, row in (("lake", 4), ("sea", 5)):
            params = {"x_m": FAKE_X0 / 100.0, "y_m": (FAKE_Y0 + row * FAKE_SPACING) / 100.0}
            asked[label] = client.get("/api/inspect", params=params).json()["elevation"]

    assert asked["lake"]["terrain_water_m"] == 2.0
    assert asked["lake"]["terrain_water_depth_m"] == 17.0
    assert asked["lake"]["terrain_water_note"] is None

    assert asked["sea"]["terrain_water_m"] == -17.0, "the sea surface was dropped as dry"
    assert asked["sea"]["terrain_water_depth_m"] is None, "an unmeasured depth was sent"
    assert "fill" in (asked["sea"]["terrain_water_note"] or ""), "the null carries no reason"


def test_the_endpoint_says_WHY_there_is_no_terrain_rather_than_leaving_a_null(monkeypatch):
    """A null with no reason beside it reads as a bug, exactly as ``fill_note`` decided.

    Two causes, and they call for different actions from the reader: no field on this
    machine means "run the generator", and no data at this point means "there is nothing
    there". So they are different sentences.
    """
    monkeypatch.setattr(web_terrain, "field", lambda: None)
    with client_over(None, None) as client:
        body = client.get("/api/inspect", params={"x_m": 0.0, "y_m": 0.0}).json()

    e = body["elevation"]
    assert e["terrain_m"] is None and e["terrain_source"] is None
    assert "no terrain field on this machine" in e["terrain_note"]
    assert "gen_world_heightmap.py" in e["terrain_note"]
