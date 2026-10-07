"""What a stored plan's source selectors resolved to, and whether they still do.

``plan_id`` catches the world moving under a stored plan; it cannot catch the SELECTORS
moving, and they can -- ``region:Spire Coast`` is a name in a table this repository
generates, and re-deriving that table took it from 51 nodes to 18 with the plan untouched.
So a plan records, per location selector, what it resolved to, and a recall re-resolves and
reports the difference. A plan with no record is reported as "cannot be checked".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData
from ....core.jsontypes import JsonObject, JsonValue
from ....core.saveio.records import instance_leaf
from ...spatial.nodes.selectors import split_spec
from ...spatial.nodes.table import NodeRecord
from ..solver.scenario import select_for
from .views import ProvenanceRecord, SelectorRecord

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState
    from .store import StoredPlan

__all__ = [
    "LEAF_CAP",
    "SelectorDrift",
    "compare",
    "notes",
    "record",
    "recorded",
]

#: Shape of the recorded block, so a later reader can tell a v1 record from a v2 one.
PROVENANCE_SCHEMA = 1

#: How many node names one selector may store. Past this the set is counted and hashed but
#: not named: "all" is 608 rows, and a plan file is something a person opens.
LEAF_CAP = 250

#: How many names one note may print per side.
NAME_CAP = 5


def _bbox(nodes: list[NodeRecord]) -> list[float] | None:
    """The box these nodes occupy, in METRES -- the form ``bbox:`` selectors take."""
    if not nodes:
        return None
    xs = [n["x"] / 100 for n in nodes]
    ys = [n["y"] / 100 for n in nodes]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def _entry(selector: str, nodes: list[NodeRecord]) -> SelectorRecord:
    leaves = sorted(instance_leaf(n["instance"]) for n in nodes)
    return {
        "selector": selector,
        "count": len(leaves),
        "hash": hashlib.sha256("\n".join(leaves).encode("utf-8")).hexdigest()[:12],
        "nodes": leaves if len(leaves) <= LEAF_CAP else [],
        "bbox": _bbox(nodes),
    }


def record(game: GameData, state: WorldState, sources: list[str] | None) -> ProvenanceRecord:
    """Resolve ``sources`` selector by selector, as a block to store with the plan.

    A spec with no location selector is the whole map, so the block is recorded EMPTY
    rather than not at all: "checked, nothing to check" is a different answer from a plan
    that predates the check. Filters alone do narrow the map and get their own entry.
    """
    locations, filters = split_spec(sources)
    entries: list[SelectorRecord] = []
    if locations:
        for selector in locations:
            sel = select_for(game, state, [selector, *filters])
            entries.append(_entry(selector, sel.nodes))
    elif filters:
        sel = select_for(game, state, list(filters))
        entries.append(_entry(" + ".join(filters), sel.nodes))
    return {"schema": PROVENANCE_SCHEMA, "selectors": entries}


def recorded(plan: StoredPlan) -> bool:
    """Whether this plan carries a resolved-set record at all."""
    return "selectors" in (plan.provenance or {})


def _plan_sources(plan: StoredPlan) -> list[str] | None:
    """The plan's ``sources`` selectors, or None when it plans over the whole map."""
    value = plan.kwargs().get("sources")
    return [str(m) for m in value] if isinstance(value, list) else None


def _saved_entries(provenance: JsonObject) -> list[JsonObject]:
    entries = provenance.get("selectors")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _whole(value: JsonValue) -> int:
    return int(value) if isinstance(value, int | float | str) else 0


def _saved_box(value: JsonValue) -> list[float] | None:
    if not isinstance(value, list):
        return None
    return [float(v) for v in value if isinstance(v, int | float)]


@dataclass(frozen=True)
class SelectorDrift:
    """One selector that no longer resolves to what the plan was saved against."""

    selector: str
    then: int
    now: int
    #: Empty when the saved set was past ``LEAF_CAP`` and so was never named.
    gone: tuple[str, ...] = ()
    appeared: tuple[str, ...] = ()
    #: Whether both sides were named node by node, so `gone`/`appeared` are complete.
    named: bool = True
    #: The box the SAVED nodes occupied, metres -- the rewrite this note suggests.
    bbox: list[float] | None = None

    @property
    def bbox_selector(self) -> str:
        if not self.bbox:
            return ""
        return "bbox:" + ",".join(f"{v:g}" for v in self.bbox)


def compare(game: GameData, state: WorldState, plan: StoredPlan) -> list[SelectorDrift]:
    """Re-resolve every recorded selector; report only the ones that moved.

    Empty means "nothing to say" -- either nothing moved, or the plan carries no record.
    Ask ``recorded`` to tell those apart.
    """
    if not recorded(plan):
        return []
    # Re-resolved by the very function that wrote the record: halves that disagree about
    # how a spec is split would report drift that is really a refactor.
    fresh_by_selector = {
        e["selector"]: e for e in record(game, state, _plan_sources(plan))["selectors"]
    }
    out: list[SelectorDrift] = []
    for entry in _saved_entries(plan.provenance):
        selector = str(entry.get("selector") or "")
        fresh = fresh_by_selector.get(selector)
        if fresh is None:
            # The plan's own `sources` no longer contain this selector, so the ARGUMENTS
            # were edited; reporting it as drift would blame the map for an edit.
            continue
        if fresh["hash"] == entry.get("hash") and fresh["count"] == entry.get("count"):
            continue
        nodes = entry.get("nodes")
        was = [str(n) for n in nodes] if isinstance(nodes, list) else []
        named = len(was) == entry.get("count") and len(fresh["nodes"]) == fresh["count"]
        out.append(
            SelectorDrift(
                selector=selector,
                then=_whole(entry.get("count") or 0),
                now=fresh["count"],
                gone=tuple(sorted(set(was) - set(fresh["nodes"]))) if named else (),
                appeared=tuple(sorted(set(fresh["nodes"]) - set(was))) if named else (),
                named=bool(named),
                bbox=_saved_box(entry.get("bbox")),
            )
        )
    return out


def _named(leaves: tuple[str, ...]) -> str:
    if not leaves:
        return "none"
    shown = ", ".join(leaves[:NAME_CAP])
    extra = len(leaves) - NAME_CAP
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def notes(game: GameData, state: WorldState, plan: StoredPlan) -> list[str]:
    """What to tell the reader on a recall. Empty when the field has not moved."""
    if not recorded(plan):
        return _unrecorded_notes(game, state, plan)
    drifts = compare(game, state, plan)
    if not drifts:
        return []
    out = [
        (
            f"plan {plan.name!r} is planning over a DIFFERENT field than the one it was "
            "saved against -- the plan did not change, its selector did. Every number in "
            "this response is the NEW field's."
        )
    ]
    for d in drifts:
        line = f"{d.selector!r}: {d.then} node(s) when saved, {d.now} now."
        if d.named:
            line += f" gone: {_named(d.gone)}. appeared: {_named(d.appeared)}."
        else:
            line += " Too many to name: the saved set was counted and hashed, not listed."
        if d.bbox_selector:
            line += f" The saved nodes sat in {d.bbox_selector} (metres)."
        out.append(line)
    if any(d.bbox_selector for d in drifts):
        out.append(
            "to plan over the field that was actually saved, pass that box as sources= "
            "instead: a region name is advisory by design and can be re-cut under a stored "
            f"plan, a box cannot. To accept the new field, re-run with save_as={plan.name!r}."
        )
    return out


def _unrecorded_notes(game: GameData, state: WorldState, plan: StoredPlan) -> list[str]:
    """The degraded path: no record, so say that, and say what the selectors mean NOW.

    Today's resolution is stated as a bbox because a reader can compare a box to a memory
    and cannot compare a hash.
    """
    fresh = record(game, state, _plan_sources(plan))["selectors"]
    if not fresh:
        return []  # whole map: no selector that could have changed meaning
    out = [
        (
            f"plan {plan.name!r} records no resolved node set -- it was saved before plans "
            "kept one, so whether its selectors still mean what they meant then CANNOT be "
            f"checked. Re-run it with save_as={plan.name!r} to record what they resolve to "
            "from here on."
        )
    ]
    for entry in fresh:
        box = entry["bbox"]
        if box:
            tail = (
                " in bbox:"
                + ",".join(f"{v:g}" for v in box)
                + " (metres) -- pass that box as sources= if the plan was drawn over "
                "another field."
            )
        else:
            # No box because nothing matched, so there is no rectangle to point at.
            tail = " -- it selects nothing in this world, so there is no field to compare."
        out.append(f"{entry['selector']!r} resolves to {entry['count']} node(s) HERE AND NOW{tail}")
    return out
