"""The merge: map placements plus save facts become the rows and ``_meta``, every number in
``_meta`` a by-product of the measurements that made the rows."""

from __future__ import annotations

import collections
from collections.abc import Callable

from pioneersav import FIRST_MODERN_BODY
from satisfactory_mcp.core.collectible_rows import MapPlacement
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.jsontypes import JsonObject
from tools.collectibles.catalog import CATEGORIES, POSITION_TOLERANCE_CM
from tools.collectibles.context import BuildContext
from tools.collectibles.hazards import HazardWorld, hazard_context_meta
from tools.collectibles.identity import (
    identity_meta,
    measure_coincident_positions,
    measure_naming,
    measure_pedestals,
    naming_meta,
)
from tools.collectibles.map_read import (
    MapWorld,
    Placement,
    class_census_meta,
    placements_source_meta,
)
from tools.collectibles.respawn import measure_durability, measure_flora, respawn_meta
from tools.collectibles.saves import SaveFacts
from tools.collectibles.status import (
    build_rows,
    exploration_meta,
    measure_exploration,
    measure_older_save_staleness,
    measure_orphans,
    measure_status,
    position_agreement_meta,
    status_evidence_meta,
    status_source_meta,
)
from tools.collectibles.totals import (
    accounting_meta,
    measure_accounting,
    measure_per_category,
    totals_meta,
)

#: The measurements, in the order they run: each may read what the earlier ones measured.
MEASUREMENTS: tuple[Callable[[BuildContext], None], ...] = (
    measure_status,
    build_rows,
    measure_pedestals,
    measure_coincident_positions,
    measure_older_save_staleness,
    measure_orphans,
    measure_exploration,
    measure_naming,
    measure_durability,
    measure_flora,
    measure_per_category,
    measure_accounting,
)


def build(
    world: MapWorld,
    hazards: HazardWorld,
    saves: list[SaveFacts],
    *,
    store: IoStore,
    scripts: ScriptObjects,
    game_build: str | None,
    pyooz_version: str,
    readable_saves: list[SaveFacts],
    files_found: int,
    other_levels: list[JsonObject],
) -> tuple[list[MapPlacement], JsonObject]:
    """Turn map placements plus the session's saves into rows and ``_meta``."""
    session_saves = sorted(saves, key=lambda f: (f.ticks, f.play_seconds))
    row_placements = [p for p in world.placements if p.cls in CATEGORIES]
    by_key = {(p.cell, p.instance): p for p in row_placements}
    by_name: dict[str, list[Placement]] = collections.defaultdict(list)
    for placement in row_placements:
        by_name[placement.instance].append(placement)
    ctx = BuildContext(
        world=world,
        hazards=hazards,
        session_saves=session_saves,
        newest=session_saves[-1],
        store=store,
        scripts=scripts,
        game_build=game_build,
        pyooz_version=pyooz_version,
        readable_saves=readable_saves,
        files_found=files_found,
        other_levels=other_levels,
        row_placements=row_placements,
        by_key=by_key,
        duplicate_keys=len(row_placements) - len(by_key),
        duplicate_names=sum(len(v) - 1 for v in by_name.values()),
        sessions=collections.Counter(f.session for f in readable_saves),
        pre_partition=[f for f in session_saves if f.save_version < FIRST_MODERN_BODY],
    )
    for measure in MEASUREMENTS:
        measure(ctx)
    return ctx.rows, assemble_meta(ctx)


def assemble_meta(ctx: BuildContext) -> JsonObject:
    """``_meta``, one builder per key, in the key order the file has always had."""
    persistent = sum(1 for r in ctx.rows if r["cell"] == "Persistent_Level")
    return {
        "description": (
            "Every one-shot collectible the classes in totals.by_category place in the "
            "world -- power slugs, somersloops, Mercer spheres and their shrines, crashed "
            "drop pods, the loot caches at crash sites, the mushrooms -- with its exact "
            "position and whether the newest save says it is still there. One-shot is a "
            "measured property and not a label: respawn re-tests every run that no emitted "
            "class ever comes back, which is why the two regrowing plants are excluded and "
            "the mushroom is not. The row set is the map's own placement list for those "
            "classes; whether the class list itself is complete is a separate question, and "
            "accounting plus source.placements.other_levels_in_the_container are what a "
            "missing class would show up in."
        ),
        "why": (
            "The world is not saved. A save lists the collectibles still standing in cells "
            "it has written, and separately the map actors it considers gone; nothing in it "
            "states how many the map placed. The denominator therefore has to come from the "
            "map itself, which is what the placements below are; the saves supply only state."
        ),
        "source": source_meta(ctx),
        "derivation": derivation_meta(),
        "respawn": respawn_meta(ctx),
        "identity": identity_meta(ctx),
        "class_census": class_census_meta(ctx.world),
        "hazard_context": hazard_context_meta(ctx.hazards, ctx.rows),
        "not_derived": not_derived_meta(),
        "status_evidence": status_evidence_meta(ctx),
        "position_agreement": position_agreement_meta(ctx),
        "totals": totals_meta(ctx),
        "exploration": exploration_meta(ctx),
        "naming": naming_meta(ctx),
        **accounting_meta(ctx),
        "join_key": "instance (matches save actor instanceName exactly)",
        "cell_note": (
            "'cell' is the cooked package the map places the actor in, and it is byte-"
            "identical to the save's own level record name -- which is what makes the join "
            f"exact. It is a world-partition cell for all but {persistent} "
            "rows, which the map puts in Persistent_Level itself. It is not a spatial box: "
            "one cell's actors can be far apart."
        ),
        "units": "centimetres; north is -Y, east is +X, up is +Z",
    }


def source_meta(ctx: BuildContext) -> JsonObject:
    """``_meta.source``: where the placements come from, and where the states do."""
    return {
        "placements": placements_source_meta(
            ctx.world,
            ctx.store,
            ctx.scripts,
            game_build=ctx.game_build,
            pyooz_version=ctx.pyooz_version,
            other_levels=ctx.other_levels,
        ),
        "status": status_source_meta(ctx),
    }


def derivation_meta() -> JsonObject:
    """``_meta.derivation``: how each row field is read."""
    return {
        "placed": (
            "an actor export of one of these classes in a cooked GameLevel01 package. "
            "EXACT for the classes named: it is the map's own placement list and not a "
            "count of sightings. It says nothing about classes not named -- see "
            "class_census and not_classified."
        ),
        "collected": "the newest save's destroyed-actor list names this (cell, instance)",
        "present": (
            "the newest save has a live actor header at this (cell, instance) whose "
            f"position is within {POSITION_TOLERANCE_CM:.0f} cm of the map's"
        ),
        "unknown": (
            "neither. The game has never had that actor loaded while writing a save, so "
            "nothing on disk says whether it is still standing. NOT 'present'."
        ),
        "position": (
            "the actor's root SceneComponent transform, composed up the AttachParent "
            "chain with class component-template defaults filled in, in world-space cm"
        ),
        "class": "the export's class, read from the package. Never inferred from a name.",
        "looted": (
            "mHasBeenLooted off the live drop-pod body. null where the pod's state is "
            "not present, because an unobserved pod's loot flag is unobserved too."
        ),
        "contents": (
            "the loot cache's own mPickupItems: an FInventoryStack whose FInventoryItem "
            "carries no tagged members, so the item class is an FPackageIndex read "
            "through the package's import map."
        ),
        "unlock_cost": (
            "the drop pod's own mUnlockCost. cost_type is null where the pod does not "
            "serialise it, which means it holds the class default -- and since other "
            "pods explicitly write both Item and Power, the default is a third value "
            "whose name is nowhere in the cooked assets. Not guessed."
        ),
        "attached_to": (
            "the instance whose root component this actor's root is attached to, from "
            "the map's own AttachParent chain -- exact, not a distance guess"
        ),
        "hazard": (
            "INFERENCE, not placement. Geometry between this collectible and other map "
            "actors, plus the radii those actors' own classes declare. See "
            "hazard_context for what each key means and what it does not."
        ),
    }


def not_derived_meta() -> JsonObject:
    """``_meta.not_derived``: context a consumer may want and this file does not produce."""
    return {
        "what": (
            "context a consumer may want that this file does NOT produce, with the "
            "reason. Listed so an absence reads as a decision rather than an oversight."
        ),
        "cave": (
            "not derivable. The nearest thing in the assets is the FGAmbientVolume sound "
            "zones, of which the ones naming a cave ambient setting are placed for audio "
            "and so neither cover every cave nor stop at its mouth. A cave is a shape a "
            "human recognises; nothing in the cooked packages draws it."
        ),
        "reachability": (
            "not derivable. There is no navmesh in the cooked packages and no jetpack, "
            "hover-pack or hazmat gate anywhere in them. Height above local ground looks "
            "promising and then inverts -- the hardest-to-reach collectibles sit LOWER "
            "above their local ground than the easy ones, because what the measure "
            "actually picks up is biome altitude. z is in every row; any verdict built "
            "on it would be this file's guess wearing data's clothes."
        ),
        "rotation": (
            "not emitted. It was extracted and validated against the saves' own "
            "quaternions, but nothing downstream asks which way a slug faces, so it is "
            "left out rather than carried as 4 more numbers on every row."
        ),
    }
