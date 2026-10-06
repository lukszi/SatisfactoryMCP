"""Recipe search, including the reverse direction: what CONSUMES an item.

Completeness is the point, and it costs two rules. The census is counted over every recipe in
Docs.json and never over the page -- ``recipe_kind``, ``include_events``, ``limit`` and ``offset``
decide what is SHOWN and never move the header's counts -- and building recipes are counted
even where they are not shown, because the build gun consumes items exactly as a Refinery does
(Rubber has 15 part consumers, 7 building and 4 manual).

The units across kinds are not comparable. Only part recipes run in a machine, so only they
have a per-minute rate; a building recipe's ``mManufactoringDuration`` is 1.0 for all 547 of
them, which would make The HUB eat 1,200 Iron Ore/min. Building and manual rows carry the
per-craft amount and are suffixed ``/build`` and ``/craft`` so no row reads as a throughput.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import GameData, Item, Recipe

__all__ = [
    "KINDS",
    "Census",
    "Hit",
    "find_items",
    "find_recipe",
    "makers_of",
    "match_recipes",
    "resolve_item",
    "search",
]

#: Report order. Part recipes first because they are what a factory runs.
KINDS = ("part", "building", "manual")

_KIND_RANK = {kind: i for i, kind in enumerate(KINDS)}


@dataclass(frozen=True)
class Hit:
    recipe: Recipe
    #: How much of the queried item this recipe consumes (or produces), in the
    #: recipe's own unit -- per minute for part, per craft for building/manual.
    qty: float
    #: True HAVE, False LOCKED, None when no save could be read.
    unlocked: bool | None


@dataclass
class Census:
    """Counts over the WHOLE recipe table, so a truncated page is still honest."""

    scanned: int
    total: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    have: dict[str, int] = field(default_factory=dict)
    locked: dict[str, int] = field(default_factory=dict)
    #: FICSMAS recipes matching the query. Hidden by default, never uncounted.
    events: int = 0

    def add(self, recipe: Recipe, unlocked: bool | None) -> None:
        self.total += 1
        self.by_kind[recipe.kind] = self.by_kind.get(recipe.kind, 0) + 1
        if unlocked is True:
            self.have[recipe.kind] = self.have.get(recipe.kind, 0) + 1
        elif unlocked is False:
            self.locked[recipe.kind] = self.locked.get(recipe.kind, 0) + 1
        if recipe.is_event:
            self.events += 1


def _qty(recipe: Recipe, item: str, side: str) -> float:
    flows = recipe.ingredients if side == "consumes" else recipe.products
    return sum(
        (flow.per_min if recipe.kind == "part" else flow.amount)
        for flow in flows
        if flow.item == item
    )


def search(
    game: GameData,
    query: str = "",
    consumes: str | None = None,
    produces: str | None = None,
    recipe_kind: str = "part",
    only_alternates: bool = False,
    include_events: bool = False,
    unlocked: set[str] | None = None,
) -> tuple[list[Hit], Census]:
    """Every recipe matching the query, plus a census over the whole table.

    ``query``/``consumes``/``produces``/``only_alternates`` define the QUESTION and
    are counted in the census. ``recipe_kind`` and ``include_events`` only filter the
    rows that come back, so the header can promise a total the rows do not reach.

    An unknown ``recipe_kind`` is a ValueError: matching nothing would read as "the game
    has no such recipe" rather than "that is not a word".
    """
    needle = query.strip().casefold()
    census = Census(scanned=len(game.recipes))
    hits: list[Hit] = []
    wanted = None if recipe_kind in ("all", "", None) else recipe_kind
    if wanted is not None and wanted not in KINDS:
        raise ValueError(
            f"unknown recipe_kind {recipe_kind!r}. Choose from: {', '.join(KINDS)}, all"
        )

    for recipe in game.recipes.values():
        if needle and needle not in recipe.name.casefold():
            continue
        if only_alternates and not recipe.is_alternate:
            continue
        if consumes and not any(flow.item == consumes for flow in recipe.ingredients):
            continue
        if produces and not any(flow.item == produces for flow in recipe.products):
            continue
        have = None if unlocked is None else (recipe.cls in unlocked)
        census.add(recipe, have)
        if wanted is not None and recipe.kind != wanted:
            continue
        if recipe.is_event and not include_events:
            continue
        side, item = ("consumes", consumes) if consumes else ("produces", produces)
        hits.append(Hit(recipe, _qty(recipe, item, side) if item else 0.0, have))

    if consumes or produces:
        # Biggest consumer first within a kind: the question is "what eats my
        # Rubber", and the answer is ordered by how much.
        hits.sort(
            key=lambda hit: (
                _KIND_RANK.get(hit.recipe.kind, 9),
                -hit.qty,
                hit.recipe.name.casefold(),
            )
        )
    else:
        hits.sort(key=lambda hit: (not hit.recipe.is_alternate, hit.recipe.name.casefold()))
    return hits, census


def find_items(game: GameData, query: str) -> list[Item]:
    """Items whose name contains ``query``: names that start with it first, event items
    last, alphabetical regardless of case within each."""
    needle = query.casefold()
    events = game.event_items()
    return sorted(
        (
            item
            for item in game.items.values()
            if needle in item.name.casefold() and item.form != "RF_INVALID"
        ),
        key=lambda item: (
            not item.name.casefold().startswith(needle),
            item.cls in events,
            item.name.casefold(),
        ),
    )


def makers_of(game: GameData, item: str) -> list[Recipe]:
    """Every automatable recipe that makes ``item``, alternates first."""
    return sorted(
        game.producers_of(item, "part"),
        key=lambda recipe: (not recipe.is_alternate, recipe.name.casefold()),
    )


def resolve_item(game: GameData, query: str) -> str | None:
    """Resolve a display name or class id to an item id."""
    if query in game.items:
        return query
    needle = query.casefold()
    exact = [cls for cls, item in game.items.items() if item.name.casefold() == needle]
    if exact:
        return exact[0]
    partial = [cls for cls, item in game.items.items() if needle in item.name.casefold()]
    return partial[0] if partial else None


def match_recipes(game: GameData, pattern: str, pool: list[str]) -> list[str]:
    """Resolve one recipe pattern against a pool of recipe ids.

    Widening, in this order: exact class id, exact display name, then case-insensitive
    substring returning EVERY match. That is what makes "Recycled" drop both Recycled
    Plastic and Recycled Rubber in one go -- banning half a loop leaves the loop intact.
    """
    if pattern in pool:
        return [pattern]
    needle = pattern.strip().casefold()
    exact = [recipe_id for recipe_id in pool if game.recipes[recipe_id].name.casefold() == needle]
    if exact:
        return exact
    return [recipe_id for recipe_id in pool if needle in game.recipes[recipe_id].name.casefold()]


def find_recipe(game: GameData, text: str) -> tuple[Recipe | None, list[str]]:
    """One recipe by class id or display name, and every id the text matched.

    The recipe is ``None`` when the text matched nothing or more than one recipe."""
    if text in game.recipes:
        return game.recipes[text], [text]
    hits = match_recipes(game, text, list(game.recipes))
    return (game.recipes[hits[0]] if len(hits) == 1 else None), hits
