"""The class catalog and the constants every stage of the collectibles generator shares.

Which class is a row, the per-category notes and the deliberate exclusions are data in
``catalog.toml``; docs/world-collectibles.md says how each tolerance was measured.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import cast

_CATALOG: dict[str, object] = tomllib.loads(
    Path(__file__).with_name("catalog.toml").read_text(encoding="utf-8")
)


def _strings(name: str) -> dict[str, str]:
    """The catalog's ``[name]`` table, refused unless every value is a string."""
    table = _CATALOG[name]
    if not isinstance(table, dict):
        raise TypeError(f"catalog.toml [{name}] is not a table")
    pairs = cast("dict[str, object]", table)
    out = {key: value for key, value in pairs.items() if isinstance(value, str)}
    if len(out) != len(pairs):
        raise TypeError(f"catalog.toml [{name}] holds a value that is not a string")
    return out


#: Map actor class -> the category this table reports.
CATEGORIES = _strings("categories")

#: Category -> the note carried into ``_meta.totals.by_category``.
CATEGORY_NOTES = _strings("category_notes")

#: Map-placed class left out on purpose -> why, reported with its count in ``_meta.excluded``.
EXCLUDED = _strings("excluded")

#: ``(cell, instance)``: the one key unique over every map-placed actor.
ActorKey = tuple[str, str]

#: A world position in centimetres.
Position = tuple[float, float, float]

#: The classes whose rows carry class-specific fields.
DROP_POD_CLASS = "BP_DropPod_C"
LOOT_CACHE_CLASS = "FGItemPickup_Spawnable"
MUSHROOM_CLASS = "BP_Shroom_01_C"

#: The save's actor instanceName is this plus the map export's name, byte for byte.
INSTANCE_PREFIX = "Persistent_Level:PersistentLevel."

#: The harvestable plants probed for respawn machinery; the probe decides which earn rows.
RESPAWN_PROBE = {"BP_BerryBush_C", "BP_NutBush_C", MUSHROOM_CLASS}

#: ``mSavedNumItems`` is listed because it is a fixed yield that reads like a countdown.
RESPAWN_PROPERTIES = ("mNumRespawns", "mUpdatedOnDayNr", "mSavedNumItems")

#: Gas pillars ship as numbered blueprints; matched so a new one is not silently ignored.
GAS_PILLAR = re.compile(r"^BP_GasPillar_\d+_C$")

#: Classes read for hazard context only. None of them is ever a row.
HAZARD_CLASSES = {
    "FGDamageOverTimeVolume",
    "BP_CreatureSpawner_C",
    "Char_CrabHatcher_C",
    "Char_BigCrabHatcher_C",
    "BP_SporeFlower_C",
    "BP_VolumeGas_01_C",
    "BP_ResourceNode_C",
    "BP_ResourceDeposit_C",
}

#: How far a save's live position may sit from the map's and still describe that actor.
#: It falls in the gap between the accepted and the rejected populations.
POSITION_TOLERANCE_CM = 100.0

#: This generator's reporting horizon for hazards, not a game value: it gates distances and
#: species lists, never a verdict.
HAZARD_RADIUS_CM = 5000.0

#: Read as truthy: the byte the game writes for true is 1 or 16 depending on saveVersion.
LOOTED_PROPERTY = "mHasBeenLooted"

#: Not radioactive itself, so it is reported as its own reason beside uranium.
NUCLEAR_HOG = "Desc_HogNuclear_C"

#: Trailing decoration a placement counter leaves on an instance name. Used only to measure
#: how wrong a name-based class rule would be, never to assign a class.
NAME_TAIL = re.compile(r"(_UAID_[0-9A-Fa-f]+)?(_\d+)?$")

#: A name whose digit is glued to the stem, so no split tells BP_WAT1 from BP_WAT2.
GLUED_WAT = re.compile(r"^BP_WAT\d")

#: The map's own placement id: a sufficient mark of a map placement, not a necessary one.
PLACEMENT_ID_MARK = "_UAID_"
