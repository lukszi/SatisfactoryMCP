"""The factory graph and everything built on it: identity, labels, selection.

Kept as its own package rather than inside ``save`` because the graph is not a view of
a save file so much as the structure the save happens to describe -- identity, health,
layout and diff all want it, and each was re-deriving fragments of it.
"""

from .build import build_graph
from .candidates import Candidate, bases, describe, lines_within, product_clusters
from .cohere import Proposal, propose
from .labels import Label, LabelStore
from .model import Edge, FactoryGraph, kind_of
from .select import SelectorError, select_machines
from .structure import Slab, Structures, build_structures

__all__ = [
    "Candidate",
    "Edge",
    "FactoryGraph",
    "Label",
    "LabelStore",
    "Proposal",
    "SelectorError",
    "Slab",
    "Structures",
    "bases",
    "build_graph",
    "build_structures",
    "describe",
    "kind_of",
    "lines_within",
    "product_clusters",
    "propose",
    "select_machines",
]
