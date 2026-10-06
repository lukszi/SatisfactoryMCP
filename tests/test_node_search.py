"""search_resource_nodes in show="nearest", and how its origin is resolved.

Distance ranking lives in the same tool as the yield-ranked field and node views: one
surface, three modes. The location grammar is the part worth pinning. Accepting a factory
name is the reason the mode has this shape -- "the nearest free coal to the coal
powerplant" is the question actually asked, and hand-copying a centroid out of another
tool's output is how the wrong coordinate gets used.
"""

from __future__ import annotations

import math

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.factories.labels import LabelStore
from satisfactory_mcp.domain.spatial.places import resolve_place
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration


class _Fake(WorldState):
    """A WorldState with labels we control, rather than whatever is on this machine."""

    def __init__(self, projection, game, labels):
        super().__init__(projection=projection, game=game)
        self._labels = labels

    @property
    def labels(self):  # type: ignore[override]
        return self._labels


def _state_with_label(game, name="probe", positions=((1000.0, 2000.0), (3000.0, 4000.0))):
    machines = [
        {
            "instance": f"L:P.Build_SmelterMk1_C_{i}",
            "cls": "Build_SmelterMk1_C",
            "recipe": "Recipe_IngotIron_C",
            "pos": [x, y, 0.0],
        }
        for i, (x, y) in enumerate(positions)
    ]
    projection = {"machines": machines, "extractors": [], "generators": [], "players": []}
    store = LabelStore(world_id="TEST")
    store.put(name, [m["instance"].rsplit(".", 1)[-1] for m in machines])
    return _Fake(projection, game, store)


def test_a_coordinate_is_read_as_metres(game):
    """Every coordinate in this MCP is quoted in metres; the save stores centimetres.
    Getting this wrong silently searches 100x too far away."""
    origin, where = resolve_place(_state_with_label(game), "-1069,-1273")
    assert origin == (-106_900.0, -127_300.0)
    assert where == "-1069,-1273"


def test_a_factory_name_resolves_to_its_centroid(game):
    st = _state_with_label(game)
    origin, where = resolve_place(st, "probe")
    assert origin == (2000.0, 3000.0)
    assert where == "probe"


def test_an_unknown_location_lists_what_is_known(game):
    st = _state_with_label(game, name="steel factory")
    with pytest.raises(ValueError, match="steel factory"):
        resolve_place(st, "nowhere")


def test_a_bad_coordinate_is_rejected_rather_than_guessed(game):
    with pytest.raises(ValueError, match="x,y pair"):
        resolve_place(_state_with_label(game), "12,north")


def test_me_needs_a_player_pawn(game):
    st = _state_with_label(game)
    with pytest.raises(ValueError, match="no player pawn"):
        resolve_place(st, "me")


def test_a_bare_platform_resolves_by_the_index_that_names_it(game):
    """factory_map lists bare platforms by an index that used to lead nowhere: `slab:N`
    is refused by the machine selector precisely because there are no machines on it,
    which is the case a reader asks about. A location grammar has no such problem."""
    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            # 3x3 tiles of 8 m, so the tile mean is the middle tile's own centre.
            "instances": [[0, gx * 800, gy * 800, 0] for gx in range(3) for gy in range(3)],
        },
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    origin, where = resolve_place(st, "slab:0")
    assert origin == (800.0, 800.0)
    assert where.startswith("slab:0 (9 tiles,")

    with pytest.raises(ValueError, match=r"out of range \(0\.\.0\)"):
        resolve_place(st, "slab:7")
    with pytest.raises(ValueError, match="integer index"):
        resolve_place(st, "slab:middle")
    with pytest.raises(ValueError, match="needs a readable save"):
        resolve_place(None, "slab:0")


def _rows(out: str) -> list[dict]:
    """Parse the tab table by HEADER, not by column position.

    The merged tool keeps a `grid` column the standalone one did not have, so
    index-based parsing silently read the wrong field and compared a coordinate against
    "X3Y2".
    """
    lines = [x for x in out.splitlines() if "\t" in x]
    if not lines:
        return []
    headers = lines[0].split("\t")
    return [dict(zip(headers, line.split("\t"), strict=False)) for line in lines[1:]]


def _dist(row: dict) -> int:
    return int(row[next(k for k in row if k.startswith("dist"))].rstrip("m"))


def test_nodes_come_back_nearest_first(game):
    """The whole point of the mode: the other two rank by yield."""
    out = srv.search_resource_nodes(resource="Coal", show="nearest", near="0,0", limit=8)
    assert not out.startswith("! ")
    distances = [_dist(r) for r in _rows(out)]
    assert distances, out
    assert distances == sorted(distances), distances


def test_distance_is_measured_from_the_given_origin(game):
    """A node's reported distance must match its reported coordinate."""
    out = srv.search_resource_nodes(resource="Coal", show="nearest", near="0,0", limit=3)
    rows = _rows(out)
    assert rows
    for row in rows:
        x_m, y_m = (float(v) for v in row["x,y(m)"].split(","))
        assert _dist(row) == pytest.approx(math.dist((x_m, y_m), (0, 0)), abs=2)


def test_the_distance_column_names_the_origin(game):
    """So a reader of the table knows what the number is measured from."""
    out = srv.search_resource_nodes(resource="Coal", show="nearest", near="0,0", limit=2)
    assert any(k.startswith("dist to ") for k in _rows(out)[0])


def test_nearest_without_an_origin_says_so(game):
    """Silently falling back to yield order would answer a different question."""
    out = srv.search_resource_nodes(resource="Coal", show="nearest")
    assert out.startswith("! show='nearest' needs near=")


def test_an_unknown_view_lists_the_views(game):
    out = srv.search_resource_nodes(resource="Coal", show="bogus")
    assert "fields, nodes, nearest" in out


def test_the_old_group_argument_names_show(game):
    """`group` predated `mode`, which `show` has now replaced -- and `group` still means a
    real category filter on collected_from_world, so one word meant two things."""
    out = srv.search_resource_nodes(resource="Coal", group="nodes", limit=3)
    assert out == "! group='nodes' is retired -- write show='nodes' instead"


def test_a_tapped_node_names_the_miner_on_it_and_its_clock(game):
    """``tapped_by`` and ``tapped_clock`` were computed for every node on every call and
    then rendered as the bare word "tapped", so "which miner is on that node, at what
    clock, is it worth reclaiming" was thrown away on each one."""
    rows = _rows(srv.search_resource_nodes(resource="Coal", show="nodes", limit=25))
    tapped = [r for r in rows if r["status"] == "tapped"]
    assert tapped, "no coal node on this save is tapped, so this proves nothing"
    assert all("@" in r["occupant"] for r in tapped), tapped
    assert all(r["occupant"] == "-" for r in rows if r["status"] != "tapped")


def test_a_switched_off_miner_is_not_a_producing_one(game):
    """``occupancy`` reads ``paused`` and ``annotate`` used to drop it, so a node with a
    switched-off miner on it read exactly like a node being mined."""
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod
    from satisfactory_mcp.interfaces.mcp.tools.spatial.nodes import _occupant

    node = {
        "instance": "L:P.BP_ResourceNode_1",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "kind": "node",
        "resource": "Desc_Coal_C",
        "purity": "pure",
    }
    off = {
        "extractors": [
            {
                "instance": "L:P.Build_MinerMk2_C_1",
                "cls": "Build_MinerMk2_C",
                "node": node["instance"],
                "clock": 2.5,
                "paused": True,
                "pos": [10.0, 20.0, 30.0],
            }
        ]
    }
    row = nodes_mod.annotate([node], game, off)[0]
    assert row["tapped"] and row["tapped_paused"] is True
    assert row["tapped_pos"] == [10.0, 20.0, 30.0]
    assert _occupant(row, game).endswith("@250% OFF")

    free = nodes_mod.annotate([node], game, {})[0]
    assert free["tapped"] is False and free["tapped_paused"] is None
    assert _occupant(free, game) == "-"


def test_only_free_narrows_the_nearest_list(game):
    everything = srv.search_resource_nodes(resource="Coal", show="nearest", near="0,0", limit=25)
    free_only = srv.search_resource_nodes(
        resource="Coal", show="nearest", near="0,0", limit=25, only_free=True
    )
    assert "tapped" in everything
    assert "tapped" not in free_only


# ---------------------------------------------------------- elevation


def test_node_rows_carry_elevation(game):
    """z was in the node table and in every machine position all along, read by nothing
    but geo.cluster's centroid. The planner concluded the tool "has no z-data" and
    guessed pump counts by hand."""
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()
    assert all("z" in n for n in table.nodes)
    out = srv.search_resource_nodes(resource="Crude Oil", show="nodes", limit=3)
    assert "z(m)" in out


def test_a_fluid_field_reports_its_head_span(game):
    """The number that decides pump counts. Reported as a SPAN, never as a pump count:
    head per pump is a game rule this project has no data for."""
    out = srv.search_resource_nodes(
        resource="Crude Oil", sources=list(REFERENCE_FIELD), show="nodes", limit=1
    )
    assert "elevation" in out
    assert "span 40m" in out, out.splitlines()[2]
    assert "pump" in out.lower()


def test_a_solid_field_says_nothing_about_head(game):
    """Elevation is a fluid concern. A coal field climbing 200 m costs a belt nothing."""
    out = srv.search_resource_nodes(resource="Coal", show="nodes", limit=1)
    assert "elevation" not in out


def test_no_pump_count_is_invented(game):
    """The tool must not turn a head span into a number of pumps until head-per-pump is
    sourced. Naming the cost is the deliverable; guessing it is not."""
    out = srv.search_resource_nodes(
        resource="Crude Oil", sources=list(REFERENCE_FIELD), show="nodes", limit=1
    )
    header = out[: out.index("node_id")]
    assert "pumps needed" not in header
    assert "1 pump" not in header


def _node_ids(out: str) -> list[str]:
    return [line.split("\t")[0] for line in out.splitlines() if line.startswith("BP_")]


def test_the_iron_tail_is_reachable(game):
    """127 iron nodes behind a 25-row cap, and the envelope's "call again with offset=N"
    named a parameter the schema did not have -- so the tail could not be read at all.

    Pinned on the ids rather than the count: a page that repeated the first page's rows
    would satisfy a length check and still answer the wrong question."""
    first = srv.search_resource_nodes(resource="Iron Ore", show="nodes", limit=5)
    second = srv.search_resource_nodes(resource="Iron Ore", show="nodes", limit=5, offset=5)
    assert "showing 5 from offset 0." in first
    assert "call again with offset=5" in first
    assert "showing 5 from offset 5." in second
    assert _node_ids(first) and _node_ids(second)
    assert not set(_node_ids(first)) & set(_node_ids(second))


def test_a_ranking_says_what_it_can_do_instead_of_naming_an_offset(game):
    """rank_build_sites orders candidates by score, so paging it would hand back the
    fields it already judged worse. The envelope has to offer what the tool actually
    takes rather than an argument it would reject."""
    out = srv.rank_build_sites(resource="Iron Ore", limit=1)
    assert "call again with offset" not in out
    assert "raise limit, or narrow with sources=" in out


def test_top_still_means_limit(game):
    """`top=` was this tool's private spelling of the row cap. Renaming it outright would
    break every stored call, so it is still accepted and still wins where both are given."""
    assert srv.rank_build_sites(resource="Iron Ore", top=1) == srv.rank_build_sites(
        resource="Iron Ore", limit=1
    )


def test_status_tapped_lists_only_nodes_an_extractor_stands_on(game, use_world, projection):
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    st = WorldState(projection=projection, game=game)
    use_world(st)
    out = stools.search_resource_nodes(resource="Iron Ore", status="tapped", show="nodes")
    assert "tapped node(s)" in out.splitlines()[0]
    rows = [line.split("\t") for line in out.splitlines() if line.startswith("BP_")]
    assert rows and {r[7] for r in rows} == {"tapped"}
    assert stools.search_resource_nodes(status="busy").startswith("! unknown status 'busy'")
    free = stools.search_resource_nodes(resource="Iron Ore", status="free")
    assert free == stools.search_resource_nodes(resource="Iron Ore", only_free=True)
