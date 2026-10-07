"""What the measurements share: the inputs ``build`` sets once, then one field group per
measurement, each filled by exactly one ``measure_*`` function."""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field

from satisfactory_mcp.core.collectible_rows import MapPlacement
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.jsontypes import JsonObject
from tools.collectibles.catalog import POSITION_TOLERANCE_CM, ActorKey, Position
from tools.collectibles.hazards import HazardWorld
from tools.collectibles.map_read import MapWorld, Placement
from tools.collectibles.saves import SaveFacts


def agrees_with_map(position: Position, placement: Placement) -> bool:
    """Whether a save's position describes this map actor rather than another one."""
    return math.dist(position, placement.position) <= POSITION_TOLERANCE_CM


@dataclass
class BuildContext:
    """The inputs, then the measured fields in the order the measurements run.

    The measured fields are ``init=False`` with no default, so reading one before its
    measurement has run is an AttributeError rather than a silently empty value.
    """

    world: MapWorld
    hazards: HazardWorld
    #: The largest session's saves, oldest first by save clock.
    session_saves: list[SaveFacts]
    newest: SaveFacts
    store: IoStore
    scripts: ScriptObjects
    game_build: str | None
    pyooz_version: str
    #: Every save that parsed, whichever session it belongs to.
    readable_saves: list[SaveFacts]
    files_found: int
    other_levels: list[JsonObject]
    #: The placements of an emitted class: one row each.
    row_placements: list[Placement]
    by_key: dict[ActorKey, Placement]
    duplicate_keys: int
    duplicate_names: int
    sessions: collections.Counter[str]
    pre_partition: list[SaveFacts]

    # measure_status
    present: dict[ActorKey, bool | None] = field(init=False)
    displaced: list[tuple[ActorKey, float]] = field(init=False)
    orphan_live: collections.Counter[str] = field(init=False)
    agreeing: list[float] = field(init=False)
    collected: set[ActorKey] = field(init=False)
    destroyed_others: collections.Counter[str] = field(init=False)
    recoverable: int = field(init=False)

    # build_rows
    rows: list[MapPlacement] = field(init=False)

    # measure_pedestals
    pedestals: JsonObject = field(init=False)

    # measure_coincident_positions
    coincident_pairs: set[tuple[ActorKey, ActorKey]] = field(init=False)
    coincident_by_category: collections.Counter[str] = field(init=False)
    coincident_states_differ: int = field(init=False)
    coincident_both_have_a_placement_id: int = field(init=False)
    coincident_pair_count: int = field(init=False)

    # measure_older_save_staleness
    keys_only_older_saves_state: set[ActorKey] = field(init=False)
    displaced_by_version: dict[int, set[ActorKey]] = field(init=False)
    joined_by_save: dict[str, int] = field(init=False)
    orphan_live_over_all_saves: collections.Counter[str] = field(init=False)
    orphan_versions: dict[ActorKey, set[int]] = field(init=False)
    pre_partition_keys: set[ActorKey] = field(init=False)
    pre_partition_cells: collections.Counter[str] = field(init=False)

    # measure_orphans
    orphan_old_layout: int = field(init=False)
    orphan_renamed: int = field(init=False)
    orphan_unexplained: int = field(init=False)

    # measure_exploration
    collectible_cells: set[str] = field(init=False)
    cells_no_record: set[str] = field(init=False)
    unknown_rows: list[MapPlacement] = field(init=False)
    unknown_no_record: int = field(init=False)
    observed_map_actors: int = field(init=False)
    map_actors_in_recorded_cells: int = field(init=False)
    map_cells: set[str] = field(init=False)

    # measure_naming
    prefix_rule_wrong: int = field(init=False)
    prefix_rule_silent: int = field(init=False)
    rows_by_foreign_stem: collections.Counter[str] = field(init=False)
    glued_index: list[Placement] = field(init=False)
    glued_index_by_category: collections.Counter[str] = field(init=False)
    glued_index_destroyed: list[Placement] = field(init=False)
    glued_index_destroyed_unresolved: int = field(init=False)
    keys_any_save_mentions: set[ActorKey] = field(init=False)
    coincident_both_recorded: int = field(init=False)

    # measure_durability
    pairs_compared: int = field(init=False)
    pairs_skipped: int = field(init=False)
    destroyed_observations: collections.Counter[str] = field(init=False)
    left_the_list: collections.Counter[str] = field(init=False)
    revived: collections.Counter[str] = field(init=False)
    revived_displaced: collections.Counter[str] = field(init=False)
    coexisting: collections.Counter[str] = field(init=False)
    coexisting_by_version: collections.Counter[int] = field(init=False)
    newest_coexisting: int = field(init=False)

    # measure_flora
    flora: dict[str, JsonObject] = field(init=False)
    flora_unreadable: int = field(init=False)

    # measure_per_category
    per_category: JsonObject = field(init=False)

    # measure_accounting
    excluded: JsonObject = field(init=False)
    excluded_placements: int = field(init=False)
    unclassified: collections.Counter[str] = field(init=False)
