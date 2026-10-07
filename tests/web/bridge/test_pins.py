"""``/api/pins`` and ``pin:`` members on plan writes (docs/planner-p3_contract.md §5 and §8)."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.planning.stored.planlog import PlanLog
from satisfactory_mcp.domain.session import journal, pins
from tests.support.reference_world import FIVE_RIP_ARGS, FIXTURE_WORLD
from tests.support.web import PAGE_ORIGIN, create_plan, journal_entries, push_ops

EVIL = {"origin": "http://evil.example"}
OIL = "BP_ResourceNode26_99"


@pytest.fixture(autouse=True)
def _web_writes():
    journal.set_writer("web")


def _pin(fresh_state_client, kind, ref, label=None):
    body = {"kind": kind, "ref": ref}
    if label is not None:
        body["label"] = label
    return fresh_state_client.post("/api/pins", json=body, headers=PAGE_ORIGIN)


def test_create_is_201_then_200_for_the_same_object(fresh_state_client):
    first = _pin(fresh_state_client, "point", {"x_m": 100.0, "y_m": -200.0}, "spot")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["id"] == "pin:1" and body["existing"] is False and body["label"] == "spot"
    again = _pin(fresh_state_client, "point", {"x_m": 100.0, "y_m": -200.0})
    assert again.status_code == 200 and again.json()["existing"] is True
    assert again.json()["n"] == 1
    [entry] = journal_entries(FIXTURE_WORLD, "pin.")
    assert entry["kind"] == "pin.add" and entry["args"] == {"n": 1, "kind": "point"}
    assert (
        entry["text"] == "pinned pin:1 point x 100, y -200 m" and entry["actor"]["kind"] == "page"
    )


def test_the_list_is_every_live_pin_with_the_version(fresh_state_client):
    _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0})
    _pin(fresh_state_client, "node", {"node": OIL})
    body = fresh_state_client.get("/api/pins").json()
    assert body["version"] == 2 and [p["id"] for p in body["pins"]] == ["pin:1", "pin:2"]
    assert body["pins"][1]["selector"] == f"node:{OIL}" and body["pins"][1]["gone"] is False


def test_bad_bodies_are_400_and_absent_objects_404(fresh_state_client):
    assert _pin(fresh_state_client, "ask", {}).status_code == 400
    assert _pin(fresh_state_client, "point", {"x_m": 1e9, "y_m": 0}).status_code == 404
    assert _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0}, "x" * 81).status_code == 400
    missing = _pin(fresh_state_client, "node", {"node": "BP_NoSuch"})
    assert missing.status_code == 404 and missing.json() == {
        "error": "no resource node “BP_NoSuch”"
    }
    assert _pin(fresh_state_client, "plan", {"plan": "0000beef"}).status_code == 404
    assert _pin(fresh_state_client, "machine", {"machine": "Build_Nothing_C_1"}).status_code == 404
    assert _pin(fresh_state_client, "factory", {"factory": "nowhere"}).status_code == 404
    assert journal_entries(FIXTURE_WORLD, "pin.") == []


def test_rename_counts_revs_and_a_stale_rev_is_a_409_with_the_row(fresh_state_client):
    _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0})
    reply = fresh_state_client.patch(
        "/api/pins/1", json={"rev": 1, "label": "home"}, headers=PAGE_ORIGIN
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["rev"] == 2 and reply.json()["label"] == "home"
    stale = fresh_state_client.patch(
        "/api/pins/1", json={"rev": 1, "label": "late"}, headers=PAGE_ORIGIN
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["error"] == "pin:1 changed since you read it" and body["stale"] is True
    assert body["pin"]["label"] == "home" and body["pin"]["rev"] == 2
    long = fresh_state_client.patch(
        "/api/pins/1", json={"rev": 2, "label": "x" * 81}, headers=PAGE_ORIGIN
    )
    assert long.status_code == 400
    assert (
        fresh_state_client.patch(
            "/api/pins/7", json={"rev": 1, "label": "x"}, headers=PAGE_ORIGIN
        ).status_code
        == 404
    )
    edit = [e for e in journal_entries(FIXTURE_WORLD, "pin.") if e["kind"] == "pin.edit"]
    assert [e["text"] for e in edit] == ["renamed pin:1 “home”"]


def test_delete_is_final_and_a_stale_rev_is_a_409(fresh_state_client):
    _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0})
    stale = fresh_state_client.request(
        "DELETE", "/api/pins/1", json={"rev": 5}, headers=PAGE_ORIGIN
    )
    assert stale.status_code == 409 and stale.json()["stale"] is True
    done = fresh_state_client.request("DELETE", "/api/pins/1", json={"rev": 1}, headers=PAGE_ORIGIN)
    assert done.status_code == 200 and done.json() == {"ok": True, "n": 1}
    again = fresh_state_client.request(
        "DELETE", "/api/pins/1", json={"rev": 2}, headers=PAGE_ORIGIN
    )
    assert again.status_code == 404 and again.json() == {"error": "pin:1 was deleted"}
    assert fresh_state_client.get("/api/pins").json()["pins"] == []
    assert _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0}).json()["n"] == 2
    assert [e["kind"] for e in journal_entries(FIXTURE_WORLD, "pin.")] == [
        "pin.add",
        "pin.drop",
        "pin.add",
    ]


def test_the_guard_refuses_writes_from_another_origin(fresh_state_client):
    assert (
        fresh_state_client.post(
            "/api/pins", json={"kind": "point", "ref": {"x_m": 1.0, "y_m": 2.0}}, headers=EVIL
        ).status_code
        == 403
    )
    _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0})
    assert (
        fresh_state_client.patch(
            "/api/pins/1", json={"rev": 1, "label": "x"}, headers=EVIL
        ).status_code
        == 403
    )
    assert (
        fresh_state_client.request(
            "DELETE", "/api/pins/1", json={"rev": 1}, headers=EVIL
        ).status_code
        == 403
    )
    assert fresh_state_client.get("/api/pins", headers={"host": "evil.example"}).status_code in (
        400,
        403,
    )
    assert fresh_state_client.get("/api/pins").json()["pins"][0]["rev"] == 1


def test_a_newer_schema_is_a_503(fresh_state_client):
    pins.path_for(FIXTURE_WORLD).parent.mkdir(parents=True, exist_ok=True)
    pins.path_for(FIXTURE_WORLD).write_text(json.dumps({"schema": 9, "pins": []}), encoding="utf-8")
    for reply in (
        fresh_state_client.get("/api/pins"),
        _pin(fresh_state_client, "point", {"x_m": 1.0, "y_m": 2.0}),
        fresh_state_client.patch("/api/pins/1", json={"rev": 1, "label": "x"}, headers=PAGE_ORIGIN),
    ):
        assert reply.status_code == 503 and reply.json()["newer_schema"] is True
        assert "pins were saved by a newer version" in reply.json()["error"]


def test_plan_and_process_pins_journal_their_plan(fresh_state_client):
    made = create_plan(fresh_state_client, "rip")
    key = made["key"]
    solved = fresh_state_client.post(
        "/api/plan/solve", json={"key": key}, headers=PAGE_ORIGIN
    ).json()
    recipe = solved["rows"][0]["recipe_id"]
    process = _pin(fresh_state_client, "process", {"plan": key, "recipe": recipe})
    assert process.status_code == 201, process.text
    assert process.json()["selector"] == recipe
    assert _pin(fresh_state_client, "plan", {"plan": key}).status_code == 201
    assert [e["plan"] for e in journal_entries(FIXTURE_WORLD, "pin.")] == [key, key]


def test_plan_writes_store_what_a_pin_stands_for(fresh_state_client):
    node = _pin(fresh_state_client, "node", {"node": OIL}).json()
    point = _pin(fresh_state_client, "point", {"x_m": 100.0, "y_m": -200.0}).json()
    made = create_plan(fresh_state_client, "pinned", {**FIVE_RIP_ARGS, "sources": [node["id"]]})
    key = made["key"]
    assert made["state"]["args"]["sources"] == [f"node:{OIL}"]
    near = {"op": "add", "field": "sources", "member": f"near:{point['id']}@300"}
    pushed = push_ops(fresh_state_client, key, 1, near)
    assert pushed.json()["state"]["args"]["sources"] == [f"node:{OIL}", "near:100,-200@300"]
    args = {**FIVE_RIP_ARGS, "sources": [node["id"]], "banned": [point["id"]]}
    refused = fresh_state_client.post(
        f"/api/plans/{key}/args", json={"base_rev": 2, "args": args}, headers=PAGE_ORIGIN
    )
    assert refused.status_code == 400
    assert refused.json() == {"error": f"{point['id']} is a point: it cannot stand for a recipe"}
    assert PlanLog(FIXTURE_WORLD).head_rev(key) == 2
    fine = fresh_state_client.post(
        f"/api/plans/{key}/args",
        json={"base_rev": 2, "args": {**FIVE_RIP_ARGS, "sources": [node["id"]]}},
        headers=PAGE_ORIGIN,
    )
    assert fine.status_code == 200 and fine.json()["state"]["args"]["sources"] == [f"node:{OIL}"]
    for commit in PlanLog(FIXTURE_WORLD).commits(key):
        assert "pin:" not in json.dumps(commit.to_dict())


def test_the_pin_bodies_reach_the_published_schema(fresh_state_client):
    schema = fresh_state_client.get("/openapi.json").json()
    patch = schema["paths"]["/api/pins/{n}"]["patch"]
    assert patch["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PinStaleResponse"
    }
    post = schema["paths"]["/api/pins"]["post"]
    assert {"200", "201"} <= set(post["responses"])
    names = schema["components"]["schemas"]
    for name in ("PinRow", "PinRef", "PinsResponse", "PinCreated", "Dropped", "RevBody"):
        assert name in names, name
    ops = {
        op["operationId"].split("_api_")[0]
        for path in ("/api/pins", "/api/pins/{n}")
        for op in schema["paths"][path].values()
    }
    assert ops == {"pins", "create_pin", "rename_pin", "drop_pin"}
