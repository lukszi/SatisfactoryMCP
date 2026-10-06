"""Factory proposals: product clusters, complete linkage, and dependents absorbed."""

from __future__ import annotations

from itertools import pairwise

import pytest

from satisfactory_mcp.domain.factories import identity
from tests.support.synthetic_factories import (
    BASE_CONCRETE,
    IRON,
    ORPHAN,
    STEEL,
    STEEL_CONCRETE,
    TOWER,
)

pytestmark = pytest.mark.integration


def test_product_clusters_split_concrete_by_position(synthetic_graph, game, synthetic_projection):
    """17 machines make Concrete on the real save; only one is "the concrete setup"."""
    clusters = identity.product_clusters(synthetic_graph, game, synthetic_projection, ["Concrete"])
    assert [c.size for c in clusters] == [3, 1]
    assert set(clusters[0].machines) == set(STEEL_CONCRETE)
    assert set(clusters[1].machines) == set(BASE_CONCRETE)


def test_name_hint_falls_back_to_buildings_when_there_is_no_recipe(
    synthetic_graph, game, synthetic_projection
):
    """Generators run no recipe, so a coal plant would otherwise render blank."""
    cand = identity.describe([TOWER], synthetic_graph, game, synthetic_projection, "test")
    assert cand.name_hint().startswith("1x ")


def test_unassigned_is_every_machine_no_label_covers(synthetic_graph):
    loose = identity.unassigned(synthetic_graph, set(STEEL))
    assert ORPHAN in loose
    assert not set(STEEL) & set(loose)


def test_complete_linkage_refuses_to_chain(synthetic_graph, game, synthetic_projection):
    """THE reason this is not a threshold plus connected components. The steel and iron
    sites are belt-connected and 800 m apart; single linkage welds them through the
    concrete machines sitting between, complete linkage does not. Measured on the real
    save the same score scores F1 0.521 chained against 0.987 unchained."""
    from satisfactory_mcp.domain.factories import cohere
    from satisfactory_mcp.domain.factories.structure import build_structures

    props = cohere.propose(
        synthetic_graph, game, synthetic_projection, build_structures(synthetic_projection)
    )
    for p in props:
        assert not (set(STEEL) & set(p.machines) and set(IRON) & set(p.machines))


def test_the_span_cap_bounds_a_proposal(synthetic_graph, game, synthetic_projection):
    """The one load-bearing constant: removing it drops precision 1.000 -> 0.776."""
    import math

    from satisfactory_mcp.domain.factories import cohere
    from satisfactory_mcp.domain.factories.structure import build_structures

    pos = {
        r["instance"].rsplit(".", 1)[-1]: r["pos"]
        for r in synthetic_projection["machines"]
        if r.get("pos")
    }
    props = cohere.propose(
        synthetic_graph,
        game,
        synthetic_projection,
        build_structures(synthetic_projection),
        max_span_m=50.0,
    )
    for p in props:
        pts = [pos[m][:2] for m in p.machines if m in pos]
        span = max((math.dist(a, b) for a in pts for b in pts), default=0.0) / 100.0
        assert span <= 50.0 + 1e-6, f"{p.machines} spans {span:.0f}m"


def test_every_machine_lands_in_exactly_one_proposal(synthetic_graph, game, synthetic_projection):
    from satisfactory_mcp.domain.factories import cohere
    from satisfactory_mcp.domain.factories.structure import build_structures

    props = cohere.propose(
        synthetic_graph, game, synthetic_projection, build_structures(synthetic_projection)
    )
    seen = [m for p in props for m in p.machines]
    assert sorted(seen) == sorted(synthetic_graph.machines())
    assert len(seen) == len(set(seen))


def test_weakening_the_weights_only_ever_refines(synthetic_graph, game, synthetic_projection):
    """The ablation's real claim. Flattening every weight to 1 costs 0.025 F1 on the
    real save (0.986 -> 0.961) and takes 15 clusters to 20 -- it splits, it never merges.
    That is the direction that matters: precision stayed 1.000 under both. A partition
    that got COARSER as evidence got weaker would mean the score is not doing what it
    claims."""
    from satisfactory_mcp.domain.factories import cohere
    from satisfactory_mcp.domain.factories.structure import build_structures

    sx = build_structures(synthetic_projection)
    base = [
        set(p.machines) for p in cohere.propose(synthetic_graph, game, synthetic_projection, sx)
    ]
    flat = cohere.propose(
        synthetic_graph, game, synthetic_projection, sx, weights=dict.fromkeys(cohere.WEIGHTS, 1.0)
    )
    for p in flat:
        assert any(set(p.machines) <= b for b in base), (
            f"{sorted(p.machines)} is not contained in any strongly-weighted cluster"
        )


def test_a_proposal_publishes_the_weakest_link_holding_it_together(
    synthetic_graph, game, synthetic_projection
):
    """The merge loop computed a real cohesion per cluster and the constructor threw it
    away for a literal 0.0, so every proposal scored the same and nothing ranked them.
    The steel site -- one product, one place -- has to outscore the cluster that only
    collects what is left over, or the number is not measuring cohesion."""
    import math

    from satisfactory_mcp.domain.factories import cohere
    from satisfactory_mcp.domain.factories.structure import build_structures

    props = cohere.propose(
        synthetic_graph, game, synthetic_projection, build_structures(synthetic_projection)
    )
    by_size = {p.size: p for p in props}
    tight = next(p for p in props if set(STEEL) <= set(p.machines))
    loose = next(p for p in props if ORPHAN in p.machines)
    assert all(math.isfinite(p.cohesion) for p in props), "a score the map cannot serialise"
    assert tight.cohesion > loose.cohesion
    assert len({p.cohesion for p in props if p.size > 1}) > 1, "one score for all is no ranking"
    # A single machine has no internal link, so it has no weakest one and ranks last.
    assert by_size[1].cohesion == 0.0


def test_proposals_carry_the_evidence_that_made_them(synthetic_graph, game, synthetic_projection):
    from satisfactory_mcp.domain.factories import cohere
    from satisfactory_mcp.domain.factories.structure import build_structures

    props = cohere.propose(
        synthetic_graph, game, synthetic_projection, build_structures(synthetic_projection)
    )
    multi = [p for p in props if p.size > 1]
    assert multi, "expected at least one machine to join another"
    assert all(p.evidence for p in multi)
    assert set(multi[0].evidence) <= set(cohere.WEIGHTS)


def test_exclusive_dependents_are_absorbed_across_a_pipe_boundary():
    """The coal plant case. Its water pumps are 21-184 m away, inside the span cap, and
    94% of what their pipes reach is that plant -- but the plant runs on TWO separate
    pipe networks, so every pump against a generator in the other network scores
    negative and complete linkage takes the MINIMUM. One blind pair vetoes the merge.
    Exclusivity is a property of a cluster, not of a pair, so it needs a second pass."""
    from satisfactory_mcp.domain.factories.cohere import attach_dependents
    from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph

    gens_a = [f"Build_GeneratorCoal_C_{i}" for i in range(3)]
    gens_b = [f"Build_GeneratorCoal_C_{10 + i}" for i in range(3)]
    pumps = [f"Build_WaterPump_C_{20 + i}" for i in range(2)]
    cls = {n: n.rsplit("_", 1)[0] for n in gens_a + gens_b + pumps}
    graph = FactoryGraph(cls=cls)
    # Pumps share a pipe network with gens_a only; gens_b is a second, separate network.
    for p in pumps:
        for g in gens_a:
            graph.material.append(Edge(a=p, b=g))
    for x, y in pairwise(gens_b):
        graph.material.append(Edge(a=x, b=y))

    plant = gens_a + gens_b
    out = attach_dependents([plant, list(pumps)], graph)
    assert len(out) == 1, "the pumps exist only to feed that plant"
    assert set(out[0]) == set(plant) | set(pumps)


def test_a_peer_is_not_absorbed_however_exclusive():
    """The size guard. Without it precision falls 1.000 -> 0.709, because two large
    factories that mostly feed each other get welded into one."""
    from satisfactory_mcp.domain.factories.cohere import attach_dependents
    from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph

    left = [f"Build_SmelterMk1_C_{i}" for i in range(4)]
    right = [f"Build_ConstructorMk1_C_{10 + i}" for i in range(4)]
    cls = {n: n.rsplit("_", 1)[0] for n in left + right}
    graph = FactoryGraph(cls=cls)
    for a in left:
        for b in right:
            graph.material.append(Edge(a=a, b=b))  # 100% exclusive, both directions

    out = attach_dependents([list(left), list(right)], graph)
    assert len(out) == 2, "equals stay equals; only dependents are absorbed"


def test_name_hint_does_not_let_one_recipe_outvote_a_power_plant():
    """32 generators run no recipe. One absorbed concrete constructor must not rename
    the coal plant to 'Concrete'."""
    from collections import Counter

    from satisfactory_mcp.domain.factories.identity import Candidate

    cand = Candidate(
        machines=[f"m{i}" for i in range(33)],
        source="test",
        products=Counter({"Concrete": 1}),
        buildings=Counter({"Build_GeneratorCoal_C": 32, "Build_ConstructorMk1_C": 1}),
    )
    hint = cand.name_hint()
    assert hint.startswith("32x Generator Coal")
    assert "Concrete" in hint, "the stray recipe is still worth mentioning, just not first"


def test_a_dependent_that_manufactures_is_a_factory_not_an_outlier():
    """The size ratio is not enough. The player's space-elevator-parts area is 15
    machines feeding a 110-machine host almost exclusively -- inside both the
    exclusivity and the size guard -- but 3 of those 15 make Automated Wiring and
    Computer, and the other 12 are the biomass burners powering them. Infrastructure
    runs no recipe; something that manufactures is its own factory."""
    from satisfactory_mcp.domain.factories.cohere import attach_dependents
    from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph

    host = [f"Build_ConstructorMk1_C_{i}" for i in range(20)]
    wiring = [f"Build_AssemblerMk1_C_{20 + i}" for i in range(3)]
    burners = [f"Build_GeneratorBiomass_C_{30 + i}" for i in range(2)]
    pumps = [f"Build_WaterPump_C_{40 + i}" for i in range(2)]
    cls = {n: n.rsplit("_", 1)[0] for n in host + wiring + burners + pumps}
    graph = FactoryGraph(cls=cls)
    for group in (wiring + burners, pumps):
        for a in group:
            for b in host:
                graph.material.append(Edge(a=a, b=b))

    clusters = [list(host), wiring + burners, list(pumps)]
    # Only the assemblers run a recipe; burners, pumps and the host's neighbours do not.
    out = attach_dependents(clusters, graph, manufacturing=set(wiring))
    assert any(set(c) == set(wiring) | set(burners) for c in out), (
        "a cluster that manufactures must survive as its own proposal"
    )
    assert any(set(pumps) <= set(c) and set(host) <= set(c) for c in out), (
        "pure infrastructure is still absorbed"
    )
    # Drop the guard and the same wiring cluster is swallowed, which is the bug.
    swallowed = attach_dependents(clusters, graph, manufacturing=set())
    assert len(swallowed) == 1


def test_a_remote_mine_is_attributed_to_the_factory_its_belt_reaches_first():
    """Exclusivity cannot attribute a remote extractor. The four mines feeding the steel
    factory reach it in 26-43 hops and the tor factory in 88-123 -- but steel and tor are
    belt-connected to EACH OTHER downstream, so counting every reachable machine dilutes
    exclusivity to 0.55 and the mine is left orphaned. First arrival is unambiguous."""
    from satisfactory_mcp.domain.factories.cohere import attach_dependents
    from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph

    near_plant = [f"Build_FoundryMk1_C_{i}" for i in range(4)]
    far_plant = [f"Build_AssemblerMk1_C_{10 + i}" for i in range(4)]
    mine = ["Build_MinerMk2_C_50"]
    belt = [f"Build_ConveyorBeltMk1_C_{100 + i}" for i in range(6)]
    cls = {n: n.rsplit("_", 1)[0] for n in near_plant + far_plant + mine + belt}
    graph = FactoryGraph(cls=cls)
    # mine -> long belt -> near_plant, and near_plant -> far_plant downstream.
    chain = [*mine, *belt, near_plant[0]]
    for a, b in pairwise(chain):
        graph.material.append(Edge(a=a, b=b))
    for a, b in pairwise(near_plant):
        graph.material.append(Edge(a=a, b=b))
    # A long belt between the two plants. The margin can only be met when the plants are
    # farther from EACH OTHER than the mine is from the nearer one -- on the real save,
    # 34 hops to steel against another 54 on to the tor factory.
    trunk = [f"Build_ConveyorBeltMk1_C_{200 + i}" for i in range(8)]
    cls.update({n: n.rsplit("_", 1)[0] for n in trunk})
    graph.cls.update({n: n.rsplit("_", 1)[0] for n in trunk})
    for a, b in pairwise([near_plant[-1], *trunk, far_plant[0]]):
        graph.material.append(Edge(a=a, b=b))
    for a, b in pairwise(far_plant):
        graph.material.append(Edge(a=a, b=b))

    clusters = [list(near_plant), list(far_plant), list(mine)]
    out = attach_dependents(clusters, graph, manufacturing=set(near_plant + far_plant))
    home = next(c for c in out if mine[0] in c)
    assert set(near_plant) <= set(home), "the mine belongs to the plant its belt reaches"
    assert not set(far_plant) & set(home), "not to everything downstream of that plant"


def test_a_mine_between_two_equally_close_factories_is_left_alone():
    """The margin guard. When first arrival is a near tie there is no honest answer, and
    an unattributed miner in the coverage report beats a wrong attribution."""
    from satisfactory_mcp.domain.factories.cohere import attach_dependents
    from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph

    left = [f"Build_FoundryMk1_C_{i}" for i in range(4)]
    right = [f"Build_AssemblerMk1_C_{10 + i}" for i in range(4)]
    mine = ["Build_MinerMk2_C_50"]
    cls = {n: n.rsplit("_", 1)[0] for n in left + right + mine}
    graph = FactoryGraph(cls=cls)
    graph.material.append(Edge(a=mine[0], b=left[0]))
    graph.material.append(Edge(a=mine[0], b=right[0]))

    out = attach_dependents(
        [list(left), list(right), list(mine)], graph, manufacturing=set(left + right)
    )
    assert any(c == mine for c in out), "a tie must stay unattributed"
