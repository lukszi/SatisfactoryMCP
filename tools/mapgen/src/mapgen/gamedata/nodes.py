"""The static resource-node table: every node's position, and the oil nodes the bake stamps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

import numpy as np

from mapgen.common import ROOT
from satisfactory_mcp.core.arrays import F64Grid

__all__ = [
    "NODE_TABLE",
    "OIL_NODE",
    "StaticNodes",
    "load_static_nodes",
    "oil_nodes",
]


#: The node table, and the node whose oil puddle every bake cell carries as a stamp.
NODE_TABLE = ROOT / "data" / "world_resource_nodes.json"
OIL_NODE = ("BP_ResourceNode_C", "Desc_LiquidOil_C")


#: One row of the table's ``nodes``, the fields read here; ``class`` is a keyword, hence
#: the functional form.
NodeRow = TypedDict(
    "NodeRow",
    {"x": float, "y": float, "z": float, "class": NotRequired[str], "resource": NotRequired[str]},
)


class StaticNodes(NamedTuple):
    """Every static node's position: world cm across, metres up."""

    x_cm: F64Grid
    y_cm: F64Grid
    z_m: F64Grid


def load_static_nodes(table: Path = NODE_TABLE) -> StaticNodes:
    """The node table's positions, in table order."""
    nodes: list[NodeRow] = json.loads(table.read_text(encoding="utf-8"))["nodes"]
    return StaticNodes(
        np.array([n["x"] for n in nodes], float),
        np.array([n["y"] for n in nodes], float),
        np.array([n["z"] for n in nodes], float) / 100.0,
    )


def oil_nodes(table: Path = NODE_TABLE) -> F64Grid:
    """``(n, 2)`` world metres of every crude oil node in the table; none without one."""
    nodes: list[NodeRow]
    try:
        nodes = json.loads(table.read_text(encoding="utf-8"))["nodes"]
    except (OSError, ValueError, KeyError):
        nodes = []
    found = [(n["x"] / 100.0, n["y"] / 100.0) for n in nodes
             if (n.get("class"), n.get("resource")) == OIL_NODE]  # fmt: skip
    return np.asarray(found, np.float64).reshape(-1, 2)
