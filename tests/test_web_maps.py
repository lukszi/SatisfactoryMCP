"""``/api/maps``: the type list, its edits, the guard on every write, and a job end to end.

``config.data_dir`` points at a scratch tree built by ``test_map_registry``'s helpers, and
the generator is the fake from ``test_map_runner``; nothing here reads a real map.
"""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from test_map_registry import local  # noqa: F401  (the fixture)
from test_map_runner import install_fake

from satisfactory_mcp.domain.maps import presets, registry
from satisfactory_mcp.interfaces.web.app import create_app
from satisfactory_mcp.interfaces.web.watch import KIND_MAPS, KINDS

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def client(local):  # noqa: F811
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: None)
    with TestClient(app) as c:
        yield c


def test_the_list_carries_every_type_its_freshness_and_what_can_run(client):
    body = client.get("/api/maps").json()
    ids = [t["id"] for t in body["types"]]
    assert set(ids) >= {"map", "terrain", "satellite", "terrain-r4-502094", "terrain-r3-502094"}
    assert body["default"] == "map"
    terrain = next(t for t in body["types"] if t["id"] == "terrain")
    assert terrain["name"] == "terrain · PCHIP r5 · data 502094/hf v5"
    assert terrain["status"] == "ready" and terrain["dir"] == "renders/terrain"
    old = next(t for t in body["types"] if t["id"] == "terrain-r3-502094")
    assert old["freshness"]["stale"][0]["text"] == "newer heightfield (v3 → v5)"
    assert set(body["can_generate"]) == {"gen", "tools", "game", "heightfield", "ok", "reason"}
    assert body["jobs"] == [] and body["queue_max"] == 4
    assert {row["name"] for row in body["inputs"]} == {"heightfield", "caves", "rocks", "paint"}
    assert KIND_MAPS in KINDS


def test_every_write_needs_this_page_as_its_origin(client):
    for method, path, body in (
        ("POST", "/api/maps/jobs", {"preset": "render"}),
        ("PUT", "/api/maps/default", {"id": "terrain"}),
        ("PATCH", "/api/maps/terrain", {"label": "x"}),
        ("DELETE", "/api/maps/terrain-r3-502094", None),
        ("POST", "/api/maps/adopt", None),
        ("DELETE", "/api/maps/cache", None),
    ):
        reply = client.request(method, path, json=body)
        assert reply.status_code == 403, (method, path, reply.text)
        evil = client.request(method, path, json=body, headers={"origin": "http://evil.example"})
        assert evil.status_code == 403
    assert "terrain-r3-502094" in {t["id"] for t in client.get("/api/maps").json()["types"]}


def test_rename_hide_default_and_a_stale_version(client):
    version = client.get("/api/maps").json()["version"]
    reply = client.patch(
        "/api/maps/terrain-r4-502094",
        json={"label": "two-regime", "in_switcher": False, "version": version},
        headers=ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    row = next(t for t in reply.json()["types"] if t["id"] == "terrain-r4-502094")
    assert row["label"] == "two-regime" and row["in_switcher"] is False
    stale = client.patch(
        "/api/maps/terrain", json={"label": "x", "version": version}, headers=ORIGIN
    )
    assert stale.status_code == 409 and stale.json()["stale"] is True
    reply = client.put("/api/maps/default", json={"id": "terrain"}, headers=ORIGIN)
    assert reply.json()["default"] == "terrain"
    assert client.patch("/api/maps/nope", json={"label": "x"}, headers=ORIGIN).status_code == 404


def test_delete_refuses_the_default_and_takes_the_rest_to_the_trash(client, local):  # noqa: F811
    refused = client.delete("/api/maps/map", headers=ORIGIN)
    assert refused.status_code == 409 and "pick another default first" in refused.json()["error"]
    reply = client.delete("/api/maps/terrain-r3-502094", headers=ORIGIN)
    assert reply.status_code == 200
    assert "terrain-r3-502094" not in {t["id"] for t in reply.json()["types"]}
    assert not (local / "renders-v1" / "terrain").exists()
    tiles = client.head("/api/maptiles/terrain-r3-502094/0/0/0")
    assert tiles.status_code == 404


def test_tiles_are_served_by_registry_id(client):
    assert client.get("/api/maptiles/terrain-r4-502094/0/0/0").status_code == 200
    assert client.get("/api/maptiles/terrain/0/0/0").status_code == 200
    unknown = client.get("/api/maptiles/terrain-r9-1/0/0/0")
    assert unknown.status_code == 404 and "terrain-r4-502094" in unknown.json()["error"]


def test_an_estimate_says_whether_the_disk_has_room(client):
    body = client.get("/api/maps/estimate?preset=render&size=1024&layers=terrain").json()
    assert body["seconds"] > 0 and body["keep_bytes"] > 0 and isinstance(body["ok"], bool)
    full = client.get("/api/maps/estimate?preset=render&size=32768").json()
    assert full["seconds"] > body["seconds"] and full["needs_bytes"] > body["needs_bytes"]
    assert client.get("/api/maps/estimate?preset=render&size=999").status_code == 400


def test_a_job_queued_from_the_page_runs_and_its_type_appears(client, local, tmp_path, monkeypatch):  # noqa: F811
    tools = tmp_path / "tools"
    tools.mkdir()
    install_fake(tools)
    monkeypatch.setattr(presets, "tools_dir", lambda: tools)
    monkeypatch.setattr(presets.config, "game_root", lambda: tmp_path / "game")
    monkeypatch.setattr(
        presets,
        "can_generate",
        lambda: {"gen": True, "tools": True, "game": True, "heightfield": True, "ok": True,
                 "reason": None},
    )  # fmt: skip
    registry.ensure()
    reply = client.post(
        "/api/maps/jobs",
        json={"preset": "render", "options": {"layers": ["terrain"], "size": 1024}},
        headers=ORIGIN,
    )
    assert reply.status_code == 202, reply.text
    job = reply.json()["job"]
    assert job["produces"] == ["terrain-r7-502094"]
    deadline = time.monotonic() + 20
    while True:
        detail = client.get(f"/api/maps/jobs/{job['id']}").json()
        if detail["job"]["status"] not in ("queued", "running"):
            break
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert detail["job"]["status"] == "done", detail
    assert any("done in" in line for line in detail["log_tail"])
    body = client.get("/api/maps").json()
    row = next(t for t in body["types"] if t["id"] == "terrain-r7-502094")
    assert row["status"] == "ready" and row["origin"] == "generated"
    assert body["jobs"][0]["id"] == job["id"]
    bad = client.post("/api/maps/jobs", json={"preset": "render", "options": {"size": 3}},
                      headers=ORIGIN)  # fmt: skip
    assert bad.status_code in (400, 422)
