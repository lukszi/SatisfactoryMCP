"""``/api/maps``: the type list, its edits, the guard on every write, and a job end to end.

``config.data_dir`` points at the ``local`` tree and the generator is the fake, both from
``tests.support.map_jobs``; nothing here reads a real map.
"""

from __future__ import annotations

import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.maps import presets, registry
from satisfactory_mcp.interfaces.web.watch.events import KIND_MAPS, KINDS
from tests.support.map_jobs import install_fake
from tests.support.web import PAGE_ORIGIN

#: Every test serves the ``local`` tree, which is set up before the client starts.
pytestmark = pytest.mark.usefixtures("local")


def test_the_list_carries_every_type_its_freshness_and_what_can_run(stateless_client):
    body = stateless_client.get("/api/maps").json()
    ids = [t["id"] for t in body["types"]]
    assert set(ids) >= {"map", "terrain", "satellite", "terrain-r4-502094", "terrain-r3-502094"}
    assert body["default"] == "map"
    terrain = next(t for t in body["types"] if t["id"] == "terrain")
    assert terrain["name"] == "terrain · PCHIP r5 · data 502094/hf v5"
    assert terrain["title"].startswith("Terrain · "), "three terrain maps are dated apart"
    artwork = next(t for t in body["types"] if t["id"] == "map")
    assert artwork["title"] == "Game map ★" and artwork["label"] is None
    assert terrain["status"] == "ready" and terrain["dir"] == "renders/terrain"
    old = next(t for t in body["types"] if t["id"] == "terrain-r3-502094")
    assert old["freshness"]["stale"][0]["text"] == "newer heightfield (v3 → v5)"
    assert set(body["can_generate"]) == {
        "gen", "tools", "game", "heightfield", "vulkan", "ok", "reason"
    }  # fmt: skip
    assert {row["tone"] for row in body["types"]} <= {"light", "dark"}
    assert terrain["tone"] == "light" and body["plain_tone"] == "dark"
    assert [row["layer"] for row in body["styles"]] == ["terrain", "painted", "relief-dark"]
    satellite = next(t for t in body["types"] if t["id"] == "satellite")
    assert satellite["status"] == "ready" and satellite["title"].startswith("Biome (old)")
    assert body["jobs"] == [] and body["queue_max"] == 4
    assert {row["name"] for row in body["inputs"]} == {"heightfield", "caves", "rocks", "paint"}
    assert KIND_MAPS in KINDS


def test_every_write_needs_this_page_as_its_origin(stateless_client):
    for method, path, body in (
        ("POST", "/api/maps/jobs", {"preset": "render"}),
        ("PUT", "/api/maps/default", {"id": "terrain"}),
        ("PATCH", "/api/maps/terrain", {"label": "x"}),
        ("DELETE", "/api/maps/terrain-r3-502094", None),
        ("POST", "/api/maps/adopt", None),
        ("DELETE", "/api/maps/cache", None),
    ):
        reply = stateless_client.request(method, path, json=body)
        assert reply.status_code == 403, (method, path, reply.text)
        evil = stateless_client.request(
            method, path, json=body, headers={"origin": "http://evil.example"}
        )
        assert evil.status_code == 403
    assert "terrain-r3-502094" in {
        t["id"] for t in stateless_client.get("/api/maps").json()["types"]
    }


def test_rename_hide_default_and_a_stale_version(stateless_client):
    version = stateless_client.get("/api/maps").json()["version"]
    reply = stateless_client.patch(
        "/api/maps/terrain-r4-502094",
        json={"label": "two-regime", "in_switcher": False, "version": version},
        headers=PAGE_ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    row = next(t for t in reply.json()["types"] if t["id"] == "terrain-r4-502094")
    assert row["label"] == "two-regime" and row["in_switcher"] is False
    assert row["title"] == "two-regime"
    stale = stateless_client.patch(
        "/api/maps/terrain", json={"label": "x", "version": version}, headers=PAGE_ORIGIN
    )
    assert stale.status_code == 409 and stale.json()["stale"] is True
    reply = stateless_client.put("/api/maps/default", json={"id": "terrain"}, headers=PAGE_ORIGIN)
    assert reply.json()["default"] == "terrain"
    assert (
        stateless_client.patch(
            "/api/maps/nope", json={"label": "x"}, headers=PAGE_ORIGIN
        ).status_code
        == 404
    )


def test_delete_refuses_the_default_and_takes_the_rest_to_the_trash(stateless_client, local):
    refused = stateless_client.delete("/api/maps/map", headers=PAGE_ORIGIN)
    assert refused.status_code == 409 and "pick another default first" in refused.json()["error"]
    reply = stateless_client.delete("/api/maps/terrain-r3-502094", headers=PAGE_ORIGIN)
    assert reply.status_code == 200
    assert "terrain-r3-502094" not in {t["id"] for t in reply.json()["types"]}
    assert not (local / "renders-v1" / "terrain").exists()
    tiles = stateless_client.head("/api/maptiles/terrain-r3-502094/0/0/0")
    assert tiles.status_code == 404


def test_tiles_are_served_by_registry_id(stateless_client):
    assert stateless_client.get("/api/maptiles/terrain-r4-502094/0/0/0").status_code == 200
    assert stateless_client.get("/api/maptiles/terrain/0/0/0").status_code == 200
    unknown = stateless_client.get("/api/maptiles/terrain-r9-1/0/0/0")
    assert unknown.status_code == 404 and "terrain-r4-502094" in unknown.json()["error"]


def test_an_estimate_says_whether_the_disk_has_room(stateless_client):
    body = stateless_client.get("/api/maps/estimate?preset=render&size=1024&layers=terrain").json()
    assert body["seconds"] > 0 and body["keep_bytes"] > 0 and isinstance(body["ok"], bool)
    full = stateless_client.get("/api/maps/estimate?preset=render&size=32768").json()
    assert full["seconds"] > body["seconds"] and full["needs_bytes"] > body["needs_bytes"]
    both = "/api/maps/estimate?preset=render&size=32768&layers=terrain,painted"
    assert stateless_client.get(both).json()["keep_bytes"] == full["keep_bytes"], "the default"
    dark = stateless_client.get("/api/maps/estimate?preset=render&size=32768&light=false").json()
    assert dark["seconds"] < full["seconds"] and dark["needs_bytes"] < full["needs_bytes"]
    assert stateless_client.get("/api/maps/estimate?preset=render&size=999").status_code == 400
    retired = stateless_client.get("/api/maps/estimate?preset=render&layers=terrain,satellite")
    assert retired.status_code == 400, "nothing draws the satellite style now"


@pytest.fixture
def fake_generator(tmp_path, monkeypatch):
    """The fake generator installed, and every check before a job passed."""
    tools = tmp_path / "tools"
    tools.mkdir()
    install_fake(tools)
    monkeypatch.setattr(presets, "tools_dir", lambda: tools)
    monkeypatch.setattr(presets.config, "game_root", lambda: tmp_path / "game")
    monkeypatch.setattr(
        presets,
        "can_generate",
        lambda: {"gen": True, "tools": True, "game": True, "heightfield": True, "vulkan": True,
                 "ok": True, "reason": None},
    )  # fmt: skip
    registry.ensure()


def _until_final(client, ident: str) -> dict:
    deadline = time.monotonic() + 20
    while True:
        detail = client.get(f"/api/maps/jobs/{ident}").json()
        if detail["job"]["status"] not in ("queued", "running"):
            return detail
        assert time.monotonic() < deadline
        time.sleep(0.1)


@pytest.mark.usefixtures("fake_generator")
def test_a_job_queued_from_the_page_runs_and_its_type_appears(stateless_client):
    reply = stateless_client.post(
        "/api/maps/jobs",
        json={"preset": "render", "options": {"layers": ["terrain"], "size": 1024}},
        headers=PAGE_ORIGIN,
    )
    assert reply.status_code == 202, reply.text
    job = reply.json()["job"]
    assert job["produces"] == ["terrain-r7-502094"]
    detail = _until_final(stateless_client, job["id"])
    assert detail["job"]["status"] == "done", detail
    assert any("done in" in line for line in detail["log_tail"])
    body = stateless_client.get("/api/maps").json()
    row = next(t for t in body["types"] if t["id"] == "terrain-r7-502094")
    assert row["status"] == "ready" and row["origin"] == "generated"
    assert body["jobs"][0]["id"] == job["id"]
    bad = stateless_client.post("/api/maps/jobs", json={"preset": "render", "options": {"size": 3}},
                      headers=PAGE_ORIGIN)  # fmt: skip
    assert bad.status_code in (400, 422)


@pytest.mark.usefixtures("fake_generator")
def test_an_unlit_job_reaches_the_generator_unlit(stateless_client):
    """The page's "live sun" box: ``light`` survives the request model as far as the argv."""
    reply = stateless_client.post(
        "/api/maps/jobs",
        json={"preset": "render",
              "options": {"layers": ["terrain"], "size": 1024, "light": False}},
        headers=PAGE_ORIGIN,
    )  # fmt: skip
    assert reply.status_code == 202, reply.text
    job = reply.json()["job"]
    assert job["options"]["light"] is False
    assert "--no-light" in job["argv"] and "--light" not in job["argv"]
    assert _until_final(stateless_client, job["id"])["job"]["status"] == "done"


def test_a_newer_map_list_is_a_503_that_names_the_map_list(stateless_client):
    registry.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    registry.manifest_path().write_text(json.dumps({"schema": 99}), encoding="utf-8")
    tile = stateless_client.get("/api/maptiles/terrain/0/0/0")
    assert tile.status_code == 503, tile.text
    error = tile.json()["error"]
    assert error.startswith("the map list was saved by a newer version"), error
    assert "plans" not in error
    listed = stateless_client.get("/api/maps")
    assert listed.status_code == 503 and listed.json()["error"] == error
