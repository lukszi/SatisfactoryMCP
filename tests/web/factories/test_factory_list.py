"""``/api/factories``: the player's own labels, and the proposals for the unnamed rest.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Both loaders are injected by the ``client`` fixture in ``conftest.py``, so nothing here
spawns the sidecar or reads a ``.sav``.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.routers.factories import factory_list as web_factories

# ------------------------------------------------------------------ factories


def test_factories_report_named_labels_and_proposals(client, state):
    body = client.get("/api/factories").json()
    assert len(body["labels"]) == len(state.labels.labels)
    assert body["proposals"], "the fixture world has proposable factories"
    first = body["proposals"][0]
    assert set(first) >= {"index", "label", "centroid_m", "machines", "score"}
    # The index is the row's position in the FULL proposal list -- named clusters are
    # filtered out ahead of it -- so it must still resolve as a proposal:N selector.
    assert first["machines"] == state.proposals[first["index"]].size
    assert len(first["centroid_m"]) == 2
    # Centroids are metres too, and the world is roughly 7 km across.
    assert abs(first["centroid_m"][0]) < 5000


def test_a_factory_carries_the_box_its_machines_occupy(client, state):
    """``bbox_m`` is what turns a label into a button: the map flies to a factory's own
    extent, and a centroid alone cannot decide a zoom. Every anchor still standing has to
    lie inside the box, in metres, or the viewport it produces cuts machines off.
    """
    from satisfactory_mcp.domain.factories import candidates

    body = client.get("/api/factories").json()
    placed = candidates.positions(state.projection)
    by_name = {label.name: label for label in state.labels.labels}

    for row in body["labels"]:
        anchors = [a for a in by_name[row["name"]].anchors if a in placed]
        if not anchors:
            assert row["bbox_m"] is None
            continue
        x_min, y_min, x_max, y_max = row["bbox_m"]
        assert abs(x_min) < 5000 and abs(y_max) < 5000, "metres, not the save's centimetres"
        cx, cy = row["centroid_m"]
        assert x_min <= cx <= x_max and y_min <= cy <= y_max
        for name in anchors:
            x, y = placed[name][0] / 100.0, placed[name][1] / 100.0
            assert x_min - 0.1 <= x <= x_max + 0.1
            assert y_min - 0.1 <= y <= y_max + 0.1

    proposal = body["proposals"][0]
    x_min, y_min, x_max, y_max = proposal["bbox_m"]
    # A box no wider than the diameter the same row already reports: the two are computed
    # from the same machines, so a disagreement means one of them is stale.
    assert max(x_max - x_min, y_max - y_min) <= proposal["spread_m"] + 0.2


def test_a_proposal_the_player_already_named_is_not_proposed_again(client, state, monkeypatch):
    """The clusterer re-discovers every named factory; the endpoint must not re-offer
    them. A proposal whose machines are majority-covered by a label's anchors would draw
    a machine-generated recipe string exactly on top of the player's own name -- and take
    its clicks, since the proposal layer is added later."""
    labelled = {a for label in state.labels.labels for a in label.anchors}
    body = client.get("/api/factories").json()
    for row in body["proposals"]:
        pr = state.proposals[row["index"]]
        overlap = sum(1 for m in pr.machines if m in labelled)
        assert 2 * overlap <= len(pr.machines), (row["index"], overlap, len(pr.machines))
    # The indices keep their position in the full list, so proposal:N still resolves.
    shown = [row["index"] for row in body["proposals"]]
    assert shown == sorted(shown)
    if len(shown) < len(state.proposals):
        assert set(shown) < set(range(len(state.proposals)))


def test_a_factory_whose_machines_are_all_gone_has_no_box_to_fly_to(labelled_client, monkeypatch):
    """A label outlives its machines -- that is the point of anchoring to instance ids --
    so the honest answer is a name with nowhere to go, not a zero box at the world centre
    that would fly the map to (0, 0) and read as a bug in the projection."""
    monkeypatch.setattr(web_factories.candidates, "positions", lambda projection: {})
    body = labelled_client.get("/api/factories").json()
    assert body["labels"], "the labels survive; only their positions are gone"
    assert all(row["bbox_m"] is None for row in body["labels"])
