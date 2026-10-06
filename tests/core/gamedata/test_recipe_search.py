"""search_recipes: the reverse lookup, and the completeness it has to be able to claim.

Every test here pins the specific way "what consumes Rubber" was got wrong before:
it was answered by guessing candidate items from memory and checking them one at a
time, which is eight speculative calls and still cannot prove the list is closed.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.core.gamedata import search

pytestmark = pytest.mark.integration

RUBBER = "Desc_Rubber_C"
PLASTIC = "Desc_Plastic_C"


def _consumers(game, item, kind):
    """A recount straight off the recipe table, independent of search()."""
    return {r.cls for r in game.consumers_of(item, kind)}


# ------------------------------------------------- the completeness claim


def test_every_consumer_of_rubber_is_found_in_one_call(game):
    """THE counter-example. Twenty-six recipes consume Rubber and the old
    name-matching search could reach none of them; the list was assembled from
    memory instead. Recounted here straight off the recipe table so the test is
    not just search() agreeing with itself."""
    hits, census = search.search(game, consumes=RUBBER, recipe_kind="all")
    found = {h.recipe.cls for h in hits}
    expected = set().union(*(_consumers(game, RUBBER, k) for k in search.KINDS))

    assert found == expected
    assert census.total == len(expected) == 26
    assert census.by_kind == {"part": 15, "building": 7, "manual": 4}


def test_a_building_recipe_that_eats_rubber_is_counted_even_when_hidden(game):
    """The exact worry: a late-tier BUILDING eating Rubber is invisible to a
    part-only view, and silence there reads as "nothing else". Seven building
    recipes consume Rubber -- the Fuel-Powered Generator at 50 per build, the
    Resource Well Pressurizer at 100 -- so the default view must still count them
    and say so."""
    hits, census = search.search(game, consumes=RUBBER, recipe_kind="part")

    assert {h.recipe.kind for h in hits} == {"part"}, "rows are filtered"
    assert census.by_kind["building"] == 7, "counts are not"
    assert census.by_kind["manual"] == 4

    out = srv.search_recipes(consumes="Rubber")
    assert "7 building" in out
    assert "recipe_kind='building'" in out, "the escape hatch has to be named, not implied"


def test_the_census_never_moves_with_the_page(game):
    """A truncated page that also truncated the total would make the header a lie,
    which is worse than no header: the reader stops looking."""
    first = srv.search_recipes(consumes="Rubber", recipe_kind="all", limit=1)
    full = srv.search_recipes(consumes="Rubber", recipe_kind="all", limit=25)
    assert first.splitlines()[0] == full.splitlines()[0]
    assert "26 recipe(s) consume Rubber" in first


def test_the_unlock_gate_is_reported_per_kind(game):
    """alternates_for_item's HAVE/LOCKED split was the praised part of it, and it
    matters more here: on the reference save 6 of the 7 building recipes that eat
    Rubber are unlocked but only 5 of the 15 part recipes are."""
    hits, census = search.search(
        game, consumes=RUBBER, recipe_kind="all", unlocked={"Recipe_GeneratorFuel_C"}
    )
    assert census.have == {"building": 1}
    assert census.locked["part"] == 15
    generator = next(h for h in hits if h.recipe.cls == "Recipe_GeneratorFuel_C")
    assert generator.unlocked is True


# ------------------------------------------------------------ the units


def test_a_build_cost_is_never_rendered_as_a_rate(game):
    """mManufactoringDuration is 1.0 on all 547 building recipes, so per_min turns
    the Fuel-Powered Generator's 50 Rubber into 3000/min and The HUB's 20 Iron Ore
    into 1200/min. Both are nonsense: a build cost is paid once."""
    hits, _ = search.search(game, consumes=RUBBER, recipe_kind="building")
    generator = next(h for h in hits if h.recipe.cls == "Recipe_GeneratorFuel_C")
    assert generator.qty == 50.0

    out = srv.search_recipes(consumes="Rubber", recipe_kind="building")
    assert "50/build" in out
    assert "3000" not in out
    assert "NOT rates" in out


def test_a_part_recipe_still_reports_a_rate(game):
    hits, _ = search.search(game, consumes=RUBBER, recipe_kind="part")
    recycled = next(h for h in hits if h.recipe.cls == "Recipe_Alternate_Plastic_1_C")
    assert recycled.qty == pytest.approx(30.0), "30 Rubber/min at 100% clock"
    assert "30/min" in srv.search_recipes(consumes="Rubber")


def test_consumers_are_ordered_by_how_much_they_eat(game):
    """The question is "what is eating my Rubber", so the answer is ordered by
    appetite within a kind rather than alphabetically."""
    hits, _ = search.search(game, consumes=RUBBER, recipe_kind="part")
    quantities = [h.qty for h in hits]
    assert quantities == sorted(quantities, reverse=True)


# ------------------------------------------------------- the other filters


def test_event_recipes_are_counted_but_not_shown(game):
    """FICSMAS recipes are real recipes that really consume things. Hiding them is
    right; letting them vanish from the count is how a total becomes untrustworthy."""
    hits, census = search.search(game, consumes="Desc_Gift_C", recipe_kind="all")
    assert census.events == 12
    assert hits == [], "every consumer of a FICSMAS Gift is an event recipe"
    assert "12 FICSMAS" in srv.search_recipes(consumes="FICSMAS Gift", recipe_kind="all")

    shown, _ = search.search(game, consumes="Desc_Gift_C", recipe_kind="all", include_events=True)
    assert len(shown) == 12


def test_produces_covers_the_kinds_alternates_for_item_cannot(game):
    """alternates_for_item is part-only by construction. The same reverse index run
    the other way is what answers "which build-gun recipe makes a Blender"."""
    hits, census = search.search(game, produces="Desc_Plastic_C", recipe_kind="part")
    assert {h.recipe.cls for h in hits} == {r.cls for r in game.producers_of(PLASTIC, "part")}
    assert census.by_kind.get("building", 0) == 0, "Plastic is not a building descriptor"

    blender, _ = search.search(game, produces="Desc_Blender_C", recipe_kind="building")
    assert [h.recipe.cls for h in blender] == ["Recipe_Blender_C"]


def test_a_name_search_still_matches_names_only(game):
    """The old behaviour is the default and must not have quietly become a
    substring match over ingredients."""
    hits, census = search.search(game, query="turbo")
    assert all("turbo" in h.recipe.name.casefold() for h in hits)
    assert census.total == 11
    assert "Recipe_MotorTurbo_C" in {h.recipe.cls for h in hits}


def test_query_and_consumes_are_anded(game):
    hits, _ = search.search(game, query="cable", consumes=RUBBER, recipe_kind="part")
    assert {h.recipe.cls for h in hits} == {
        "Recipe_Alternate_Cable_1_C",
        "Recipe_Alternate_Cable_2_C",
    }


def test_an_unknown_item_is_refused_not_silently_empty(game):
    """An empty result for a typo reads as "nothing consumes this", which is the
    one answer this tool must never give by accident."""
    assert srv.search_recipes(consumes="Rubbr Concrte").startswith("no item matching")


def test_both_gated_tools_answer_about_the_world_they_were_asked_about(game, monkeypatch):
    """HAVE/LOCKED is world state, and both tools took `save` and not `world`.

    On a one-world install the two arguments pick the same file and the omission is
    invisible; on a multi-world install `world=` was accepted nowhere and the answer came
    silently from the default world -- a confident unlock table about somebody else's
    factory. `list_buildings`, in the same module and with the same column, has always
    taken it.
    """
    from satisfactory_mcp.interfaces.mcp.tools import gamedata

    seen = []

    def spy(save=None, world=None, as_of=None):
        seen.append((save, world))
        raise RuntimeError("no such world")

    monkeypatch.setattr(gamedata, "_state", spy)
    gamedata.search_recipes(consumes="Rubber", world="Other Save")
    gamedata.alternates_for_item(item="Plastic", world="Other Save")
    assert seen == [(None, "Other Save"), (None, "Other Save")]


def test_the_response_fits_the_context_budget(game):
    for kwargs in ({"consumes": "Rubber"}, {"consumes": "Rubber", "kind": "all", "limit": 25}):
        out = srv.search_recipes(**kwargs)
        assert len(out) < 5000, f"{kwargs} returned {len(out)} chars"


# ------------------------------------------------- what grants a locked recipe


def test_a_locked_row_says_which_schematic_would_grant_it(game):
    """LOCKED on its own is a dead end: it tells a player the recipe exists and gives
    them nowhere to go. "hard drive" and "milestone: Particle Enrichment" are completely
    different work, and which one it is decides the next hour of play."""
    out = srv.search_recipes(produces="Nuclear Pasta")
    row = next(r for r in out.splitlines() if r.startswith("Nuclear Pasta\t"))
    assert "granted by" in out
    assert row.endswith("milestone: Particle Enrichment")


def test_the_column_appears_only_where_something_is_locked(game):
    """A column of blanks costs every reader of every fully-unlocked page. Aluminum
    Ingot's two recipes are both HAVE on this world."""
    assert "granted by" not in srv.search_recipes(produces="Aluminum Ingot")


def test_a_hard_drive_alternate_does_not_repeat_its_own_name(game):
    """The schematic that grants an alternate is usually named after it, and the row
    already prints that name in its first column. Saying "hard drive: Alternate: Cheap
    Silica" beside "Alternate: Cheap Silica" spends 25 characters on nothing."""
    out = srv.alternates_for_item("Silica")
    cheap = next(r for r in out.splitlines() if r.startswith("Alternate: Cheap Silica\t"))
    assert cheap.endswith("\thard drive")


def test_a_chained_sub_schematic_names_the_purchase_a_player_can_actually_make(game):
    """Distilled Silica is granted by a schematic called "Alternate: Distilled Silica",
    which is not a thing anyone can buy: it is chained off the hard drive "Alternate:
    Quartz Purification". Printing the sub-schematic answers with the question."""
    from satisfactory_mcp.core.gamedata.unlocks import granted_by

    r = game.recipes["Recipe_Alternate_Silica_Distilled_C"]
    assert r.unlocked_by == ("Schematic_Alternate_Silica_Distilled_C",)
    assert granted_by(game, r) == ["hard drive: Alternate: Quartz Purification"]


def test_more_than_one_grant_is_counted_rather_than_silently_reduced(game):
    """30 recipes have two or more sources -- Silica comes from a MAM node AND a
    milestone -- and quoting the first as if it were the only one tells the player to do
    work they may not need. The cell says how many it is standing in for."""
    from satisfactory_mcp.core.gamedata.unlocks import granted_by, granted_by_label

    silica = game.recipes["Recipe_Silica_C"]
    sources = granted_by(game, silica)
    # The MAM node is itself called "Silica", so it names the currency and stops there.
    assert sources == ["MAM research", "milestone: Bauxite Refinement"]
    assert granted_by_label(game, silica) == "; ".join(sources)
    assert granted_by_label(game, silica, width=20) == "MAM research (first of 2)"
