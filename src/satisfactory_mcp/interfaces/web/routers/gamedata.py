"""``/api/gamedata/…``: the Recipes codex -- items, recipes, one recipe, an item's makers, and
what this save has unlocked. docs/frontend_vision.md §10. Wire rules: docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata import search
from ....core.gamedata.model import GameData, Recipe
from ....core.gamedata.unlocks import granted_by
from ....domain.planning.scenario import find_recipe, resolve_item
from ..serial import _fail, _state

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
    item: str
    name: str
    per_min: float


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


class AlternatesResponse(TypedDict):
    item: str
    name: str
    fluid: bool
    energy_mj: float
    sink_points: int
    save_note: str | None
    recipes: list[MakerRow]


class UnlockedRow(TypedDict):
    cls: str
    name: str
    machine: str | None


class UnlockedResponse(TypedDict):
    age_note: str
    alternates_unlocked: int
    alternates_total: int
    automatable_total: int
    recipes: list[UnlockedRow]


def _have(
    request: Request, save: str | None, world: str | None
) -> tuple[set[str] | None, str | None]:
    try:
        return _state(request, save, world).available_recipe_ids, None
    except Exception as exc:
        return None, f"no save could be read ({exc}), so HAVE/LOCKED is unknown"


def _machine(g: GameData, r: Recipe) -> str | None:
    b = g.machine(r)
    return b.name if b else None


def _rates(g: GameData, flows) -> list[Rate]:
    return [
        {"item": f.item, "name": g.item_name(f.item), "per_min": float(f.per_min)} for f in flows
    ]


def _item_row(i) -> ItemRow:
    return {
        "cls": i.cls,
        "name": i.name,
        "fluid": i.is_fluid,
        "energy_mj": float(i.energy_mj),
        "sink_points": int(i.sink_points),
    }


@router.get("/gamedata/items", response_model=ItemsResponse)
def gamedata_items(request: Request, q: str = "", limit: int = ITEMS_MAX) -> Any:
    """Items whose name contains ``q``, the ``search_items`` order. Needs no save."""
    hits = search.find_items(request.app.state.game(), q.strip())
    return {
        "total": len(hits),
        "items": [_item_row(i) for i in hits[: max(0, min(limit, ITEMS_MAX))]],
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
) -> Any:
    """``search_recipes``: by name, or by what a recipe eats or makes, marked HAVE or LOCKED."""
    g = request.app.state.game()
    ids = {}
    for key, text in (("consumes", consumes), ("produces", produces)):
        if text:
            ids[key] = resolve_item(g, text)
            if ids[key] is None:
                return _fail(f"no item matching {text!r}", 404)
    have, note = _have(request, save, world)
    try:
        hits, census = search.search(
            g,
            query=q,
            consumes=ids.get("consumes"),
            produces=ids.get("produces"),
            recipe_kind=recipe_kind,
            only_alternates=only_alternates,
            include_events=include_events,
            unlocked=have,
        )
    except ValueError as exc:
        return _fail(str(exc))
    return {
        "census": {
            "total": census.total,
            "by_kind": census.by_kind,
            "have": census.have,
            "locked": census.locked,
            "events": census.events,
        },
        "save_note": note,
        "recipes": [
            {
                "cls": h.recipe.cls,
                "name": h.recipe.name,
                "kind": h.recipe.kind,
                "alternate": h.recipe.is_alternate,
                "machine": _machine(g, h.recipe),
                "qty": float(h.qty),
                "unlocked": h.unlocked,
            }
            for h in hits
        ],
    }


@router.get("/gamedata/recipe", response_model=RecipeDetail)
def gamedata_recipe(
    request: Request, recipe: str, save: str | None = None, world: str | None = None
) -> Any:
    """``recipe_detail``: one recipe by class id or display name."""
    g = request.app.state.game()
    r, hits = find_recipe(g, recipe)
    if r is None and hits:
        shown = ", ".join(g.recipes[h].name for h in hits[:8])
        return _fail(f"{recipe!r} matches {len(hits)} recipes: {shown}", 409)
    if r is None:
        return _fail(f"unknown recipe {recipe!r}", 404)
    have, note = _have(request, save, world)
    return {
        "cls": r.cls,
        "name": r.name,
        "kind": r.kind,
        "alternate": r.is_alternate,
        "machine": _machine(g, r),
        "duration_s": float(r.duration_s),
        "power_mw": float(g.recipe_power_mw(r)),
        "power_range_mw": (
            (float(r.power_min_mw), float(r.power_max_mw)) if r.is_variable_power else None
        ),
        "ingredients": _rates(g, r.ingredients),
        "products": _rates(g, r.products),
        "granted_by": granted_by(g, r),
        "unlocked": None if have is None else r.cls in have,
        "save_note": note,
    }


@router.get("/gamedata/alternates", response_model=AlternatesResponse)
def gamedata_alternates(
    request: Request, item: str, save: str | None = None, world: str | None = None
) -> Any:
    """``alternates_for_item``: every automatable recipe that makes an item, alternates first."""
    g = request.app.state.game()
    iid = resolve_item(g, item)
    if iid is None:
        return _fail(f"no item matching {item!r}", 404)
    have, note = _have(request, save, world)
    rows = []
    for r in search.makers_of(g, iid):
        unlocked = None if have is None else r.cls in have
        rows.append(
            {
                "cls": r.cls,
                "name": r.name,
                "alternate": r.is_alternate,
                "machine": _machine(g, r),
                "power_mw": float(g.recipe_power_mw(r)),
                "ingredients": _rates(g, r.ingredients),
                "products": _rates(g, r.products),
                "unlocked": unlocked,
                "granted_by": [] if unlocked else granted_by(g, r),
            }
        )
    return {**_item_row(g.items[iid]), "item": iid, "save_note": note, "recipes": rows}


@router.get("/gamedata/unlocked", response_model=UnlockedResponse)
def gamedata_unlocked(
    request: Request,
    only_alternates: bool = True,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """``unlocked_recipes``: the recipes this save has, alternates only by default."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    picks = st.unlocked_alternates if only_alternates else st.unlocked_recipes("part")
    return {
        "age_note": st.age_note,
        "alternates_unlocked": len(st.unlocked_alternates),
        "alternates_total": len(st.game.alternates()),
        "automatable_total": len(st.unlocked_recipes("part")),
        "recipes": [
            {"cls": r.cls, "name": r.name, "machine": _machine(st.game, r)}
            for r in sorted(picks, key=lambda r: r.name)
        ],
    }
