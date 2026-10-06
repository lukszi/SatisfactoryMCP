"""The factory graph and everything built on it: identity, labels, selection.

Identity, health, layout and diff all want the graph, so it is built once here rather than
re-derived in fragments by each.
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
