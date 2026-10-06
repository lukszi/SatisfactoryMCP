"""Whether each row is exactly one collectible: pedestals paired to what they carry, rows that
sit on top of each other, and what an instance name can and cannot say about a class."""

from __future__ import annotations

import collections
import re

from satisfactory_mcp.core.jsontypes import JsonObject
from tools.collectibles.catalog import (
    CATEGORIES,
    GLUED_WAT,
    INSTANCE_PREFIX,
    NAME_TAIL,
    PLACEMENT_ID_MARK,
    POSITION_TOLERANCE_CM,
    ActorKey,
    Position,
)
from tools.collectibles.context import BuildContext
from tools.collectibles.hazards import SpatialIndex
from tools.collectibles.rows import CollectibleRow
from tools.collectibles.stats import by_count


def measure_pedestals(ctx: BuildContext) -> None:
    """Check that a shrine's AttachParent pairs it 1:1 with the artifact it carries."""
    pedestals: JsonObject = {}
    row_category: dict[str | None, str] = {row["instance"]: row["category"] for row in ctx.rows}
    for category in ("mercer_shrine", "somersloop_shrine"):
        mine = [r for r in ctx.rows if r["category"] == category]
        parents = [r.get("attached_to") for r in mine]
        parent_categories = collections.Counter(
            row_category.get(p, "not a row here") for p in parents
        )
        pedestals[category] = {
            "rows": len(mine),
            "with_a_parent": sum(1 for p in parents if p),
            "distinct_parents": len({p for p in parents if p}),
            "parent_category": by_count(parent_categories),
            "one_to_one": len({p for p in parents if p}) == len(mine),
        }

    ctx.pedestals = pedestals


def _row_position(row: CollectibleRow) -> Position:
    return (row["x"], row["y"], row["z"])


def measure_coincident_positions(ctx: BuildContext) -> None:
    """Pairs of rows of one category within a metre: one collectible counted twice, or two."""
    grids: dict[str, SpatialIndex[CollectibleRow]] = collections.defaultdict(
        lambda: SpatialIndex[CollectibleRow](POSITION_TOLERANCE_CM)
    )
    for row in ctx.rows:
        grids[row["category"]].add(_row_position(row), row)
    coincident_pairs: set[tuple[ActorKey, ActorKey]] = set()
    coincident_by_category: collections.Counter[str] = collections.Counter()
    coincident_states_differ = 0
    coincident_both_have_a_placement_id = 0
    for row in ctx.rows:
        for gap, other in grids[row["category"]].near(_row_position(row)):
            if other is row or gap > POSITION_TOLERANCE_CM:
                continue
            first = (row["cell"], row["instance"].removeprefix(INSTANCE_PREFIX))
            second = (other["cell"], other["instance"].removeprefix(INSTANCE_PREFIX))
            pair = (first, second) if first < second else (second, first)
            if pair in coincident_pairs:
                continue
            coincident_pairs.add(pair)
            coincident_by_category[row["category"]] += 1
            if row["state"] != other["state"]:
                coincident_states_differ += 1
            if PLACEMENT_ID_MARK in first[1] and PLACEMENT_ID_MARK in second[1]:
                coincident_both_have_a_placement_id += 1

    ctx.coincident_pairs = coincident_pairs
    ctx.coincident_by_category = coincident_by_category
    ctx.coincident_states_differ = coincident_states_differ
    ctx.coincident_both_have_a_placement_id = coincident_both_have_a_placement_id
    ctx.coincident_pair_count = len(coincident_pairs)


def _name_stem_class(leaf: str, stems: dict[str, str]) -> str | None:
    """The class a longest-prefix rule over instance names WOULD pick. Diagnostic only."""
    base = NAME_TAIL.sub("", leaf)
    base = re.sub(r"_C$", "", base)
    base = re.sub(r"_?\d+$", "", base)
    best: tuple[str, str] | None = None
    for stem, cls in stems.items():
        if (leaf.startswith(stem) or base.startswith(stem)) and (
            best is None or len(stem) > len(best[0])
        ):
            best = (stem, cls)
    return best[1] if best else None


def measure_naming(ctx: BuildContext) -> None:
    """Score a name-based class rule against the map's own answer for every row."""
    stems = {cls.removesuffix("_C"): cls for cls in CATEGORIES}
    prefix_rule_wrong = prefix_rule_silent = 0
    for placement in ctx.row_placements:
        guess = _name_stem_class(placement.instance, stems)
        if guess is None:
            prefix_rule_silent += 1
        elif guess != placement.cls:
            prefix_rule_wrong += 1
    # Against every map-placed class too: a name can point at a class this file never emits.
    all_stems = {cls.removesuffix("_C"): cls for cls in ctx.world.class_counts}
    rows_by_foreign_stem: collections.Counter[str] = collections.Counter()
    for placement in ctx.row_placements:
        guess = _name_stem_class(placement.instance, all_stems)
        if guess is not None and guess != placement.cls:
            rows_by_foreign_stem[f"{guess} -> {placement.cls}"] += 1
    glued_index = [p for p in ctx.row_placements if GLUED_WAT.match(p.instance)]
    destroyed = ctx.newest.destroyed

    # A row key any save mentions, live or gone; none at all means the class is not
    # save-serialised and can only ever be located.
    keys_any_save_mentions: set[ActorKey] = set()
    for save in ctx.session_saves:
        keys_any_save_mentions |= save.live.keys() & ctx.by_key.keys()
        keys_any_save_mentions |= save.destroyed & ctx.by_key.keys()

    ctx.prefix_rule_wrong = prefix_rule_wrong
    ctx.prefix_rule_silent = prefix_rule_silent
    ctx.rows_by_foreign_stem = rows_by_foreign_stem
    ctx.glued_index = glued_index
    ctx.glued_index_by_category = collections.Counter(CATEGORIES[p.cls] for p in glued_index)
    ctx.glued_index_destroyed = [
        ctx.by_key[key] for key in destroyed if GLUED_WAT.match(key[1]) and key in ctx.by_key
    ]
    ctx.glued_index_destroyed_unresolved = sum(
        1 for key in destroyed if GLUED_WAT.match(key[1]) and key not in ctx.by_key
    )
    ctx.keys_any_save_mentions = keys_any_save_mentions
    # Two records the game holds for a coincident pair make it two actors, not one row twice.
    ctx.coincident_both_recorded = sum(
        1
        for first, second in ctx.coincident_pairs
        if first in keys_any_save_mentions and second in keys_any_save_mentions
    )


def identity_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.identity``: the key, its uniqueness, and the rows that sit on top of each other."""
    return {
        "key": "(cell, instance)",
        "key_is_unique": ctx.duplicate_keys == 0,
        "duplicate_keys": ctx.duplicate_keys,
        "duplicate_instance_names_among_rows": ctx.duplicate_names,
        "map_actors_in_gamelevel01": ctx.world.actor_count,
        "map_actors_with_a_blueprint_class": sum(ctx.world.game_class_counts.values()),
        "blueprint_actor_classes": len(ctx.world.game_class_counts),
        "widening_note": (
            "map_actors_with_a_blueprint_class is what a /Game/ prefix walk sees, and it "
            "is reported beside the full count so the widening to native classes can be "
            "checked as additive rather than taken on trust: the blueprint figure must "
            "not move."
        ),
        "distinct_keys": ctx.world.distinct_keys,
        "distinct_instance_names": ctx.world.distinct_names,
        "instance_names_carrying_two_classes": ctx.world.names_with_two_classes,
        "names_with_the_map_s_placement_id": ctx.world.placement_id_names,
        "names_with_the_map_s_placement_id_distinct": ctx.world.placement_id_names_distinct,
        "actors_reusing_an_instance_name_by_class": by_count(ctx.world.name_repeats_by_class),
        "note": (
            "(cell, instance) is unique over every map-placed actor -- duplicate_keys is "
            "0. The bare instance name is NOT, and rather than say which classes are to "
            "blame, actors_reusing_an_instance_name_by_class counts them: widening the "
            "walk to native classes brought in per-cell housekeeping singletons that "
            "reuse one name across every cell, and they account for the whole of the "
            "difference between map_actors_in_gamelevel01 and distinct_instance_names. "
            "None of them is a collectible class. The _UAID_ names -- the map's own "
            "placement ids -- stay globally unique."
        ),
        "placement_id_note": (
            "the _UAID_ suffix is a sufficient mark of a map placement and not a "
            "necessary one: some map-placed actors carry names inherited from older "
            "hand-placed ones. with_the_map_s_own_placement_id is reported per category "
            "so a consumer filtering on the suffix can see what that filter would cost."
        ),
        "coincident_pairs_in_the_same_category": ctx.coincident_pair_count,
        "coincident_pairs_by_category": by_count(ctx.coincident_by_category),
        "coincident_pairs_the_saves_hold_a_record_for_both_of": ctx.coincident_both_recorded,
        "coincident_pairs_where_both_carry_a_placement_id": (
            ctx.coincident_both_have_a_placement_id
        ),
        "coincident_pairs_the_saves_give_different_states": ctx.coincident_states_differ,
        "coincident_note": (
            "a pair is two rows of one category within a metre of each other, and a "
            "question rather than an error: such a pair COULD be one physical collectible "
            "emitted twice. For every artefact class the count is 0, which is the result "
            "worth having. It is not 0 for the mushroom, which is what a mushroom is -- "
            "they grow in clumps. What settles those pairs is identity, not distance, and "
            "the three numbers beside the count are exactly how far the evidence goes: "
            "the saves hold a SEPARATE record for both members of "
            "coincident_pairs_the_saves_hold_a_record_for_both_of of them, so the game "
            "itself tracks two actors there; both members carry the map's own globally "
            "unique _UAID_ placement id in "
            "coincident_pairs_where_both_carry_a_placement_id of them. "
            "coincident_pairs_the_saves_give_different_states would be the strongest "
            "evidence of all -- one harvested while the other still stands, which one "
            "actor cannot be -- and it is reported even though this player's saves happen "
            "to give it as 0: the pairs are all in the same state, so that particular "
            "proof is simply not available here."
        ),
    }


def naming_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.naming``: what a consumer may and may not infer from an instance name."""
    return {
        "what": (
            "a naming aid, kept apart from the placements. The placements are map facts; "
            "this section is about what a consumer may and may not infer from a name."
        ),
        "rule": (
            "An instance name never decides a class. The digits are a placement counter, "
            "not a class index -- a BP_WAT1_C is named BP_WAT133 -- and the map's actors "
            "kept the names of the actors they were copied from, so BP_Crystal_mk2_C "
            "instances named BP_Crystal_C_15 exist. The class comes from the map."
        ),
        "resolves_from": (
            "the (cell, instance) of the placements below. A save's destroyed-actor entry "
            "is a bare path with no class; look it up here and the class is exact."
        ),
        "prefix_rule_wrong": ctx.prefix_rule_wrong,
        "prefix_rule_silent": ctx.prefix_rule_silent,
        "prefix_rule_checked": len(ctx.row_placements),
        "prefix_rule_note": (
            "a longest-prefix rule over class stems, scored against the map's own "
            "answer for the very rows in this file. 'silent' is a name no stem matched -- "
            "and most of the silence is the mushroom: 1,363 of its placements are named "
            "BP_Shroom_<counter>, with the digits glued to a stem that is BP_Shroom_01, "
            "so no split recovers the class. The same failure as BP_WAT1 vs BP_WAT2."
        ),
        "rows_whose_name_stem_is_a_different_map_class": by_count(ctx.rows_by_foreign_stem),
        "borrowed_name_note": (
            "read as 'a name-based rule would say the first and the map says the second'. "
            "Scored against every class the map places, not only the emitted ones, which "
            "is what makes it worth its space: 154 mushrooms carry names beginning "
            "BP_BerryBush, so a name rule would file them under a class this file "
            "deliberately does NOT emit because it regrows -- the answer would not merely "
            "be the wrong category, it would be a row that should not exist keyed to a "
            "plant that does. Empty would mean no row's name belongs to another class."
        ),
        "glued_index_names": len(ctx.glued_index),
        "glued_index_note": (
            "names matching ^BP_WAT[0-9], where the placement counter is glued to the "
            "stem so no split can tell BP_WAT1 (somersloop) from BP_WAT2 (Mercer "
            "sphere). The map resolves all of them."
        ),
        "glued_index_by_category": by_count(ctx.glued_index_by_category),
        "glued_index_destroyed_in_newest_save": len(ctx.glued_index_destroyed),
        "glued_index_destroyed_resolved": by_count(
            collections.Counter(CATEGORIES[p.cls] for p in ctx.glued_index_destroyed)
        ),
        "glued_index_destroyed_unresolved": ctx.glued_index_destroyed_unresolved,
    }
