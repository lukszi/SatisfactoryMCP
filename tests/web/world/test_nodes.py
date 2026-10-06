"""``/api/nodes``: the static node table, joined to what this save built on it.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``
or by building its own app -- so nothing in this file spawns the sidecar or reads a
``.sav``.
"""

from __future__ import annotations

import pytest

from tests.support.web import client_over, failing_state_loader

fastapi = pytest.importorskip("fastapi")


from satisfactory_mcp.domain.spatial import nodes as spatial_nodes


def test_nodes_carry_the_occupancy_join_and_a_reusable_selector(client, state):
    body = client.get("/api/nodes").json()
    rows = body["nodes"]
    assert rows, "the committed node table is not empty"
    row = rows[0]
    assert set(row) >= {"id", "resource", "name", "purity", "x_m", "y_m", "z_m", "occupied"}
    # The id must survive intact: the page offers it as a `node:` selector for the MCP
    # tools, and a shortened one would not resolve there.
    assert row["id"].startswith("Persistent_Level:")
    assert row["name"] == row["id"].rsplit(".", 1)[-1]
    # Occupancy resolution is partial by design, but this save has extractors placed.
    assert body["occupied"] > 0
    assert any(r["occupant_cls"] for r in rows if r["occupied"])


def test_nodes_survive_a_save_that_cannot_be_read(game):
    """The node table is static and needs no ``.sav`` -- the rule /api/inspect already
    follows. A failed save costs the occupancy join, never the geography, and the loss
    is said out loud: ``save_error`` set, ``occupied`` null rather than a measured 0."""
    with client_over(failing_state_loader, game) as c:
        r = c.get("/api/nodes")
    assert r.status_code == 200
    body = r.json()
    assert "sidecar produced no output" in body["save_error"]
    assert body["occupied"] is None
    assert len(body["nodes"]) > 500
    assert all(row["occupied"] is False for row in body["nodes"])


def test_nodes_resolve_their_occupant_to_a_display_name(client):
    """One popup, one vocabulary: 'occupied by Miner Mk.2', not Build_MinerMk2_C."""
    rows = client.get("/api/nodes").json()["nodes"]
    held = [r for r in rows if r["occupied"]]
    assert held
    for row in held:
        assert row["occupant_name"], row
        assert not row["occupant_name"].startswith("Build_")
    assert all(r["occupant_name"] is None for r in rows if not r["occupied"])


def test_nodes_resolve_their_resource_to_the_word_the_tools_use(client):
    """One vocabulary across both surfaces: 'Iron Ore', not OreIron and not Desc_OreIron_C.

    The class id stays beside it, because it is what the layer keys and the ore colours are
    keyed by -- the page needs both and must not have to cut one out of the other.

    ``Desc_Geyser_C`` is the case that makes this more than a lookup: a geyser is a placement
    target rather than an item, so the docs dump has no entry for it and ``item_name`` alone
    would hand the class id straight back into the popup.
    """
    rows = client.get("/api/nodes").json()["nodes"]
    names = {r["resource"]: r["resource_name"] for r in rows}
    assert names["Desc_OreIron_C"] == "Iron Ore"
    assert names["Desc_OreGold_C"] == "Caterium Ore"
    assert names["Desc_Geyser_C"] == "Geyser"
    assert not any(n.startswith("Desc_") for n in names.values()), names


def test_nodes_can_be_filtered_by_resource(client):
    body = client.get("/api/nodes", params={"resource": "Desc_OreIron_C"}).json()
    assert body["resource"] == "Desc_OreIron_C"
    assert body["nodes"]
    assert {r["resource"] for r in body["nodes"]} == {"Desc_OreIron_C"}


def test_nodes_say_which_of_them_this_world_cannot_work_yet(client, state):
    """The LOCKED state the text surface has and the map did not.

    ``search_resource_nodes`` prints ``LOCKED`` and leaves these out of free capacity, so a
    map that drew them as ordinary free dots was inviting a plan onto a node nothing the
    player has researched can extract from. Checked against the domain predicate rather than
    against a hardcoded count: which nodes are locked is a fact about this save's research,
    and it changes as the reference save advances.
    """
    rows = client.get("/api/nodes").json()["nodes"]
    table = {n["instance"]: n for n in spatial_nodes.load_nodes().nodes}
    unlocked = state.unlocked_building_ids
    for row in rows:
        assert row["reachable"] == spatial_nodes.reachable(table[row["id"]], unlocked), row
    # Both states are present on this save -- an agreement over one answer is not agreement.
    assert any(r["reachable"] is False for r in rows)
    assert any(r["reachable"] is True for r in rows)


def test_a_save_that_cannot_be_read_leaves_reachability_unknown_rather_than_true(game):
    """Null, never true: reachability is a claim about what this world has researched.

    The node table needs no ``.sav`` and the unlock set is nothing but ``.sav``, so the two
    halves of a row part company here. ``reachable`` defaults to true one layer down -- which
    is right for a capacity sum over no world and wrong for a dot somebody plans around.
    """
    with client_over(failing_state_loader, game) as c:
        rows = c.get("/api/nodes").json()["nodes"]
    assert rows
    assert all(row["reachable"] is None for row in rows)


def test_nodes_carry_the_region_they_sit_in(client):
    """Joined server-side, so the raster -- and its orientation trap -- lives in one place.

    ``verified`` used to appear here and does not any more. It meant "a human read this
    node's region off the wiki's biome image", which was the best answer available while the
    region geometry was a trace of that image; the geometry is the game's own map areas now,
    so the override table it justified is gone and every node is named by where it stands.
    ``unnamed`` is the word that replaced it in this set: the game leaves the outer coast to
    ``No Man's Land``, and nodes stand there.
    """
    rows = client.get("/api/nodes").json()["nodes"]
    named = [r for r in rows if r["region"]]
    assert len(named) > 500, "the raster covers the nodes; a handful of nulls is the limit"
    known = set(client.get("/api/regions").json()["regions"])
    assert {r["region"]["name"] for r in named} <= known
    # The confidence word travels with the name, or a boundary guess reads as a fact.
    assert {r["region"]["confidence"] for r in named} <= {
        "unnamed",
        "boundary",
        "interior",
    }
    assert "verified" not in {r["region"]["confidence"] for r in named}, (
        "verified was a claim about a wiki image and there is no wiki image any more"
    )
    assert any(r["region"]["confidence"] == "interior" for r in named)


def test_a_node_is_a_spoiler_exactly_when_it_is_locked(client):
    rows = client.get("/api/nodes").json()["nodes"]
    for row in rows:
        assert row["spoiler"] == (row["reachable"] is False and not row["occupied"]), row
    assert any(r["spoiler"] for r in rows) and not all(r["spoiler"] for r in rows)


def test_without_a_save_no_node_is_a_spoiler(game):
    with client_over(failing_state_loader, game) as c:
        rows = c.get("/api/nodes").json()["nodes"]
    assert not any(r["spoiler"] for r in rows)
