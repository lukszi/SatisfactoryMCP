"""Item categories for the Items page, from the shop, the item's own data and nuclear chains."""

from __future__ import annotations

from satisfactory_mcp.core.gamedata.categories import CATEGORIES, item_categories


def _by_name(game, name):
    return next(cls for cls, item in game.items.items() if item.name == name)


def test_every_item_gets_a_known_category(game):
    cats = item_categories(game)
    assert set(cats) == set(game.items)
    assert set(cats.values()) <= set(CATEGORIES)


def test_shop_categories_and_the_rules_place_familiar_items(game):
    cats = item_categories(game)
    expect = {
        "Iron Ore": "raw",
        "Water": "fluids",
        "Iron Ingot": "ingots",
        "Concrete": "minerals",
        "Iron Plate": "standard parts",
        "Motor": "industrial parts",
        "Computer": "electronics",
        "Smart Plating": "space elevator",
        "Uranium Waste": "nuclear",
        "Encased Uranium Cell": "nuclear",
        "Power Shard": "boosts",
        "Somersloop": "boosts",
        "Blue Power Slug": "boosts",
        "Rifle Ammo": "other",
        "Nuke Nobelisk": "other",
        "Packaged Water": "other",
    }
    assert {name: cats[_by_name(game, name)] for name in expect} == expect


def test_shop_schematics_carry_their_category_and_items(game):
    shop = [s for s in game.schematics.values() if s.shop_category == "StandardParts"]
    assert shop and all(s.gives_items for s in shop)
