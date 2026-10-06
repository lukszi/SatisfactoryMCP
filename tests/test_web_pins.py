"""``/api/pins`` and ``pin:`` members on plan writes (docs/planner-p3_contract.md §5 and §8).

Every write lands in temporary pins, plans, activity and ui directories.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.planlog import PlanLog
from satisfactory_mcp.domain.session import journal, pins
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app

ORIGIN = {"origin": "http://testserver"}
EVIL = {"origin": "http://evil.example"}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
RIP = "Reinforced Iron Plate"
HMF_ARGS = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
OIL = "BP_ResourceNode26_99"


@pytest.fixture
def client(tmp_path, monkeypatch, projection, game):
    for name in ("plans_dir", "labels_dir", "activity_dir", "ui_dir", "pins_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    journal.set_writer("web")
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def _pin(client, kind, ref, label=None):
    body = {"kind": kind, "ref": ref}
    if label is not None:
        body["label"] = label
    return client.post("/api/pins", json=body, headers=ORIGIN)


def _plan(client, args=None):
    reply = client.post(
        "/api/plans", json={"name": "rip", "args": args or HMF_ARGS}, headers=ORIGIN
    )
    assert reply.status_code == 201, reply.text
    return reply.json()


def _journal():
    return [e for e in journal.read(WORLD) if e["kind"].startswith("pin.")]


def test_create_is_201_then_200_for_the_same_object(client):
    first = _pin(client, "point", {"x_m": 100.0, "y_m": -200.0}, "spot")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["id"] == "pin:1" and body["existing"] is False and body["label"] == "spot"
    again = _pin(client, "point", {"x_m": 100.0, "y_m": -200.0})
    assert again.status_code == 200 and again.json()["existing"] is True
    assert again.json()["n"] == 1
    [entry] = _journal()
    assert entry["kind"] == "pin.add" and entry["args"] == {"n": 1, "kind": "point"}
    assert (
        entry["text"] == "pinned pin:1 point x 100, y -200 m" and entry["actor"]["kind"] == "page"
    )


def test_the_list_is_every_live_pin_with_the_version(client):
    _pin(client, "point", {"x_m": 1.0, "y_m": 2.0})
    _pin(client, "node", {"node": OIL})
    body = client.get("/api/pins").json()
    assert body["version"] == 2 and [p["id"] for p in body["pins"]] == ["pin:1", "pin:2"]
    assert body["pins"][1]["selector"] == f"node:{OIL}" and body["pins"][1]["gone"] is False


def test_bad_bodies_are_400_and_absent_objects_404(client):
    assert _pin(client, "ask", {}).status_code == 400
    assert _pin(client, "point", {"x_m": 1e9, "y_m": 0}).status_code == 404
    assert _pin(client, "point", {"x_m": 1.0, "y_m": 2.0}, "x" * 81).status_code == 400
    missing = _pin(client, "node", {"node": "BP_NoSuch"})
    assert missing.status_code == 404 and missing.json() == {
        "error": "no resource node “BP_NoSuch”"
    }
    assert _pin(client, "plan", {"plan": "0000beef"}).status_code == 404
    assert _pin(client, "machine", {"machine": "Build_Nothing_C_1"}).status_code == 404
    assert _pin(client, "factory", {"factory": "nowhere"}).status_code == 404
    assert _journal() == []


def test_rename_counts_revs_and_a_stale_rev_is_a_409_with_the_row(client):
    _pin(client, "point", {"x_m": 1.0, "y_m": 2.0})
    reply = client.patch("/api/pins/1", json={"rev": 1, "label": "home"}, headers=ORIGIN)
    assert reply.status_code == 200, reply.text
    assert reply.json()["rev"] == 2 and reply.json()["label"] == "home"
    stale = client.patch("/api/pins/1", json={"rev": 1, "label": "late"}, headers=ORIGIN)
    assert stale.status_code == 409
    body = stale.json()
    assert body["error"] == "pin:1 changed since you read it" and body["stale"] is True
    assert body["pin"]["label"] == "home" and body["pin"]["rev"] == 2
    long = client.patch("/api/pins/1", json={"rev": 2, "label": "x" * 81}, headers=ORIGIN)
    assert long.status_code == 400
    assert (
        client.patch("/api/pins/7", json={"rev": 1, "label": "x"}, headers=ORIGIN).status_code
        == 404
    )
    edit = [e for e in _journal() if e["kind"] == "pin.edit"]
    assert [e["text"] for e in edit] == ["renamed pin:1 “home”"]


def test_delete_is_final_and_a_stale_rev_is_a_409(client):
    _pin(client, "point", {"x_m": 1.0, "y_m": 2.0})
    stale = client.request("DELETE", "/api/pins/1", json={"rev": 5}, headers=ORIGIN)
    assert stale.status_code == 409 and stale.json()["stale"] is True
    done = client.request("DELETE", "/api/pins/1", json={"rev": 1}, headers=ORIGIN)
    assert done.status_code == 200 and done.json() == {"ok": True, "n": 1}
    again = client.request("DELETE", "/api/pins/1", json={"rev": 2}, headers=ORIGIN)
    assert again.status_code == 404 and again.json() == {"error": "pin:1 was deleted"}
    assert client.get("/api/pins").json()["pins"] == []
    assert _pin(client, "point", {"x_m": 1.0, "y_m": 2.0}).json()["n"] == 2
    assert [e["kind"] for e in _journal()] == ["pin.add", "pin.drop", "pin.add"]


def test_the_guard_refuses_writes_from_another_origin(client):
    assert (
        client.post(
            "/api/pins", json={"kind": "point", "ref": {"x_m": 1.0, "y_m": 2.0}}, headers=EVIL
        ).status_code
        == 403
    )
    _pin(client, "point", {"x_m": 1.0, "y_m": 2.0})
    assert (
        client.patch("/api/pins/1", json={"rev": 1, "label": "x"}, headers=EVIL).status_code == 403
    )
    assert client.request("DELETE", "/api/pins/1", json={"rev": 1}, headers=EVIL).status_code == 403
    assert client.get("/api/pins", headers={"host": "evil.example"}).status_code in (400, 403)
    assert client.get("/api/pins").json()["pins"][0]["rev"] == 1


def test_a_newer_schema_is_a_503(client):
    pins.path_for(WORLD).write_text(json.dumps({"schema": 9, "pins": []}), encoding="utf-8")
    for reply in (
        client.get("/api/pins"),
        _pin(client, "point", {"x_m": 1.0, "y_m": 2.0}),
        client.patch("/api/pins/1", json={"rev": 1, "label": "x"}, headers=ORIGIN),
    ):
        assert reply.status_code == 503 and reply.json()["newer_schema"] is True
        assert "pins were saved by a newer version" in reply.json()["error"]


def test_plan_and_process_pins_journal_their_plan(client):
    made = _plan(client)
    key = made["key"]
    solved = client.post("/api/plan/solve", json={"key": key}, headers=ORIGIN).json()
    recipe = solved["rows"][0]["recipe_id"]
    process = _pin(client, "process", {"plan": key, "recipe": recipe})
    assert process.status_code == 201, process.text
    assert process.json()["selector"] == recipe
    assert _pin(client, "plan", {"plan": key}).status_code == 201
    assert [e["plan"] for e in _journal()] == [key, key]


def test_plan_writes_store_what_a_pin_stands_for(client):
    node = _pin(client, "node", {"node": OIL}).json()
    point = _pin(client, "point", {"x_m": 100.0, "y_m": -200.0}).json()
    made = client.post(
        "/api/plans",
        json={"name": "pinned", "args": {**HMF_ARGS, "sources": [node["id"]]}},
        headers=ORIGIN,
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]
    assert made.json()["state"]["args"]["sources"] == [f"node:{OIL}"]
    pushed = client.post(
        f"/api/plans/{key}/ops",
        json={
            "base_rev": 1,
            "ops": [{"op": "add", "field": "sources", "member": f"near:{point['id']}@300"}],
        },
        headers=ORIGIN,
    )
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["state"]["args"]["sources"] == [f"node:{OIL}", "near:100,-200@300"]
    args = {**HMF_ARGS, "sources": [node["id"]], "banned": [point["id"]]}
    refused = client.post(
        f"/api/plans/{key}/args", json={"base_rev": 2, "args": args}, headers=ORIGIN
    )
    assert refused.status_code == 400
    assert refused.json() == {"error": f"{point['id']} is a point: it cannot stand for a recipe"}
    assert PlanLog(WORLD).head_rev(key) == 2
    fine = client.post(
        f"/api/plans/{key}/args",
        json={"base_rev": 2, "args": {**HMF_ARGS, "sources": [node["id"]]}},
        headers=ORIGIN,
    )
    assert fine.status_code == 200 and fine.json()["state"]["args"]["sources"] == [f"node:{OIL}"]
    for commit in PlanLog(WORLD).commits(key):
        assert "pin:" not in json.dumps(commit.to_dict())


def test_the_pin_bodies_reach_the_published_schema(client):
    schema = client.get("/openapi.json").json()
    patch = schema["paths"]["/api/pins/{n}"]["patch"]
    assert patch["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PinStaleResponse"
    }
    post = schema["paths"]["/api/pins"]["post"]
    assert {"200", "201"} <= set(post["responses"])
    names = schema["components"]["schemas"]
    for name in ("PinRow", "PinRef", "PinsResponse", "PinCreated", "PinDropped"):
        assert name in names, name
    ops = {
        op["operationId"].split("_api_")[0]
        for path in ("/api/pins", "/api/pins/{n}")
        for op in schema["paths"][path].values()
    }
    assert ops == {"pins", "create_pin", "rename_pin", "drop_pin"}
