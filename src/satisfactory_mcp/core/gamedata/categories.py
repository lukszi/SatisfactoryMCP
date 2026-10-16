"""Each item's category for the Items page, worked out from the game data alone.

The Docs carry no item category; the rules are in docs/frontend_kit.md, "Items".
"""

from __future__ import annotations

from .model import GameData

__all__ = ["CATEGORIES", "item_categories"]

CATEGORIES = (
    "raw",
    "fluids",
    "ingots",
    "minerals",
    "oil products",
    "standard parts",
    "industrial parts",
    "electronics",
    "space elevator",
    "nuclear",
    "boosts",
    "other",
)

_SHOP: dict[str, str] = {
    "Minerals": "minerals",
    "OilProducts": "oil products",
    "StandardParts": "standard parts",
    "IndustrialParts": "industrial parts",
    "Electronics": "electronics",
    "Communications": "electronics",
}

#: Natives of equipment, ammunition and consumables: carried, never a factory part.
_GEAR = ("FGEquipmentDescriptor", "FGAmmoType", "FGConsumableDescriptor")

_BY_SHA: dict[str, dict[str, str]] = {}


def _shop(game: GameData) -> dict[str, str]:
    out: dict[str, str] = {}
    for schematic in game.schematics.values():
        category = _SHOP.get(schematic.shop_category)
        for item in schematic.gives_items if category else ():
            out.setdefault(item, category or "")
    return out


def _nuclear(game: GameData, settled: set[str]) -> set[str]:
    """Fuel rods and their wastes, what default recipes make out of those, and what goes
    into a fuel rod; items another rule already placed stay where they are."""
    rods = {cls for cls, item in game.items.items() if item.native.endswith("NuclearFuel")}
    found = set(rods)
    for building in game.buildings.values():
        found.update(
            f.byproduct_class for f in building.fuels if f.fuel_class in rods and f.byproduct_class
        )
    defaults = [r for r in game.part_recipes() if not r.is_alternate]
    for recipe in defaults:
        if recipe.main_product in rods:
            found.update(f.item for f in recipe.ingredients)
    found -= settled
    grew = True
    while grew:
        grew = False
        for recipe in defaults:
            if any(f.item in found for f in recipe.ingredients):
                fresh = {f.item for f in recipe.products} - found - settled
                found |= fresh
                grew = grew or bool(fresh)
    return found


def _first_pass(game: GameData, cls: str, shop: dict[str, str], boosts: set[str]) -> str:
    item = game.items[cls]
    if item.is_fluid:
        return "fluids"
    if cls in boosts:
        return "boosts"
    if item.is_resource:
        return "raw"
    if cls.startswith("Desc_SpaceElevatorPart_"):
        return "space elevator"
    if item.native.startswith(_GEAR) or item.name.startswith("Packaged "):
        return "other"
    if item.name.endswith(" Ingot"):
        return "ingots"
    return shop.get(cls, "")


def item_categories(game: GameData) -> dict[str, str]:
    """Item class -> one of :data:`CATEGORIES`, for every item in the game data."""
    cached = _BY_SHA.get(game.docs_sha256)
    if cached is not None:
        return cached
    shop = _shop(game)
    boosts = {c for c, it in game.items.items() if it.native == "FGPowerShardDescriptor"}
    boosts |= set(game.slug_yields())
    out = {cls: _first_pass(game, cls, shop, boosts) for cls in game.items}
    settled = {cls for cls, category in out.items() if category}
    for cls in _nuclear(game, settled):
        if cls in out:
            out[cls] = "nuclear"
    out = {cls: category or "other" for cls, category in out.items()}
    _BY_SHA[game.docs_sha256] = out
    return out
