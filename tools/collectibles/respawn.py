"""The premise every state rests on -- a taken collectible stays gone -- tested over the saves,
and the harvestable plants probed for the machinery that would make one come back."""

from __future__ import annotations

import collections
import itertools

from tools.collectibles.catalog import CATEGORIES, RESPAWN_PROBE, RESPAWN_PROPERTIES, ActorKey
from tools.collectibles.context import BuildContext, agrees_with_map
from tools.collectibles.stats import by_count


def _map_class(ctx: BuildContext, key: ActorKey) -> str:
    return ctx.world.class_by_key.get(key, "not a map-placed actor at all")


def measure_durability(ctx: BuildContext) -> None:
    """Whether any key leaves a destroyed list, or turns up live after one.

    Only consecutive saves of the same build are compared for the first: a build that
    re-issues instance names makes a key vanish without anything coming back.
    """
    left_the_list: collections.Counter = collections.Counter()
    pairs_compared = pairs_skipped = 0
    for older, newer in itertools.pairwise(ctx.session_saves):
        if older.build_version != newer.build_version:
            pairs_skipped += 1
            continue
        pairs_compared += 1
        for key in older.destroyed - newer.destroyed:
            left_the_list[_map_class(ctx, key)] += 1
    destroyed_observations: collections.Counter = collections.Counter()
    for save in ctx.session_saves:
        for key in save.destroyed:
            destroyed_observations[_map_class(ctx, key)] += 1

    ctx.pairs_compared = pairs_compared
    ctx.pairs_skipped = pairs_skipped
    ctx.destroyed_observations = destroyed_observations
    ctx.left_the_list = left_the_list
    _measure_destroyed_then_live(ctx)
    ctx.newest_coexisting = sum(
        1
        for key in ctx.newest.destroyed & ctx.newest.live.keys()
        if (placement := ctx.by_key.get(key)) is not None
        and agrees_with_map(ctx.newest.live[key][1], placement)
    )


def _measure_destroyed_then_live(ctx: BuildContext) -> None:
    """Split every "destroyed, and live" observation three ways; only a later live record at
    the map's position would falsify the premise. The position gate is what tells a
    player-dropped crate sharing a bare name from a resurrection."""
    first_destroyed: dict[ActorKey, int] = {}
    revived: collections.Counter = collections.Counter()
    revived_displaced: collections.Counter = collections.Counter()
    coexisting: collections.Counter = collections.Counter()
    coexisting_by_version: collections.Counter = collections.Counter()
    for index, save in enumerate(ctx.session_saves):
        # One save naming a key both ways is the game's own migration, not a respawn.
        for key in save.destroyed & save.live.keys():
            placement = ctx.by_key.get(key)
            _cls, position, _looted = save.live[key]
            if placement is not None and agrees_with_map(position, placement):
                coexisting[_map_class(ctx, key)] += 1
                coexisting_by_version[save.save_version] += 1
        for key, (_cls, position, _looted) in save.live.items():
            first = first_destroyed.get(key)
            if first is None or first >= index or key in save.destroyed:
                continue
            placement = ctx.by_key.get(key)
            if placement is not None and agrees_with_map(position, placement):
                revived[_map_class(ctx, key)] += 1
            else:
                revived_displaced[_map_class(ctx, key)] += 1
        for key in save.destroyed:
            first_destroyed.setdefault(key, index)

    ctx.revived = revived
    ctx.revived_displaced = revived_displaced
    ctx.coexisting = coexisting
    ctx.coexisting_by_version = coexisting_by_version


def measure_flora(ctx: BuildContext) -> None:
    """The respawn properties of the probed plants: a class that carries no ``mNumRespawns``
    at all has no respawn machinery, which is structural rather than inferred."""
    flora_records: collections.Counter = collections.Counter()
    flora_props: collections.Counter = collections.Counter()
    flora_values: collections.Counter = collections.Counter()
    flora_unreadable = 0
    for save in ctx.session_saves:
        flora_records.update(save.flora_records)
        flora_props.update(save.flora_property_records)
        flora_values.update(save.flora_values)
        flora_unreadable += save.flora_unreadable
    first, rose, fell, incomparable = _respawn_counter_moves(ctx)

    flora: dict[str, dict] = {}
    for cls in sorted(RESPAWN_PROBE):
        values: dict[str, dict[str, int]] = {}
        for name in RESPAWN_PROPERTIES:
            mine = {v: n for (c, p, v), n in flora_values.items() if c == cls and p == name}
            values[name] = {str(v): mine[v] for v in sorted(mine)}
        respawns = [int(v) for v in values["mNumRespawns"]]
        flora[cls] = {
            "placed_by_the_map": ctx.world.class_counts.get(cls, 0),
            "live_records_over_the_saves_used": flora_records.get(cls, 0),
            "records_carrying": {
                name: flora_props.get((cls, name), 0) for name in RESPAWN_PROPERTIES
            },
            "values": values,
            "highest_mNumRespawns_seen": max(respawns) if respawns else None,
            "counter_first_appearances": first.get(cls, 0),
            "counter_rose": rose.get(cls, 0),
            "counter_fell": fell.get(cls, 0),
            "counter_pairs_a_build_change_made_incomparable": incomparable.get(cls, 0),
            "destroyed_observations": ctx.destroyed_observations.get(cls, 0),
            "keys_that_left_the_destroyed_list": ctx.left_the_list.get(cls, 0),
            "respawns": bool(flora_props.get((cls, "mNumRespawns"), 0)),
            "emitted_as": CATEGORIES.get(cls),
        }

    ctx.flora = flora
    ctx.flora_unreadable = flora_unreadable


def _respawn_counter_moves(
    ctx: BuildContext,
) -> tuple[collections.Counter, collections.Counter, collections.Counter, collections.Counter]:
    """Per plant class: first sightings of a counter, and how it moved between saves of one
    build. A fall is the move that would contradict the model."""
    first: collections.Counter = collections.Counter()
    rose: collections.Counter = collections.Counter()
    fell: collections.Counter = collections.Counter()
    incomparable: collections.Counter = collections.Counter()
    seen: dict[ActorKey, tuple[int, int]] = {}
    for save in ctx.session_saves:
        for key, (cls, value) in save.flora_counter.items():
            previous = seen.get(key)
            if previous is None:
                first[cls] += 1
            elif previous[0] != save.build_version:
                incomparable[cls] += 1
            elif value > previous[1]:
                rose[cls] += 1
            elif value < previous[1]:
                fell[cls] += 1
            seen[key] = (save.build_version, value)
    return first, rose, fell, incomparable


def respawn_meta(ctx: BuildContext) -> dict:
    """``_meta.respawn``: the durability test and the flora probe."""
    return {
        "what": (
            "the premise every 'state' in this file rests on, tested rather than "
            "asserted: that taking a collectible removes its actor for good, so "
            "'collected' is durable. If any emitted class came back, 'collected' would "
            "be a snapshot of one save's opinion and the table would have to say so."
        ),
        "schema_rule": (
            "a class whose contents regrow gets no row. It would need a state field "
            "that cannot be filled: a harvested berry bush is NOT destroyed -- it stays "
            "live with mUpdatedOnDayNr set and its fruit comes back -- so 'present' "
            "would mean 'a bush is here', which a consumer would read as 'there is "
            "fruit here'. Rather than ship a state-shaped field that cannot carry a "
            "state, the two regrowing classes are in _meta.excluded with their map "
            "counts, and nothing in this file offers a remaining-set for them. "
            "mSavedNumItems in the values below is the plant's fixed YIELD and is never "
            "a countdown: every nut bush on disk writes 5."
        ),
        "durability": {
            "method": (
                "over the saves used, in save-clock order. Two failure modes: a key "
                "leaving a destroyed list, and a key a save called destroyed turning up "
                "live in a later one. The first is only meaningful between saves of the "
                "SAME build, because a build that re-issues instance names makes a key "
                "disappear without anything coming back -- those pairs are counted and "
                "skipped. The second is gated on position, because an auto-numbered "
                "instance name is not identity."
            ),
            "consecutive_same_build_pairs_compared": ctx.pairs_compared,
            "pairs_skipped_because_the_build_changed": ctx.pairs_skipped,
            "destroyed_observations_by_class": by_count(ctx.destroyed_observations),
            "keys_that_left_the_destroyed_list_by_class": by_count(ctx.left_the_list),
            "keys_that_left_the_destroyed_list_note": (
                "by the class the MAP gives that key, so 'not a map-placed actor at all' "
                "means one of the player's own actors -- a destroyed record for something "
                "the player built, which the game is free to forget. No emitted class "
                "appearing here is the result that matters."
            ),
            "rows_destroyed_then_live_again_by_class": by_count(ctx.revived),
            "rows_destroyed_then_live_again_rejected_by_position_by_class": by_count(
                ctx.revived_displaced
            ),
            "rows_a_single_save_lists_as_both_destroyed_and_live_by_class": by_count(
                ctx.coexisting
            ),
            "rows_a_single_save_lists_as_both_by_save_version": {
                str(version): count for version, count in sorted(ctx.coexisting_by_version.items())
            },
            "in_the_newest_save": ctx.newest_coexisting,
            "both_note": (
                "a save CAN name one (cell, instance) in its destroyed list and hold a "
                "live actor at that same key whose position agrees with the map. Those "
                "records contradict each other and neither is a respawn: before the world "
                "was partitioned the bare name BP_Crystal1 belonged to two different "
                "slugs in two different levels, and the migration keyed one of them into "
                "the other's cell. That is the game's own migration hitting the same "
                "identity problem this file refuses to ignore. The version "
                "breakdown is the point: they are a saveVersion 52 phenomenon and "
                "in_the_newest_save is 0, so no state in this file currently rests on the "
                "tie. Should it ever be non-zero, the precedence is that the destroyed "
                "list wins and the row reads collected -- stated here because it would "
                "otherwise be an accident of the order two ifs are written in."
            ),
            "rejected_by_position_note": (
                "these are what a name-only test would have called resurrections. A "
                "player-dropped FGItemPickup_Spawnable can carry the same bare "
                "auto-numbered name as a map cache and sit 100 m away, and the same "
                "goes for a record the 52 -> 60 rename left pointing at the wrong "
                "cell. Reported as its own number so the value of the position gate is "
                "visible rather than argued: keys_that_left_the_destroyed_list and "
                "rows_destroyed_then_live_again are what survives it."
            ),
        },
        "flora": ctx.flora,
        "flora_note": (
            "the three harvestable plants, probed on every live record in every save of the "
            "session used. "
            "records_carrying is presence, not value: a class carrying no mNumRespawns "
            "on any of tens of thousands of those records has no respawn machinery, which is "
            "structural evidence and not an inference from never having watched one "
            "regrow. 'respawns' is set from exactly that, and 'emitted_as' says what "
            "this file did about it -- null means the class is in _meta.excluded. "
            "counter_fell is the number that would contradict the model."
        ),
        "flora_records_whose_properties_would_not_decode": ctx.flora_unreadable,
    }
