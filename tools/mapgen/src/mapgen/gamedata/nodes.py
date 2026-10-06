"""The static resource-node table, and the oil nodes the bake stamps."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mapgen.common import ROOT

__all__ = [
    "NODE_TABLE",
    "OIL_NODE",
    "oil_nodes",
]


#: The node table, and the node whose oil puddle every bake cell carries as a stamp.
NODE_TABLE = ROOT / "data" / "world_resource_nodes.json"
OIL_NODE = ("BP_ResourceNode_C", "Desc_LiquidOil_C")


def oil_nodes(table: Path = NODE_TABLE) -> np.ndarray:
    """``(n, 2)`` world metres of every crude oil node in the table; none without one."""
    try:
        nodes = json.loads(table.read_text(encoding="utf-8"))["nodes"]
    except (OSError, ValueError, KeyError):
        nodes = []
    found = [(n["x"] / 100.0, n["y"] / 100.0) for n in nodes
             if (n.get("class"), n.get("resource")) == OIL_NODE]  # fmt: skip
    return np.asarray(found, np.float64).reshape(-1, 2)
