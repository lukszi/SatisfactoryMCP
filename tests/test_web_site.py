"""``/api/plan/site-preview`` and the site drop through ``/api/plans/{key}/ops``, plus chat's
``site_plan(preview=True)`` feeding the page's ghost pad (docs/planner-p5_contract.md §4, §7).

Every write lands in temporary directories; the fixture world is read and never written.
"""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.planlog import PlanLog
from satisfactory_mcp.domain.session import journal
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools import planning
from satisfactory_mcp.interfaces.web import terrain
from satisfactory_mcp.interfaces.web.app import create_app
from satisfactory_mcp.interfaces.web.routers import plan_site

ORIGIN = {"origin": "http://testserver"}
WORLD = "X2faPVKjX06VaRzClNv5KQ"
RIP = "Reinforced Iron Plate"
ARGS = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
PREVIEW = "/api/plan/site-preview"


@pytest.fixture
def client(tmp_path, monkeypatch, projection, game):
    for name in ("plans_dir", "labels_dir", "activity_dir", "ui_dir", "pins_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})
    monkeypatch.setattr(plan_site, "_SESSIONS", type(plan_site._SESSIONS)())
    monkeypatch.setattr(terrain, "field", lambda: None)

    one = WorldState(projection=projection, game=game)
    monkeypatch.setattr(planning, "_state", lambda *_a, **_k: one)
    app = create_app(state_loader=lambda save=None, world=None: one, game_loader=lambda: game)
    with TestClient(app) as c:
        yield c


def _plan(client) -> dict:
    reply = client.post("/api/plans", json={"name": "rip 5", "args": ARGS}, headers=ORIGIN)
    assert reply.status_code == 201, reply.text
    return reply.json()


def _site(x, y, yaw=0.0, side=200.0, label="map"):
    return {
        "schema": 1,
        "origin_m": [x, y, None],
        "yaw_deg": yaw,
        "footprint_m": [side, side],
        "footprint_source": "given",
        "origin_label": label,
        "when": "",
    }


def _drop(client, key, base, value):
    return client.post(
        f"/api/plans/{key}/ops",
        json={"base_rev": base, "ops": [{"op": "site", "value": value}]},
        headers=ORIGIN,
    )


def test_the_first_reply_starts_an_unsited_plan_and_carries_the_static_parts(client):
    key = _plan(client)["key"]
    body = client.get(f"{PREVIEW}?key={key}&first=1").json()
    assert body["key"] == key and body["rev"] == 1 and body["sited"] is False
    assert body["source"] in ("layout", "default") and body["w_m"] > 0
    assert isinstance(body["nodes"], list) and len(body["content_bbox_m"]) == 4
    assert body["token"] and body["z_note"] == "terrain height: pending"
    step = client.get(f"{PREVIEW}?key={key}&x_m=-238&y_m=-1466&yaw_deg=30").json()
    assert (step["x_m"], step["y_m"], step["yaw_deg"]) == (-238.0, -1466.0, 30.0)
    assert step["nodes"] is None and step["content_bbox_m"] is None
    assert step["built"]["mode"] == "auto"


def test_unknown_plans_are_404s_and_outside_the_map_is_a_200(client):
    assert client.get(f"{PREVIEW}?key=0000beef").status_code == 404
    assert client.get(f"{PREVIEW}?key=nope").status_code == 404
    key = _plan(client)["key"]
    assert client.get(f"{PREVIEW}?key={key}&rev=9").status_code == 404
    out = client.get(f"{PREVIEW}?key={key}&x_m=4250&y_m=0&w_m=100&d_m=100").json()
    assert out["in_map"] is False and out["trunks"] == []


def test_a_drop_outside_the_map_is_a_400_and_writes_nothing(client):
    key = _plan(client)["key"]
    reply = _drop(client, key, 1, _site(5000.0, 0.0))
    assert reply.status_code == 400
    assert reply.json()["error"] == "the site is outside the map (x must be -3,247…4,253 m)"
    assert client.get(f"/api/plans/{key}").json()["rev"] == 1


def test_a_drop_is_one_version_and_undo_takes_it_back(client):
    key = _plan(client)["key"]
    first = _drop(client, key, 1, _site(-238.0, -1466.0)).json()
    moved = _drop(client, key, 2, _site(3000.0, 3000.0)).json()
    assert (first["rev"], moved["rev"]) == (2, 3)
    (commit,) = client.get(f"/api/plans/{key}/ops?since=2").json()["commits"]
    assert commit["text"] == "v3 page: site moved 5,516 m south-east"
    back = client.post(
        f"/api/plans/{key}/undo", json={"base_rev": 3, "rev": 3}, headers=ORIGIN
    ).json()
    assert back["state"]["siting"]["origin_m"][:2] == [-238.0, -1466.0]


def test_the_preview_follows_the_new_head_and_reports_the_loss(client):
    key = _plan(client)["key"]
    _drop(client, key, 1, _site(-238.0, -1466.0))
    away = client.get(f"{PREVIEW}?key={key}&x_m=3000&y_m=3000").json()
    assert away["rev"] == 2 and away["sited"] is True
    assert away["now"]["built"] and away["built"]["built"] == 0
    assert away["loses"]["text"].endswith("→ 0 at the new spot")


def test_preview_steps_stay_inside_the_budget(client):
    key = _plan(client)["key"]
    _drop(client, key, 1, _site(-238.0, -1466.0))
    client.get(f"{PREVIEW}?key={key}&first=1")
    took = []
    for i in range(20):
        t = time.perf_counter()
        assert client.get(f"{PREVIEW}?key={key}&x_m={-238 + 9 * i}&y_m=-1466").status_code == 200
        took.append(time.perf_counter() - t)
    took.sort()
    assert took[18] < 0.06, took


def test_chat_preview_writes_nothing_and_the_page_uses_it_as_one_version(client):
    key = _plan(client)["key"]
    journal.set_writer("chat")
    out = srv.site_plan(plan="rip 5", at="-238.4,-1466.2", yaw_deg=37, preview=True)
    assert out.startswith("# preview of plan 'rip 5' v1 at -238, -1,466, yaw 30°")
    assert "nothing written" in out.splitlines()[0] and "built here:" in out
    assert PlanLog(WORLD).head_rev(key) == 1
    (entry,) = journal.read(WORLD)
    assert (entry["kind"], entry["tool"], entry["rev"]) == ("plan.view", "site_plan", 1)
    args = entry["args"]
    assert args["view"] == "site" and (args["x_m"], args["y_m"], args["yaw_deg"]) == (
        -238.0,
        -1466.0,
        30.0,
    )
    value = _site(args["x_m"], args["y_m"], args["yaw_deg"], args["w_m"], label="chat preview")
    used = _drop(client, key, 1, value).json()
    assert used["rev"] == 2 and used["state"]["siting"]["origin_m"][:2] == [-238.0, -1466.0]


def test_chat_writes_snap_to_the_shared_lattice(client):
    _plan(client)
    out = srv.site_plan(plan="rip 5", at="100.4,-200.6", yaw_deg=37, footprint="96", base_rev=1)
    assert "origin 100,-201m" in out and "yaw 30deg" in out
    client.patch("/api/settings", json={"values": {"site_snap": "grid8"}}, headers=ORIGIN)
    out = srv.site_plan(plan="rip 5", at="103,-197", yaw_deg=50, footprint="96", base_rev=2)
    assert "origin 104,-200m" in out and "yaw 90deg" in out


def test_chat_preview_on_grid8_turns_in_quarter_turns(client):
    _plan(client)
    client.patch("/api/settings", json={"values": {"site_snap": "grid8"}}, headers=ORIGIN)
    out = srv.site_plan(plan="rip 5", at="-238.4,-1466.2", yaw_deg=50, preview=True)
    assert out.startswith("# preview of plan 'rip 5' v1 at ") and ", yaw 90°" in out.splitlines()[0]
