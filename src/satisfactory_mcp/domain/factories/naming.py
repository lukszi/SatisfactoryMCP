"""The name the page offers for a factory nobody has named yet, in one of ``STYLES``.

``lead`` picks the item, ``suggest`` words it; docs/frontend_vision.md §9.4 states both rules.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...core.gamedata.model import GameData
from .flowgraph import FlowGraph
from .identity import Candidate
from .labels import slugify

__all__ = ["DEFAULT_STYLE", "STYLES", "lead", "suggest"]

STYLES = ("mine", "product, region")

DEFAULT_STYLE = "mine"


def lead(
    flows: FlowGraph, cand: Candidate, game: GameData, extracted: set[str]
) -> tuple[str, bool]:
    """The item a name leads with, and whether it is a product (``True``) or a guess.

    In order: the top product a recipe makes, by rate; the commonest generator, for a set
    that makes power; the top extracted product; the top item made at all; the commonest
    building. Only the last two are guesses.
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
    if flows.produced:
        return max(flows.produced.items(), key=lambda kv: (kv[1], kv[0]))[0], False
    if cand.buildings:
        cls = cand.buildings.most_common(1)[0][0]
        return game.building_name(cls) or cls, False
    return "factory", False


def _base(item: str, region: str | None, style: str) -> str:
    if style == "mine":
        return f"{item.lower()} factory"
    if style == "product, region":
        return item + (f", {region}" if region else "")
    raise ValueError(f"unknown naming style {style!r}; known: {', '.join(STYLES)}")


def suggest(item: str, region: str | None, taken: Iterable[str], style: str = DEFAULT_STYLE) -> str:
    """``item`` worded in ``style``, numbered past any name in ``taken`` or its slug."""
    base = _base(item, region, style)
    held = list(taken)
    names = {t.strip().casefold() for t in held}
    slugs = {slugify(t) for t in held}
    name, n = base, 2
    while name.casefold() in names or slugify(name) in slugs:
        name, n = f"{base} {n}", n + 1
    return name
