"""Each row's state, and the evidence that the newest save alone may decide it: the rows, the
older saves' staleness, the orphans, how much of the map the saves have observed, and the
``_meta`` blocks that report each."""

from __future__ import annotations

import collections
import math

from pioneersav import FIRST_MODERN_BODY
from satisfactory_mcp.core.collectible_rows import MapPlacement, RowState
from satisfactory_mcp.core.jsontypes import JsonObject
from tools.collectibles.catalog import (
    CATEGORIES,
    DROP_POD_CLASS,
    INSTANCE_PREFIX,
    POSITION_TOLERANCE_CM,
    ActorKey,
)
from tools.collectibles.context import BuildContext, agrees_with_map
from tools.collectibles.hazards import hazard_context
from tools.collectibles.map_read import Placement
from tools.collectibles.stats import by_count, json_array, spread


def measure_status(ctx: BuildContext) -> None:
    """The newest save's live and destroyed records, joined to the map's rows."""
    present: dict[ActorKey, bool | None] = {}
    displaced: list[tuple[ActorKey, float]] = []
    orphan_live: collections.Counter[str] = collections.Counter()
    agreeing: list[float] = []
    for key, (cls, position, looted) in ctx.newest.live.items():
        placement = ctx.by_key.get(key)
        if placement is None:
            orphan_live[cls] += 1
            continue
        gap = math.dist(position, placement.position)
        if gap <= POSITION_TOLERANCE_CM:
            present[key] = looted
            agreeing.append(gap)
        else:
            displaced.append((key, gap))

    collected = {key for key in ctx.newest.destroyed if key in ctx.by_key}
    # The rest of the destroyed list, by the map's own class for each key, so "the
    # remainder is scenery" is derived rather than assumed.
    destroyed_others: collections.Counter[str] = collections.Counter()
    for key in ctx.newest.destroyed:
        if key not in ctx.by_key:
            destroyed_others[ctx.world.class_by_key.get(key, "not a map-placed actor at all")] += 1

    ctx.present = present
    ctx.displaced = displaced
    ctx.orphan_live = orphan_live
    ctx.agreeing = agreeing
    ctx.collected = collected
    ctx.destroyed_others = destroyed_others
    ctx.recoverable = _recoverable_by_position(ctx, displaced)


def _recoverable_by_position(ctx: BuildContext, displaced: list[tuple[ActorKey, float]]) -> int:
    """Displaced records one unambiguous nearby placement could claim. Reported, never applied:
    state has one derivation, and an honest unknown beats a heuristic overriding it."""
    by_class: dict[str, list[Placement]] = collections.defaultdict(list)
    for placement in ctx.row_placements:
        by_class[placement.cls].append(placement)
    recoverable = 0
    for key, _gap in displaced:
        cls, position, _looted = ctx.newest.live[key]
        ranked = sorted(math.dist(position, p.position) for p in by_class[cls])
        if (
            ranked
            and ranked[0] <= POSITION_TOLERANCE_CM
            and (len(ranked) == 1 or ranked[1] > 10 * max(ranked[0], 1.0))
        ):
            recoverable += 1
    return recoverable


def build_rows(ctx: BuildContext) -> None:
    """One row per placement, its state the merge ``measure_status`` made."""
    rows: list[MapPlacement] = []
    for placement in ctx.row_placements:
        key = (placement.cell, placement.instance)
        state: RowState
        if key in ctx.collected:
            state = "collected"
        elif key in ctx.present:
            state = "present"
        else:
            state = "unknown"
        row: MapPlacement = {
            "instance": INSTANCE_PREFIX + placement.instance,
            "cell": placement.cell,
            "category": CATEGORIES[placement.cls],
            "class": placement.cls,
            "x": round(placement.position[0], 1),
            "y": round(placement.position[1], 1),
            "z": round(placement.position[2], 1),
            "state": state,
        }
        if placement.attached_to:
            row["attached_to"] = INSTANCE_PREFIX + placement.attached_to
        if placement.cls == DROP_POD_CLASS:
            row["looted"] = ctx.present.get(key) if state == "present" else None
        if placement.contents is not None:
            row["contents"] = placement.contents
        if placement.unlock_cost is not None:
            row["unlock_cost"] = placement.unlock_cost
        context = hazard_context(placement.position, ctx.hazards)
        if context:
            row["hazard"] = context
        rows.append(row)
    rows.sort(key=lambda r: (r["category"], r["cell"], r["instance"]))
    ctx.rows = rows


def measure_older_save_staleness(ctx: BuildContext) -> None:
    """What the older saves add, and how stale their keys are: if their union resolved rows
    the newest save cannot, the newest save would not be the authority."""
    keys_only_older_saves_state: set[ActorKey] = set()
    displaced_by_version: dict[int, set[ActorKey]] = collections.defaultdict(set)
    joined_by_save: dict[str, int] = {}
    orphan_live_over_all_saves: collections.Counter[str] = collections.Counter()
    orphan_versions: dict[ActorKey, set[int]] = collections.defaultdict(set)
    pre_partition_keys: set[ActorKey] = set()
    pre_partition_cells: collections.Counter[str] = collections.Counter()
    for save in ctx.session_saves:
        old = save.save_version < FIRST_MODERN_BODY
        joined = 0
        for key, (cls, position, _looted) in save.live.items():
            placement = ctx.by_key.get(key)
            if placement is None:
                orphan_live_over_all_saves[cls] += 1
                orphan_versions[key].add(save.save_version)
                continue
            joined += 1
            if old:
                pre_partition_keys.add(key)
                pre_partition_cells[key[0]] += 1
            if not agrees_with_map(position, placement):
                displaced_by_version[save.save_version].add(key)
            elif key not in ctx.present and key not in ctx.collected:
                keys_only_older_saves_state.add(key)
        for key in save.destroyed:
            if key in ctx.by_key:
                joined += 1
                if old:
                    pre_partition_keys.add(key)
                    pre_partition_cells[key[0]] += 1
                if key not in ctx.collected and key not in ctx.present:
                    keys_only_older_saves_state.add(key)
        joined_by_save[save.name] = joined

    ctx.keys_only_older_saves_state = keys_only_older_saves_state
    ctx.displaced_by_version = displaced_by_version
    ctx.joined_by_save = joined_by_save
    ctx.orphan_live_over_all_saves = orphan_live_over_all_saves
    ctx.orphan_versions = orphan_versions
    ctx.pre_partition_keys = pre_partition_keys
    ctx.pre_partition_cells = pre_partition_cells


def measure_orphans(ctx: BuildContext) -> None:
    """Sort the orphans -- live records of an emitted class with no row -- into the two known
    causes; the remainder would be a collectible the map read missed."""
    row_names = {p.instance for p in ctx.row_placements}
    orphan_old_layout = orphan_renamed = orphan_unexplained = 0
    for key, versions in ctx.orphan_versions.items():
        if max(versions) < FIRST_MODERN_BODY:
            orphan_old_layout += 1
        elif key[1] in row_names:
            orphan_renamed += 1
        else:
            orphan_unexplained += 1

    ctx.orphan_old_layout = orphan_old_layout
    ctx.orphan_renamed = orphan_renamed
    ctx.orphan_unexplained = orphan_unexplained


def measure_exploration(ctx: BuildContext) -> None:
    """How much of the world the saves have observed, per collectible: a recorded cell may
    have streamed only in part, so a cell is not a unit of coverage."""
    collectible_cells = {p.cell for p in ctx.row_placements}
    recorded_union: set[str] = set()
    for save in ctx.session_saves:
        recorded_union |= save.recorded_cells
    cells_no_record = collectible_cells - recorded_union
    unknown_rows = [r for r in ctx.rows if r["state"] == "unknown"]
    unknown_no_record = sum(1 for r in unknown_rows if r["cell"] in cells_no_record)

    in_recorded_cells = [k for k in ctx.world.class_by_key if k[0] in ctx.newest.recorded_cells]
    observed_map_actors = sum(
        1 for k in in_recorded_cells if k in ctx.newest.live_any_class or k in ctx.newest.destroyed
    )

    ctx.collectible_cells = collectible_cells
    ctx.cells_no_record = cells_no_record
    ctx.unknown_rows = unknown_rows
    ctx.unknown_no_record = unknown_no_record
    ctx.observed_map_actors = observed_map_actors
    ctx.map_actors_in_recorded_cells = len(in_recorded_cells)
    ctx.map_cells = {cell for cell, _instance in ctx.world.class_by_key}


def status_source_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.source.status``: which saves were read, and which one decides."""
    return {
        "kind": "the player's own save files",
        "role": "state only -- collected / present / unknown. Never a position.",
        "session": ctx.newest.session,
        "save_files_found": ctx.files_found,
        "saves_read": len(ctx.readable_saves),
        "files_that_are_not_a_save": ctx.files_found - len(ctx.readable_saves),
        "files_that_are_not_a_save_note": (
            "the game drops a 105-byte ServerManager_V2.sav beside the real saves. "
            "It is not a save game; any other count here would be a save this "
            "project's parser cannot read, which is a bug worth chasing."
        ),
        "sessions_on_disk": by_count(ctx.sessions),
        "saves_used": len(ctx.session_saves),
        "saves_in_another_session": len(ctx.readable_saves) - len(ctx.session_saves),
        "sessions_note": (
            "the union of two sessions is not a world: an instance name means "
            "whatever the save that wrote it meant. Only the largest session's "
            "saves are used, and the rest are counted here and dropped."
        ),
        "save_versions": json_array(sorted({f.save_version for f in ctx.session_saves})),
        "build_versions": json_array(sorted({f.build_version for f in ctx.session_saves})),
        "saves_predating_world_partition": len(ctx.pre_partition),
        "saves_that_join_no_row_here": sum(1 for v in ctx.joined_by_save.values() if v == 0),
        "rows_the_pre_partition_saves_can_key": len(ctx.pre_partition_keys),
        "cells_the_pre_partition_saves_key_through": by_count(ctx.pre_partition_cells),
        "pre_partition_note": (
            f"{len(ctx.pre_partition)} of the saves used are below saveVersion "
            f"{FIRST_MODERN_BODY}, from before the world was partitioned. They parse -- "
            "the body layout is version-gated on the header's own save_version -- and "
            "they are read because they are evidence for the rename below. What they "
            "can key is measured rather than assumed: their per-tile level records "
            "carry a package path for a tile the current map has no cell for, which "
            "leaves only the names both layouts share -- see "
            "cells_the_pre_partition_saves_key_through for what those turn out to be. "
            "They set no state either way: the newest save does that."
        ),
        "saved_between": [
            ctx.session_saves[0].when.date().isoformat(),
            ctx.newest.when.date().isoformat(),
        ],
        "play_duration_hours": [
            round(ctx.session_saves[0].play_seconds / 3600, 1),
            round(ctx.newest.play_seconds / 3600, 1),
        ],
        "newest_save": ctx.newest.name,
        "state_authority": (
            "the newest save by the clock it was written at, not by file mtime -- an "
            "autosave rewritten in place has a fresh mtime and an old clock. Its live "
            "set and destroyed list are cumulative, and "
            "rows_only_older_saves_could_state below measures whether the union of "
            "all of them would say anything more."
        ),
    }


def status_evidence_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.status_evidence``: what the live and destroyed records did and did not join."""
    return {
        "live_records_accepted": len(ctx.present),
        "live_records_displaced": len(ctx.displaced),
        "live_records_displaced_note": (
            "a live header whose (cell, instance) hits a map row but whose position does "
            "not. The saveVersion 52 -> 60 patch re-issued names and cells, and the game "
            "migrates a cell's saved records only when that cell is next streamed, so a "
            "cell not revisited since the patch still holds pre-patch records that key "
            "the wrong map row. These set no state; the rows they touch stay unknown."
        ),
        "displaced_gap_cm": (
            [
                round(min(g for _k, g in ctx.displaced), 1),
                round(max(g for _k, g in ctx.displaced), 1),
            ]
            if ctx.displaced
            else None
        ),
        "displaced_gap_note": (
            f"the {POSITION_TOLERANCE_CM:.0f} cm line sits in a gap in the data rather "
            "than inside a cluster: the worst ACCEPTED record is at "
            "position_agreement.max_cm and the nearest REJECT is the first number in "
            "displaced_gap_cm, with nothing between them. Read the two together -- the "
            "margin is not enormous, and if a future game version narrowed it this is "
            "where that would be visible."
        ),
        "displaced_records_a_position_match_could_re_attach": ctx.recoverable,
        "displaced_records_re_attached": 0,
        "displaced_note": (
            "so at most this many rows called unknown here are in fact standing. Measured "
            "and reported rather than applied: state has one derivation, and a distance "
            "heuristic that silently overrides it is worse than an honest unknown."
        ),
        "live_records_with_no_map_row": sum(ctx.orphan_live.values()),
        "live_records_with_no_map_row_by_class": by_count(ctx.orphan_live),
        "live_records_with_no_map_row_in_any_save_used": sum(
            ctx.orphan_live_over_all_saves.values()
        ),
        "live_records_with_no_map_row_in_any_save_used_by_class": by_count(
            ctx.orphan_live_over_all_saves
        ),
        "orphan_keys_distinct": len(ctx.orphan_versions),
        "orphan_keys_only_a_pre_partition_save_names": ctx.orphan_old_layout,
        "orphan_keys_whose_name_the_map_places_in_another_cell": ctx.orphan_renamed,
        "orphan_keys_unexplained": ctx.orphan_unexplained,
        "orphan_keys_unexplained_note": (
            "THIS is the number that would say a collectible is missing from the table. "
            "An orphan is a save's live record of an emitted class with no row here, and "
            "it can only be three things: a record written under the pre-partition world "
            "layout, whose level names no current cell matches; a pre-patch record whose "
            "instance name the map now places in a different cell, which is the 52 -> 60 "
            "rename again; or a collectible the map read failed to find. The first two are "
            "counted above and this is the remainder. Scope: LIVE records of the emitted "
            "classes. 0 means no save on disk holds a live record of one of those classes "
            "that this table cannot account for -- it is not a statement about the "
            "destroyed lists, which are accounted for separately in "
            "destroyed_entries_that_are_not_rows_here_by_class, nor about classes this "
            "file does not emit."
        ),
        "live_records_with_no_map_row_note": (
            "a live header of an emitted class whose (cell, instance) is not a row here. "
            "Two things land in it and both are excluded from the table by the same "
            "mechanism -- having no row: a crate the player dropped, which shares "
            "FGItemPickup_Spawnable with the map's loot caches, and an actor from a cell "
            "layout the current map no longer has, which is why the figure over every "
            "save used is large while the newest save's is what it is. The map's own "
            "caches are told apart by having a row at all, never by their name."
        ),
        "destroyed_entries_in_newest_save": len(ctx.newest.destroyed),
        "destroyed_entries_that_are_rows_here": len(ctx.collected),
        "destroyed_entries_that_are_not_rows_here_by_class": by_count(ctx.destroyed_others),
        "destroyed_entries_note": (
            "what the remainder is, by the class the MAP gives that (cell, instance) -- "
            "not a guess about it. A key the map does not place at all -- one of the "
            "player's own actors, or a pre-patch key the game has not migrated -- would "
            "appear under its own label rather than be folded into a class."
        ),
        "rows_only_older_saves_could_state": len(ctx.keys_only_older_saves_state),
        "rows_only_older_saves_could_state_note": (
            "0 means the newest save is sufficient and the other saves are corroboration."
        ),
        "distinct_displaced_rows_by_save_version": {
            str(version): len(keys) for version, keys in sorted(ctx.displaced_by_version.items())
        },
        "displaced_by_version_note": (
            "distinct rows some save of that version displaces. The pre-patch 52 saves "
            "displace more, as they must, but the count does not fall to zero at 60 -- "
            "which is the whole point: the game has migrated the cells this player has "
            "revisited since the patch and not the others."
        ),
    }


def position_agreement_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.position_agreement``: the accepted positions against the newest save's own."""
    return {
        "what": (
            "the accepted positions re-measured against the newest save's own live actor "
            "headers, which is the game's word on where its actors are. The displaced "
            "records are excluded and counted under status_evidence instead."
        ),
        **spread(ctx.agreeing),
        "over_1cm_note": (
            "a level-design edit between game versions nudged some actors by whole "
            "centimetres and the game rewrites the saved transform when the cell is next "
            "streamed, so a handful of cells still carry the pre-edit value. The map is "
            "the current one."
        ),
        "guard": (
            "a component's transform is serialised only where it differs from its class "
            "template, and BP_WAT2's root template is scaled 2.7. Composing with a "
            "default of 1.0 instead puts every shrine 73.1 cm out, which would show up "
            "here as a median in the tens of centimetres rather than a small fraction of "
            "one."
        ),
    }


def exploration_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.exploration``: how much of the map the saves have observed."""
    return {
        "what": (
            "how much of the map the SAVES have observed. This is about the player: the "
            "row set does not depend on it, because the rows come from the map."
        ),
        "collectibles_observed": len(ctx.rows) - len(ctx.unknown_rows),
        "collectibles_observed_pct": round(
            100 * (len(ctx.rows) - len(ctx.unknown_rows)) / len(ctx.rows), 1
        ),
        "collectibles_unknown": len(ctx.unknown_rows),
        "unknown_in_a_cell_with_no_level_record_in_any_save": ctx.unknown_no_record,
        "unknown_in_a_cell_the_saves_have_partly_streamed": len(ctx.unknown_rows)
        - ctx.unknown_no_record,
        "cells_holding_a_collectible": len(ctx.collectible_cells),
        "cells_holding_a_collectible_with_no_level_record": len(ctx.cells_no_record),
        "cells_the_newest_save_has_a_level_record_for": len(ctx.newest.recorded_cells),
        "cells_the_newest_save_s_grid_table_declares": len(ctx.newest.declared_cells),
        "cells_holding_a_collectible_the_grid_table_does_not_declare": len(
            ctx.collectible_cells - ctx.newest.declared_cells
        ),
        "there_is_deliberately_no_cells_the_map_places_an_actor_in_figure": (
            f"it would be {len(ctx.map_cells)}, which is exactly source.placements."
            "packages_read, because every cooked cell holds the per-cell housekeeping "
            "singletons (FGWorldSettings, Model) whatever else is in it. It used to be "
            "printed above and its only use was to be divided into the two save-side cell "
            "counts, which measures nothing at all. The cell population that is about "
            "collectibles is cells_holding_a_collectible."
        ),
        "cell_counts_are_not_denominators": (
            "the cell counts above are still three different populations and none divides "
            "another. The map places collectibles in one set of cells, the save's grid "
            "table declares another set of runtime cells, and the save has level records "
            "for a third -- including cells the grid table does not declare, some of which "
            "hold a collectible, which is what "
            "cells_holding_a_collectible_the_grid_table_does_not_declare counts. Read "
            "them side by side, not as a fraction."
        ),
        "map_actors_in_cells_the_newest_save_records": ctx.map_actors_in_recorded_cells,
        "of_those_the_newest_save_has_a_record_of": ctx.observed_map_actors,
        "cell_is_not_a_unit_of_coverage": (
            "a partition cell streams in pieces. Over all "
            f"{len(ctx.world.class_counts)} map-placed classes, the newest save has a level "
            f"record for cells holding {ctx.map_actors_in_recorded_cells} map actors and a "
            f"record of only {ctx.observed_map_actors} of them "
            f"({100 * ctx.observed_map_actors / max(ctx.map_actors_in_recorded_cells, 1):.0f}"
            "%), so "
            "'the cell has been visited' does not mean 'its contents are known'. That is "
            "why unknown is a per-collectible state here and not a per-cell one, and why "
            "the cell counts above are context rather than the coverage figure."
        ),
    }
