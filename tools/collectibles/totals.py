"""The tallies: per category, and the accounting that puts every class the map places in
exactly one of a category, ``excluded`` or ``not_classified``."""

from __future__ import annotations

import collections

from satisfactory_mcp.core.collectible_rows import MapPlacement
from satisfactory_mcp.core.jsontypes import JsonObject
from tools.collectibles.catalog import (
    CATEGORIES,
    CATEGORY_NOTES,
    EXCLUDED,
    GAS_PILLAR,
    MUSHROOM_CLASS,
    PLACEMENT_ID_MARK,
)
from tools.collectibles.context import BuildContext
from tools.collectibles.stats import by_count, ranked


def measure_per_category(ctx: BuildContext) -> None:
    """Per category: placed is exact; collected, present and unknown are what the saves saw."""
    ctx.per_category = {
        category: _category_entry(ctx, category) for category in sorted(set(CATEGORIES.values()))
    }


def _category_entry(ctx: BuildContext, category: str) -> JsonObject:
    mine = [r for r in ctx.rows if r["category"] == category]
    placements = [p for p in ctx.row_placements if CATEGORIES[p.cls] == category]
    entry: JsonObject = {
        "placed": len(mine),
        "collected": sum(1 for r in mine if r["state"] == "collected"),
        "present": sum(1 for r in mine if r["state"] == "present"),
        "unknown": sum(1 for r in mine if r["state"] == "unknown"),
        "rows_any_save_mentions": sum(
            1 for p in placements if (p.cell, p.instance) in ctx.keys_any_save_mentions
        ),
        "with_the_map_s_own_placement_id": sum(
            1 for p in placements if PLACEMENT_ID_MARK in p.instance
        ),
    }
    if category == "crashed_drop_pod":
        entry.update(_drop_pod_tally(mine))
    if category == "loot_cache":
        entry.update(_loot_cache_tally(mine))
    if category == "mushroom":
        entry["contents_read"] = sum(1 for r in mine if r.get("contents"))
        entry["respawns"] = ctx.flora[MUSHROOM_CLASS]["respawns"]
        entry["respawn_evidence"] = f"_meta.respawn.flora.{MUSHROOM_CLASS}"
    if category in CATEGORY_NOTES:
        entry["note"] = CATEGORY_NOTES[category]
    entry["class"] = next(c for c, cat in CATEGORIES.items() if cat == category)
    # None for a category the map no longer places: a game update must not kill the run.
    entry["class_path"] = next((p.class_path for p in placements), None)
    return entry


def _drop_pod_tally(pods: list[MapPlacement]) -> JsonObject:
    costs = [r["unlock_cost"] for r in pods if "unlock_cost" in r]
    return {
        "present_and_looted": sum(1 for r in pods if r.get("looted")),
        "unlock_cost_serialised": len(costs),
        "unlock_cost_with_an_item": sum(1 for cost in costs if cost.get("amount")),
        "unlock_cost_with_a_power_figure": sum(1 for cost in costs if cost.get("power_mw")),
        "unlock_cost_type_unserialised": sum(1 for cost in costs if cost["cost_type"] is None),
    }


def _loot_cache_tally(caches: list[MapPlacement]) -> JsonObject:
    items: collections.Counter[str] = collections.Counter()
    total = 0
    for row in caches:
        contents = row.get("contents")
        if contents is None:
            continue
        item, count = contents["item"], contents["count"] or 0
        if item:
            items[item] += count
            total += count
    return {
        "contents_read": sum(1 for r in caches if r.get("contents")),
        "distinct_item_types": len(items),
        "items_in_total": total,
        "items_by_type": by_count(items),
    }


def measure_accounting(ctx: BuildContext) -> None:
    """Bucket every class the map places that is not a row: excluded, or not classified."""
    excluded = {cls: (ctx.world.class_counts.get(cls, 0), why) for cls, why in EXCLUDED.items()}
    # Matched rather than listed, so a new numbered pillar is excluded with its count.
    for cls, count in ctx.world.class_counts.items():
        if GAS_PILLAR.match(cls):
            excluded[cls] = (
                count,
                (
                    "part of a gas field, and scenery rather than a pickup. Its position "
                    "feeds hazard.nearest_gas_cm; it declares no radius of its own, so no "
                    "containment test is derived from it."
                ),
            )
    # From the excluded dict, not EXCLUDED: the pillars join it by pattern above, and a class
    # must land in exactly one bucket.
    ctx.unclassified = collections.Counter(
        {
            cls: count
            for cls, count in ctx.world.class_counts.items()
            if cls not in CATEGORIES and cls not in excluded
        }
    )
    ctx.excluded = {
        cls: {"placed_by_the_map": placed, "why": why} for cls, (placed, why) in excluded.items()
    }
    ctx.excluded_placements = sum(placed for placed, _why in excluded.values())


def totals_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.totals``: the per-category table and the pedestal pairing."""
    return {
        "denominator": (
            "'placed' is EXACT for the classes this file names -- it is the map's own "
            "count. 'collected', 'present' and 'unknown' are OBSERVED, and only through "
            "this player's saves: they are what the saves know, and they shift as the "
            "player explores. placed = collected + present + unknown, always."
        ),
        "rows_any_save_mentions_note": (
            "rows some save on disk names at all, live or gone, however displaced. It is "
            "at least collected + present, and where it is 0 the class is not "
            "save-serialised: the game keeps no record of it, so it can be located and "
            "never state-tracked."
        ),
        "by_category": ctx.per_category,
        "rows": len(ctx.rows),
        "collected": sum(1 for r in ctx.rows if r["state"] == "collected"),
        "present": sum(1 for r in ctx.rows if r["state"] == "present"),
        "unknown": sum(1 for r in ctx.rows if r["state"] == "unknown"),
        "pedestals": ctx.pedestals,
        "pedestals_note": (
            "a shrine is the base the artifact above it stands on, named exactly by the "
            "map's own AttachParent -- see attached_to on each shrine row. The pairing is "
            "1:1, so a shrine is a second row about one find and NOT a second collectible: "
            "summing every category over-counts artifacts by the number of shrines."
        ),
    }


def accounting_meta(ctx: BuildContext) -> JsonObject:
    """``accounting``, ``excluded`` and ``not_classified``, with their notes, in ``_meta``'s
    order: every actor the map places, in exactly one of three buckets."""
    largest = ", ".join(f"{cls} ({count})" for cls, count in ranked(ctx.unclassified)[:5])
    return {
        "accounting": {
            "what": (
                "every actor the map places, split three ways. There is no fourth bucket and "
                "no overlap, so adds_up being true is the check that a class cannot go "
                "missing the way the loot caches did -- silently, with nothing in the file to "
                "show for it."
            ),
            "map_actors_in_gamelevel01": ctx.world.actor_count,
            "emitted_as_rows": len(ctx.rows),
            "excluded_on_purpose": ctx.excluded_placements,
            "not_classified": sum(ctx.unclassified.values()),
            "adds_up": len(ctx.rows) + ctx.excluded_placements + sum(ctx.unclassified.values())
            == ctx.world.actor_count,
        },
        "excluded": ctx.excluded,
        "excluded_note": (
            "map-placed classes deliberately not emitted as rows, with the map's own count "
            "of each so 'not a collectible' can never be read as 'we missed it'. This list "
            "is a set of decisions, NOT a proof of completeness: a class absent from both "
            "this list and the rows is in not_classified with its count, and class_census is "
            "the check that specifically covers pickups."
        ),
        "not_classified": by_count(ctx.unclassified),
        "not_classified_note": (
            "every remaining actor class the packages under source.placements.level_read "
            "place, native and blueprint alike, with counts. What that buys is bounded and "
            "worth stating: every actor the walk FOUND lands in a category, in excluded or "
            "here, and accounting.adds_up checks the three against the map's own actor "
            "count -- so a class cannot go missing the way the loot caches did. It is not "
            "evidence that the walk found every actor, which is what "
            "source.placements.actors_read_but_given_no_transform, "
            "packages_that_failed_to_parse and packages_with_no_level_export are for, nor "
            "that this level is the only one -- "
            "see other_levels_in_the_container. "
            f"The largest are {largest}. "
            "One entry may read 'export:N': that is an actor whose class is an export of its "
            "own package rather than an import, reported as it is found rather than binned."
        ),
    }
