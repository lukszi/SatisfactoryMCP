"""Resolve human source selectors to a concrete set of resource nodes.

A *source spec* is a list of selectors. Location selectors union together; filter
selectors narrow the result. So ``["Northern Forest", "near:0,-2000@800",
"resource:Crude Oil"]`` means "crude oil in the Northern Forest, plus any crude
within 800 m of (0, -2000)". docs/selectors.md has the whole grammar, shared with the
machine selectors, and why an unmatched selector is reported rather than dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from ....core.saveio.records import instance_leaf
from .. import geo
from ..places import parse_near, resolve_place
from ..regions import load_regions
from .table import KINDS, PURITIES

__all__ = ["SELECTOR_HELP", "Selection", "select_nodes", "split_spec"]

SELECTOR_HELP = (
    "selectors: north|south|east|west|northeast|... , region:<name>, grid:X3Y4, "
    "node:<instance>, pin:<n>, near:<place>@<radius_m>, bbox:<x1>,<y1>,<x2>,<y2>, "
    "resource:<name>, purity:pure|normal|impure, kind:node|well_sat|geyser. Any "
    "filter takes all, which means no filter; all on its own is every node"
)

_LOCATION_PREFIXES = ("region", "grid", "node", "near", "bbox")
_FILTER_PREFIXES = ("resource", "purity", "kind")


@dataclass
class Selection:
    nodes: list[dict] = field(default_factory=list)
    described: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: True when no location selector was given, so the whole map is in scope.
    whole_map: bool = False

    @property
    def description(self) -> str:
        if self.whole_map:
            return "whole map"
        return " + ".join(self.described) or "nothing"


def _split(selector: str) -> tuple[str | None, str]:
    if ":" in selector:
        head, _, tail = selector.partition(":")
        head = head.strip().casefold()
        if head in _LOCATION_PREFIXES or head in _FILTER_PREFIXES:
            return head, tail.strip()
    return None, selector.strip()


def _numbers(text: str, count: int) -> list[float] | None:
    parts = [p for p in text.replace(" ", "").split(",") if p]
    if len(parts) != count:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def split_spec(spec: list[str] | str | None) -> tuple[list[str], list[str]]:
    """Partition a source spec into its LOCATION selectors and the filters that narrow them.

    Filters are per-node predicates, so filtering each location set and then unioning gives
    exactly the same nodes as filtering the union -- which is what lets a caller ask what
    ONE selector of a multi-selector spec contributed without re-deriving the whole spec.
    A stored plan uses that to record its field selector by selector, so a note can name
    which one changed meaning rather than only that the total moved.
    """
    if spec is None:
        return [], []
    if isinstance(spec, str):
        spec = [spec]
    locations: list[str] = []
    filters: list[str] = []
    for entry in (e.strip() for e in spec):
        if not entry:
            continue
        prefix, _value = _split(entry)
        (filters if prefix in _FILTER_PREFIXES else locations).append(entry)
    return locations, filters


@dataclass
class _SelectContext:
    """What every location resolver reads: the nodes, their indexes and the caller's frame."""

    nodes: list[dict]
    selection: Selection
    origin: tuple[float, float] | None
    half_angle: float
    st: object
    by_instance: dict[str, dict] = field(default_factory=dict)
    by_leaf: dict[str, dict] = field(default_factory=dict)
    _regions: object = None

    def __post_init__(self) -> None:
        self.by_instance = {n["instance"]: n for n in self.nodes}
        self.by_leaf = {instance_leaf(n["instance"]): n for n in self.nodes}

    @property
    def regions(self):
        if self._regions is None:
            self._regions = load_regions()
        return self._regions

    def error(self, text: str) -> _Rejected:
        self.selection.errors.append(text)
        return _REJECTED


class _Match(NamedTuple):
    """A location resolved: its nodes, how to describe it, and a warning if any."""

    hits: list[dict]
    described: str
    error: str | None = None


class _Rejected:
    """The selector was this resolver's and failed; its error is already recorded."""


class _NoMatch:
    """The selector is not this resolver's; try the next."""


_REJECTED = _Rejected()
_NO_MATCH = _NoMatch()


def _select_pin(ctx: _SelectContext, prefix, value: str):
    if prefix is not None or not value.casefold().startswith("pin:"):
        return _NO_MATCH
    from ...session import pins

    n = pins.parse(value)
    if n is None:
        return ctx.error(f"{value!r} is not a pin: write pin:<n>. {SELECTOR_HELP}")
    try:
        wanted, echo = pins.selector_terms(ctx.st, n, "nodes")
    except pins.PinError as exc:
        return ctx.error(str(exc))
    hits = []
    for term in wanted:
        key = term.removeprefix("node:")
        hit = ctx.by_instance.get(key) or ctx.by_leaf.get(key)
        if hit is not None:
            hits.append(hit)
    return _Match(hits, f"{echo} ({len(hits)} nodes)")


def _select_all(ctx: _SelectContext, prefix, value: str):
    if prefix is not None or value.casefold() != "all":
        return _NO_MATCH
    return _Match(list(ctx.nodes), "whole map")


def _select_direction(ctx: _SelectContext, prefix, value: str):
    if prefix is not None:
        return _NO_MATCH
    try:
        direction = geo.normalise_direction(value)
    except ValueError:
        return _NO_MATCH
    hits = [
        n
        for n in ctx.nodes
        if geo.in_direction(n["x"], n["y"], direction, ctx.origin, ctx.half_angle)
    ]
    scope = f"{direction} of {ctx.origin}" if ctx.origin else f"{direction}ern half"
    return _Match(hits, f"{scope} ({len(hits)} nodes)")


def _select_region(ctx: _SelectContext, prefix, value: str):
    if prefix not in ("region", None):
        return _NO_MATCH
    resolved = ctx.regions.resolve(value)
    if resolved is not None:
        hits = ctx.regions.nodes_in_region(ctx.nodes, resolved)
        return _Match(hits, f"{resolved} ({len(hits)} nodes)")
    if prefix == "region":
        return ctx.error(f"unknown region {value!r}; known: {', '.join(ctx.regions.names())}")
    return _NO_MATCH


def _select_grid(ctx: _SelectContext, prefix, value: str):
    if not (prefix == "grid" or (prefix is None and _looks_like_grid(value))):
        return _NO_MATCH
    want = value.upper().replace(" ", "")
    hits = [n for n in ctx.nodes if geo.grid_cell(n["x"], n["y"]) == want]
    error = None if hits else f"grid cell {want} contains no nodes"
    return _Match(hits, f"grid {want} ({len(hits)} nodes)", error)


def _select_node_id(ctx: _SelectContext, prefix, value: str):
    if prefix not in ("node", None):
        return _NO_MATCH
    hit = ctx.by_instance.get(value) or ctx.by_leaf.get(value)
    if hit is not None:
        return _Match([hit], instance_leaf(hit["instance"]))
    if prefix == "node":
        return ctx.error(f"unknown node {value!r}")
    return _NO_MATCH


def _select_near(ctx: _SelectContext, prefix, value: str):
    if prefix != "near":
        return _NO_MATCH
    try:
        place, radius = parse_near(value)
        centre, where = resolve_place(ctx.st, place)
    except ValueError as exc:
        return ctx.error(str(exc))
    hits = [n for n in ctx.nodes if geo.distance_m((n["x"], n["y"]), centre) <= radius]
    return _Match(hits, f"within {radius:g}m of {where} ({len(hits)} nodes)")


def _select_bbox(ctx: _SelectContext, prefix, value: str):
    if prefix != "bbox":
        return _NO_MATCH
    nums = _numbers(value, 4)
    if nums is None:
        return ctx.error(f"bbox: expects <x1>,<y1>,<x2>,<y2> in metres, got {value!r}")
    x1, y1, x2, y2 = (v * 100 for v in nums)
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    hits = [n for n in ctx.nodes if lo_x <= n["x"] <= hi_x and lo_y <= n["y"] <= hi_y]
    return _Match(hits, f"bbox ({len(hits)} nodes)")


#: Tried in this order for every location selector; the first that claims it decides.
_RESOLVERS: tuple[Callable, ...] = (
    _select_pin,
    _select_all,
    _select_direction,
    _select_region,
    _select_grid,
    _select_node_id,
    _select_near,
    _select_bbox,
)


def _apply_filters(
    nodes: list[dict], filters: list[tuple[str, str]], resolve_resource, selection: Selection
) -> list[dict]:
    """Narrow the located nodes by every filter; ``all`` is the surface's word for none."""
    result = nodes
    for kind, value in filters:
        low = value.casefold()
        if low == "all":
            continue
        if kind == "resource":
            resource_id = resolve_resource(value) if resolve_resource else value
            if resource_id is None:
                selection.errors.append(f"unknown resource {value!r}")
                continue
            result = [n for n in result if n["resource"] == resource_id]
            selection.described.append(f"resource={value}")
        elif kind == "purity":
            if low not in PURITIES:
                selection.errors.append(f"purity must be {'|'.join(PURITIES)}, got {value!r}")
                continue
            result = [n for n in result if n["purity"] == low]
            selection.described.append(f"purity={low}")
        elif kind == "kind":
            if low not in KINDS:
                selection.errors.append(f"kind must be {'|'.join(KINDS)}, got {value!r}")
                continue
            result = [n for n in result if n["kind"] == low]
            selection.described.append(f"kind={low}")
    return result


def select_nodes(
    spec: list[str] | str | None,
    nodes: list[dict],
    resolve_resource=None,
    origin: tuple[float, float] | None = None,
    half_angle: float = 60.0,
    st=None,
) -> Selection:
    """Apply a source spec to ``nodes``.

    ``resolve_resource`` maps a display name to an item id; when omitted, a
    ``resource:`` selector must already use the class id.

    ``origin`` turns direction selectors into cones from that point; leaving it None
    keeps them as map hemispheres, which is the better reading of "what oil is in the
    north". ``st`` is separate and deliberately narrow -- it only resolves the PLACE
    inside a ``near:`` term, so supplying it can never silently reinterpret a direction.
    Without it only the place kinds that need no save resolve, and the rest say so.
    """
    selection = Selection()
    if spec is None:
        selection.nodes = list(nodes)
        selection.whole_map = True
        return selection
    if isinstance(spec, str):
        spec = [spec]
    entries = [s for s in (e.strip() for e in spec) if s]
    if not entries:
        selection.nodes = list(nodes)
        selection.whole_map = True
        return selection

    ctx = _SelectContext(nodes, selection, origin, half_angle, st)
    picked: dict[str, dict] = {}
    filters: list[tuple[str, str]] = []
    location_seen = False
    # "No location asked for" is the whole map; "asked for and none resolved" is nothing,
    # because falling back to the whole map on a typo answers a different question.
    location_attempted = False

    for entry in entries:
        prefix, value = _split(entry)
        if prefix in _FILTER_PREFIXES:
            filters.append((prefix, value))
            continue
        location_attempted = True
        for resolver in _RESOLVERS:
            outcome = resolver(ctx, prefix, value)
            if outcome is _NO_MATCH:
                continue
            if isinstance(outcome, _Match):
                location_seen = True
                picked.update({n["instance"]: n for n in outcome.hits})
                selection.described.append(outcome.described)
                if outcome.error:
                    selection.errors.append(outcome.error)
            break
        else:
            selection.errors.append(f"unrecognised selector {entry!r}. {SELECTOR_HELP}")

    if location_seen:
        located = list(picked.values())
        selection.whole_map = False
    elif location_attempted:
        selection.nodes = []
        selection.described.append("no location resolved")
        selection.whole_map = False
        return selection
    else:
        located = list(nodes)
        selection.whole_map = True

    selection.nodes = _apply_filters(located, filters, resolve_resource, selection)
    return selection


def _looks_like_grid(value: str) -> bool:
    v = value.upper().replace(" ", "")
    return (
        len(v) >= 4
        and v[0] == "X"
        and "Y" in v[1:]
        and v[1 : v.index("Y", 1)].isdigit()
        and v[v.index("Y", 1) + 1 :].isdigit()
    )
