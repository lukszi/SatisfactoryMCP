"""``/api/gamedata/…``: the Recipes codex -- items, recipes, one recipe, an item's makers, and
what this save has unlocked. docs/frontend_vision.md §10. Wire rules: docs/web-wire.md.
A recipe the save has not unlocked is a spoiler (``spoiler``, ``?spoilers=``): §12.3 there.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.gamedata import search
from .....core.gamedata.model import GameData, Recipe
from .....core.gamedata.search import find_recipe, resolve_item
from .....core.gamedata.unlocks import granted_by
from .....core.text import ago
from ...serial import error_response, machine_name, require_world, world_state

__all__ = ["router"]

router = APIRouter(prefix="/api")

ITEMS_MAX = 200


class ItemRow(TypedDict):
    cls: str
    name: str
    fluid: bool
    energy_mj: float
    sink_points: int


class ItemsResponse(TypedDict):
    total: int
    items: list[ItemRow]


class RecipeRow(TypedDict):
    """``unlocked`` is null when no save could be read; ``qty`` is the amount of the queried
    item per minute (part) or per craft (building, manual), 0 with no item asked."""

    cls: str
    name: str
    kind: str
    alternate: bool
    machine: str | None
    qty: float
    unlocked: bool | None
    spoiler: bool


class Census(TypedDict):
    total: int
    by_kind: dict[str, int]
    have: dict[str, int]
    locked: dict[str, int]
    events: int


class RecipesResponse(TypedDict):
    """``save_note`` says why ``unlocked`` is null on every row, and is null otherwise."""

    census: Census
    save_note: str | None
    recipes: list[RecipeRow]


class Rate(TypedDict):
    """``amount`` is per cycle: per craft or per build for a manual or building recipe,
    whose ``per_min`` rests on a placeholder cycle and means nothing."""

    item: str
    name: str
    per_min: float
    amount: float


class RecipeDetail(TypedDict):
    cls: str
    name: str
    kind: str
    alternate: bool
    machine: str | None
    duration_s: float
    power_mw: float
    power_range_mw: tuple[float, float] | None
    ingredients: list[Rate]
    products: list[Rate]
    granted_by: list[str]
    unlocked: bool | None
    spoiler: bool
    save_note: str | None


class MakerRow(TypedDict):
    cls: str
    name: str
    alternate: bool
    machine: str | None
    power_mw: float
    ingredients: list[Rate]
    products: list[Rate]
    unlocked: bool | None
    granted_by: list[str]
    spoiler: bool


class AlternatesResponse(TypedDict):
    """``build_recipe`` is the build-gun recipe when the item is a building, else null."""

    item: str
    name: str
    fluid: bool
    energy_mj: float
    sink_points: int
    save_note: str | None
    build_recipe: str | None
    recipes: list[MakerRow]


class UnlockedRow(TypedDict):
    cls: str
    name: str
    machine: str | None


class UnlockedResponse(TypedDict):
    """``save_kind`` is "autosave" or "manual save"; ``written_ago`` is null with no mtime."""

    age_note: str
    save_kind: str
    written_ago: str | None
    alternates_unlocked: int
    alternates_total: int
    automatable_total: int
    recipes: list[UnlockedRow]


def _unlocked_ids_or_note(
    request: Request, save: str | None, world: str | None
) -> tuple[set[str] | None, str | None]:
    """The save's unlocked recipe ids, or ``None`` and the note that says why not."""
    try:
        return world_state(request, save, world).available_recipe_ids, None
    except Exception as exc:
        return None, f"no save could be read ({exc}), so have and locked are unknown"


def _rates(game: GameData, flows) -> list[Rate]:
    return [
        {
            "item": flow.item,
            "name": game.item_name(flow.item),
            "per_min": float(flow.per_min),
            "amount": float(flow.amount),
        }
        for flow in flows
    ]


def _census(census: search.Census) -> Census:
    return {
        "total": census.total,
        "by_kind": census.by_kind,
        "have": census.have,
        "locked": census.locked,
        "events": census.events,
    }


def _census_without_locked(game: GameData, have: set[str] | None, **question: Any) -> Census:
    """The census of a search over every kind, counting only what is not known to be locked."""
    every, _ = search.search(
        game, recipe_kind="all", include_events=True, unlocked=have, **question
    )
    tally = search.Census(scanned=len(game.recipes))
    for hit in every:
        if hit.unlocked is not False:
            tally.add(hit.recipe, hit.unlocked)
    return _census(tally)


def _item_row(item) -> ItemRow:
    return {
        "cls": item.cls,
        "name": item.name,
        "fluid": item.is_fluid,
        "energy_mj": float(item.energy_mj),
        "sink_points": int(item.sink_points),
    }


def _maker_row(game: GameData, recipe: Recipe, unlocked: bool | None) -> MakerRow:
    return {
        "cls": recipe.cls,
        "name": recipe.name,
        "alternate": recipe.is_alternate,
        "machine": machine_name(game, recipe),
        "power_mw": float(game.recipe_power_mw(recipe)),
        "ingredients": _rates(game, recipe.ingredients),
        "products": _rates(game, recipe.products),
        "unlocked": unlocked,
        "granted_by": granted_by(game, recipe),
        "spoiler": unlocked is False,
    }


@router.get("/gamedata/items", response_model=ItemsResponse)
def gamedata_items(request: Request, q: str = "", limit: int = ITEMS_MAX) -> Any:
    """Items whose name contains ``q``, the ``search_items`` order. Needs no save."""
    hits = search.find_items(request.app.state.game(), q.strip())
    return {
        "total": len(hits),
        "items": [_item_row(item) for item in hits[: max(0, min(limit, ITEMS_MAX))]],
    }


@router.get("/gamedata/recipes", response_model=RecipesResponse)
def gamedata_recipes(
    request: Request,
    q: str = "",
    consumes: str | None = None,
    produces: str | None = None,
    recipe_kind: str = "part",
    only_alternates: bool = False,
    include_events: bool = False,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """``search_recipes``: by name, or by what a recipe eats or makes, marked HAVE or LOCKED.

    With ``spoilers=0`` locked recipes leave the rows and the census alike.
    """
    game = request.app.state.game()
    ids = {}
    for key, text in (("consumes", consumes), ("produces", produces)):
        if text:
            ids[key] = resolve_item(game, text)
            if ids[key] is None:
                return error_response(f"no item matching “{text}”", 404)
    have, note = _unlocked_ids_or_note(request, save, world)
    question = {
        "query": q,
        "consumes": ids.get("consumes"),
        "produces": ids.get("produces"),
        "only_alternates": only_alternates,
    }
    try:
        hits, census = search.search(
            game,
            recipe_kind=recipe_kind,
            include_events=include_events,
            unlocked=have,
            **question,
        )
    except ValueError as exc:
        return error_response(str(exc))
    counted = _census(census)
    if spoilers is False:
        hits = [hit for hit in hits if hit.unlocked is not False]
        counted = _census_without_locked(game, have, **question)
    return {
        "census": counted,
        "save_note": note,
        "recipes": [
            {
                "cls": hit.recipe.cls,
                "name": hit.recipe.name,
                "kind": hit.recipe.kind,
                "alternate": hit.recipe.is_alternate,
                "machine": machine_name(game, hit.recipe),
                "qty": float(hit.qty),
                "unlocked": hit.unlocked,
                "spoiler": hit.unlocked is False,
            }
            for hit in hits
        ],
    }


@router.get("/gamedata/recipe", response_model=RecipeDetail)
def gamedata_recipe(
    request: Request,
    recipe: str,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """``recipe_detail``: one recipe by class id or display name.

    With ``spoilers=0`` an ambiguous name counts only the unlocked candidates.
    """
    game = request.app.state.game()
    found, hits = find_recipe(game, recipe)
    have, note = _unlocked_ids_or_note(request, save, world)
    if found is None and spoilers is False and have is not None:
        hits = [hit for hit in hits if hit in have]
    if found is None and hits:
        return error_response(f"“{recipe}” matches {len(hits)} recipes", 409)
    if found is None:
        return error_response(f"no recipe named “{recipe}”", 404)
    unlocked = None if have is None else found.cls in have
    return {
        "cls": found.cls,
        "name": found.name,
        "kind": found.kind,
        "alternate": found.is_alternate,
        "machine": machine_name(game, found),
        "duration_s": float(found.duration_s),
        "power_mw": float(game.recipe_power_mw(found)),
        "power_range_mw": (
            (float(found.power_min_mw), float(found.power_max_mw))
            if found.is_variable_power
            else None
        ),
        "ingredients": _rates(game, found.ingredients),
        "products": _rates(game, found.products),
        "granted_by": granted_by(game, found),
        "unlocked": unlocked,
        "spoiler": unlocked is False,
        "save_note": note,
    }


@router.get("/gamedata/alternates", response_model=AlternatesResponse)
def gamedata_alternates(
    request: Request,
    item: str,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """``alternates_for_item``: every automatable recipe that makes an item, alternates first."""
    game = request.app.state.game()
    item_id = resolve_item(game, item)
    if item_id is None:
        return error_response(f"no item named “{item}”", 404)
    have, note = _unlocked_ids_or_note(request, save, world)
    rows = []
    for maker in search.makers_of(game, item_id):
        unlocked = None if have is None else maker.cls in have
        if spoilers is False and unlocked is False:
            continue
        rows.append(_maker_row(game, maker, unlocked))
    built = next(
        (
            candidate.cls
            for candidate in game.recipes.values()
            if candidate.kind == "building" and any(f.item == item_id for f in candidate.products)
        ),
        None,
    )
    return {
        **_item_row(game.items[item_id]),
        "item": item_id,
        "save_note": note,
        "build_recipe": built,
        "recipes": rows,
    }


@router.get("/gamedata/unlocked", response_model=UnlockedResponse)
def gamedata_unlocked(
    request: Request,
    only_alternates: bool = True,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """``unlocked_recipes``: the recipes this save has, alternates only by default."""
    st = require_world(request, save, world)
    picks = st.unlocked_alternates if only_alternates else st.unlocked_recipes("part")
    return {
        "age_note": st.age_note,
        "save_kind": st.identity.save_kind,
        "written_ago": ago(st.header.get("mtime_ns")),
        "alternates_unlocked": len(st.unlocked_alternates),
        "alternates_total": len(st.game.alternates()),
        "automatable_total": len(st.unlocked_recipes("part")),
        "recipes": [
            {"cls": pick.cls, "name": pick.name, "machine": machine_name(st.game, pick)}
            for pick in sorted(picks, key=lambda r: r.name)
        ],
    }
