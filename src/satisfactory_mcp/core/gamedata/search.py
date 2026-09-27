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

__all__ = ["KINDS", "Census", "Hit", "find_items", "makers_of", "search"]

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

    def line(self, subject: str) -> str:
        """The completeness claim, and the evidence for it, in one line."""
        if not self.total:
            return f"# no recipe {subject} (searched all {self.scanned} recipes)"
        parts = []
        for kind in KINDS:
            n = self.by_kind.get(kind, 0)
            if not n:
                continue
            gate = ""
            have, locked = self.have.get(kind, 0), self.locked.get(kind, 0)
            if have or locked:
                gate = f" [{have} HAVE, {locked} LOCKED]"
            parts.append(f"{n} {kind}{gate}")
        return (
            f"# {self.total} recipe(s) {subject}: "
            + ", ".join(parts)
            + f". Counted over all {self.scanned} recipes;"
            + " recipe_kind/limit change the rows, never these totals."
        )


def _qty(recipe: Recipe, item: str, side: str) -> float:
    flows = recipe.ingredients if side == "consumes" else recipe.products
    return sum((f.per_min if recipe.kind == "part" else f.amount) for f in flows if f.item == item)


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

    An unknown ``recipe_kind`` is a ValueError. It used to match nothing and report an
    empty table, which reads as "the game has no such recipe" rather than "that is not a
    word".
    """
    q = query.strip().casefold()
    census = Census(scanned=len(game.recipes))
    hits: list[Hit] = []
    wanted = None if recipe_kind in ("all", "", None) else recipe_kind
    if wanted is not None and wanted not in KINDS:
        raise ValueError(
            f"unknown recipe_kind {recipe_kind!r}. Choose from: {', '.join(KINDS)}, all"
        )

    for r in game.recipes.values():
        if q and q not in r.name.casefold():
            continue
        if only_alternates and not r.is_alternate:
            continue
        if consumes and not any(f.item == consumes for f in r.ingredients):
            continue
        if produces and not any(f.item == produces for f in r.products):
            continue
        have = None if unlocked is None else (r.cls in unlocked)
        census.add(r, have)
        if wanted is not None and r.kind != wanted:
            continue
        if r.is_event and not include_events:
            continue
        side, item = ("consumes", consumes) if consumes else ("produces", produces)
        hits.append(Hit(r, _qty(r, item, side) if item else 0.0, have))

    if consumes or produces:
        # Biggest consumer first within a kind: the question is "what eats my
        # Rubber", and the answer is ordered by how much.
        hits.sort(key=lambda h: (_KIND_RANK.get(h.recipe.kind, 9), -h.qty, h.recipe.name))
    else:
        hits.sort(key=lambda h: (not h.recipe.is_alternate, h.recipe.name))
    return hits, census


def find_items(game: GameData, query: str) -> list[Item]:
    """Items whose name contains ``query``, names that start with it first."""
    q = query.casefold()
    return sorted(
        (i for i in game.items.values() if q in i.name.casefold() and i.form != "RF_INVALID"),
        key=lambda i: (not i.name.casefold().startswith(q), i.name),
    )


def makers_of(game: GameData, item: str) -> list[Recipe]:
    """Every automatable recipe that makes ``item``, alternates first."""
    return sorted(game.producers_of(item, "part"), key=lambda r: (not r.is_alternate, r.name))
