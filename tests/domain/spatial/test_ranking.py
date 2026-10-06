"""Build-site ranking.

The scoring itself is a judgement call, so these tests pin the things that would make
it *wrong* rather than merely differently weighted.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.domain.spatial.ranking import SITE_PAD_M, WEIGHTS, rank_sites


def _node(x, y, z=0, rate=120.0, purity="normal", tapped=False, reachable=True):
    return {
        "instance": f"n{x}_{y}",
        "x": x,
        "y": y,
        "z": z,
        "rate": rate,
        "purity": purity,
        "kind": "node",
        "tapped": tapped,
        "reachable": reachable,
    }


def _cluster(nodes):
    (c,) = geo.cluster(nodes, link_m=10_000.0)
    return c


def test_fully_tapped_field_is_not_a_candidate():
    """A field whose nodes all have extractors offers nothing, whatever its size."""
    c = _cluster([_node(0, 0, tapped=True), _node(1000, 0, tapped=True)])
    assert rank_sites([c]) == []


def test_unreachable_capacity_is_excluded():
    """Well satellites are worthless until the Pressurizer is unlocked, so they must
    not inflate a site's throughput."""
    c = _cluster([_node(0, 0, reachable=False), _node(1000, 0, rate=60.0)])
    (scored,) = rank_sites([c])
    assert scored.raw["untapped_rate"] == pytest.approx(60.0)
    assert scored.raw["total_rate"] == pytest.approx(180.0)


def test_higher_throughput_wins_all_else_equal():
    big = _cluster([_node(0, 0, rate=480.0)])
    small = _cluster([_node(100_000, 0, rate=60.0)])
    ranked = rank_sites([small, big], infra=[(0.0, 0.0)])
    assert ranked[0].cluster is big


def test_closer_wins_when_throughput_is_equal():
    near = _cluster([_node(10_000, 0)])
    far = _cluster([_node(500_000, 0)])
    ranked = rank_sites([near, far], infra=[(0.0, 0.0)])
    assert ranked[0].cluster is near
    assert ranked[0].raw["distance_to_infra_m"] < ranked[1].raw["distance_to_infra_m"]


def test_purity_breaks_a_tie():
    pure = _cluster([_node(0, 0, rate=240.0, purity="pure")])
    impure = _cluster([_node(100_000, 0, rate=240.0, purity="impure")])
    ranked = rank_sites([pure, impure], infra=[(0.0, 0.0), (100_000.0, 0.0)])
    assert ranked[0].cluster is pure
    assert ranked[0].raw["purity_quality"] > ranked[1].raw["purity_quality"]


def test_raw_components_are_always_returned():
    """The single score is a starting point; a caller must be able to re-weight."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[(50_000.0, 0.0)])
    for key in (
        "untapped_rate",
        "total_rate",
        "nodes",
        "spread_m",
        "distance_to_infra_m",
        "purity_quality",
    ):
        assert key in scored.raw, key
    assert set(scored.normalised) == set(WEIGHTS)


class _FakeTerrain:
    """A field that answers by x: everything east of 0 is a cliff, everything west a lawn."""

    def __init__(self):
        self.asked = []

    def window(self, x0_cm, y0_cm, x1_cm, y1_cm, max_texels=1_000_000):
        self.asked.append((x0_cm, y0_cm, x1_cm, y1_cm))
        steep = (x0_cm + x1_cm) / 2 >= 0
        return hf.Area(
            x0_cm=x0_cm,
            y0_cm=y0_cm,
            x1_cm=x1_cm,
            y1_cm=y1_cm,
            stride=1,
            requested_texels=40_401,
            texels=40_401,
            nodata_pct=0.0,
            z_min_m=0.0,
            z_max_m=90.0 if steep else 2.0,
            roughness_m=38.0 if steep else 0.4,
            slope_mean_deg=41.0 if steep else 1.5,
            submerged_pct=0.0,
        )


def test_terrain_never_vetoes_a_richer_field():
    """The study's warning, pinned. Buildable is not flat -- stilts are ordinary play and a
    cliff is sometimes the point -- so a 38 m-rough site with twice the ore must still win.
    A weight large enough to reverse this would be a veto wearing a weight's clothes."""
    cliff_rich = _cluster([_node(50_000, 0, rate=480.0)])
    lawn_poor = _cluster([_node(-50_000, 0, rate=240.0)])
    ranked = rank_sites([cliff_rich, lawn_poor], infra=[(0.0, 0.0)], terrain=_FakeTerrain())
    assert ranked[0].cluster is cliff_rich
    assert ranked[0].raw["pad_roughness_m"] == 38.0
    assert ranked[1].raw["pad_roughness_m"] == 0.4


def test_terrain_breaks_a_tie_and_reads_a_pad_at_the_centroid():
    rough = _cluster([_node(50_000, 0, rate=240.0)])
    smooth = _cluster([_node(-50_000, 0, rate=240.0)])
    terrain = _FakeTerrain()
    ranked = rank_sites([rough, smooth], infra=[(0.0, 0.0)], terrain=terrain)
    assert ranked[0].cluster is smooth
    half = SITE_PAD_M * 100 / 2
    assert (-50_000 - half, -half, -50_000 + half, half) in terrain.asked


def test_without_a_field_the_terrain_columns_are_absent_and_not_zero():
    """Most machines have no heightfield. A missing pad must read as unknown, because 0 m
    of roughness is a claim that the ground is perfectly flat."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[(0.0, 0.0)])
    assert scored.raw["pad_roughness_m"] is None
    assert scored.raw["pad_submerged_pct"] is None


def test_an_unmeasured_pad_does_not_outrank_a_measured_one():
    """A field off the edge of the heightmap scores as the middle of what is known. As 0 it
    would be the flattest site on the map; as the worst it would be rejected for being
    unmapped, and both are inventions."""

    class _Partial(_FakeTerrain):
        def window(self, x0_cm, y0_cm, x1_cm, y1_cm, max_texels=1_000_000):
            area = super().window(x0_cm, y0_cm, x1_cm, y1_cm, max_texels)
            if x0_cm > 100_000:
                return hf.Area(
                    x0_cm=x0_cm,
                    y0_cm=y0_cm,
                    x1_cm=x1_cm,
                    y1_cm=y1_cm,
                    stride=1,
                    requested_texels=40_401,
                    texels=0,
                    nodata_pct=100.0,
                )
            return area

    off_map = _cluster([_node(500_000, 0, rate=240.0)])
    lawn = _cluster([_node(-50_000, 0, rate=240.0)])
    cliff = _cluster([_node(50_000, 0, rate=240.0)])
    ranked = rank_sites([off_map, lawn, cliff], infra=[(0.0, 0.0)], terrain=_Partial())
    by_cluster = {id(s.cluster): s for s in ranked}
    assert by_cluster[id(off_map)].raw["pad_roughness_m"] is None
    unknown = by_cluster[id(off_map)].normalised["roughness"]
    assert (
        by_cluster[id(lawn)].normalised["roughness"]
        <= unknown
        <= by_cluster[id(cliff)].normalised["roughness"]
    )


def test_missing_infrastructure_does_not_score_as_zero_distance():
    """With no known buildings, distance must not silently become the best possible
    value -- that would rank every field as adjacent to the base."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[])
    assert scored.raw["distance_to_infra_m"] is None


def test_single_candidate_gets_neutral_normalisation():
    """A degenerate min-max spread must not arbitrarily favour or punish the only
    candidate."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[(0.0, 0.0)])
    assert all(v == pytest.approx(0.5) for v in scored.normalised.values())


def test_altitude_sign_favours_a_field_above_the_consumer():
    """Positive means the fluid flows DOWNHILL to the refineries, needing no pumps.
    Getting this backwards would rank uphill fields as cheap."""
    high = _cluster([_node(0, 0, z=25_000)])
    (scored,) = rank_sites([high], consumer_z=-1_700.0)
    assert scored.raw["altitude_vs_consumer_m"] == pytest.approx(267.0)


def test_altitude_absent_without_a_consumer():
    c = _cluster([_node(0, 0, z=25_000)])
    (scored,) = rank_sites([c])
    assert scored.raw["altitude_vs_consumer_m"] is None


def test_custom_weights_change_the_order():
    near_small = _cluster([_node(10_000, 0, rate=120.0)])
    far_big = _cluster([_node(500_000, 0, rate=480.0)])
    default = rank_sites([near_small, far_big], infra=[(0.0, 0.0)])
    assert default[0].cluster is far_big
    distance_first = rank_sites(
        [near_small, far_big],
        infra=[(0.0, 0.0)],
        weights={"throughput": 0.1, "distance": -1.0},
    )
    assert distance_first[0].cluster is near_small


def test_a_miner_is_never_offered_for_a_liquid_node(game):
    """search_resource_nodes reported every oil node at DOUBLE its real rate: a pure
    node read 480 m3/min where an Oil Extractor gives 240.

    node_rate takes the best extractor for the node's kind, filtered by
    mAllowedResources -- but that field is only populated when
    mOnlyAllowCertainResources is True, which is False on every miner. So miners looked
    unrestricted, and Miner Mk.3 (base 240) out-bid the Oil Extractor (base 120).
    mAllowedResourceForms is the field that actually encodes it: RF_SOLID on miners,
    RF_LIQUID on the pumps. It was already parsed and simply not consulted.
    """
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()
    oil = [n for n in table.nodes if n["resource"] == "Desc_LiquidOil_C" and n["kind"] == "node"]
    assert oil
    rates = {n["purity"]: nodes_mod.node_rate(n, game) for n in oil}
    assert rates == {"impure": 60.0, "normal": 120.0, "pure": 240.0}


def test_node_rates_agree_with_the_extractor_that_can_tap_them(game):
    """The general invariant. Every node's rate must be achievable by some extractor
    whose allowed FORM matches the resource."""
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()
    for node in table.nodes:
        if node["kind"] != "node":
            continue
        item = game.items.get(node["resource"])
        rate = nodes_mod.node_rate(node, game)
        if not rate or item is None:
            continue
        usable = [
            b
            for cls in nodes_mod.EXTRACTOR_FOR_KIND["node"]
            if (b := game.buildings.get(cls))
            and (not b.allowed_forms or item.form in b.allowed_forms)
        ]
        assert usable, node["resource"]
        assert rate == max(b.extract_rate(node["purity"]) for b in usable)


def test_the_base_rates_match_the_dumps_own_cycle_fields(game):
    """Derived independently of our parser: items-per-cycle over cycle-time, with
    litres converted to m3 for fluids. Confirms the extractor model was right all along
    and only node_rate was wrong."""
    from satisfactory_mcp import config
    from satisfactory_mcp.core.gamedata.loader import load_docs

    raw = load_docs(config.docs_path())
    idx = raw.index(
        "FGBuildableResourceExtractor", "FGBuildableWaterPump", "FGBuildableFrackingExtractor"
    )
    for cls in ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_MinerMk3_C", "Build_OilPump_C"):
        entry = idx[cls]
        per_cycle = float(entry.get("mItemsPerCycle", 0))
        cycle = float(entry["mExtractCycleTime"])
        fluid = "RF_LIQUID" in entry.get("mAllowedResourceForms", "")
        derived = per_cycle / cycle * 60 / (1000 if fluid else 1)
        assert game.buildings[cls].base_extract_rate == derived, cls


def test_oil_rates_match_the_published_table(game):
    """Third independent confirmation of the same six numbers.

    The wiki's Crude Oil "Resource acquisition" table gives m3/min at 100% and at 250%
    by purity. It agrees cell-for-cell with our building model, with the derivation from
    the dump's own cycle fields above, and -- after the miner/liquid fix -- with
    node_rate. Before that fix node_rate disagreed with all three by exactly 2x.

    The 250% column is pinned as well as the 100% one because that is the figure a plan
    is actually built against: a pure node overclocked is 600 m3/min, not 1,200.
    """
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    published = {"impure": (60.0, 150.0), "normal": (120.0, 300.0), "pure": (240.0, 600.0)}
    pump = game.buildings["Build_OilPump_C"]
    table = nodes_mod.load_nodes()

    for purity, (at_100, at_250) in published.items():
        assert pump.extract_rate(purity) == at_100, purity
        assert pump.extract_rate(purity, 2.5) == at_250, purity
        node = next(
            n
            for n in table.nodes
            if n["resource"] == "Desc_LiquidOil_C" and n["kind"] == "node" and n["purity"] == purity
        )
        assert nodes_mod.node_rate(node, game) == at_100, purity
