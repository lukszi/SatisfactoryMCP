"""``/api/search``: the header's one box, over items, recipes of every kind and named factories.

Called per keystroke (debounced), so it reads the cached state and scans in memory only.
docs/frontend_vision.md §10. Wire rules: docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata import search as gsearch
from ..serial import machine_name, world_state

__all__ = ["router"]

router = APIRouter(prefix="/api")

SHOWN = 8


class ItemHit(TypedDict):
    cls: str
    name: str


class RecipeHit(TypedDict):
    """``unlocked`` is null when no save could be read; ``spoiler`` means locked. ``kind`` is
    part, building or manual; ``machine`` names a part recipe's machine."""

    cls: str
    name: str
    kind: str
    machine: str | None
    alternate: bool
    unlocked: bool | None
    spoiler: bool


class FactoryHit(TypedDict):
    name: str
    machines: int


class SearchResponse(TypedDict):
    """Each list holds the first few hits; the ``*_total`` fields count them all."""

    items: list[ItemHit]
    items_total: int
    recipes: list[RecipeHit]
    recipes_total: int
    factories: list[FactoryHit]
    factories_total: int
    save_note: str | None


def _prefix_match_key(name: str, query: str) -> tuple[bool, str]:
    """Sorts names that start with the query first, then alphabetically."""
    return (not name.casefold().startswith(query), name)


@router.get("/search", response_model=SearchResponse)
def search(
    request: Request,
    q: str = "",
    only_unlocked: bool = False,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """Items, recipes of every kind and named factories whose names contain ``q``.

    ``spoilers=0``, or its older alias ``only_unlocked``, drops locked recipes before the cut
    and the count, so neither the list nor ``recipes_total`` says what the save has not
    reached.
    """
    text = q.strip()
    if not text:
        return {
            "items": [],
            "items_total": 0,
            "recipes": [],
            "recipes_total": 0,
            "factories": [],
            "factories_total": 0,
            "save_note": None,
        }
    key = text.casefold()
    game = request.app.state.game()
    items = gsearch.find_items(game, text)
    have = None
    labels = []
    note = None
    try:
        st = world_state(request, save, world)
        have = st.available_recipe_ids
        labels = [label for label in st.labels.labels if key in label.name.casefold()]
    except Exception as exc:
        note = f"no save could be read ({exc}): factories are missing and unlocks are unknown"
    hits, _census = gsearch.search(game, query=text, recipe_kind="all", unlocked=have)
    if only_unlocked or spoilers is False:
        hits = [hit for hit in hits if hit.unlocked is not False]
    hits.sort(key=lambda hit: _prefix_match_key(hit.recipe.name, key))
    labels.sort(key=lambda label: _prefix_match_key(label.name, key))
    return {
        "items": [{"cls": item.cls, "name": item.name} for item in items[:SHOWN]],
        "items_total": len(items),
        "recipes": [
            {
                "cls": hit.recipe.cls,
                "name": hit.recipe.name,
                "kind": hit.recipe.kind,
                "machine": machine_name(game, hit.recipe),
                "alternate": hit.recipe.is_alternate,
                "unlocked": hit.unlocked,
                "spoiler": hit.unlocked is False,
            }
            for hit in hits[:SHOWN]
        ],
        "recipes_total": len(hits),
        "factories": [
            {"name": label.name, "machines": len(label.anchors)} for label in labels[:SHOWN]
        ],
        "factories_total": len(labels),
        "save_note": note,
    }
