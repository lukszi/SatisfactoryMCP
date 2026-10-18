"""The node table of one save's world: the map's table with a randomized world's overrides.

A 1.2 game mode can re-roll every node's resource and purity at world creation, and the save
then carries both per node (``docs/save-projection.md`` §6.18). Positions never move, so a
caller that reads only where nodes are may keep ``load_nodes``; anything that reads what a
node yields goes through ``nodes_for``.
"""

from __future__ import annotations

from collections.abc import Mapping

from ....core.saveio.schema import NodeState, Projection
from . import table as node_table
from .table import NodeRecord, NodeTable, WellCoreRecord

__all__ = ["nodes_for", "overlaid"]

#: (base table, projection) -> this world's table. The entry holds both objects, because the
#: key is their ``id()`` and a freed object's address is reused.
_WORLDS: dict[tuple[int, int], tuple[NodeTable, Projection, NodeTable]] = {}
_WORLDS_MAX = 4


def nodes_for(projection: Projection | None) -> NodeTable:
    """The node table as this save's world has it; the map's own without a save."""
    base = node_table.load_nodes()
    if projection is None:
        return base
    key = (id(base), id(projection))
    hit = _WORLDS.get(key)
    if hit is not None and hit[0] is base and hit[1] is projection:
        return hit[2]
    table = overlaid(base, projection.get("node_state") or {})
    while len(_WORLDS) >= _WORLDS_MAX:
        del _WORLDS[next(iter(_WORLDS))]
    _WORLDS[key] = (base, projection, table)
    return table


def overlaid(base: NodeTable, node_state: Mapping[str, NodeState]) -> NodeTable:
    """``base`` with each override applied; ``base`` itself when the save overrides nothing."""
    if not any("resource" in s or "purity" in s for s in node_state.values()):
        return base
    nodes = [_node(n, node_state.get(n["instance"])) for n in base.nodes]
    by_instance = {n["instance"]: n for n in nodes}
    cores = [_core(c, node_state.get(c["instance"]), by_instance) for c in base.well_cores]
    return NodeTable(nodes=nodes, meta=base.meta, well_cores=cores, randomized=True)


def _node(node: NodeRecord, state: NodeState | None) -> NodeRecord:
    if not state or ("resource" not in state and "purity" not in state):
        return node
    out = node.copy()
    out["resource"] = state.get("resource", node["resource"])
    out["purity"] = state.get("purity", node["purity"])
    return out


def _core(
    core: WellCoreRecord, state: NodeState | None, nodes: Mapping[str, NodeRecord]
) -> WellCoreRecord:
    """A core's own override; else its satellites' resource when they agree on one."""
    resource = (state or {}).get("resource")
    if resource is None:
        found = {nodes[s]["resource"] for s in core["satellites"] if s in nodes}
        resource = found.pop() if len(found) == 1 else core["resource"]
    if resource == core["resource"]:
        return core
    out = core.copy()
    out["resource"] = resource
    return out
