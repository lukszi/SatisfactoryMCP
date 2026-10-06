"""The factory graph: one structure, three kinds of edge, built once from a save projection.

The layers stay separate because no one of them identifies a factory: **material** is what
feeds what, **power** separates outposts once tower wires are dropped, and **transport** is a
deliberate connection BETWEEN factories. docs/save-projection.md §6.1 has the measurements.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

__all__ = ["PRODUCTION", "Edge", "FactoryGraph", "kind_of"]

#: Substrings identifying a machine that does work, as opposed to logistics or
#: infrastructure. Matched against the buildable class name.
PRODUCTION = (
    "Constructor",
    "Smelter",
    "Foundry",
    "Assembler",
    "Manufacturer",
    "OilRefinery",
    "Blender",
    "Packager",
    "HadronCollider",
    "Converter",
    "QuantumEncoder",
    "MinerMk",
    "OilPump",
    "WaterPump",
    "FrackingExtractor",
    "Generator",
)

_TRANSPORT = ("TrainStation", "DroneStation", "TruckStation", "Locomotive", "FreightWagon")


def kind_of(cls: str) -> str:
    """Classify an actor by its buildable class name."""
    if any(k in cls for k in _TRANSPORT):
        return "transport"
    if "Tower" in cls:
        return "tower"
    if "Pole" in cls and "Conveyor" not in cls:
        return "pole"
    if any(k in cls for k in PRODUCTION):
        return "machine"
    if any(k in cls for k in ("Conveyor", "Pipeline", "PipeStorage", "Storage")):
        return "logistics"
    return "other"


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    #: Connector role at each end, e.g. Output1 / ConveyorAny0. Empty for power.
    role_a: str = ""
    role_b: str = ""

    def other(self, node: str) -> str:
        return self.b if node == self.a else self.a


@dataclass
class FactoryGraph:
    """Actors, their class, and the edges between them."""

    cls: dict[str, str] = field(default_factory=dict)
    material: list[Edge] = field(default_factory=list)
    power: list[Edge] = field(default_factory=list)
    transport: list[Edge] = field(default_factory=list)
    #: Hypertubes, written into the belts' edge list: kept so a tube stays findable, apart so
    #: no material question can traverse one.
    hyper: list[Edge] = field(default_factory=list)

    _adj: dict[str, dict[str, list[Edge]]] = field(default_factory=dict, repr=False)

    # ---- lookups -------------------------------------------------------

    def kind(self, node: str) -> str:
        return kind_of(self.cls.get(node, ""))

    def is_machine(self, node: str) -> bool:
        return self.kind(node) == "machine"

    def machines(self) -> list[str]:
        return [n for n in self.cls if self.is_machine(n)]

    def edges(self, layer: str) -> list[Edge]:
        return {
            "material": self.material,
            "power": self.power,
            "transport": self.transport,
            "hyper": self.hyper,
        }[layer]

    def adjacency(self, layer: str) -> dict[str, list[Edge]]:
        """Neighbour index for one layer, built lazily and cached."""
        cached = self._adj.get(layer)
        if cached is None:
            cached = defaultdict(list)
            for e in self.edges(layer):
                cached[e.a].append(e)
                cached[e.b].append(e)
            self._adj[layer] = cached
        return cached

    def neighbours(self, node: str, layer: str = "material") -> list[str]:
        return [e.other(node) for e in self.adjacency(layer).get(node, ())]

    # ---- traversal -----------------------------------------------------

    def components(
        self,
        layer: str = "material",
        skip: set[str] | None = None,
        nodes: list[str] | None = None,
    ) -> list[list[str]]:
        """Connected components, optionally with some nodes removed.

        ``skip`` is what makes the power layer useful: dropping every tower leaves
        the local distribution islands, which is how outposts separate from the base.
        """
        adjacency = self.adjacency(layer)
        pool = nodes if nodes is not None else list(adjacency)
        allowed = set(pool)
        if skip:
            allowed -= skip
        seen: set[str] = set()
        out: list[list[str]] = []
        for start in pool:
            if start in seen or start not in allowed:
                continue
            stack = [start]
            group: list[str] = []
            while stack:
                node = stack.pop()
                if node in seen or node not in allowed:
                    continue
                seen.add(node)
                group.append(node)
                stack.extend(e.other(node) for e in adjacency.get(node, ()))
            out.append(group)
        out.sort(key=len, reverse=True)
        return out

    def machine_components(self, layer: str = "material", skip: set[str] | None = None):
        """Components reduced to their machines, dropping any with none."""
        out = []
        for comp in self.components(layer, skip=skip):
            machines = [n for n in comp if self.is_machine(n)]
            if machines:
                out.append(machines)
        out.sort(key=len, reverse=True)
        return out

    def towers(self) -> set[str]:
        return {n for n in self.cls if self.kind(n) == "tower"}

    def summary(self) -> dict:
        return {
            "actors": len(self.cls),
            "machines": len(self.machines()),
            "material_edges": len(self.material),
            "power_edges": len(self.power),
            "transport_edges": len(self.transport),
            "hyper_edges": len(self.hyper),
            "towers": len(self.towers()),
        }
