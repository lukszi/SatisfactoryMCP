"""Resolve human source selectors to a concrete set of resource nodes.

A *source spec* is a list of selectors. Location selectors union together; filter
selectors narrow the result. So ``["Northern Forest", "near:0,-2000@800",
"resource:Crude Oil"]`` means "crude oil in the Northern Forest, plus any crude
within 800 m of (0, -2000)". The full grammar, shared with the machine selectors, is
in `docs/selectors.md`.

Supported selectors (prefix optional where unambiguous)::

    north | south | east | west | northeast | ...   compass hemisphere or cone
    region:Northern Forest                          named region (advisory names)
    grid:X3Y4                                       exact 1.024 km biome grid cell
    node:BP_ResourceNode26_99                       one specific node, by instance
    pin:3                                           a node or field pin's nodes
    near:<place>@<radius_m>                         circle around any place
    bbox:<x1>,<y1>,<x2>,<y2>                        rectangle, metres
    resource:Crude Oil                              filter: resource type
    purity:pure|normal|impure                       filter: purity
    kind:node|well_sat|geyser|all                   filter: node kind
    all                                             every node on the map

Unmatched selectors are REPORTED, never silently dropped: a typo'd region name that
quietly returns the whole map would produce a confidently wrong plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import geo
from ..places import parse_near, resolve_place
from ..regions import load_regions

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
    sel = Selection()
    if spec is None:
        sel.nodes = list(nodes)
        sel.whole_map = True
        return sel
    if isinstance(spec, str):
        spec = [spec]
    entries = [s for s in (e.strip() for e in spec) if s]
    if not entries:
        sel.nodes = list(nodes)
        sel.whole_map = True
        return sel

    by_instance = {n["instance"]: n for n in nodes}
    short_index: dict[str, dict] = {}
    for n in nodes:
        short_index[n["instance"].rsplit(".", 1)[-1]] = n

    regions = None
    picked: dict[str, dict] = {}
    filters: list[tuple[str, str]] = []
    location_seen = False
    #: Distinguishes "no location was asked for" (whole map is the right answer) from
    #: "a location was asked for and did not resolve". Falling back to the whole map
    #: on a typo'd region name would answer a completely different question.
    location_attempted = False

    for entry in entries:
        prefix, value = _split(entry)
        low = value.casefold()

        if prefix in _FILTER_PREFIXES:
            filters.append((prefix, value))
            continue

        location_attempted = True

        if prefix is None and low.startswith("pin:"):
            hits, where = _pin_nodes(st, value, by_instance, short_index, sel)
            if hits is not None:
                location_seen = True
                picked.update({n["instance"]: n for n in hits})
                sel.described.append(f"{where} ({len(hits)} nodes)")
            continue

        if prefix is None and low == "all":
            location_seen = True
            picked.update({n["instance"]: n for n in nodes})
            sel.described.append("whole map")
            continue

        # ---- direction ------------------------------------------------
        if prefix is None:
            try:
                direction = geo.normalise_direction(value)
            except ValueError:
                direction = None
            if direction is not None:
                location_seen = True
                hits = [
                    n
                    for n in nodes
                    if geo.in_direction(n["x"], n["y"], direction, origin, half_angle)
                ]
                picked.update({n["instance"]: n for n in hits})
                scope = f"{direction} of {origin}" if origin else f"{direction}ern half"
                sel.described.append(f"{scope} ({len(hits)} nodes)")
                continue

        # ---- named region ---------------------------------------------
        if prefix == "region" or prefix is None:
            if regions is None:
                regions = load_regions()
            resolved = regions.resolve(value)
            if resolved is not None:
                location_seen = True
                hits = regions.filter_nodes(nodes, resolved)
                picked.update({n["instance"]: n for n in hits})
                sel.described.append(f"{resolved} ({len(hits)} nodes)")
                continue
            if prefix == "region":
                sel.errors.append(f"unknown region {value!r}; known: {', '.join(regions.names())}")
                continue

        # ---- grid cell -------------------------------------------------
        if prefix == "grid" or (prefix is None and _looks_like_grid(value)):
            want = value.upper().replace(" ", "")
            hits = [n for n in nodes if geo.grid_cell(n["x"], n["y"]) == want]
            location_seen = True
            picked.update({n["instance"]: n for n in hits})
            sel.described.append(f"grid {want} ({len(hits)} nodes)")
            if not hits:
                sel.errors.append(f"grid cell {want} contains no nodes")
            continue

        # ---- explicit node id ------------------------------------------
        if prefix == "node" or prefix is None:
            hit = by_instance.get(value) or short_index.get(value)
            if hit is not None:
                location_seen = True
                picked[hit["instance"]] = hit
                sel.described.append(hit["instance"].rsplit(".", 1)[-1])
                continue
            if prefix == "node":
                sel.errors.append(f"unknown node {value!r}")
                continue

        # ---- circle ----------------------------------------------------
        if prefix == "near":
            try:
                place, radius = parse_near(value)
                centre, where = resolve_place(st, place)
            except ValueError as exc:
                sel.errors.append(str(exc))
                continue
            hits = [n for n in nodes if geo.distance_m((n["x"], n["y"]), centre) <= radius]
            location_seen = True
            picked.update({n["instance"]: n for n in hits})
            sel.described.append(f"within {radius:g}m of {where} ({len(hits)} nodes)")
            continue

        # ---- rectangle -------------------------------------------------
        if prefix == "bbox":
            nums = _numbers(value, 4)
            if nums is None:
                sel.errors.append(f"bbox: expects <x1>,<y1>,<x2>,<y2> in metres, got {value!r}")
                continue
            x1, y1, x2, y2 = (v * 100 for v in nums)
            lo_x, hi_x = min(x1, x2), max(x1, x2)
            lo_y, hi_y = min(y1, y2), max(y1, y2)
            hits = [n for n in nodes if lo_x <= n["x"] <= hi_x and lo_y <= n["y"] <= hi_y]
            location_seen = True
            picked.update({n["instance"]: n for n in hits})
            sel.described.append(f"bbox ({len(hits)} nodes)")
            continue

        sel.errors.append(f"unrecognised selector {entry!r}. {SELECTOR_HELP}")

    if location_seen:
        result = list(picked.values())
        sel.whole_map = False
    elif location_attempted:
        # Every location selector failed. Return nothing so the caller reports the
        # error, rather than quietly planning against the entire map.
        sel.nodes = []
        sel.described.append("no location resolved")
        sel.whole_map = False
        return sel
    else:
        result = list(nodes)
        sel.whole_map = True

    # ---- filters ------------------------------------------------------
    for kind, value in filters:
        low = value.casefold()
        # `all` is the surface's one word for "no filter", and a filter term that refused
        # it made the same word a wildcard on the tools and an error inside the selector.
        if low == "all":
            continue
        if kind == "resource":
            rid = resolve_resource(value) if resolve_resource else value
            if rid is None:
                sel.errors.append(f"unknown resource {value!r}")
                continue
            result = [n for n in result if n["resource"] == rid]
            sel.described.append(f"resource={value}")
        elif kind == "purity":
            if low not in ("pure", "normal", "impure"):
                sel.errors.append(f"purity must be pure|normal|impure, got {value!r}")
                continue
            result = [n for n in result if n["purity"] == low]
            sel.described.append(f"purity={low}")
        elif kind == "kind":
            if low not in ("node", "well_sat", "geyser"):
                sel.errors.append(f"kind must be node|well_sat|geyser, got {value!r}")
                continue
            result = [n for n in result if n["kind"] == low]
            sel.described.append(f"kind={low}")

    sel.nodes = result
    return sel


def _pin_nodes(st, value: str, by_instance: dict, short_index: dict, sel: Selection):
    from ...session import pins

    n = pins.parse(value)
    if n is None:
        sel.errors.append(f"{value!r} is not a pin: write pin:<n>. {SELECTOR_HELP}")
        return None, ""
    try:
        wanted, echo = pins.selector_terms(st, n, "nodes")
    except pins.PinError as exc:
        sel.errors.append(str(exc))
        return None, ""
    hits = []
    for term in wanted:
        key = term.removeprefix("node:")
        hit = by_instance.get(key) or short_index.get(key)
        if hit is not None:
            hits.append(hit)
    return hits, echo


def _looks_like_grid(value: str) -> bool:
    v = value.upper().replace(" ", "")
    return (
        len(v) >= 4
        and v[0] == "X"
        and "Y" in v[1:]
        and v[1 : v.index("Y", 1)].isdigit()
        and v[v.index("Y", 1) + 1 :].isdigit()
    )
