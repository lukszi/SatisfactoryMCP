"""How a locked recipe is actually obtained.

Every surface that prints LOCKED prints this beside it, because "you need a hard drive" and
"you need to finish a milestone" are different answers and LOCKED alone is a dead end. The
join is ``Recipe.unlocked_by``, which ``normalize`` fills from every schematic's
``mUnlocksRecipes``.
"""

from __future__ import annotations

from .model import GameData, Recipe

__all__ = ["SOURCE_OF_TYPE", "granted_by", "granted_by_label"]

#: Schematic type -> the currency the player pays in. The player-facing distinction is the
#: whole point: a hard drive and a milestone are completely different work.
SOURCE_OF_TYPE = {
    "EST_Alternate": "hard drive",
    "EST_MAM": "MAM research",
    "EST_Milestone": "milestone",
    "EST_Tutorial": "milestone",
    "EST_ResourceSink": "AWESOME shop",
    "EST_HardDrive": "hard drive",
}

#: Docs hash -> child schematic -> the schematics that chain to it. Built once per dump.
_PARENTS: dict[str, dict[str, tuple[str, ...]]] = {}


def _parents(game: GameData) -> dict[str, tuple[str, ...]]:
    cached = _PARENTS.get(game.docs_sha256)
    if cached is None:
        collected: dict[str, list[str]] = {}
        for schematic in game.schematics.values():
            for child in schematic.unlocks_schematics:
                collected.setdefault(child, []).append(schematic.cls)
        cached = _PARENTS[game.docs_sha256] = {k: tuple(v) for k, v in collected.items()}
    return cached


def _sources(game: GameData, schematic_id: str, recipe_name: str = "") -> list[str]:
    """What buying ``schematic_id`` costs the player, as ``source: name``.

    The 73 ``EST_Custom`` schematics are sub-schematics chained off a real purchase and are
    named after what they grant, so quoting one answers nothing -- Distilled Silica is
    granted by "Alternate: Distilled Silica", which the player buys as the hard drive
    "Alternate: Quartz Purification". One level of parent is enough and is all that is
    followed: no unlabelled schematic in the dump has an unlabelled parent.
    """
    schematic = game.schematics.get(schematic_id)
    if schematic is None:
        return []
    kind = SOURCE_OF_TYPE.get(schematic.type)
    if kind:
        # Most alternates are granted by a schematic of the same name, and the row this
        # goes in already says that name.
        return [kind if schematic.name == recipe_name else f"{kind}: {schematic.name}"]
    parent_labels = [
        label
        for parent in _parents(game).get(schematic_id, ())
        if (label := _labelled(game, parent))
    ]
    return parent_labels or [schematic.name]


def _labelled(game: GameData, schematic_id: str) -> str | None:
    schematic = game.schematics.get(schematic_id)
    kind = SOURCE_OF_TYPE.get(schematic.type) if schematic else None
    return f"{kind}: {schematic.name}" if kind and schematic else None


def granted_by(game: GameData, recipe: Recipe) -> list[str]:
    """Every way to obtain ``recipe``, each as ``source: name``, in dump order.

    30 recipes have more than one -- Silica comes from both a milestone and a MAM node --
    so callers print the count rather than the first entry alone.
    """
    out: list[str] = []
    for schematic_id in recipe.unlocked_by:
        out.extend(
            source for source in _sources(game, schematic_id, recipe.name) if source not in out
        )
    return out


def granted_by_label(game: GameData, recipe: Recipe, width: int = 0) -> str:
    """The grants as one table cell: all of them, or the first and how many follow.

    ``width`` is the character budget a caller can spare; 0 spends whatever it takes. It
    collapses rather than truncates -- a cut-off schematic name is a name the reader cannot
    look up. ``no known unlock`` is an answer, not a gap.
    """
    sources = granted_by(game, recipe)
    if not sources:
        return "no known unlock"
    joined = "; ".join(sources)
    if len(sources) == 1 or not width or len(joined) <= width:
        return joined
    return f"{sources[0]} (first of {len(sources)})"
