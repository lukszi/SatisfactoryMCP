"""``/api/world/*``: the World finder routes over the fixture world.

``importorskip`` at module scope: ``fastapi`` lives in the optional ``web`` extra. Both
loaders are injected by the ``client`` fixture, so nothing here reads a ``.sav``.
"""

from __future__ import annotations

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app
from satisfactory_mcp.interfaces.web.pinning import STALE

STALE_TOKEN = "sav:000000000000"
ROUTES = [
    ("/api/world/here", {}),
    ("/api/world/nodes", {}),
    ("/api/world/sites", {"resource": "Iron Ore"}),
    ("/api/world/conduits", {}),
    ("/api/world/regions", {}),
]


def _broken(game):
    return TestClient(create_app(state_loader=_explode, game_loader=lambda: game))


# ---------------------------------------------------------------------- here


def test_here_names_the_players_place_and_the_nodes_around_it(client, state):
    body = client.get("/api/world/here").json()
    assert body["save_token"] == state.token
    assert body["age_note"] == state.age_note
    assert body["written_ago"] and body["written_ago"] in state.age_note
    x, _y, _z = state.player_position()
    assert body["player"]["x_m"] == pytest.approx(x / 100, abs=0.1)
    assert body["region"]["name"] and body["grid"] and body["direction"]
    assert body["radius_m"] == 500.0
    d = [n["distance_m"] for n in body["nodes"]]
    assert d == sorted(d) and all(v <= 500 for v in d)
    assert body["nodes_total"] == len(body["nodes"])
    assert body["nearest_building"]["name"]
    assert body["pawns"] >= 1
    assert body["stale"] == []


def test_here_without_a_pawn_answers_with_no_position(game):
    st = WorldState(projection={"players": [], "header": {}}, game=game)
    app = create_app(state_loader=lambda save=None, world=None: st, game_loader=lambda: game)
    with TestClient(app) as c:
        body = c.get("/api/world/here").json()
    assert body["player"] is None and body["nodes"] == [] and body["region"] is None


def test_here_radius_is_bounded_and_needs_a_save(client, game):
    assert client.get("/api/world/here", params={"radius_m": 0}).status_code == 422
    assert client.get("/api/world/here", params={"radius_m": 5001}).status_code == 422
    with _broken(game) as c:
        reply = c.get("/api/world/here")
    assert reply.status_code == 404 and "could not read save" in reply.json()["error"]


# --------------------------------------------------------------------- nodes


def test_nodes_default_to_every_node_with_its_status(client):
    body = client.get("/api/world/nodes").json()
    assert body["view"] == "nodes" and body["fields"] == []
    assert body["count"] == len(body["nodes"]) > 500
    row = body["nodes"][0]
    assert set(row) >= {"id", "name", "status", "occupant", "region", "moved", "spoiler", "rate"}
    assert {r["status"] for r in body["nodes"]} == {"free", "tapped", "locked"}
    assert all(r["spoiler"] == (r["status"] == "locked") for r in body["nodes"])
    assert body["hidden_spoilers"] == 0
    assert body["choices"]["resources"] and body["choices"]["purities"]
    assert body["save_error"] is None and body["stale"] is None


def test_nodes_filters_become_selectors_and_near_adds_distance(client):
    body = client.get(
        "/api/world/nodes",
        params={"resource": "Desc_OreIron_C", "status": "free", "near": "me"},
    ).json()
    assert body["selectors"] == ["resource:Iron Ore"]
    by_name = client.get("/api/world/nodes", params={"source": body["selectors"]}).json()
    assert by_name["count"] >= body["count"]
    assert body["where"] == "you"
    assert {r["resource"] for r in body["nodes"]} == {"Desc_OreIron_C"}
    assert all(r["status"] != "tapped" and r["distance_m"] is not None for r in body["nodes"])
    assert body["unit"] == "/min"


def test_nodes_fields_view_sends_fields_and_the_tools_totals(client):
    body = client.get(
        "/api/world/nodes", params={"view": "fields", "resource": "Copper Ore"}
    ).json()
    assert body["nodes"] == [] and body["fields"]
    assert sum(f["size"] for f in body["fields"]) == body["count"]
    f = body["fields"][0]
    assert f["key"].startswith("field:") and f["selector"].startswith("near:")
    assert f["resources"] == ["Copper Ore"]
    assert len(f["bbox_m"]) == 4


def test_spoilers_zero_drops_locked_nodes_and_the_counts_follow(client):
    every = client.get("/api/world/nodes", params={"resource": "Crude Oil"}).json()
    hidden = client.get("/api/world/nodes", params={"resource": "Crude Oil", "spoilers": 0}).json()
    locked = [r for r in every["nodes"] if r["spoiler"]]
    assert locked
    assert hidden["hidden_spoilers"] == len(locked)
    assert hidden["count"] == every["count"] - len(locked)
    assert not any(r["spoiler"] for r in hidden["nodes"])
    assert hidden["total"] == pytest.approx(sum(r["rate"] for r in hidden["nodes"]))
    assert every["hidden_spoilers"] == 0
    assert client.get("/api/world/nodes", params={"spoilers": 2}).status_code == 422


@pytest.mark.parametrize(
    "params, needle",
    [
        ({"view": "sideways"}, "unknown view"),
        ({"status": "busy"}, "unknown status"),
        ({"purity": "shiny"}, "unknown purity"),
        ({"kind": "volcano"}, "unknown kind"),
        ({"resource": "Unobtainium"}, "unknown resource"),
        ({"near": "nowhere at all"}, "does not name a place"),
        ({"view": "nearest"}, "needs near"),
        ({"source": "region:Nowhere"}, "no selector resolved"),
    ],
)
def test_nodes_refuse_bad_values_with_400(client, params, needle):
    reply = client.get("/api/world/nodes", params=params)
    assert reply.status_code == 400
    assert needle in reply.json()["error"]


def test_node_notes_are_page_words_not_engine_names(client):
    body = client.get("/api/world/nodes", params={"resource": "Water"}).json()
    text = " ".join(body["notes"])
    assert body["notes"] and "open water is not a node" in text
    for raw in ("FGWaterVolume", "mExtractableResource", "NO NODE", "LOCKED", "BP_", "Build_"):
        assert raw not in text
    for n in [n for n in body["notes"] if "left out of free" in n]:
        figure = n.split(" per min")[0]
        assert figure.replace(",", "").isdigit()
        assert len(figure) < 4 or "," in figure


def test_nodes_survive_a_save_that_cannot_be_read(game):
    with _broken(game) as c:
        reply = c.get("/api/world/nodes", params={"resource": "Iron Ore"})
    assert reply.status_code == 200
    body = reply.json()
    assert "sidecar produced no output" in body["save_error"]
    assert {r["status"] for r in body["nodes"]} == {"free"}


def test_nodes_mark_rows_a_later_build_moved(client, monkeypatch):
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()
    leaf = table.nodes[0]["instance"]
    fake = nodes_mod.TableSkew(
        pin={"build_version": 1},
        measured_against={"build_version": 2},
        save={"build_version": 2},
        moved_cm={leaf: 40.0},
        dz_cm={leaf: -40.0},
        unjoinable=(),
        renamed_to={},
        renamed_moved_cm=None,
        vertical_only=True,
        resource_and_purity_verified=True,
    )
    monkeypatch.setattr(nodes_mod, "skew_for_save", lambda header, table=None: fake)
    body = client.get("/api/world/nodes").json()
    moved = [r for r in body["nodes"] if r["moved"]]
    assert [r["id"] for r in moved] == [leaf]
    assert body["stale"]["behind"] and body["stale"]["moved"] == 1
    assert body["stale"]["notes"]


# --------------------------------------------------------------------- sites


def test_sites_rank_one_resource_best_first(client):
    body = client.get("/api/world/sites", params={"resource": "Iron Ore", "limit": 5}).json()
    assert body["resource"] == "Desc_OreIron_C" and body["resource_name"] == "Iron Ore"
    assert len(body["sites"]) == 5 and body["count"] >= 5
    assert [s["rank"] for s in body["sites"]] == [1, 2, 3, 4, 5]
    scores = [s["score"] for s in body["sites"]]
    assert scores == sorted(scores, reverse=True)
    assert set(body["weights"]) == {"throughput", "spread", "distance", "purity", "roughness"}


def test_sites_refusals(client, game):
    assert client.get("/api/world/sites").status_code == 422
    assert (
        client.get("/api/world/sites", params={"resource": "Iron Ore", "limit": 51}).status_code
        == 422
    )
    assert client.get("/api/world/sites", params={"resource": "Unobtainium"}).status_code == 400
    reply = client.get(
        "/api/world/sites", params={"resource": "Iron Ore", "source": "region:Nowhere"}
    )
    assert reply.status_code == 400
    with _broken(game) as c:
        assert c.get("/api/world/sites", params={"resource": "Iron Ore"}).status_code == 404


# ------------------------------------------------------------------ conduits


def test_conduits_near_me_page_runs_with_their_lines(client):
    body = client.get("/api/world/conduits", params={"limit": 20}).json()
    assert body["view"] == "runs" and body["where"] == "you"
    assert len(body["runs"]) == 20 and body["total"] > 20
    assert body["belts"] + body["pipes"] == body["total"]
    run = body["runs"][0]
    assert run["id"].startswith(("chain:", "pipe:"))
    assert run["lines_m"] and len(run["lines_m"][0][0]) == 2
    assert run["distance_m"] <= 250
    assert [r["length_m"] for r in body["runs"]] == sorted(
        (r["length_m"] for r in body["runs"]), reverse=True
    )
    later = client.get("/api/world/conduits", params={"limit": 20, "offset": 20}).json()
    assert later["offset"] == 20 and later["runs"][0]["id"] not in {r["id"] for r in body["runs"]}


def test_conduits_networks_view_and_network_filter(client):
    nets = client.get("/api/world/conduits", params={"view": "networks"}).json()
    assert nets["runs"] == [] and nets["networks"]
    net = next(n for n in nets["networks"] if n["network"] is not None)
    runs = client.get(
        "/api/world/conduits", params={"network": net["network"], "limit": 500}
    ).json()
    assert runs["total"] == net["pieces"]
    assert {r["network"] for r in runs["runs"]} == {net["network"]}


def test_conduits_run_picks_one(client):
    body = client.get("/api/world/conduits", params={"run": "chain:7"}).json()
    assert [r["id"] for r in body["runs"]] == ["chain:7"]


@pytest.mark.parametrize(
    "params, code",
    [
        ({"conduit_kind": "rope"}, 400),
        ({"view": "grid"}, 400),
        ({"view": "networks", "conduit_kind": "belt"}, 400),
        ({"near": "nowhere at all"}, 400),
        ({"run": "chain:99999999"}, 400),
        ({"radius_m": 2001}, 422),
        ({"limit": 0}, 422),
    ],
)
def test_conduits_refusals(client, params, code):
    assert client.get("/api/world/conduits", params=params).status_code == code


def test_conduits_need_a_save(game):
    with _broken(game) as c:
        assert c.get("/api/world/conduits").status_code == 404


# ------------------------------------------------------------------- regions


def test_regions_list_and_filter(client):
    every = client.get("/api/world/regions").json()
    assert every["resource"] is None and every["rows"] and every["accuracy_m"] == 64
    crude = client.get("/api/world/regions", params={"resource": "Crude Oil"}).json()
    assert crude["resource_name"] == "Crude Oil"
    assert all(r["nodes"] > 0 for r in crude["rows"])
    hidden = client.get(
        "/api/world/regions", params={"resource": "Crude Oil", "spoilers": 0}
    ).json()
    assert hidden["hidden_spoilers"] > 0
    assert sum(r["nodes"] for r in hidden["rows"]) < sum(r["nodes"] for r in crude["rows"])
    assert client.get("/api/world/regions", params={"resource": "Unobtainium"}).status_code == 400


# ------------------------------------------------------------ pinning and host


@pytest.mark.parametrize("path, params", ROUTES)
def test_every_world_route_is_pinned_by_as_of(client, state, path, params):
    assert client.get(path, params={**params, "as_of": state.token}).status_code == 200
    reply = client.get(path, params={**params, "as_of": STALE_TOKEN})
    assert reply.status_code == 409 and reply.json()["error"] == STALE


@pytest.mark.parametrize("path, params", ROUTES)
def test_every_world_route_refuses_a_foreign_host(client, path, params):
    assert client.get(path, params=params, headers={"host": "evil.example"}).status_code == 403
