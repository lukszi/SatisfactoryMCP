# World collectibles

`data/world_collectibles.json` lists every one-shot collectible the map places — power slugs,
somersloops, Mercer spheres and their shrines, crashed drop pods, the loot caches round the crash
sites, the mushrooms — with its position and whether the newest save says it is still there.
Regenerate it with

    uv run --extra gen python tools/gen_world_collectibles.py <saves dir>

The script is a thin entry point; the code is the `tools/collectibles/` package. The table's own
`_meta` carries every number this page talks about, re-measured on each run.

## Where the denominator comes from

A save cannot list map-placed collectibles by what exists. It records the **negative**: which
map-placed actors are gone. "250 slugs collected" has no denominator in a save. The denominator
comes out of the cooked world instead — 4,521 packages under `Map/GameLevel01`, one per
world-partition cell plus the persistent level — and the saves are asked only for status.

An actor is any export whose Outer is the package's `/Script/Engine.Level` export. That
structural rule is what reaches the natively classed actors: a native class is not a path but an
`FPackageObjectIndex` script import, a 62-bit hash resolved through `global.utoc`'s ScriptObjects
chunk, so no `/Game/` prefix test can match one. The loot caches (`FGItemPickup_Spawnable`) are
native, and an earlier revision missed every one of them for exactly that reason.

That `Map/GameLevel01` is the whole world is measured rather than assumed: every other `.umap`
in the container is walked with the same rule and reported, with how many actors of an emitted
class it places, under `_meta.source.placements.other_levels_in_the_container`.

## Three states, because two would lie

* `collected` — the newest save's destroyed-actor list names this (cell, instance).
* `present` — the newest save has a live actor header at this key whose position agrees with
  the map's to within `POSITION_TOLERANCE_CM`.
* `unknown` — neither. The game has never had that actor loaded while saving, so nothing on disk
  says whether it is still standing.

All three are printed per category, so no two of them can be added up into the third. A drop pod
stays in the world after it is looted, so its rows carry `looted` beside `state`.

The newest save alone decides, and that it can is measured: the union over every save of the
session resolves no row it cannot (`rows_only_older_saves_could_state`). The newest save is
chosen by the clock it recorded, not by file mtime, because an autosave rewritten in place has a
fresh mtime and an old clock; and only the largest session's saves are used, because the union of
two sessions is not a world.

The older saves are read as evidence for `_meta.respawn`, which re-tests the premise under the
whole file — that a collected thing stays gone. A key may leave a destroyed list only between
saves of the same build (a build that re-issues instance names makes keys vanish without anything
coming back), and a key a save called destroyed must not turn up live, at the map's position, in a
later save. The position gate matters: a player-dropped crate can share a bare name with a map
cache and sit 100 m away. A single save naming one key both destroyed and live is the game's own
migration rather than a respawn — before the world was partitioned the bare name `BP_Crystal1`
belonged to two slugs in two levels — and should one ever appear in the newest save, the destroyed
list wins.

The three harvestable plants are probed for respawn machinery on every record. Berry and nut
bushes carry `mNumRespawns` and `mUpdatedOnDayNr` and regrow, so they are excluded; the mushroom
carries neither and is a row. `mSavedNumItems` is a plant's fixed yield, never a countdown: every
nut bush writes 5.

## The key is (cell, instance)

`(cell, instance)` is unique over every map-placed actor in `GameLevel01`; the bare instance
name is not. Between saveVersion 52 and 60 the level was edited — actors renamed, moved between
cells, nudged by centimetres — and the game migrates a cell's saved records only when that cell
is next streamed, so a stale record's key can now belong to a different map actor. A live record
is therefore accepted only if its position agrees with the map's, and the rejects are counted in
`_meta.status_evidence.live_records_displaced`. A displaced record that one unambiguous nearby
placement could claim is counted, never re-attached: state has one derivation.

The save's `instanceName` is `Persistent_Level:PersistentLevel.` plus the map export's name, byte
for byte, which is what makes `join_key` exact. An instance name never decides a class: the digits
are a placement counter, and `_meta.naming` scores how wrong a name rule would be. The `_UAID_`
placement id appears on names the world-partition cooker issued and not on names inherited from
older hand-placed actors, so it marks a map placement sufficiently but not necessarily.

A shrine is the pedestal under a Mercer sphere or a somersloop, attached to its root across an
actor boundary. The pairing is checked 1:1 every run (`totals.pedestals`): were it not, summing
the categories would count one find twice.

## Positions: two traps

Both move a shrine 73 cm:

* A shrine's root `SceneComponent` is attached to the sphere's root, so its `RelativeLocation`
  is not a world position and the chain has to be composed — rotator to quaternion, parent scale
  applied.
* A component's transform is serialised on the placed instance only where it differs from its
  class template, and `BP_WAT2`'s root carries `RelativeScale3D = 2.7` in the class, so the
  template is read from the class `.uasset`'s `<Name>_GEN_VARIABLE` export.

`_meta.position_agreement` re-measures the result against the save's own live actors every run.
`POSITION_TOLERANCE_CM` (1 m) falls in the gap between two populations rather than inside either:
accepted records agree to a fraction of a centimetre, or to tens of them where the game has not
re-migrated a cell, while the rejects run from just over a metre to kilometres
(`status_evidence.displaced_gap_cm`).

## Hazard context is inference

It sits in each row's own `hazard` object, kept apart from the placement. Where the game states a
radius — a spore flower's damage sphere, a spawner's `mSpawnRadius`, a crab hatcher's
`mDetectionRadius` — the containment test uses that number and is a fact. Where it does not, the
row carries the distance and no verdict. `HAZARD_RADIUS_CM` (50 m) is this generator's reporting
horizon, sized against how far a gas volume's own `mProximityPillarWorldLocations` reach
(`sources.gas_field_own_span_cm`); the lookup grid is wider, because a spawner's own radius can
reach further.

Hostile means `mIsPassiveCreature` is not set on the creature's class default object; the flag is
serialised on the passive creatures and on none of the hostile ones. Radioactivity is
`mRadioactiveDecay` on the resource classes the map's nodes and deposits name, which closes the
set: manufactured radioactive parts do not exist until a player makes them. The nuclear hog
carries no decay of its own, so it is a separate reason. `FGDamageOverTimeVolume` looks like the
gas channel and is the world-boundary kill box instead, which its resolved `mDotClass` shows.

"Is it in a cave" and "can you reach it without a jetpack" are properties of no actor and are not
derived at all (`_meta.not_derived`).

## Nothing asserted where it can be counted

`_meta` carries the class census, the respawn probe that decides which plants earn rows, the
identity checks, and the accounting that puts every map-placed class in exactly one of a
category, `_meta.excluded` or `_meta.not_classified`. An exclusion whose class the map no longer
places is printed as stale. The coordinates are facts about Coffee Stain's map read from the
installed game, with no third-party world table involved in any form.

## Reading the cooked assets

* A loot cache's `mPickupItems` is an `FInventoryStack` — `{FInventoryItem Item; int32
  NumItems}` — and `FInventoryItem` has no tagged members: its payload is an `int32 ItemClass`,
  an `FPackageIndex` into the import map, then an `int32 ItemState`.
* A drop pod's `mUnlockCost` is `FGDropPodUnlockCost {CostType, ItemCost, PowerConsumption}`.
  Other pods write both `Item` and `Power` explicitly, so a pod that writes no `CostType` holds a
  third default value whose name is nowhere in the cooked assets; it is emitted as null.
* A drop pod's `mHasBeenLooted` is read as truthy: the byte for true is 1 on saveVersion 52
  bodies and 1 or 16 on version 60 ones.
* Transforms are composed only for the emitted and the hazard classes; the export table alone
  settles the class histogram, and property parsing is most of a run's cost.

## Reading the saves

Everything below a save's header is version-gated on its own `save_version`: a pre-1.0 body has
a 48-byte chunk preamble and a flat level list. Only the level walk is eager (0.23 s against
1.99 s for a full parse, the property blocks being 86% of the cost); property blocks are decoded
by offset for the drop pods and the probed plants only. The game drops a 105-byte
`ServerManager_V2.sav` beside the real saves; it is not a save and is skipped as unreadable.

## The package

| Module | Holds |
| --- | --- |
| `catalog.py`, `catalog.toml` | which class is a row, the category notes and the exclusions (data); the shared constants |
| `rows.py` | the shape of a row and of the objects nested in it |
| `stats.py` | how `_meta` carries numbers: a distance spread, a count table, typed values as JSON |
| `map_read.py` | the map walk; `_meta.source.placements` and `_meta.class_census` |
| `saves.py` | one save's facts |
| `hazards.py` | hazard sources, the per-row hazard block, `_meta.hazard_context` |
| `context.py` | `BuildContext`, the state the measurements share |
| `status.py` | rows and their state, the older saves, orphans, exploration, and their `_meta` blocks |
| `identity.py` | pedestals, coincident rows, naming, and their `_meta` blocks |
| `respawn.py` | the durability test and the flora probe, `_meta.respawn` |
| `totals.py` | per-category tallies and the accounting |
| `build.py` | the measurement order and the `_meta` key order |
| `report.py`, `command.py` | the console summary, and the command itself |

Each `_meta` block is built beside the measurement it reports. The measurements run in the fixed
order `build.MEASUREMENTS` gives, because each may read what an earlier one left on the context.
