"""The resource node table: complete against the save both ways, with its provenance
and its cross-checks against the installed game recorded.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def node_table():
    return nodes_mod.load_nodes()


def test_node_table_matches_the_save_in_both_directions(node_table, projection):
    """The join must be checked BOTH ways.

    The original check only asserted that every table entry appears in the save,
    which passed at 607/607 while the table was silently missing a node the save
    contained -- a pure Limestone node absent from the SCIM extract. Counting save
    actors against table rows is what caught it.
    """
    counts = projection["building_counts"]
    by_kind: dict[str, int] = {}
    for n in node_table.nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1

    assert by_kind["node"] == counts["BP_ResourceNode_C"]
    assert by_kind["well_sat"] == counts["BP_FrackingSatellite_C"]
    assert by_kind["geyser"] == counts["BP_ResourceNodeGeyser_C"]


#: Where the recovered pure Limestone node stands, centimetres. See the test below for
#: why this is the anchor and an instance name is not.
_RECOVERED_LIMESTONE_XY = (-279_020.0, -186_294.0)


def test_the_recovered_limestone_node_is_present(node_table):
    """A pure Limestone node that the third-party extract omitted is in this table.

    The node it protects is the one recovered from the game's own assets: the SCIM extract
    was missing it, both counts read 607, and only counting save actors against table rows
    caught it. That requirement is unchanged and is what is asserted here.

    What changed is the ANCHOR. This test used to name ``BP_ResourceNode11``, and a game
    update renamed that instance -- it exists in no current save, so the assertion had
    quietly become a claim about a build nobody runs. An instance name is exactly the wrong
    identity for a node that a map change can rename, which is the failure the skew gate
    exists to make loud. Position, resource and purity are what actually survive: the
    installed build confirms both attributes on every compared row, and the renamed row
    moved 150 cm, so a metres-wide tolerance around the recorded spot still names one node
    and only one. The nearest other pure Limestone node is 51 m away.
    """
    hits = [
        n
        for n in node_table.nodes
        if n["resource"] == "Desc_Stone_C"
        and n["purity"] == "pure"
        and geo.distance_m((n["x"], n["y"]), _RECOVERED_LIMESTONE_XY) <= 10.0
    ]
    assert len(hits) == 1, [n["instance"] for n in hits]
    assert hits[0]["kind"] == "node"


def test_the_recovered_limestone_node_is_joinable_or_disclosed(node_table, projection):
    """The other half of the old docstring: "and in the save".

    That clause is no longer true of the id the table ships, and it is the reason the
    count check above cannot see the problem -- 459 stays 459 across a rename. So the
    requirement becomes a disjunction, and either branch is acceptable: the row's instance
    name is one this save can join, OR the software says out loud that it cannot. What is
    NOT acceptable is a row whose name no save carries while every tool stays quiet,
    because that is the case that answers confidently and wrongly.
    """
    node = next(
        n
        for n in node_table.nodes
        if n["resource"] == "Desc_Stone_C"
        and n["purity"] == "pure"
        and geo.distance_m((n["x"], n["y"]), _RECOVERED_LIMESTONE_XY) <= 10.0
    )
    skew = nodes_mod.skew_for_save(projection["header"], node_table)
    disclosed = nodes_mod.identity_notes(skew, [node["instance"]])
    unjoinable = node["instance"] in (skew.unjoinable if skew else ())

    if unjoinable:
        assert disclosed, "the row this save cannot join is reported by nothing"
        assert node["instance"].rsplit(".", 1)[-1] in disclosed[0]
        # And the disclosure must lead somewhere: a name with no replacement is a dead
        # end for anyone trying to find the node in their own save.
        assert skew.renamed_to.get(node["instance"]), skew
    else:
        assert disclosed == [], disclosed


def test_deposits_and_cores_are_excluded_from_the_node_table(node_table):
    """A deposit is hand-mineable only and a fracking core produces nothing, so
    neither may advertise capacity."""
    kinds = {n["kind"] for n in node_table.nodes}
    assert kinds == {"node", "well_sat", "geyser"}


def test_every_well_satellite_keeps_its_core_link(node_table):
    """Well rate is summed per core, so a satellite without one is unplannable."""
    sats = [n for n in node_table.nodes if n["kind"] == "well_sat"]
    assert sats
    assert all(n["well_core"] for n in sats)


def test_sources_are_recorded_with_licences(node_table):
    """The source names its provenance and the game build it was read at.

    Two literals have died here, each for the right reason. ``"1.2.0.0" in ...`` described
    the GPL-3.0 SCIM table that supplied the satellite/core mapping; ``licence == "MIT"``
    described the vendored rockfactory node set that supplied everything else. Both are
    deleted: the node set, resource, purity, position AND the well link now come from
    ``data/world_resource_nodes.json``, this repository's own extraction from the installed
    game's map package, so the claims pinned are the new ones -- first-party, no external
    licence or attribution obligation, and still pinned to the build it was read at so a
    consumer can tell whether the table predates the build they are running. The
    requirement that provenance stays RECORDED is the part that has never changed.
    """
    sources = node_table.meta["sources"]
    primary = sources["primary"]
    assert primary["name"] == "data/world_resource_nodes.json"
    assert "first-party" in primary["licence"]
    assert "no external licence" in primary["licence"]
    assert "no attribution" in primary["licence"]
    assert "502094" in primary["game_version_pinned"]
    # And the two retired sources stay on the record rather than being tidied away.
    assert "retired_mit_table" in sources["retired"]
    assert "GPL" in sources["retired"]


def test_purity_and_resource_agree_with_the_installed_game(node_table):
    """Purity and resource are the installed game's own, via the projection this table is.

    These assertions have meant three things in their life. First "agrees with the SCIM
    table", then -- SCIM deleted -- "agrees with ``mPurity``/``mResourceClass`` in the
    installed build's ``Persistent_Level.umap``", recorded as mismatch lists because the
    node set itself was still third-party. Now the third party is gone too and the node set
    IS that package read (``data/world_resource_nodes.json``), so recorded mismatch lists
    against it would be the generator agreeing with itself. What is checked instead is the
    same claim with no generator in the loop: the served table re-joined against the
    committed world table, row for row, on identity, resource and purity. Geysers are the
    one mapped value -- the asset carries no ``mResourceClass``, and the synthetic label
    says so in ``_meta.geyser_note``.
    """
    world = json.loads(
        (REPO_ROOT / "data" / "world_resource_nodes.json").read_text(encoding="utf-8")
    )
    rows = {r["id"]: r for r in world["nodes"] if r["class"] != "BP_FrackingCore_C"}
    assert {n["instance"].rsplit(".", 1)[-1] for n in node_table.nodes} == set(rows)
    for n in node_table.nodes:
        src = rows[n["instance"].rsplit(".", 1)[-1]]
        assert n["purity"] == src["purity"], n["instance"]
        assert n["resource"] == (src["resource"] or "Desc_Geyser_C"), n["instance"]
        if n["kind"] == "well_sat":
            assert n["well_core"].rsplit(".", 1)[-1] == src["core"], n["instance"]
    assert "geyser" in node_table.meta["geyser_note"].casefold()


def test_position_deltas_are_recorded_against_both_builds(node_table):
    """One comparison now, because there is only one build in play -- and it must be exact.

    The two-block record this name still remembers ("against the build this table was cut
    from" AND "against the installed build") existed because the vendored node set was cut
    from an older build than the one installed, and a single delta could not be honest
    about that. The table is now cut FROM the installed build, so a second block would be
    manufacturing a build to lag. What survives is every property that made the record
    trustworthy: the one block declares its rounding floor, its measured worst delta sits
    inside that floor, no row is named past it (a summary must never outrun its rows),
    nothing is missing on either side, and the build, method and date stamps are present.
    These are exactly the preconditions under which the skew gate in
    ``domain/spatial/nodes.py`` finds a pin and no drift, and stays silent -- which
    ``tests/data/test_node_table_skew`` asserts against the reference save.
    """
    positions = node_table.meta["cross_validation"]["positions"]
    assert set(positions) == {"against_the_installed_build"}
    block = positions["against_the_installed_build"]

    floor = block["rounding_floor_cm"]
    worst = block["max_position_delta_cm"]
    assert isinstance(worst, (int, float)), "no measured delta stated"
    assert 0 < floor < 1.0
    assert 0 <= worst <= floor
    assert block["rows_past_the_rounding_floor"] == []
    assert block["rows_only_in_this_table"] == []
    assert block["rows_only_in_the_installed_build"] == []
    assert block["rows_compared"] == len(node_table.nodes)
    assert block["measured"] and block["build"] and block["method"]
    assert "502094" in block["build"]
