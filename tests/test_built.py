"""``built.detect`` over the fixture world (docs/planner_p4.md, "How built is found").

Each named factory stands in for a plan: its machines are what the plan wants, sited at its
centroid. The fixture world is read and never written; labels come from a private store.
"""

from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace as NS

import pytest
from conftest import FIXTURE_WORLD, FIXTURES

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import built
from satisfactory_mcp.domain.world.state import WorldState

#: The two factories that stand on one spot and run the same recipes.
SAME_SPOT = ("tier 1&2", "temporary iron line")


def _labels_world(game, projection, tmp_path, monkeypatch, drop: tuple[str, ...] = ()):
    raw = json.loads((FIXTURES / "labels_reference.json").read_text(encoding="utf-8"))
    raw["labels"] = [x for x in raw["labels"] if x["name"] not in drop]
    labels = tmp_path / "labels"
    labels.mkdir(exist_ok=True)
    (labels / f"{FIXTURE_WORLD}.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(config, "labels_dir", lambda: labels)
    return WorldState(projection=projection, game=game)


@pytest.fixture
def named(game, projection, tmp_path, monkeypatch):
    return _labels_world(game, projection, tmp_path, monkeypatch)


@pytest.fixture
def same_spot(game, projection, tmp_path, monkeypatch):
    """Two factories on one spot with the same recipes, only the big one named: the plan's
    own factory is unnamed, so the other-factory rule is tested without reading its answer
    out of the label it is judged against."""
    return _labels_world(game, projection, tmp_path, monkeypatch, drop=(SAME_SPOT[1],))


def _truth(st, name):
    raw = json.loads((FIXTURES / "labels_reference.json").read_text(encoding="utf-8"))
    anchors = next(x["anchors"] for x in raw["labels"] if x["name"] == name)
    recs = built._records(st)
    return recs, {m for m in anchors if m in recs and recs[m].group != "extractors"}


def _pseudo(recs, machines, site=None, factory="", keep=None):
    """A plan wanting exactly ``machines``' jobs at their clocks, sited at their centroid."""
    by: dict[tuple, list] = {}
    for m in sorted(machines):
        by.setdefault(recs[m].key, []).append(recs[m])
    procs = [
        {
            "kind": key[0],
            "building_id": key[1],
            "building": key[1],
            "recipe": key[2] if key[0] == "recipe" else None,
            "machines": len(rs),
            "clock": sum(r.rate for r in rs) / len(rs),
            "mw": 0.0,
            "label": str(key),
            "rates": {},
            "pid": str(key),
        }
        for key, rs in by.items()
    ]
    points = [recs[m].xy for m in machines if recs[m].xy]
    cx = sum(p[0] for p in points) / len(points) / 100.0
    cy = sum(p[1] for p in points) / len(points) / 100.0
    if site is not None:
        cx, cy = site
    prepared = NS(solution=NS(processes=procs), request=NS(node_rows=[]))
    state = NS(factory=factory, siting={"origin_m": [cx, cy, None]}, args=NS(sources=[]))
    return prepared, state


def _found(b, recs):
    return {m for m in (b.scope or ()) if recs[m].group != "extractors"}


def test_every_named_factory_is_found_whole_at_its_centroid(named):
    exact = []
    for label in named.labels.labels:
        recs, truth = _truth(named, label.name)
        if not truth:
            continue
        prepared, state = _pseudo(recs, truth)
        found = _found(built.detect(named.game, named, state, prepared), recs)
        assert found >= truth, label.name
        if found == truth:
            exact.append(label.name)
    # The one miss is three unnamed machines inside tor factory's own cluster, on its recipes.
    assert len(exact) == 14 and "tor factory" not in exact
    assert {"tier 1&2", "temporary iron line", "scratch area"} <= set(exact)


def test_another_named_factory_never_counts(named):
    recs, truth = _truth(named, "temporary iron line")
    prepared, state = _pseudo(recs, truth)
    b = built.detect(named.game, named, state, prepared)
    assert _found(b, recs) == truth
    assert b.top.name == "temporary iron line" and b.confidence == "likely"
    assert dict(b.foreign)["tier 1&2"] == 54
    assert any("belong to “tier 1&2”" in line for line in b.details())


def test_same_spot_with_the_plans_factory_unnamed(same_spot):
    recs, truth = _truth(same_spot, SAME_SPOT[1])
    prepared, state = _pseudo(recs, truth)
    b = built.detect(same_spot.game, same_spot, state, prepared)
    assert _found(b, recs) == truth
    assert b.top.kind == "cluster" and b.top.name
    assert dict(b.foreign).get("tier 1&2", 0) > 0
    _recs, big = _truth(same_spot, SAME_SPOT[0])
    assert not _found(b, recs) & big


def test_a_partial_build_is_found_from_one_seed(named, game, projection):
    recs, truth = _truth(named, "copper setup")
    kept = set(sorted(truth)[: max(1, len(truth) * 3 // 10)])
    gone = truth - kept
    cut = {
        **projection,
        "machines": [m for m in projection["machines"] if built._leaf(m["instance"]) not in gone],
    }
    st = WorldState(projection=cut, game=game)
    prepared, state = _pseudo(recs, truth)
    b = built.detect(game, st, state, prepared)
    assert _found(b, built._records(st)) == kept


def test_no_site_means_no_progress(named):
    recs, truth = _truth(named, "copper setup")
    prepared, state = _pseudo(recs, truth)
    state.siting = {}
    b = built.detect(named.game, named, state, prepared)
    assert b.confidence == "no site" and b.scope is None
    assert b.where().startswith("not placed")


def test_nothing_at_the_site(named):
    recs, truth = _truth(named, "copper setup")
    prepared, state = _pseudo(recs, truth, site=(0.0, 0.0))
    b = built.detect(named.game, named, state, prepared)
    assert b.confidence == "nothing" and b.scope == set()
    assert b.where() == "nothing of this plan stands at its site"


def test_the_stored_value_always_wins_the_count(named):
    recs, truth = _truth(named, "copper setup")
    prepared, state = _pseudo(recs, truth, factory="/none")
    b = built.detect(named.game, named, state, prepared)
    assert b.mode == "none" and b.scope == set() and b.hint.startswith("auto sees")
    prepared, state = _pseudo(recs, truth, factory="/world")
    assert built.detect(named.game, named, state, prepared).scope is None
    prepared, state = _pseudo(recs, truth, factory="steel factory")
    b = built.detect(named.game, named, state, prepared)
    assert b.mode == "picked" and b.picked == "steel factory"
    assert "copper setup" in b.hint
    prepared, state = _pseudo(recs, truth, factory="gone for good")
    b = built.detect(named.game, named, state, prepared)
    assert b.mode == "auto" and b.fallback.startswith("“gone for good” has no machines left")
    assert _found(b, recs) == truth


def test_candidates_are_ordered_deterministically(named):
    recs, truth = _truth(named, "tier 1&2")
    prepared, state = _pseudo(recs, truth)
    first = built.detect(named.game, named, state, prepared)
    again = built.detect(named.game, named, state, prepared)
    assert [c.name for c in first.candidates] == [c.name for c in again.candidates]
    assert first.candidates[0].name == "tier 1&2"


def test_search_area_rules(named, game):
    prepared = NS(solution=NS(processes=[]), request=NS(node_rows=[]))
    pad = NS(
        siting={"origin_m": [10.0, 20.0, None], "footprint_m": [40.0, 20.0]}, args=NS(sources=[])
    )
    area = built.search_area(game, named, pad, prepared)
    assert area.kind == "footprint" and area.words == "its pad"
    assert area.contains(1000.0 + 60 * 100, 2000.0) and not area.contains(1000.0 + 80 * 100, 2000.0)
    spot = NS(siting={"origin_m": [10.0, 20.0, None]}, args=NS(sources=[]))
    assert built.search_area(game, named, spot, prepared).kind == "circle"
    near = NS(siting={}, args=NS(sources=["near:1475,-2098@300"]))
    area = built.search_area(game, named, near, prepared)
    assert area.kind == "circle" and area.circles[0][:2] == (147500.0, -209800.0)
    assert area.circles[0][2] == built.SITE_MIN_RADIUS_M
    region = NS(siting={}, args=NS(sources=["region:Spire Coast"]))
    assert built.search_area(game, named, region, prepared) is None
    anywhere = NS(siting={}, args=NS(sources=[]))
    assert built.search_area(game, named, anywhere, prepared) is None
    rows = [{"kind": "node", "instance": "L.BP_ResourceNode1", "x": 0.0, "y": 0.0}]
    nodes = NS(siting={}, args=NS(sources=["node:BP_ResourceNode1"]))
    area = built.search_area(
        game, named, nodes, NS(solution=NS(processes=[]), request=NS(node_rows=rows))
    )
    assert area.kind == "nodes" and area.node_ids == frozenset({"BP_ResourceNode1"})


def test_the_radius_grows_with_the_plan(game):
    small = NS(
        solution=NS(
            processes=[{"kind": "recipe", "building_id": "Build_ConstructorMk1_C", "machines": 2}]
        )
    )
    big = NS(
        solution=NS(
            processes=[{"kind": "recipe", "building_id": "Build_OilRefinery_C", "machines": 120}]
        )
    )
    assert built.plan_radius_m(game, small) == built.SITE_MIN_RADIUS_M
    assert built.plan_radius_m(game, big) > 2 * built.SITE_MIN_RADIUS_M


def test_node_owner_names_the_cluster_its_nodes_feed(named):
    recs = built._records(named)
    pump = next(r for r in recs.values() if r.group == "extractors" and r.node)
    node = {
        "kind": "node",
        "instance": "x." + pump.node,
        "resource": "Desc_OreIron_C",
        "x": 0.0,
        "y": 0.0,
    }
    proc = {
        "kind": "recipe",
        "building_id": "Build_ConstructorMk1_C",
        "building": "Constructor",
        "recipe": "Recipe_NoSuchThing_C",
        "machines": 1,
        "clock": 1.0,
        "mw": 0.0,
        "label": "x",
        "rates": {},
        "pid": "x",
    }
    prepared = NS(solution=NS(processes=[proc]), request=NS(node_rows=[node]))
    owners = [pr for pr in named.proposals if pump.leaf in pr.machines]
    state = NS(factory="", siting={"origin_m": [0.0, 0.0, None]}, args=NS(sources=[]))
    b = built.detect(named.game, named, state, prepared)
    makes = owners and any(recs[m].group != "extractors" for m in owners[0].machines if m in recs)
    if makes or pump.leaf in {m for x in named.labels.labels for m in x.anchors}:
        assert b.node_owner.startswith("its 1 iron ore node already feed")
        assert pump.leaf not in (b.scope or ())
    else:
        assert pump.leaf in (b.scope or ())


def test_the_progress_figure(named):
    recs, truth = _truth(named, "copper setup")
    prepared, state = _pseudo(recs, truth)
    b = built.detect(named.game, named, state, prepared)
    rows = [
        NS(
            need=4,
            have=4,
            have_min=None,
            need_rate=4.0,
            have_rate=4.0,
            plan_clock=1.0,
            build=0,
            building="Smelter",
            process="Copper Ingot",
        ),
        NS(
            need=6,
            have=2,
            have_min=None,
            need_rate=6.0,
            have_rate=2.5,
            plan_clock=1.0,
            build=4,
            building="Constructor",
            process="Wire",
        ),
    ]
    built.fill_progress(b, NS(rows=rows))
    assert (b.built, b.built_max, b.total) == (6, 6, 10)
    assert b.percent == 65.0 and b.figure() == "6 / 10"
    assert b.missing == ["4 Constructors (Wire)"]
    assert Counter(b.tool_text().split(" · ")[1:2]) == Counter(
        ["6 / 10 machines built (65% of the rate)"]
    )

