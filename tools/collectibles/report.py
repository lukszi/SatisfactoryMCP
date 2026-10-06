"""The run's closing summary, read back out of the ``_meta`` it has just written: one line per
check, so a regression shows on the console before anyone opens the file."""

from __future__ import annotations

from pathlib import Path

from mapgen.common import ROOT
from tools.collectibles.catalog import CATEGORIES, HAZARD_RADIUS_CM


def print_summary(meta: dict, rows: list[dict], out_path: Path) -> None:
    totals = meta["totals"]
    shown = out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path
    print(f"\nwrote {shown}  {len(rows)} placements  {out_path.stat().st_size} B")
    _print_category_table(totals)
    _print_identity(meta["identity"], totals)
    for cls, entry in meta["class_census"]["classes"].items():
        print(
            f"  pickup class {cls:34} {entry['placed_by_the_map']:>5} placed  "
            f"{'native' if entry['native_class'] else 'blueprint':<9} -> "
            f"{entry['emitted_as'] or 'NOT EMITTED'}"
        )
    tally = meta["accounting"]
    print(
        f"accounting: {tally['emitted_as_rows']} rows + {tally['excluded_on_purpose']} "
        f"excluded on purpose + {tally['not_classified']} not classified = "
        f"{tally['map_actors_in_gamelevel01']} map actors"
        f"{'' if tally['adds_up'] else '  -- DOES NOT ADD UP, a class is in two buckets'}"
    )
    _print_respawn(meta["respawn"])
    agree = meta["position_agreement"]
    print(
        f"positions: {agree['matched']} accepted records agree with the map to a median of "
        f"{agree['median_cm']} cm, p90 {agree['p90_cm']} cm, worst {agree['max_cm']} cm "
        f"({agree['over_1cm']} over 1 cm, all of them cells the game has not re-migrated)"
    )
    for category, check in totals["pedestals"].items():
        print(
            f"pedestals: {check['rows']} {category} rows attach to "
            f"{check['distinct_parents']} distinct parents, {check['parent_category']}"
            f"{'' if check['one_to_one'] else '  -- NOT 1:1, see _meta'}"
        )
    _print_status(meta["status_evidence"])
    _print_coverage(meta, totals)


def _print_category_table(totals: dict) -> None:
    print(f"{'category':30}{'placed':>7}{'collected':>10}{'present':>9}{'unknown':>9}")
    for category, entry in totals["by_category"].items():
        extra = []
        if entry.get("present_and_looted") is not None:
            extra.append(f"{entry['present_and_looted']} of those present already looted")
        if entry.get("distinct_item_types") is not None:
            extra.append(f"{entry['items_in_total']} items of {entry['distinct_item_types']} types")
        tail = f"   ({'; '.join(extra)})" if extra else ""
        print(
            f"{category:30}{entry['placed']:>7}{entry['collected']:>10}"
            f"{entry['present']:>9}{entry['unknown']:>9}{tail}"
        )
    print(
        f"{'TOTAL':30}{totals['rows']:>7}{totals['collected']:>10}"
        f"{totals['present']:>9}{totals['unknown']:>9}"
    )


def _print_identity(ident: dict, totals: dict) -> None:
    print(
        f"\nidentity: key {ident['key']}, {ident['duplicate_keys']} duplicate keys over "
        f"{totals['rows']} rows; {ident['map_actors_with_a_blueprint_class']} of "
        f"{ident['map_actors_in_gamelevel01']} map actors are blueprint-classed; "
        f"{ident['distinct_instance_names']} distinct names, "
        f"{ident['instance_names_carrying_two_classes']} carrying two classes, "
        f"{ident['names_with_the_map_s_placement_id_distinct']} of "
        f"{ident['names_with_the_map_s_placement_id']} placement-id names distinct; "
        f"{ident['rows_within_1m_of_another_row_in_the_same_category']} rows within 1 m of "
        "another in the same category"
    )


def _print_respawn(respawn: dict) -> None:
    durable = respawn["durability"]
    left = durable["keys_that_left_the_destroyed_list_by_class"]
    both = durable["rows_a_single_save_lists_as_both_destroyed_and_live_by_class"]
    print(
        f"respawn: over {durable['consecutive_same_build_pairs_compared']} same-build save "
        f"pairs ({durable['pairs_skipped_because_the_build_changed']} skipped at a build "
        f"change), {sum(left.values())} keys left a destroyed list "
        f"({sum(n for c, n in left.items() if c in CATEGORIES)} of a class emitted here) and "
        f"{sum(durable['rows_destroyed_then_live_again_by_class'].values())} rows went "
        "destroyed -> live again; "
        f"{sum(both.values())}"
        " destroyed/live coexistences, "
        f"{durable['in_the_newest_save']} of them in the newest save"
    )
    for cls, entry in respawn["flora"].items():
        print(
            f"  {cls:20} {entry['live_records_over_the_saves_used']:>7} records, "
            f"mNumRespawns on {entry['records_carrying']['mNumRespawns']}, "
            f"mUpdatedOnDayNr on {entry['records_carrying']['mUpdatedOnDayNr']}, "
            f"counter +{entry['counter_rose']}/-{entry['counter_fell']}, "
            f"max {entry['highest_mNumRespawns_seen']} -> "
            f"{'REGROWS, excluded' if entry['respawns'] else 'one-shot'}"
            f"{'' if entry['respawns'] else ', emitted as ' + str(entry['emitted_as'])}"
        )


def _print_status(evidence: dict) -> None:
    print(
        f"status: {evidence['live_records_accepted']} live records accepted, "
        f"{evidence['live_records_displaced']} displaced by the 52->60 rename and ignored "
        f"({evidence['displaced_records_a_position_match_could_re_attach']} of those could be "
        "re-attached by position, reported not applied); "
        f"{evidence['destroyed_entries_that_are_rows_here']} of "
        f"{evidence['destroyed_entries_in_newest_save']} destroyed entries are rows here; "
        f"{evidence['live_records_with_no_map_row']} live records of an emitted class have no "
        f"map row ({evidence['live_records_with_no_map_row_by_class']}); "
        f"{evidence['rows_only_older_saves_could_state']} rows only an older save could state"
    )
    print(
        f"completeness: over every save used, {evidence['orphan_keys_distinct']} distinct "
        f"(cell, instance) of an emitted class have no row here -- "
        f"{evidence['orphan_keys_only_a_pre_partition_save_names']} only a pre-partition save "
        f"names, {evidence['orphan_keys_whose_name_the_map_places_in_another_cell']} are the "
        f"52->60 rename, {evidence['orphan_keys_unexplained']} unexplained"
        f"{'' if not evidence['orphan_keys_unexplained'] else '  -- A ROW MAY BE MISSING'}"
    )


def _print_coverage(meta: dict, totals: dict) -> None:
    explore = meta["exploration"]
    print(
        f"exploration: the saves have observed {explore['collectibles_observed']} of "
        f"{totals['rows']} collectibles ({explore['collectibles_observed_pct']}%); of the "
        f"{explore['collectibles_unknown']} unknown, "
        f"{explore['unknown_in_a_cell_with_no_level_record_in_any_save']} are in a cell no "
        "save has a record of and the rest in cells streamed only in part"
    )
    context = meta["hazard_context"]["rows_touched"]
    print(
        f"hazard context (reporting radius {HAZARD_RADIUS_CM / 100:.0f} m, inference not "
        f"placement): {context['with_a_hostile_in_reporting_radius']} rows have a hostile "
        f"near, {context['with_a_hostile_whose_own_radius_contains_them']} sit inside a "
        f"spawner's or hatcher's OWN declared radius, "
        f"{context['inside_a_spore_flower_damage_sphere']} inside a spore flower's declared "
        f"damage sphere, {context['with_gas_in_reporting_radius']} have gas near, "
        f"{context['with_uranium_in_reporting_radius']} uranium, "
        f"{context['with_a_nuclear_hog_spawner_in_reporting_radius']} a nuclear hog; "
        f"{context['with_no_hazard_context_at_all']} rows have none of it"
    )
    naming = meta["naming"]
    print(
        f"naming: a longest-prefix rule over instance names would get "
        f"{naming['prefix_rule_wrong']} of {naming['prefix_rule_checked']} classes wrong and "
        f"{naming['prefix_rule_silent']} not at all; the {naming['glued_index_names']} "
        f"^BP_WAT[0-9] names resolve to {naming['glued_index_by_category']}, and the "
        f"{naming['glued_index_destroyed_in_newest_save']} of them the newest save has "
        f"destroyed resolve to {naming['glued_index_destroyed_resolved']} with "
        f"{naming['glued_index_destroyed_unresolved']} unresolved"
    )
