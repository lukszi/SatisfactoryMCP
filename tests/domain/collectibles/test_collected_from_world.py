"""What has been collected off the map, what is left of it, and what is closest.

Two sources, and the whole design is that neither is asked the other's question.

* **The map** says what exists. Every collectible is placed by the cooked level packages
  and never written into a save, so ``placed`` is the map's own count of its own
  placements -- exact, and unavailable from a save at any price.
* **The save** says what is gone. A save never mentions a slug still lying there; it
  records the negative, which actors have been removed. So the destroyed list *is* the
  collected list, also exact.

``remaining`` is the subtraction. What makes it honest rather than merely arithmetic is the
third state: a placement in a world-partition cell no save has ever streamed in. Nothing
collected it, so it is remaining -- and nothing has ever looked at it, so it must never be
reported as standing there. That distinction is pinned in
``test_a_placement_no_save_has_ever_loaded_is_remaining_but_never_present``.

**Why the join is by ``(cell, instance name)``.** A destroyed entry carries no class at all,
and a name does not imply one: the map's actors kept the names of the actors they were copied
from, so 98 placements the map calls ``BP_Crystal_mk2_C`` are named ``BP_Crystal_C_<n>`` --
spelling a class outright, and the wrong one. The name-prefix rule this census used to use is
still in the tree as the no-table fallback, and
``test_the_name_rule_this_replaced_calls_40_yellow_slugs_blue`` scores it against the map's
answer for the very rows it was reporting on.

Numbers come from the committed projection (the reference save: 889 destroyed records in 284
cells) joined against ``data/world_collectibles.json``. That file is untracked, so every test
needing it goes through the ``table`` fixture and skips without it -- and the degraded path it
skips to is itself tested.
"""

from __future__ import annotations

import collections
import copy

import pytest

from satisfactory_mcp.domain.collectibles import service
from satisfactory_mcp.domain.collectibles.service import GENERATOR_COMMAND
from satisfactory_mcp.domain.collectibles.table import CollectiblesUnreadable
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.world import state as state_mod
from satisfactory_mcp.domain.world.state import WorldState, _name_stem, load_collectibles
from satisfactory_mcp.interfaces.mcp.tools import progression
from satisfactory_mcp.interfaces.mcp.tools.progression import collected_from_world

pytestmark = pytest.mark.integration

#: What the reference save has collected, per the MAP's answer for each destroyed record.
#: Every figure here was a different one before the join existed: the name-prefix rule read
#: 163 blue / 50 yellow / 37 purple, filed 65 artifacts as undecidable, called 96 mushrooms
#: "flora" and 170 map loot caches "dropped_pickup".
COLLECTED = {
    "loot_cache": 170,
    "power_slug_blue": 119,
    "mushroom": 96,
    "power_slug_yellow": 87,
    "mercer_shrine": 80,
    "mercer_sphere": 80,
    "power_slug_purple": 44,
    "crashed_drop_pod": 19,
    "somersloop": 18,
}

#: The map's own placement count per category. Not observations: these come from the cooked
#: packages and do not move when the player explores.
PLACED = {
    "mushroom": 1615,
    "loot_cache": 703,
    "power_slug_blue": 596,
    "power_slug_yellow": 389,
    "mercer_shrine": 298,
    "mercer_sphere": 298,
    "power_slug_purple": 257,
    "crashed_drop_pod": 118,
    "somersloop": 106,
    "somersloop_shrine": 62,
    "tape_pickup": 3,
    "customization_unlock_pickup": 1,
}

#: What the name-prefix fallback reports, kept so the two can be compared in a test rather
#: than described in a comment. It is what this tool used to ship.
BY_NAME = {
    "flora": 185,
    "dropped_pickup": 170,
    "slug_blue": 163,
    "mercer_shrine": 80,
    "artifact_unsplit": 65,
    "crash_site": 55,
    "debris": 51,
    "slug_yellow": 50,
    "slug_purple": 37,
    "mercer_sphere": 27,
    "somersloop": 6,
}

TOTAL_DESTROYED = 889
RESOLVED = 713
UNRESOLVED = 176


@pytest.fixture(scope="session")
def table():
    """The map's placement table. Untracked, so absent is a normal condition."""
    loaded = load_collectibles()
    if loaded is None:
        pytest.skip(
            "needs data/world_collectibles.json (uv run python tools/gen_world_collectibles.py)"
        )
    return loaded


@pytest.fixture
def save_only(monkeypatch, state):
    """A state that cannot see the placement table, as a fresh clone cannot."""
    monkeypatch.setattr(state_mod, "load_collectibles", lambda: None)
    return WorldState(projection=state.projection, game=state.game)


# ------------------------------------------------------- the map resolves the classes


def test_every_collected_actor_is_resolved_by_the_map_and_the_counts_are_the_maps(state, table):
    """The census, and the arithmetic that says nothing was dropped on the way.

    ``resolved + unresolved == total`` is the guard: a destroyed record the map places
    nothing at is counted and named rather than vanishing, so "713 collected" cannot quietly
    become the answer to "889 things happened".
    """
    out = state.removed_actors()
    assert out["source"] == "map"
    assert out["total"] == TOTAL_DESTROYED
    assert out["cells"] == 284
    assert out["groups"] == dict(sorted(COLLECTED.items(), key=lambda kv: -kv[1]))
    assert out["resolved"] == RESOLVED == sum(COLLECTED.values())
    assert out["unresolved"] == UNRESOLVED
    assert out["resolved"] + out["unresolved"] == out["total"]


def test_the_name_rule_this_replaced_calls_40_yellow_slugs_blue(state, table):
    """THE test for this feature: the same rows, scored two ways.

    The name-prefix rule is still in the tree as the fallback, so its verdict for every
    destroyed record can be compared with the map's. It misfiles 51 of the 713 -- 40 yellow
    slugs and 6 purple ones read as blue -- and refuses another 65 outright. Those 51 are the
    reason a census cannot be built from names: the totals stayed plausible while the tiers
    were wrong, which is the dangerous kind of wrong.
    """
    same = {
        "slug_blue": "power_slug_blue",
        "slug_yellow": "power_slug_yellow",
        "slug_purple": "power_slug_purple",
        "somersloop": "somersloop",
        "mercer_sphere": "mercer_sphere",
        "mercer_shrine": "mercer_shrine",
        "crash_site": "crashed_drop_pod",
        "flora": "mushroom",
        "dropped_pickup": "loot_cache",
    }
    wrong: collections.Counter = collections.Counter()
    refused = 0
    for cell, name in state.destroyed_keys:
        row = table.by_key.get((cell, name))
        if row is None:
            continue
        label = state.removed_group(name)
        if label is None or label == "artifact_unsplit":
            refused += 1
        elif same[label] != row["category"]:
            wrong[(label, row["category"])] += 1

    assert wrong[("slug_blue", "power_slug_yellow")] == 40
    assert wrong[("slug_blue", "power_slug_purple")] == 6
    assert sum(wrong.values()) == 51
    assert refused == 65
    # And what the wrong answer looked like, next to the right one.
    assert BY_NAME["slug_blue"] == 163
    assert COLLECTED["power_slug_blue"] == 119


def test_artifact_unsplit_is_gone_because_the_map_splits_every_glued_name(state, table):
    """``BP_WAT1`` (somersloop) and ``BP_WAT2`` (Mercer sphere) differ in the one digit the
    game glues a placement counter onto, so ``BP_WAT112`` is undecidable from its name and
    the old census said so by refusing. The map does not have to refuse: it resolves all 98
    removed ``BP_WAT*`` names as 80 spheres and 18 somersloops, with none left over.
    """
    out = state.removed_actors()
    assert "artifact_unsplit" not in out["groups"]
    wat = [(cell, name) for cell, name in state.destroyed_keys if name.startswith("BP_WAT")]
    assert len(wat) == 98
    resolved = collections.Counter(table.by_key[k]["category"] for k in wat if k in table.by_key)
    assert resolved == {"mercer_sphere": 80, "somersloop": 18}
    # The name rule refuses 65 of those same 98 and mis-assigns one of the rest.
    assert state.removed_group("BP_WAT112_14") == "artifact_unsplit"


def test_a_class_is_never_taken_from_a_name(state, table):
    """Every group in the census is a real map category, and every actor listed under one
    carries that category's class -- read from the map, not recovered from the name. The
    somersloop group holds names like ``BP_WAT147`` that no name rule assigns."""
    for category in state.removed_actors()["groups"]:
        assert category in table.by_category
        for placement in state.removed_actors(category)["actors"]:
            assert placement["cls"] == table.cls_of(category)
    listed = {p["name"] for p in state.removed_actors("somersloop")["actors"]}
    assert "BP_WAT147" in listed


def test_the_join_needs_the_cell_as_well_as_the_name(state, table):
    """``(cell, name)`` is the key, and a name-only fallback is what this rules out.

    A bare instance name is not identity -- auto-numbered placements reuse names across
    cells -- so the same name in the wrong cell must resolve to nothing rather than to the
    row that happens to carry it.
    """
    sloop = table.by_category["somersloop"][0]
    name = sloop["instance"].rsplit(".", 1)[-1]
    projection = copy.deepcopy(state.projection)
    projection["removed"]["cells"].append("NOT_A_REAL_CELL_00000000")
    projection["removed"]["instances"].append([len(projection["removed"]["cells"]) - 1, name])
    out = WorldState(projection=projection, game=state.game).removed_actors()
    assert out["total"] == TOTAL_DESTROYED + 1
    assert out["resolved"] == RESOLVED, "the name alone must not resolve it"
    assert out["unresolved"] == UNRESOLVED + 1


def test_a_destroyed_record_the_map_places_nothing_at_is_named_not_dropped(state, table):
    """The tolerance that keeps this honest. 176 of the 889 records are classes the table
    excludes on purpose -- crash-site scenery, regrowing bushes -- or actors the map never
    placed at all, which is what a pickup the player dropped is. They are reported by name
    stem with the table's own reason, so "collected 713" is never confused with "889 things
    are accounted for"."""
    out = state.removed_actors()
    stems = out["unresolved_stems"]
    assert sum(stems.values()) == UNRESOLVED
    assert stems["BP_SporeFlower"] == 79
    assert stems["BP_BerryBush"] == 9
    assert "regrows" in table.excluded_reason("BP_BerryBush")
    assert "scenery" in table.excluded_reason("BP_Ship")


def test_a_class_nobody_anticipated_lands_in_the_unresolved_bucket(state, table):
    """A collectible the game adds must show up as a number under its own name rather than
    shrinking the census, so the next person can regenerate the table."""
    projection = copy.deepcopy(state.projection)
    projection["removed"]["instances"] += [[0, "BP_SomethingNew_7"]] * 3
    out = WorldState(projection=projection, game=state.game).removed_actors()
    assert out["unresolved_stems"]["BP_SomethingNew"] == 3
    assert out["total"] == TOTAL_DESTROYED + 3
    assert out["resolved"] == RESOLVED
    assert table.excluded_reason("BP_SomethingNew") is None


def test_a_glued_counter_is_stripped_only_after_a_letter(table):
    """The stem is a LABEL and never a decision, but a label with the counter still on it
    turns 79 spore flowers into 40 one-row entries. Digits after a letter are the glued
    counter; digits after an underscore are part of the class name and must survive --
    ``BP_DebrisActor_02`` is a real class and ``BP_DebrisActor_0`` is not."""
    assert _name_stem("BP_SporeFlower369") == "BP_SporeFlower"
    assert _name_stem("BP_DebrisActor_02_C_5") == "BP_DebrisActor_02"
    assert _name_stem("BP_Crystal_C_UAID_04421A9713F0395B01_1557158296") == "BP_Crystal"
    # Ambiguous stems are named as such rather than resolved to one of their siblings.
    reason = table.excluded_reason("BP_DebrisActor")
    assert "BP_DebrisActor_01_C" in reason and "a name does not say which" in reason


# --------------------------------------------------------------- placed, and what is left


def test_remaining_is_placed_minus_collected_and_the_parts_add_up(state, table):
    """The invariant the whole census rests on. ``placed`` is the map's, ``collected`` is
    the save's, and every remaining placement is in exactly one observation bucket."""
    for row in state.collectible_census():
        assert row["placed"] == PLACED[row["category"]]
        buckets = (
            row["standing"] + row["never_streamed"] + row["gone_in_a_later_save"] + row["unstated"]
        )
        assert row["collected"] + buckets == row["placed"], row["category"]
        if row["state_tracked"]:
            assert row["remaining"] == row["placed"] - row["collected"] == buckets
    census = {r["category"]: r for r in state.collectible_census()}
    assert census["power_slug_blue"]["remaining"] == 596 - 119 == 477
    assert census["power_slug_blue"]["standing"] == 381
    assert census["power_slug_blue"]["never_streamed"] == 96
    assert census["somersloop"]["remaining"] == 88


def test_a_placement_no_save_has_ever_loaded_is_remaining_but_never_present(state, table):
    """The honesty this feature exists for. 802 placements sit in cells no save on disk has
    ever streamed in. Nothing collected them, so they are remaining; nothing has ever looked
    at them, so their state is unobserved -- and the word for them is ``never_streamed``,
    never ``standing``. Conflating the two turns "3 remain" into "3 are there", which is a
    claim about the world nothing on disk supports.
    """
    remaining = state.placements(remaining_only=True)
    unseen = [p for p in remaining if p["observed"] == "never_streamed"]
    assert len(unseen) == 802
    assert {p["observed"] for p in remaining} <= {
        "standing",
        "never_streamed",
        "gone_in_a_later_save",
    }
    # Never silently present: the label comes from the table's own state, not a default.
    by_key = {(p["cell"], p["name"]): p for p in remaining}
    for row in table.rows:
        placement = by_key.get((row["cell"], row["instance"].rsplit(".", 1)[-1]))
        if placement is None:
            continue
        assert (placement["observed"] == "standing") == (row["state"] == "present")


def test_a_class_no_save_ever_mentions_cannot_have_a_remaining_figure(state, table):
    """A count-only class: the map places 62 somersloop shrines and no save on disk names one,
    live or gone. So a collection would leave nothing to read, and ``placed - collected``
    would equal 62 whether the player has taken every one or none. ``remaining`` is withheld
    rather than reported, and the 62 are all ``never_streamed``, which says exactly what is
    known: where they are and nothing else.
    """
    census = {r["category"]: r for r in state.collectible_census()}
    shrine = census["somersloop_shrine"]
    assert table.state_tracked("somersloop_shrine") is False
    assert shrine["remaining"] is None
    assert shrine["placed"] == 62 and shrine["collected"] == 0
    assert shrine["never_streamed"] == 62 and shrine["standing"] == 0
    # And the tracked ones are not withheld along with it.
    assert census["somersloop"]["remaining"] == 88
    assert table.state_tracked("somersloop") is True


def test_a_shrine_is_not_a_second_find(state, table):
    """Summing categories over-counts: all 298 Mercer shrines pair 1:1 with a sphere by the
    map's own AttachParent, so the pedestal is a second row about one find. The pairing is
    read from the table rather than assumed, and the collected counts agree with it."""
    assert table.pedestal_of("mercer_shrine") == "mercer_sphere"
    assert table.pedestal_of("somersloop_shrine") == "somersloop"
    assert table.pedestal_of("power_slug_blue") is None
    assert COLLECTED["mercer_shrine"] == COLLECTED["mercer_sphere"] == 80


def test_a_looted_drop_pod_still_stands_so_remaining_is_not_hard_drives_left(state, table):
    """The one category where remaining is true and misleading at once. A pod is only
    destroyed when it is dismantled, so 99 remain -- but 30 of the 88 standing ones have
    already given up their hard drive. The count that answers "where is a drive" is the
    unlooted one, and the tool has to say which is which."""
    census = {r["category"]: r for r in state.collectible_census()}
    pods = census["crashed_drop_pod"]
    assert pods["remaining"] == 99 and pods["standing"] == 88
    assert pods["looted_and_standing"] == 30
    assert "looted" in pods["note"]
    remaining = state.placements("crashed_drop_pod", remaining_only=True)
    assert sum(1 for p in remaining if p["looted"] is True) == 30
    assert sum(1 for p in remaining if p["looted"] is False) == 58
    # A pod whose state was never observed has no loot flag either -- null, not false.
    unseen = [p for p in remaining if p["observed"] == "never_streamed"]
    assert len(unseen) == 11 and all(p["looted"] is None for p in unseen)


def test_collected_placements_carry_the_position_the_save_cannot(state, table):
    """A destroyed record has no transform at all -- the old listing could only show the
    cell. The map supplies the coordinate, so "where was the sloop I already took" is
    answerable, and every listed actor is one this save really did collect."""
    listed = state.removed_actors("somersloop")["actors"]
    assert len(listed) == COLLECTED["somersloop"] == 18
    for placement in listed:
        assert placement["collected"] is True
        assert placement["observed"] is None, "a collected row has no standing state"
        assert len(placement["pos"]) == 3 and any(placement["pos"])
        assert len(placement["cell"]) == 25 and placement["cell"].isalnum()


def test_remaining_excludes_exactly_what_this_save_collected(state, table):
    """``placements`` and the census are the same walk, so the listing cannot disagree with
    the count. Every placement is remaining or collected and never both."""
    for category, collected in COLLECTED.items():
        every = state.placements(category)
        left = state.placements(category, remaining_only=True)
        assert len(every) == PLACED[category]
        assert len(every) - len(left) == collected
        assert not any(p["collected"] for p in left)


def test_what_can_be_listed_is_what_was_counted(state, table):
    """The invariant a previous defect broke, over every category the map places rather than
    only the ones with a collection. The census used to be built from one reading of the
    save and the listing from another, so the parts stopped adding to the whole; both now
    come from ``placements``."""
    census = {r["category"]: r for r in state.collectible_census()}
    groups = state.removed_actors()["groups"]
    assert set(census) == set(table.by_category) == set(PLACED)
    for category, row in census.items():
        listed = state.placements(category)
        assert len(listed) == row["placed"]
        assert sum(1 for p in listed if p["collected"]) == row["collected"]
        assert row["collected"] == groups.get(category, 0)
    assert sum(r["placed"] for r in census.values()) == len(table) == 4446


def test_nearest_is_sorted_by_metres_from_the_origin(state, table):
    """Distance is planar metres, computed against the same helper every other tool uses, so
    a slug 80 m away really is 80 m away in the units the answer says."""
    origin = (12_000.0, -34_000.0)
    rows = state.nearest_placements(origin, "power_slug_purple")
    assert len(rows) == PLACED["power_slug_purple"] - COLLECTED["power_slug_purple"]
    assert [r["distance_m"] for r in rows] == sorted(r["distance_m"] for r in rows)
    for row in rows[:5]:
        assert row["distance_m"] == pytest.approx(
            geo.distance_m((row["pos"][0], row["pos"][1]), origin)
        )
    assert not any(r["collected"] for r in rows), "a collected one is not somewhere to go"


def test_a_projection_with_no_removed_key_reports_nothing_rather_than_failing(state, table):
    """Anything cached before schema 11 has no such key. Zero collected is the wrong answer
    and the tool says so in a note; what this pins is that it does not raise on the way --
    and that with the map table present, everything the map placed still reads as remaining
    rather than the census collapsing to nothing."""
    projection = copy.deepcopy(state.projection)
    del projection["removed"]
    older = WorldState(projection=projection, game=state.game)
    out = older.removed_actors()
    assert out["total"] == 0 and out["cells"] == 0
    assert out["groups"] == {} and out["unresolved"] == 0
    assert {r["category"]: r["remaining"] for r in out["census"]}["somersloop"] == 106


def test_an_unknown_group_comes_back_as_an_error_not_an_exception(state, table):
    """The name reaches this from a tool argument, so a typo is a user event and not a bug.
    It must answer with the census plus an ``error`` naming the categories the map places."""
    out = state.removed_actors("no_such_group")
    assert "actors" not in out
    assert out["total"] == TOTAL_DESTROYED
    assert out["error"].startswith("unknown group 'no_such_group'; the map places: [")
    assert "'power_slug_blue'" in out["error"]


# --------------------------------------------------------------------------- the tool


@pytest.fixture
def tool(monkeypatch, state):
    """``collected_from_world`` against the committed projection rather than the live save.

    The tool resolves its own state and the numbers here are exact, so the fixture is
    substituted for the autosave -- otherwise every count below would drift with play.
    """
    monkeypatch.setattr(progression, "_state", lambda save, world, as_of=None: state)
    return collected_from_world


def test_the_census_shows_placed_collected_and_remaining(tool, table):
    out = tool()
    assert "whole_world_collected=713" in out
    assert "destroyed_records=889" in out
    assert "unresolved=176" in out
    assert "showing=every category" in out
    assert "category\tplaced\tcollected\tremaining\tstanding\tnever_streamed" in out
    assert "power_slug_blue\t596\t119\t477\t381\t96" in out
    assert "somersloop\t106\t18\t88\t66\t22" in out


def test_the_census_names_no_group_the_map_cannot_resolve(tool, table):
    """The retired buckets must not survive anywhere in the output: every one of them was a
    guess, and seeing 'slug_blue' next to 'power_slug_blue' would suggest they differ."""
    labels = {line.split("\t")[0] for line in tool().splitlines()}
    # The buckets that were only ever a guess. Three prefix names -- somersloop,
    # mercer_sphere, mercer_shrine -- are also real category names, so they are not
    # evidence either way.
    guesses = set(BY_NAME) - set(table.by_category)
    assert guesses == {
        "artifact_unsplit",
        "crash_site",
        "debris",
        "dropped_pickup",
        "flora",
        "slug_blue",
        "slug_purple",
        "slug_yellow",
    }
    assert not labels & guesses, "a name-prefix bucket reached the output"
    assert "power_slug_blue" in labels


def test_the_tool_separates_what_remains_from_what_has_been_seen(tool, table):
    """ "3 remain" and "3 that I know of" must not be readable as the same sentence."""
    out = tool()
    assert "never_streamed is a placement in a cell no save on disk has ever loaded" in out
    assert "it is NOT present" in out
    assert "standing + never_streamed" in out


def test_the_tool_withholds_a_remaining_figure_it_cannot_stand_behind(tool, table):
    """somersloop_shrine renders as ``-`` and the reason is a note, not silence."""
    out = tool()
    assert "somersloop_shrine\t62\t0\t-\t0\t62" in out
    assert "remaining is withheld (-) for somersloop_shrine" in out
    assert "would be a fabrication" in out


def test_the_tool_warns_that_a_shrine_is_not_a_second_collectible(tool, table):
    out = tool()
    assert "never add categories together" in out
    assert "mercer_shrine is the base mercer_sphere stands on" in out


def test_the_tool_says_a_looted_pod_is_still_standing(tool, table):
    out = tool()
    assert "30 of the 88 standing ones are already LOOTED" in out
    assert "not a count of hard drives left" in out


def test_the_tool_explains_the_unresolved_records_including_dropped_loot(tool, table):
    """170 map caches used to be reported as 'dropped_pickup'. The distinction still has to
    be made -- a pickup the player dropped shares the caches' native class and is not
    map-placed -- but now it is made about the unresolved bucket rather than about a
    category."""
    out = tool()
    assert "a pickup the PLAYER dropped is" in out
    assert "name_stem\tdestroyed\twhy the map table has no row for it" in out
    assert "BP_SporeFlower\t79" in out


def test_the_tool_lists_remaining_placements_with_coordinates(tool, table):
    out = tool(group="somersloop", show="remaining", limit=5)
    assert "show=remaining group=somersloop" in out
    assert "rows=88" in out
    assert "standing=66" in out and "never_streamed=22" in out
    assert "category\tname\tobserved\tgrid\tx,y\tz" in out
    assert "# 88 match(es), showing 5 from offset 0." in out


def test_the_tool_measures_nearest_from_the_player_by_default(tool, table):
    """The projection already knows where the player is, so the common question needs no
    coordinates. Distances are metres and ascending."""
    out = tool(group="somersloop", show="nearest", limit=5)
    assert "from you" in out
    assert "distance is planar metres from you" in out
    metres = [
        int(cell[:-1])
        for line in out.splitlines()
        for cell in line.split("\t")
        if cell.endswith("m") and cell[:-1].isdigit()
    ]
    assert metres and metres == sorted(metres)


def test_the_tool_measures_nearest_from_a_coordinate_in_metres(tool, table):
    out = tool(group="power_slug_purple", show="nearest", near="120,-340", limit=3)
    assert "from 120,-340" in out
    assert "show=nearest group=power_slug_purple" in out


def test_an_ungrouped_listing_hides_the_pedestals_and_says_it_did(tool, table):
    """Otherwise every sphere comes with a shrine row a metre away -- the double-count the
    census warns about, in listing form."""
    out = tool(show="nearest", limit=5)
    assert "row(s) are not shown" in out
    assert "mercer_shrine" in out
    assert tool(group="mercer_shrine", show="nearest", limit=3).count("mercer_shrine\t") >= 1


def test_a_collected_listing_says_the_coordinates_are_where_it_was(tool, table):
    out = tool(group="somersloop", show="collected", limit=5)
    assert "rows=18" in out
    assert "collected=18" in out
    assert "the coordinates say where they WERE" in out


def test_a_retired_group_name_is_renamed_or_explained_never_answered(tool, table):
    """A stored call using the old name should keep working where the new name is the same
    thing, and be told what happened where it is not. What it must not do is answer with a
    census for a different question."""
    assert "power_slug_blue\t596" in tool(group="slug_blue")
    refused = tool(group="artifact_unsplit")
    assert refused.startswith("! 'artifact_unsplit' is no longer a category")
    assert "somersloop" in refused and "total_removed" not in refused
    assert "REGROW" in tool(group="flora")


def test_a_group_narrows_the_census_and_the_total_stays_labelled_whole_world(tool, table):
    """A scoped table under an unscoped total is how a category count gets read as a world
    count, so the summary line names its own scope and does not shrink."""
    out = tool(group="somersloop")
    assert "showing=somersloop" in out
    assert "whole_world_collected=713" in out
    assert "somersloop\t106\t18\t88\t66\t22" in out
    assert "power_slug_blue" not in out
    # The global unresolved table belongs to no category, so it is not shown scoped.
    assert "name_stem" not in out


def test_an_unknown_group_returns_the_known_names_and_no_table(tool, table):
    out = tool(group="sloops")
    assert out.startswith("! unknown group 'sloops'; the map places: [")
    assert "whole_world_collected" not in out


def test_an_unknown_view_is_refused_with_the_valid_ones(tool, table):
    out = tool(show="everything")
    assert out.startswith("! unknown view 'everything'.")
    assert "nearest" in out


def test_an_empty_removed_key_is_called_unreadable_rather_than_none(monkeypatch, state, table):
    """A projection cached before schema 11 has no list, and "0 collected" would be a
    confident wrong answer -- the player has certainly picked up a slug."""
    projection = copy.deepcopy(state.projection)
    projection["removed"] = {"cells": [], "instances": [], "counts": {}}
    older = WorldState(projection=projection, game=state.game)
    monkeypatch.setattr(progression, "_state", lambda save, world, as_of=None: older)
    out = collected_from_world()
    assert "destroyed_records=0" in out
    assert "power_slug_blue\t596\t0\t596" in out


def test_an_unreadable_save_is_a_sentence_not_a_traceback(monkeypatch):
    """Every save-reading tool in this package answers that way, and a raise here would reach
    the MCP client as a protocol error instead of something the model can act on."""

    def boom(save, world, as_of=None):
        raise RuntimeError("no such save")

    monkeypatch.setattr(progression, "_state", boom)
    assert collected_from_world() == "could not read save: no such save"


# ------------------------------------------------- with no placement table at all


def test_without_the_map_table_the_census_degrades_to_names_and_says_so(save_only):
    """``data/world_collectibles.json`` is untracked, so a fresh clone has none. The save
    still knows what it destroyed, so that answer is given -- labelled, with the wrongness
    of the grouping quantified, rather than raising or pretending."""
    out = save_only.removed_actors()
    assert out["source"] == "save-only"
    assert out["total"] == TOTAL_DESTROYED
    assert out["groups"] == dict(sorted(BY_NAME.items(), key=lambda kv: -kv[1]))
    assert sum(out["groups"].values()) == TOTAL_DESTROYED
    assert "census" not in out and "resolved" not in out


def test_the_degraded_tool_labels_itself_and_refuses_what_it_cannot_do(monkeypatch, save_only):
    """It must not answer a narrower question quietly: show=remaining and show=nearest are
    refused outright, because without the map nothing knows how many exist.

    Both the label and the refusal name the command that fixes it. "Regenerate it" is not
    something a player can type, and this answer's whole job is to end the dead end.
    """
    monkeypatch.setattr(progression, "_state", lambda save, world, as_of=None: save_only)
    census = collected_from_world()
    assert "DEGRADED: data/world_collectibles.json has never been generated" in census
    assert "misfiles 51 of 713" in census
    assert "collected, not remaining" in census
    assert "slug_blue\t163" in census
    assert "'dropped_pickup' is loot the player dropped" in census
    assert GENERATOR_COMMAND in census

    for view in ("remaining", "nearest"):
        refused = collected_from_world(show=view)
        assert refused.startswith(f"! the {view!r} view needs the map's own placement table")
        assert GENERATOR_COMMAND in refused
        assert "total_removed" not in refused


def test_a_corrupt_table_says_so_instead_of_reading_as_never_generated(monkeypatch, save_only):
    """The two states the strict loader exists to separate, at the one place a player meets
    them. A half-written file is fixed by deleting it and running the generator; a missing
    one is fixed by running the generator. Told apart, each answer is actionable; told as
    one, the reader is left to guess which of the two they are in.
    """
    monkeypatch.setattr(progression, "_state", lambda save, world, as_of=None: save_only)

    def unreadable(*, strict: bool = False):
        """The loader's answer for a file that is there and will not parse."""
        if strict:
            raise CollectiblesUnreadable("data/world_collectibles.json exists but will not read")

    monkeypatch.setattr(service, "load_collectibles", unreadable)
    out = collected_from_world()
    assert out.startswith("! the map's placement table is CORRUPT, not missing")
    assert "will not read" in out
    assert GENERATOR_COMMAND in out
    # No census under it: a corrupt table is a refusal, not a degraded answer.
    assert "total_removed" not in out


def test_the_degraded_census_still_refuses_to_guess_an_artifact(save_only):
    """The strict rule earns its keep only here, and here it is still right: with no map,
    ``BP_WAT112`` could be somersloop 12, sphere nothing, or a blueprint named
    ``BP_WAT112`` -- and the save holding ``BP_WAT60``, ``BP_WAT73`` and ``BP_WAT84``
    proves the third reading is real. 65 names stay undecidable and are reported as such.
    """
    assert save_only.removed_group("BP_WAT1_C_11") == "somersloop"
    assert save_only.removed_group("BP_WAT2_C_18") == "mercer_sphere"
    for name in ("BP_WAT112_14", "BP_WAT60", "BP_WAT73", "BP_WAT84", "BP_WAT2_228"):
        assert save_only.removed_group(name) == "artifact_unsplit", name
    assert save_only.removed_actors()["groups"]["artifact_unsplit"] == 65


def test_the_degraded_census_still_puts_first_match_first(save_only):
    """``REMOVED_GROUPS`` is ordered, not a mapping: every slug name starts with
    ``BP_Crystal``, so testing the plain prefix first would file all 250 as blue."""
    assert save_only.removed_group("BP_Crystal_mk3_C_10") == "slug_purple"
    assert save_only.removed_group("BP_Crystal_mk2_C_4") == "slug_yellow"
    assert save_only.removed_group("BP_Crystal_mk21_23") == "slug_yellow"
    assert save_only.removed_group("BP_Crystal_mk316") == "slug_purple"
    assert save_only.removed_group("Build_Foundation_8x1_01_C") is None
    assert save_only.removed_group("") is None


def test_the_degraded_census_reports_an_unmatched_class_rather_than_dropping_it(save_only):
    projection = copy.deepcopy(save_only.projection)
    projection["removed"]["instances"] += [[0, "BP_SomethingNew_7"]] * 3
    out = WorldState(projection=projection, game=save_only.game).removed_actors()
    assert out["other"] == {"BP_SomethingNew": 3}
    assert out["total"] == TOTAL_DESTROYED + 3
    assert sum(out["groups"].values()) == TOTAL_DESTROYED
