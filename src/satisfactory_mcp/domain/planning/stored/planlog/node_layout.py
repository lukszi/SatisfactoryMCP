"""A plan's graph layout: node id to ``[x, y]`` in graph units, checked on write and on read.

The layout never reaches the solver. docs/planner_slice_contract.md §3 has the two ops.
"""

from __future__ import annotations

import math

from .....core.jsontypes import is_object_dict, is_object_sequence
from ..plan_args import InvalidOp

__all__ = ["checked_layout", "checked_node", "checked_position", "stored_layout"]

MAX_NODES = 2_000
MAX_ID = 256
MAX_COORD = 1e7


def checked_node(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ID:
        raise InvalidOp(f"a graph node id must be text of 1 to {MAX_ID} characters")
    return value


def checked_position(value: object) -> list[float] | None:
    """``[x, y]`` as finite floats, or None to forget a node's place."""
    if value is None:
        return None
    if not is_object_sequence(value) or len(value) != 2:
        raise InvalidOp(f"a node position must be [x, y], not {value!r}")
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise InvalidOp(f"a node position must be two finite numbers, not {value!r}")
        if abs(v) > MAX_COORD:
            raise InvalidOp(f"a node position must be within ±{MAX_COORD:g}, not {value!r}")
        out.append(float(v))
    return out


def checked_layout(value: object) -> dict[str, list[float]]:
    """A whole layout; None is the empty one."""
    if value is None:
        return {}
    if not is_object_dict(value):
        raise InvalidOp("a layout must be an object of node id to [x, y]")
    if len(value) > MAX_NODES:
        raise InvalidOp(f"a layout holds at most {MAX_NODES:,} nodes")
    out: dict[str, list[float]] = {}
    for key, pos in value.items():
        place = checked_position(pos)
        if place is not None:
            out[checked_node(key)] = place
    return out


def stored_layout(value: object) -> dict[str, list[float]]:
    """A layout read back from the log; one that no longer checks reads as empty."""
    try:
        return checked_layout(value)
    except InvalidOp:
        return {}
