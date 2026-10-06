"""Resource nodes: the static table, its game-version skew, and what the save taps."""

from .extraction import (
    annotate,
    blocking_buildings,
    capacity,
    node_rate,
    occupancy,
    reachable,
    unresolved_extractors,
)
from .skew import (
    TableSkew,
    drifted,
    identity_notes,
    position_notes,
    skew_for_save,
    skew_from_meta,
    skew_notes,
    table_age,
)
from .table import (
    EXTRA_FOR_KIND,
    EXTRACTOR_FOR_KIND,
    GEYSER_CONSUMER,
    NodeTable,
    load_nodes,
)

__all__ = [
    "EXTRACTOR_FOR_KIND",
    "EXTRA_FOR_KIND",
    "GEYSER_CONSUMER",
    "NodeTable",
    "TableSkew",
    "annotate",
    "blocking_buildings",
    "capacity",
    "drifted",
    "identity_notes",
    "load_nodes",
    "node_rate",
    "occupancy",
    "position_notes",
    "reachable",
    "skew_for_save",
    "skew_from_meta",
    "skew_notes",
    "table_age",
    "unresolved_extractors",
]
