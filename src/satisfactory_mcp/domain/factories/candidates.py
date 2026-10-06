"""Candidate factories, from three signals that each fail alone.

A *base* comes from power islands, a *line* from material components, and a *cluster* from
what machines make and where they stand. They are offered as candidates rather than as an
answer: which grouping is "a factory" is a naming decision, which is why labels attach to
arbitrary machine sets. docs/save-projection.md §6.2 has the measurements.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData, pretty_class
from ...core.saveio.records import instance_leaf, iter_machine_records
from ...core.saveio.schema import Projection
from ..spatial import geo
from .model import FactoryGraph

__all__ = [
    "Candidate",
    "bases",
    "describe",
    "lines_within",
    "machines_making",
    "positions",
    "product_clusters",
    "recipes_by_machine",
]

#: Machines further apart than this were not built as one thing; holds the 95 m steel site.
CLUSTER_LINK_M = 150.0

#: Below this a "factory" is a stray machine or two, reported as fragments instead.
MIN_MACHINES = 3


@dataclass
class Candidate:
    """A proposed factory, with the evidence that produced it."""

    machines: list[str]
    source: str  # power | material | product
    products: Counter[str] = field(default_factory=Counter[str])
    recipes: Counter[str] = field(default_factory=Counter[str])
    buildings: Counter[str] = field(default_factory=Counter[str])
    centroid: tuple[float, float] = (0.0, 0.0)
    spread_m: float = 0.0
    label: str | None = None

    @property
    def size(self) -> int:
        return len(self.machines)

    def name_hint(self) -> str:
        """A name from what it makes, since that is how a player refers to it.

        Products only lead when they describe most of the cluster. Generators and
        extractors run no recipe, so a coal plant that has absorbed its water pumps and
        one stray concrete constructor has exactly ONE product across 47 machines -- and
        rendering that as "Concrete" would be a worse name than no name at all.
        """
        if self.products and sum(self.products.values()) * 2 >= self.size:
            return " + ".join(n for n, _ in self.products.most_common(2))
        if self.buildings:
            top, count = self.buildings.most_common(1)[0]
            hint = f"{count}x {pretty_class(top)}"
            if self.products:
                hint += f" + {self.products.most_common(1)[0][0]}"
            return hint
        return "unnamed"


def positions(projection: Projection) -> dict[str, tuple[float, float, float]]:
    """Every placed machine, extractor and generator, by instance leaf, in centimetres.

    A record with no ``pos`` is absent rather than zeroed, so a lookup of it fails loudly
    instead of placing the machine at the world centre.
    """
    out: dict[str, tuple[float, float, float]] = {}
    for _group, leaf, record in iter_machine_records(projection):
        pos = record.get("pos")
        if pos:
            out[leaf] = (pos[0], pos[1], pos[2])
    return out


def recipes_by_machine(projection: Projection) -> dict[str, str]:
    """Every manufacturer with a recipe set, by instance leaf, to its recipe id."""
    return {
        instance_leaf(r["instance"]): recipe
        for r in projection.get("machines", ())
        if (recipe := r.get("recipe"))
    }


def machines_making(
    game: GameData, projection: Projection, products: list[str], within: list[str] | None = None
) -> list[str]:
    """Machines whose recipe makes any of ``products`` (item names, any case)."""
    wanted = {p.casefold() for p in products}
    scope = set(within) if within is not None else None
    hits: list[str] = []
    for machine, recipe_id in recipes_by_machine(projection).items():
        if scope is not None and machine not in scope:
            continue
        recipe = game.recipes.get(recipe_id)
        if recipe is None:
            continue
        if any(game.item_name(f.item).casefold() in wanted for f in recipe.products):
            hits.append(machine)
    return hits


def describe(
    machines: list[str],
    graph: FactoryGraph,
    game: GameData,
    projection: Projection,
    source: str,
) -> Candidate:
    """Attach products, buildings and geometry to a set of machines."""
    pos = positions(projection)
    recipe_of = recipes_by_machine(projection)
    products: Counter[str] = Counter()
    recipes: Counter[str] = Counter()
    buildings: Counter[str] = Counter()

    for m in machines:
        buildings[graph.cls.get(m, "?")] += 1
        recipe = game.recipes.get(recipe_of.get(m) or "")
        if recipe is None:
            continue
        recipes[recipe.name] += 1
        for flow in recipe.products[:1]:
            products[game.item_name(flow.item)] += 1

    pts = [(pos[m][0], pos[m][1]) for m in machines if m in pos]
    cx, cy = geo.centroid(pts) or (0.0, 0.0)
    spread = geo.diameter_m(pts)

    return Candidate(
        machines=sorted(machines),
        source=source,
        products=products,
        recipes=recipes,
        buildings=buildings,
        centroid=(cx, cy),
        spread_m=spread,
    )


def bases(graph: FactoryGraph) -> list[list[str]]:
    """Power islands with the tower backbone removed.

    Towers carry no machines and their wires are an order of magnitude longer than
    pole wires, so they are transmission rather than structure. Dropping them
    separates outposts from the main base.
    """
    return graph.machine_components("power", skip=graph.towers())


def lines_within(graph: FactoryGraph, machines: list[str]) -> list[list[str]]:
    """Material components restricted to one base."""
    inside = set(machines)
    out: list[list[str]] = []
    for comp in graph.machine_components("material"):
        members = [m for m in comp if m in inside]
        if members:
            out.append(members)
    out.sort(key=len, reverse=True)
    return out


def _cluster(
    machines: list[str], pos: dict[str, tuple[float, float, float]], link_m: float
) -> list[list[str]]:
    """Single-linkage on XY. Cheap, and the sets here are small."""
    remaining = [m for m in machines if m in pos]
    out: list[list[str]] = []
    while remaining:
        group = [remaining.pop()]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                if any(geo.distance_m(pos[candidate], pos[m]) <= link_m for m in group):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        out.append(group)
    out.sort(key=len, reverse=True)
    return out


def product_clusters(
    graph: FactoryGraph,
    game: GameData,
    projection: Projection,
    products: list[str],
    link_m: float = CLUSTER_LINK_M,
    within: list[str] | None = None,
) -> list[Candidate]:
    """Machines making any of ``products``, grouped by position.

    This is what recovers a factory buried inside a belt-connected base: product alone
    over-collects, and position is what separates the sites (§6.2).
    """
    hits = machines_making(game, projection, products, within)
    return [
        describe(group, graph, game, projection, "product")
        for group in _cluster(hits, positions(projection), link_m)
    ]


def bases_and_lines(
    graph: FactoryGraph, game: GameData, projection: Projection
) -> tuple[list[Candidate], list[Candidate]]:
    """Every base, and the lines inside each. Returns (bases, lines)."""
    base_cands: list[Candidate] = []
    line_cands: list[Candidate] = []
    for base in bases(graph):
        base_cands.append(describe(base, graph, game, projection, "power"))
        for line in lines_within(graph, base):
            if len(line) >= MIN_MACHINES:
                line_cands.append(describe(line, graph, game, projection, "material"))
    base_cands.sort(key=lambda c: -c.size)
    line_cands.sort(key=lambda c: -c.size)
    return base_cands, line_cands


def unassigned(graph: FactoryGraph, assigned: set[str]) -> list[str]:
    """Machines no label covers -- the answer to "what have I forgotten"."""
    return sorted(m for m in graph.machines() if m not in assigned)


def cluster_machines(
    machines: list[str], projection: Projection, link_m: float = CLUSTER_LINK_M
) -> list[list[str]]:
    """Public spatial grouping, for carving an arbitrary machine set."""
    return _cluster(machines, positions(projection), link_m)
