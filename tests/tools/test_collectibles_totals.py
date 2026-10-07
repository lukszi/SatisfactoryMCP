"""``tools/collectibles/totals.py``: the per-category table when a category goes unplaced.

A game update that drops the last placement of a category (one customization pickup, three
tapes) must leave a ``placed: 0`` entry with no class path, not end the run in StopIteration.
"""

from __future__ import annotations

from types import SimpleNamespace

from tools.collectibles.catalog import CATEGORIES, MUSHROOM_CLASS
from tools.collectibles.totals import measure_per_category

BLUE_SLUG = "BP_Crystal_C"
BLUE_SLUG_PATH = "/Game/FactoryGame/Resource/Environment/Crystal/BP_Crystal.BP_Crystal_C"


def _one_blue_slug() -> SimpleNamespace:
    """The fields ``measure_per_category`` reads, holding one placement and its row."""
    placement = SimpleNamespace(
        cls=BLUE_SLUG, cell="Cell_0", instance="BP_Crystal_C_1", class_path=BLUE_SLUG_PATH
    )
    return SimpleNamespace(
        rows=[{"category": CATEGORIES[BLUE_SLUG], "state": "present"}],
        row_placements=[placement],
        keys_any_save_mentions=set(),
        flora={MUSHROOM_CLASS: {"respawns": False}},
    )


def test_a_category_with_no_placement_is_tallied_empty_rather_than_raising():
    ctx = _one_blue_slug()
    measure_per_category(ctx)

    table = ctx.per_category
    assert len(table) == 12 and set(table) == set(CATEGORIES.values())
    blue = table.pop(CATEGORIES[BLUE_SLUG])
    assert blue["placed"] == 1 and blue["present"] == 1
    assert blue["class_path"] == BLUE_SLUG_PATH
    for category, entry in table.items():
        assert entry["placed"] == 0, category
        assert entry["class_path"] is None, category
        assert entry["class"] in CATEGORIES, category
