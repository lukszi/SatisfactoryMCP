"""The name the page offers for a factory nobody has named yet, in one of ``STYLES``.

``lead`` picks the item, ``suggest`` words it; docs/frontend_vision.md §9.4 states both rules.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData
from . import flowgraph
from .candidates import Candidate, describe
from .cohere import Proposal
from .flowgraph import FlowGraph
from .labels import slugify
from .query import build_view

if TYPE_CHECKING:
    from ..spatial.regions import RegionMap
    from ..world.state import WorldState

__all__ = ["DEFAULT_STYLE", "STYLES", "lead", "lead_of", "proposal_names", "suggest"]

STYLES = ("short", "product, region")

DEFAULT_STYLE = "short"


def lead(
    flows: FlowGraph, cand: Candidate, game: GameData, extracted: set[str]
) -> tuple[str, bool]:
    """The item a name leads with, and whether it is a product (``True``) or a guess.

    In order: the top product a recipe makes, by rate; the commonest generator, for a set
    that makes power; the top extracted product; the top item made at all; the commonest
    building. Only the last two are guesses, and the guess prefers a made item to an ore.
    """
    products = flows.listed("product")
    made = [row for row in products if row[0] not in extracted]
    if made:
        return made[0][0], True
    generators = [
        (n, cls)
        for cls, n in cand.buildings.items()
        if (b := game.buildings.get(cls)) and b.is_generator
    ]
    if generators:
        cls = max(generators)[1]
        return game.building_name(cls) or cls, True
    if products:
        return products[0][0], True
    made_at_all = [kv for kv in flows.produced.items() if kv[0] not in extracted]
    for pool in (made_at_all, list(flows.produced.items())):
        if pool:
            return max(pool, key=lambda kv: (kv[1], kv[0]))[0], False
    if cand.buildings:
        cls = cand.buildings.most_common(1)[0][0]
        return game.building_name(cls) or cls, False
    return "factory", False


def _worded(item: str, region: str | None, style: str, n: int) -> str:
    number = f" {n}" if n > 1 else ""
    if style == "short":
        return f"{item.lower()} factory{number}"
    if style == "product, region":
        return item + number + (f", {region}" if region else "")
    raise ValueError(f"unknown naming style {style!r}; known: {', '.join(STYLES)}")


def suggest(item: str, region: str | None, taken: Iterable[str], style: str = DEFAULT_STYLE) -> str:
    """``item`` worded in ``style``, numbered past any name in ``taken`` or its slug."""
    held = list(taken)
    names = {t.strip().casefold() for t in held}
    slugs = {slugify(t) for t in held}
    n = 1
    name = _worded(item, region, style, n)
    while name.casefold() in names or slugify(name) in slugs:
        n += 1
        name = _worded(item, region, style, n)
    return name


def lead_of(st: WorldState, machines: list[str], cand: Candidate | None = None) -> tuple[str, bool]:
    """``lead`` for a machine set of ``st``, building the view and flow graph it reads."""
    if cand is None:
        cand = describe(machines, st.graph, st.game, st.projection, "proposal")
    view = build_view("proposal", machines, st.graph, st.game, st.projection)
    extracted = {row.resource for row in view.nodes}
    return lead(flowgraph.build(st, st.game, view), cand, st.game, extracted)


def proposal_names(
    st: WorldState,
    proposals: list[Proposal],
    style: str = DEFAULT_STYLE,
    region_map: RegionMap | None = None,
) -> dict[int, str]:
    """The suggested name of every proposal no label covers, by index, numbered in index
    order past the names already taken -- one answer for the map, Detect and chat."""
    taken = [label.name for label in st.labels.labels]
    out: dict[int, str] = {}
    for index, proposal in enumerate(proposals):
        if st.labels.covers(proposal.machines):
            continue
        cand = describe(proposal.machines, st.graph, st.game, st.projection, "proposal")
        item, _confident = lead_of(st, proposal.machines, cand)
        region = region_map.label_for(*cand.centroid).name if region_map else None
        out[index] = suggest(item, region, taken, style)
        taken.append(out[index])
    return out
