"""``/api/labels/amend`` and ``/api/factories/machines``: amend a label by an area drawn on the map."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp import config
from satisfactory_mcp.domain.factories import edits
from satisfactory_mcp.domain.factories.labels import Label, LabelError, LabelStore
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.web import PAGE_ORIGIN


@pytest.fixture
def store_dir():
    return config.labels_dir()


def _stored(store_dir) -> dict:
    return json.loads(next(store_dir.glob("*.json")).read_text())


def _named(fresh_state_client):
    """Name the first candidate "steel"; return the detect reply and the second candidate."""
    body = fresh_state_client.get(
        "/api/factories/candidates", params={"fed_only": False, "min_machines": 1}
    ).json()
    first, second = body["candidates"][0], body["candidates"][1]
    reply = fresh_state_client.post(
        "/api/labels",
        json={"name": "steel", "proposal": first["index"], "as_of": body["token"], "version": 0},
        headers=PAGE_ORIGIN,
    )
    assert reply.status_code == 200
    return body, second


def _spots(fresh_state_client, **params):
    reply = fresh_state_client.get("/api/factories/machines", params=params)
    assert reply.status_code == 200, reply.text
    return reply.json()["machines"]


def _square(spot, half=0.5):
    x, y = spot["x_m"], spot["y_m"]
    return [[x - half, y - half], [x + half, y - half], [x + half, y + half], [x - half, y + half]]


def _amend(fresh_state_client, area, mode="add", version=1, dry_run=False, as_of=None, token=""):
    return fresh_state_client.post(
        "/api/labels/amend",
        json={
            "name": "steel",
            "area": area,
            "mode": mode,
            "as_of": as_of or token,
            "version": version,
            "dry_run": dry_run,
        },
        headers=PAGE_ORIGIN,
    )


def _lone(spots):
    """A spot no other spot shares its square with, so the area picks exactly one machine."""
    for s in spots:
        near = [
            t for t in spots if abs(t["x_m"] - s["x_m"]) <= 0.5 and abs(t["y_m"] - s["y_m"]) <= 0.5
        ]
        if len(near) == 1:
            return s
    pytest.skip("no isolated machine in the fixture")


def test_inside_is_an_even_odd_test():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert geo.inside((5, 5), square)
    assert not geo.inside((15, 5), square)
    notch = [(0, 0), (10, 0), (10, 10), (5, 5), (0, 10)]
    assert not geo.inside((5, 8), notch) and geo.inside((5, 3), notch)


def test_a_candidate_and_a_factory_list_their_machines_where_they_stand(fresh_state_client):
    body, second = _named(fresh_state_client)
    cand = _spots(fresh_state_client, candidate=second["selector"], token=body["token"])
    assert len(cand) == second["machines"]
    assert all(s["factory"] is None for s in cand)
    held = _spots(fresh_state_client, factory="steel")
    assert held and all(s["factory"] == "steel" for s in held)
    assert (
        fresh_state_client.get("/api/factories/machines", params={"factory": "nope"}).status_code
        == 404
    )
    stale = {"candidate": second["selector"], "token": "sav:000000000000"}
    assert fresh_state_client.get("/api/factories/machines", params=stale).status_code == 409


def test_a_dry_run_previews_and_writes_nothing(fresh_state_client, store_dir):
    body, second = _named(fresh_state_client)
    spot = _lone(_spots(fresh_state_client, candidate=second["selector"], token=body["token"]))
    before = _stored(store_dir)
    reply = _amend(fresh_state_client, _square(spot), dry_run=True, token=body["token"])
    assert reply.status_code == 200, reply.text
    out = reply.json()
    assert [s["id"] for s in out["added"]] == [spot["id"]] and not out["dropped"]
    assert out["after"] == out["before"] + 1 and not out["written"] and out["version"] == 1
    assert _stored(store_dir) == before


def test_an_area_adds_and_then_drops_a_machine(fresh_state_client, store_dir):
    body, second = _named(fresh_state_client)
    spot = _lone(_spots(fresh_state_client, candidate=second["selector"], token=body["token"]))
    added = _amend(fresh_state_client, _square(spot), token=body["token"]).json()
    assert added["written"] and added["version"] == 2
    assert spot["id"] in _stored(store_dir)["labels"][0]["anchors"]
    dropped = _amend(
        fresh_state_client, _square(spot), mode="drop", version=2, token=body["token"]
    ).json()
    assert [s["id"] for s in dropped["dropped"]] == [spot["id"]] and dropped["version"] == 3
    assert spot["id"] not in _stored(store_dir)["labels"][0]["anchors"]


def test_extra_areas_add_to_the_first(fresh_state_client, store_dir):
    body, second = _named(fresh_state_client)
    spots = _spots(fresh_state_client, candidate=second["selector"], token=body["token"])
    one = _lone(spots)
    two = _lone(
        [s for s in spots if abs(s["x_m"] - one["x_m"]) > 1 or abs(s["y_m"] - one["y_m"]) > 1]
    )
    reply = fresh_state_client.post(
        "/api/labels/amend",
        json={
            "name": "steel",
            "area": _square(one),
            "extra_areas": [_square(two)],
            "mode": "add",
            "as_of": body["token"],
            "version": 1,
            "dry_run": True,
        },
        headers=PAGE_ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    assert sorted(s["id"] for s in reply.json()["added"]) == sorted([one["id"], two["id"]])
    short = dict(
        name="steel",
        area=_square(one),
        extra_areas=[[[0, 0], [1, 1]]],
        mode="add",
        as_of=body["token"],
        version=1,
        dry_run=True,
    )
    assert (
        fresh_state_client.post("/api/labels/amend", json=short, headers=PAGE_ORIGIN).status_code
        == 400
    )


def test_an_area_with_nothing_new_writes_nothing(fresh_state_client, store_dir):
    body, _second = _named(fresh_state_client)
    out = _amend(fresh_state_client, [[0, 0], [0.1, 0], [0.1, 0.1]], token=body["token"]).json()
    assert not out["added"] and not out["written"] and out["version"] == 1


def test_amend_refuses_stale_moved_emptying_and_bad_requests(fresh_state_client, store_dir):
    body, second = _named(fresh_state_client)
    spot = _lone(_spots(fresh_state_client, candidate=second["selector"], token=body["token"]))
    flags = ("stale", "name_taken", "pin")
    stale = _amend(fresh_state_client, _square(spot), version=0, token=body["token"])
    assert stale.status_code == 409 and [stale.json()[k] for k in flags] == [True, False, False]
    moved = _amend(fresh_state_client, _square(spot), as_of="sav:000000000000")
    assert moved.status_code == 409 and moved.json()["pin"]
    everywhere = [[-1e6, -1e6], [1e6, -1e6], [1e6, 1e6], [-1e6, 1e6]]
    empty = _amend(fresh_state_client, everywhere, mode="drop", token=body["token"])
    assert empty.status_code == 409 and "no machines" in empty.json()["error"]
    assert (
        _amend(fresh_state_client, _square(spot), mode="swap", token=body["token"]).status_code
        == 400
    )
    assert _amend(fresh_state_client, [[0, 0], [1, 1]], token=body["token"]).status_code == 400
    lost = fresh_state_client.post(
        "/api/labels/amend",
        json={
            "name": "nope",
            "area": _square(spot),
            "mode": "add",
            "as_of": body["token"],
            "version": 1,
        },
        headers=PAGE_ORIGIN,
    )
    assert lost.status_code == 404
    assert _stored(store_dir)["version"] == 1


def test_amend_needs_an_origin(fresh_state_client):
    body, _second = _named(fresh_state_client)
    reply = fresh_state_client.post(
        "/api/labels/amend",
        json={
            "name": "steel",
            "area": [[0, 0], [1, 0], [1, 1]],
            "mode": "add",
            "as_of": body["token"],
            "version": 1,
        },
    )
    assert reply.status_code == 403


def test_the_page_and_the_tool_amend_to_the_same_label(
    fresh_state_client, store_dir, projection, game, monkeypatch
):
    from satisfactory_mcp.interfaces.mcp.tools import factories as tools

    monkeypatch.setattr(
        tools, "_state", lambda *a, **k: WorldState(projection=projection, game=game)
    )
    results = []
    for via in ("page", "tool"):
        for f in store_dir.glob("*.json"):
            f.unlink()
        body, second = _named(fresh_state_client)
        spot = _lone(_spots(fresh_state_client, candidate=second["selector"], token=body["token"]))
        if via == "page":
            assert _amend(fresh_state_client, _square(spot), token=body["token"]).json()["written"]
        else:
            assert "amended" in tools.amend_factory("steel", add=[f"machine:{spot['id']}"])
        results.append(_stored(store_dir)["labels"])
    assert results[0] == results[1]


def test_the_amend_route_declares_every_refusal_it_sends(fresh_state_client):
    declared = fresh_state_client.get("/openapi.json").json()["paths"]["/api/labels/amend"]["post"][
        "responses"
    ]
    assert {"400", "404", "409"} <= set(declared)


def test_plan_amend_leaves_the_label_as_it_was():
    store = LabelStore(world_id="w")
    label = Label(id="a", name="a", anchors=["m1", "m2"])
    other = Label(id="b", name="b", anchors=["m3"])
    store.labels = [label, other]
    plan = edits.preview_amendment(store, label, ["m3"], {"m1"}, {"m1", "m2", "m3"})
    assert plan.added == ["m3"] and plan.dropped == ["m1"] and plan.after == ["m2", "m3"]
    assert plan.overlaps == {"b": 1} and plan.named
    assert label.anchors == ["m1", "m2"]
    with pytest.raises(LabelError):
        edits.preview_amendment(store, label, [], {"m1", "m2"}, set())
