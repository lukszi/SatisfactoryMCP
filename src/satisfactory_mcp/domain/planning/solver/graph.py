"""The item-flow graph: strongly connected components, chain depth and item cycles."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence

from .model import MW, ProcessRow

__all__ = ["chain_depth", "chain_depth_of_rates", "item_cycles", "strongly_connected"]


def strongly_connected(n: int, edges: dict[int, set[int]]) -> list[list[int]]:
    """Tarjan's SCC, iterative so a deep chain cannot blow the recursion limit."""
    index: list[int | None] = [None] * n
    low = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0

    for root in range(n):
        if index[root] is not None:
            continue
        work: list[tuple[int, Iterator[int]]] = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True

        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                visited = index[nxt]
                if visited is None:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if on_stack[nxt]:
                    low[node] = min(low[node], visited)
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component: list[int] = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
    return result


def chain_depth(nodes: Sequence[tuple[Iterable[str], Iterable[str]]]) -> list[int]:
    """Longest-path depth per node over the condensation of the item graph.

    Each node is ``(inputs, outputs)`` as item ids; raw consumers sit at 0. Members of a
    cycle share one depth, since they are built together. The layout's floors and the diff's
    stages both use it (docs/planning.md §8.5, §8.5h).
    """
    producers: dict[str, list[int]] = {}
    for i, (_ins, outs) in enumerate(nodes):
        for item in outs:
            producers.setdefault(item, []).append(i)

    edges: dict[int, set[int]] = {}
    for i, (ins, _outs) in enumerate(nodes):
        for item in ins:
            for src in producers.get(item, ()):
                if src != i:
                    edges.setdefault(src, set()).add(i)

    components = strongly_connected(len(nodes), edges)
    component_of: dict[int, int] = {}
    for cid, members in enumerate(components):
        for member in members:
            component_of[member] = cid

    # Longest path over the condensation, which is a DAG.
    condensed: dict[int, set[int]] = {}
    for src, dsts in edges.items():
        for dst in dsts:
            src_component, dst_component = component_of[src], component_of[dst]
            if src_component != dst_component:
                condensed.setdefault(src_component, set()).add(dst_component)

    depth = [0] * len(components)
    for _ in range(len(components)):
        changed = False
        for src_component, dst_components in condensed.items():
            for dst_component in dst_components:
                if depth[dst_component] < depth[src_component] + 1:
                    depth[dst_component] = depth[src_component] + 1
                    changed = True
        if not changed:
            break

    return [depth[component_of[i]] for i in range(len(nodes))]


def chain_depth_of_rates(rate_maps: Sequence[Mapping[str, float]]) -> list[int]:
    """``chain_depth`` over net rates per process: a negative rate is an input, positive an output.

    ``MW`` is left out: as a dependency it would tie every consumer to every generator.
    """
    return chain_depth(
        [
            (
                [item for item, rate in rates.items() if rate < 0 and item != MW],
                [item for item, rate in rates.items() if rate > 0 and item != MW],
            )
            for rates in rate_maps
        ]
    )


def item_cycles(processes: Iterable[ProcessRow]) -> list[list[str]]:
    """Items on a production cycle: components of two or more in the input-to-output graph.

    Each cycle is sorted, and the cycles are ordered by their first item.
    """
    outputs_of: dict[str, set[str]] = {}
    for row in processes:
        rates = row["rates"]
        outputs = [item for item, rate in rates.items() if rate > 0]
        for item, rate in rates.items():
            if rate < 0:
                outputs_of.setdefault(item, set()).update(outputs)
    items = sorted(set(outputs_of) | {item for outs in outputs_of.values() for item in outs})
    index = {item: i for i, item in enumerate(items)}
    edges = {index[src]: {index[dst] for dst in dsts} for src, dsts in outputs_of.items()}
    components = strongly_connected(len(items), edges)
    return sorted(sorted(items[i] for i in members) for members in components if len(members) > 1)
