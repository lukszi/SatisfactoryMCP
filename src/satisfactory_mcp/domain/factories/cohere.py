"""One coherence score over every signal, agglomerated into proposed factories.

Each signal fails alone; scored together over every machine pair and merged by complete
linkage under a span cap, they never merge two factories and only ever split one. The weights
barely matter, the linkage rule and the span cap carry the result, and a second pass absorbs
dependents no pairwise score can see. docs/save-projection.md §6.2b has the validation.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeAlias

from ...core.gamedata.model import GameData
from ...core.saveio.schema import Projection
from ...core.unionfind import UnionFind
from ..spatial import geo
from .candidates import positions, recipes_by_machine
from .model import Edge, FactoryGraph
from .structure import Structures

__all__ = [
    "MAX_DEPENDENT_RECIPES",
    "MAX_SPAN_M",
    "MIN_EXCLUSIVITY",
    "NEAREST_MARGIN",
    "WEIGHTS",
    "Proposal",
    "attach_dependents",
    "propose",
]

#: Signal weights, round because they are not load-bearing; kept to name what fired.
WEIGHTS = {
    "slab": 10.0,  # same foundation platform
    "near": 5.0,  # within NEAR_M of each other
    "prod": 4.0,  # makes the same thing
    "belt": 3.0,  # same belt/pipe component
    "supply": 3.0,  # one's output is the other's input
}

#: Evidence has to beat this for two clusters to merge; only its sign matters.
PRIOR = 1.0

#: Proximity threshold for the ``near`` signal, in metres.
NEAR_M = 100.0

#: A dependent is absorbed when this share of what its belts and pipes reach is one cluster.
MIN_EXCLUSIVITY = 0.8

#: ...and it is at most this fraction of that cluster, or factories feeding each other weld.
MAX_DEPENDENT_RATIO = 0.5

#: ...and at most this many of its machines run a recipe: infrastructure runs none.
MAX_DEPENDENT_RECIPES = 2

#: ...or the cluster its belts reach FIRST is this many times nearer than the runner-up.
NEAREST_MARGIN = 2.0

#: No proposal may span more than this: THE load-bearing constant.
MAX_SPAN_M = 250.0

#: A machine pair's signals by name, and their distance in metres.
Features: TypeAlias = Callable[[str, str], tuple[dict[str, float], float]]
#: The material layer's neighbour index, as ``FactoryGraph.adjacency`` builds it.
Adjacency: TypeAlias = dict[str, list[Edge]]
#: A pair of pool indices, the lower first.
Pair: TypeAlias = tuple[int, int]


@dataclass
class Proposal:
    """A proposed factory and the evidence that produced it."""

    machines: list[str]
    #: Weakest internal link, which every pair clears under complete linkage; 0.0 for one.
    cohesion: float = 0.0
    evidence: Counter[str] = field(default_factory=Counter[str])
    seeded_by: str = ""
    #: Sizes of the pieces this was assembled from, largest first; several mean absorbed
    #: dependents ("47 = 32 + 6 + 6 + 2 + 1").
    parts: list[int] = field(default_factory=list[int])

    @property
    def size(self) -> int:
        return len(self.machines)


def _feature_fn(
    graph: FactoryGraph,
    game: GameData,
    projection: Projection,
    structures: Structures,
) -> Features:
    """Build the per-pair feature extractor once, with every lookup pre-indexed."""
    pos = positions(projection)
    products: dict[str, frozenset[str]] = {}
    ingredients: dict[str, frozenset[str]] = {}
    for machine, recipe_id in recipes_by_machine(projection).items():
        recipe = game.recipes.get(recipe_id)
        if recipe is None:
            continue
        products[machine] = frozenset(game.item_name(f.item) for f in recipe.products)
        ingredients[machine] = frozenset(game.item_name(f.item) for f in recipe.ingredients)

    component = {m: k for k, comp in enumerate(graph.machine_components("material")) for m in comp}
    slab = structures.slab_of

    def features(a: str, b: str) -> tuple[dict[str, float], float]:
        pa, pb = pos.get(a), pos.get(b)
        distance_m = geo.distance_m(pa, pb) if pa and pb else math.inf
        sa, sb = slab.get(a), slab.get(b)
        prod_a, prod_b = products.get(a), products.get(b)
        feats = {
            "slab": float(sa is not None and sa == sb),
            "near": float(distance_m <= NEAR_M),
            "prod": float(bool(prod_a) and prod_a == prod_b),
            "belt": float(component.get(a, -1) == component.get(b, -2)),
            "supply": float(
                bool(prod_a and ingredients.get(b) and prod_a & ingredients[b])
                or bool(prod_b and ingredients.get(a) and prod_b & ingredients[a])
            ),
        }
        return feats, distance_m

    return features


def _material_reach(graph: FactoryGraph, adjacency: Adjacency, seed: list[str]) -> set[str]:
    """Every machine reachable from ``seed`` over belts and pipes, walking through logistics."""
    seen, frontier = set(seed), list(seed)
    out: set[str] = set()
    while frontier:
        next_frontier: list[str] = []
        for node in frontier:
            for edge in adjacency.get(node, ()):
                other = edge.other(node)
                if other in seen:
                    continue
                seen.add(other)
                next_frontier.append(other)
                if graph.is_machine(other):
                    out.add(other)
        frontier = next_frontier
    return out


def _first_arrival(
    graph: FactoryGraph, adjacency: Adjacency, seed: list[str], owner: dict[str, int], self_id: int
) -> dict[int, int]:
    """Hop depth at which each other cluster is first reached."""
    held = set(seed)
    seen = set(seed)
    queue = deque((m, 0) for m in seed)
    out: dict[int, int] = {}
    while queue:
        node, depth = queue.popleft()
        if node not in held and graph.is_machine(node):
            target = owner.get(node)
            if target is not None and target != self_id and target not in out:
                out[target] = depth
        for edge in adjacency.get(node, ()):
            other = edge.other(node)
            if other not in seen:
                seen.add(other)
                queue.append((other, depth + 1))
    return out


def _dependent_target(
    graph: FactoryGraph,
    adjacency: Adjacency,
    groups: list[list[str]],
    owner: dict[str, int],
    k: int,
    min_exclusivity: float,
    nearest_margin: float,
) -> int | None:
    """The cluster group ``k`` serves: most of what it reaches, else the one it reaches first
    by ``nearest_margin``; ``None`` when neither settles it."""
    members = groups[k]
    outside = _material_reach(graph, adjacency, members) - set(members)
    if not outside:
        return None
    targets = Counter(owner[m] for m in outside if m in owner)
    targets.pop(k, None)
    if not targets:
        return None
    best, hits = targets.most_common(1)[0]
    if hits / len(outside) >= min_exclusivity:
        return best
    # Not embedded in one cluster -- but it may sit at the end of a belt that plainly leads
    # somewhere, so fall back to first arrival.
    arrival = _first_arrival(graph, adjacency, members, owner, k)
    order = sorted(arrival.items(), key=lambda kv: kv[1])
    if not order:
        return None
    if len(order) > 1 and order[1][1] < order[0][1] * nearest_margin:
        return None  # too close to call
    return order[0][0]


def attach_dependents(
    clusters: list[list[str]],
    graph: FactoryGraph,
    manufacturing: set[str] | None = None,
    min_exclusivity: float = MIN_EXCLUSIVITY,
    max_ratio: float = MAX_DEPENDENT_RATIO,
    max_recipes: int = MAX_DEPENDENT_RECIPES,
    nearest_margin: float = NEAREST_MARGIN,
    rounds: int = 3,
) -> list[list[str]]:
    """Absorb clusters whose entire material existence serves one other cluster.

    Exclusivity belongs to a cluster rather than to a pair, so complete linkage cannot see it
    and this second pass does. A candidate qualifies by exclusivity or by nearest consumer,
    and one with more than ``max_recipes`` machines in ``manufacturing`` is left alone.
    """
    adjacency = graph.adjacency("material")
    makes = manufacturing or set()
    groups = [list(c) for c in clusters]
    for _ in range(rounds):
        owner = {m: k for k, c in enumerate(groups) for m in c}
        wanted: dict[int, int] = {}
        for k, members in enumerate(groups):
            if sum(1 for m in members if m in makes) > max_recipes:
                continue
            best = _dependent_target(
                graph, adjacency, groups, owner, k, min_exclusivity, nearest_margin
            )
            if best is None or len(members) > max_ratio * len(groups[best]):
                continue
            wanted[k] = best
        if not wanted:
            break
        joins = UnionFind[int]()
        for child, host in wanted.items():
            joins.union(child, host)
        merged: dict[int, list[str]] = defaultdict(list)
        for k, members in enumerate(groups):
            merged[joins.find(k)] += members
        groups = list(merged.values())
    return groups


def _pair_scores(
    pool: list[str], features: Features, weights: dict[str, float], max_span_m: float, prior: float
) -> tuple[dict[Pair, float], dict[Pair, tuple[str, ...]]]:
    """Every pair's score, ``-inf`` past the span cap so no linkage can cross it, and the
    signals that fired for it."""
    pair: dict[Pair, float] = {}
    fired: dict[Pair, tuple[str, ...]] = {}
    for i, a in enumerate(pool):
        for j in range(i + 1, len(pool)):
            feats, distance_m = features(a, pool[j])
            if distance_m > max_span_m:
                pair[(i, j)] = -math.inf
                continue
            pair[(i, j)] = sum(weights[k] * v for k, v in feats.items()) - prior
            fired[(i, j)] = tuple(k for k, v in feats.items() if v)
    return pair, fired


def _slab_seeds(
    pool: list[str], index: dict[str, int], structures: Structures
) -> tuple[list[list[int]], list[str]]:
    """Starting clusters: one per slab, one per ground-built machine; and what seeded each."""
    slab_seed: dict[int, list[int]] = defaultdict(list)
    clusters: list[list[int]] = []
    seeded: list[str] = []
    for machine in pool:
        if machine in structures.slab_of:
            slab_seed[structures.slab_of[machine]].append(index[machine])
        else:
            clusters.append([index[machine]])
            seeded.append("ground")
    for slab_id, members in sorted(slab_seed.items()):
        clusters.append(members)
        seeded.append(f"slab:{slab_id}")
    return clusters, seeded


def _complete_linkage(
    clusters: list[list[int]], seeded: list[str], score: Callable[[int, int], float]
) -> tuple[list[int], dict[int, float]]:
    """Merge while some pair of clusters has EVERY cross pair above zero, best first.

    Returns the surviving cluster indices and each cluster's weakest internal score. Single
    linkage on the same score chains a whole base together through one adjacent pair.
    """
    link: dict[Pair, float] = {}
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            link[(i, j)] = min(score(a, b) for a in clusters[i] for b in clusters[j])

    alive = set(range(len(clusters)))
    # From each seed's own weakest pair rather than infinity: a slab seed that never merges
    # is a proposal like any other and has a real cohesion to report.
    cohesion = {
        i: min((score(a, b) for x, a in enumerate(c) for b in c[x + 1 :]), default=math.inf)
        for i, c in enumerate(clusters)
    }
    while True:
        best = 0.0
        target: Pair | None = None
        for (i, j), value in link.items():
            if i in alive and j in alive and value > best:
                best, target = value, (i, j)
        if target is None:
            break
        i, j = target
        clusters[i] = clusters[i] + clusters[j]
        seeded[i] = seeded[i] if seeded[i].startswith("slab") else seeded[j]
        cohesion[i] = min(cohesion[i], cohesion[j], best)
        alive.discard(j)
        for k in alive:
            if k == i:
                continue
            a, b = (min(i, k), max(i, k)), (min(j, k), max(j, k))
            link[a] = min(link.get(a, math.inf), link.get(b, math.inf))
    return sorted(alive), cohesion


def _assemble_proposals(
    final: list[list[str]],
    linked: list[list[str]],
    seeds: dict[frozenset[str], str],
    weakest: dict[frozenset[str], float],
    index: dict[str, int],
    fired: dict[Pair, tuple[str, ...]],
) -> list[Proposal]:
    """One ``Proposal`` per final group, carrying the evidence of the pieces it absorbed."""
    pieces = {frozenset(c): len(c) for c in linked}
    out: list[Proposal] = []
    for members in final:
        held = frozenset(members)
        parts = sorted((n for c, n in pieces.items() if c <= held), reverse=True) or [len(members)]
        evidence: Counter[str] = Counter()
        ids = [index[m] for m in members]
        for x, a in enumerate(ids):
            for b in ids[x + 1 :]:
                for name in fired.get((min(a, b), max(a, b)), ()):
                    evidence[name] += 1
        inner = next((v for c, v in weakest.items() if c <= held), math.inf)
        out.append(
            Proposal(
                machines=sorted(members),
                cohesion=0.0 if math.isinf(inner) else inner,
                evidence=evidence,
                seeded_by=next((s for c, s in seeds.items() if c <= held), ""),
                parts=parts,
            )
        )
    out.sort(key=lambda p: -p.size)
    return out


def propose(
    graph: FactoryGraph,
    game: GameData,
    projection: Projection,
    structures: Structures,
    machines: list[str] | None = None,
    weights: dict[str, float] | None = None,
    max_span_m: float = MAX_SPAN_M,
    prior: float = PRIOR,
    attach: bool = True,
) -> list[Proposal]:
    """Agglomerate machines into proposed factories, most cohesive first.

    Seeded from foundation slabs rather than from singletons: slabs score precision 1.000
    as a same-factory signal, so starting there costs nothing and starts a long way along.
    """
    weights = {**WEIGHTS, **(weights or {})}
    pool = sorted(machines if machines is not None else graph.machines())
    if not pool:
        return []

    features = _feature_fn(graph, game, projection, structures)
    index = {m: i for i, m in enumerate(pool)}
    pair, fired = _pair_scores(pool, features, weights, max_span_m, prior)

    def score(i: int, j: int) -> float:
        return pair[(i, j)] if i < j else pair[(j, i)]

    clusters, seeded = _slab_seeds(pool, index, structures)
    alive, cohesion = _complete_linkage(clusters, seeded, score)

    linked = [[pool[x] for x in clusters[i]] for i in alive]
    seeds = {frozenset(c): seeded[i] for i, c in zip(alive, linked, strict=False)}
    weakest = {frozenset(c): cohesion[i] for i, c in zip(alive, linked, strict=False)}
    manufacturing = set(recipes_by_machine(projection))
    final = attach_dependents(linked, graph, manufacturing) if attach else linked
    return _assemble_proposals(final, linked, seeds, weakest, index, fired)
