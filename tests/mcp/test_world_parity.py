"""One question, one answer: each World route against the MCP tool that asks the same thing.

The tool's text is parsed back into ids and numbers and compared with the route's JSON over
the fixture world, so a change to either side that makes them disagree fails here.
"""

from __future__ import annotations

import pytest

from tests.support import tables

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.mcp.tools import collectibles, spatial  # noqa: E402


@pytest.fixture
def tools(state, use_world):
    use_world(state)
    return spatial


def test_nearest_free_iron_lists_the_same_nodes_in_the_same_order(tools, client):
    text = tools.search_resource_nodes(
        resource="Iron Ore", only_free=True, show="nearest", near="me", limit=100
    )
    tool_ids = [row[0] for row in tables.rows(text, "node_id")]
    body = client.get(
        "/api/world/nodes",
        params={"resource": "Iron Ore", "status": "free", "near": "me"},
    ).json()
    by_distance = sorted(body["nodes"], key=lambda r: r["distance_m"])
    assert tool_ids == [r["name"] for r in by_distance][: len(tool_ids)]
    assert f"{body['count']} node(s)" in text.splitlines()[0]


def test_the_census_line_matches_the_tools_header(tools, client):
    text = tools.search_resource_nodes(resource="Iron Ore", only_free=True)
    body = client.get("/api/world/nodes", params={"resource": "Iron Ore", "status": "free"}).json()
    head = text.splitlines()[0]
    assert f"{body['count']} node(s)" in head
    assert f"{body['total']:g} /min total" in head
    assert f"{body['free']:g} free and reachable" in head


def test_fields_have_the_same_centres_and_totals(tools, client):
    text = tools.search_resource_nodes(resource="Copper Ore", show="fields", limit=100)
    rows = tables.rows(text, "region\tgrid")
    body = client.get(
        "/api/world/nodes", params={"view": "fields", "resource": "Copper Ore"}
    ).json()
    assert f"{len(body['fields'])} match(es)" in text
    for row, f in zip(rows, body["fields"], strict=False):
        cx, cy = (int(v) for v in row[3].split(","))
        assert abs(cx - f["x_m"]) <= 1 and abs(cy - f["y_m"]) <= 1
        assert int(row[4]) == f["size"]
        assert float(row[6]) == pytest.approx(f["total"])
        assert float(row[7]) == pytest.approx(f["free"])


def test_sites_come_in_the_same_order(tools, client):
    text = tools.rank_build_sites(resource="Copper Ore", limit=10)
    rows = tables.rows(text, "score")
    body = client.get("/api/world/sites", params={"resource": "Copper Ore", "limit": 10}).json()
    assert len(rows) == len(body["sites"]) == 10
    for row, site in zip(rows, body["sites"], strict=True):
        cx, cy = (int(v) for v in row[3].split(","))
        assert abs(cx - site["x_m"]) <= 1 and abs(cy - site["y_m"]) <= 1
    assert [float(r[0]) for r in rows] == pytest.approx(
        [s["score"] for s in body["sites"]], abs=0.01
    )


def test_conduits_near_me_are_the_same_runs(tools, client):
    text = tools.search_conduits(near="me", limit=50)
    tool_ids = [row[0] for row in tables.rows(text, "id\tkind")]
    body = client.get("/api/world/conduits", params={"limit": 50}).json()
    assert tool_ids == [r["id"] for r in body["runs"]][: len(tool_ids)]
    assert f"# {body['total']} conduit run(s) within 250m of you" in text


def test_networks_are_the_same_systems(tools, client):
    text = tools.search_conduits(near="me", show="networks", limit=50)
    tool = [row[0] for row in tables.rows(text, "network\tcarries")]
    body = client.get("/api/world/conduits", params={"view": "networks"}).json()
    assert tool == [
        str(n["network"]) if n["network"] is not None else "-" for n in body["networks"]
    ]


def test_here_matches_whereami(tools, client):
    text = tools.whereami(limit=50)
    body = client.get("/api/world/here").json()
    assert f"# {body['nodes_total']} node(s) within 500m" in text
    rows = tables.rows(text, "resource\tpurity")
    assert [r[3] for r in rows] == [f"{n['distance_m']:.0f}m" for n in body["nodes"]]


def test_regions_match_list_regions(tools, client):
    text = tools.list_regions(resource="Iron Ore")
    rows = tables.rows(text, "region\tdir")
    body = client.get("/api/world/regions", params={"resource": "Iron Ore"}).json()
    assert [(r[0], int(r[5])) for r in rows] == [(r["name"], r["nodes"]) for r in body["rows"]]


def test_the_census_numbers_match_collected_from_world(tools, client, state):
    text = collectibles.collected_from_world()
    rows = {r[0]: r for r in tables.rows(text, "category\tplaced")}
    body = client.get("/api/collectibles", params={"mode": "census"}).json()
    assert body["census"]
    for c in body["census"]:
        row = rows[c["category"]]
        assert int(row[1]) == c["placed"] and int(row[2]) == c["collected"]
    collected = sum(c["collected"] for c in body["census"])
    assert f"whole_world_collected={collected}" in text


@pytest.fixture
def followed(tools, state):
    from satisfactory_mcp.domain.session import journal

    journal.set_writer("chat")
    return lambda: journal.read(state.world_id, limit=50)


def test_finder_calls_tell_a_following_page_where_to_go(tools, followed):
    tools.search_resource_nodes(resource="Iron Ore", show="nodes", only_free=True, near="me")
    tools.rank_build_sites(resource="Copper Ore", sources=["near:hub@1500", "purity:pure"])
    tools.rank_build_sites(resource="Copper Ore", sources=["region:Grass Fields"])
    tools.search_conduits(near="me", conduit_kind="pipe")
    tools.whereami()
    collectibles.collected_from_world(show="nearest", group="somersloop")
    found = [(e["kind"], e["tool"], e["args"]) for e in followed()]
    assert found == [
        (
            "world.find",
            "search_resource_nodes",
            {
                "view": "nodes",
                "params": {"resource": "Desc_OreIron_C", "status": "free", "near": "me"},
            },
        ),
        (
            "world.find",
            "rank_build_sites",
            {
                "view": "rank",
                "params": {
                    "resource": "Desc_OreCopper_C",
                    "at": "hub",
                    "within_m": "1500",
                    "pure": "1",
                },
            },
        ),
        (
            "world.find",
            "rank_build_sites",
            {"view": "rank", "params": {"resource": "Desc_OreCopper_C"}},
        ),
        (
            "world.find",
            "search_conduits",
            {"view": "conduits", "params": {"near": "me", "conduit_kind": "pipe"}},
        ),
        ("world.find", "whereami", {"view": "", "params": {}}),
        (
            "world.find",
            "collected_from_world",
            {"view": "pickups", "params": {"view": "nearest", "group": "somersloop"}},
        ),
    ]
