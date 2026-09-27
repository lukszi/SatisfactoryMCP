"""Save-file extractor. Runs as a SEPARATE PROCESS from the MCP server.

    python -m satisfactory_mcp.core.saveio.extract <path-to-sav> [--header-only]

Emits a JSON projection on stdout; diagnostics go to stderr so stdout stays clean. A
subprocess because the parser hard-fails on an unrecognised saveVersion and because a torn
autosave -- the file is rewritten in place every ~5 min while playing -- must not take the
server down, and because the projection is small enough to BE the test fixture, so the whole
suite runs with no game install and no 2.9 MB .sav in git. This module and not ``pioneersav``
owns the schema: the parser answers "what does this file say" and this answers "what does the
MCP server need", and it is the only place in the tree allowed to import a parser at all.

Property-access hazards handled here, all of which fail SILENTLY otherwise:
  * ComponentHeader has no typePath attribute at all.
  * BoolProperty is a raw uint8 where 16 means True (observed 16/1/0).
  * UE omits empty TArray SaveGame properties, so absent means empty, not missing.
  * ObjectReference defines __str__ but not __repr__, so any repr()-based search
    finds nothing.
  * mItemsPickedUp is a MapProperty keyed by player state containing an inner map.

There is one parser: ``pioneersav``. See the comment above the import.
"""

from __future__ import annotations

import collections
import json
import math
import os
import sys
import traceback
from pathlib import Path

# There is one parser. The vendored GPL-3.0 `sat_sav_parse` it replaced is deleted, and so is
# the `SATISFACTORY_SAVPARSE` switch that ran a save through both and diffed the projection.
#
# What that diff said at the moment the library was removed: the two agreed on **every
# projection key, leaf for leaf, on all 31 saves the vendored parser could read**. It can never
# be run again, so it is banked in `tests/fixtures/vendor_parity.json` as a per-save, per-key
# digest of what the vendored parser produced, and `test_savparse_parity.py` holds this parser
# to it. `pioneersav` additionally reads the 35 pre-1.0 saves the old one refused.
#
# Absolute, and the only import in this file: the parser is a top-level package beside
# `satisfactory_mcp`, so this module runs the same whether it is started with `-m` or as a
# plain file path.
import pioneersav

read_save_info = pioneersav.read_info
read_full_save = pioneersav.read_full_save
#: What "this save cannot be read" looks like. Resolved here rather than at the raise site so
#: that main()'s except clause names one thing.
PARSE_ERROR: tuple[type[BaseException], ...] = (pioneersav.ParseError,)

SCHEMA_VERSION = 22

#: The classes the game drops on the ground when items have nowhere else to go. Schema 18;
#: see `_crates`.
#:
#: One class, because ``AFGCrate`` (``FGCrate.h``) is the whole family: a death crate and a
#: dismantle crate are the same actor with a different ``mCrateType``, so the KIND is read off
#: a property rather than off a name.
#:
#: NOT the item pickups beside them. ``FGItemPickup_Spawnable`` and
#: ``BP_ItemPickup_Spawnable_C`` are map-placed loot, which is the ``collectibles`` layer's
#: business; a crate did not exist until the player made it exist, by dying or by dismantling
#: something with a full inventory.
CRATE_CLASSES = ("BP_Crate_C",)

#: ``EFGCrateType`` (``FGCrate.h``) as the projection spells it: the enum's own three values,
#: renamed only by dropping the ``CT_`` and lowercasing.
#:
#: ``none`` is the game's OWN third value and not a parse failure. ``mCrateType`` is a
#: ``SaveGame`` property, so UE omits it while it sits at the class default of ``CT_None``,
#: and a crate built before the game distinguished death from dismantle carries no type and
#: never will -- the property appears in no save below build 433351, and crates created under
#: builds 201717 and 186638 still read ``none`` under 495413. "Unknown kind" is therefore a
#: permanent, ordinary state of an old crate rather than something to resolve to ``death``.
CRATE_KINDS = {"CT_None": "none", "CT_DismantleCrate": "dismantle", "CT_DeathCrate": "death"}

#: The classes drawn as POLES by the power layer: where a wire can end at something the
#: player placed for the purpose of ending wires at it. Schema 17; see `_power`.
#:
#: Listed rather than matched on "PowerPole", for the reason PIPE_CLASSES and STORAGE_CLASSES
#: both give: ``Build_ConveyorPole_C`` and ``Build_PipelineSupport_C`` are poles by every
#: naming test and carry no power at all, while ``Build_PowerTowerPlatform_C`` carries no
#: "Pole" in its name and is the biggest piece of transmission a world has. The wall outlets
#: are here because they are wire endpoints and nothing else is; leaving them out would draw
#: their wires ending in mid-air.
#:
#: The Power Switch, Priority Power Switch and Power Storage are absent because no save here
#: contains one, so nothing can say what a connector offset or a glyph for it would be. Their
#: wires still DRAW: the geometry comes off the wire actor and not off what it lands on, so
#: such a world loses the switch's glyph and not the line running to it.
POWER_POLE_CLASSES = (
    "Build_PowerPoleMk1_C",
    "Build_PowerPoleMk2_C",
    "Build_PowerPoleMk3_C",
    "Build_PowerPoleWall_C",
    "Build_PowerPoleWall_Mk2_C",
    "Build_PowerPoleWallDouble_Mk2_C",
    "Build_PowerTowerPlatform_C",
)

#: The four pipeline classes that carry an ``mSplineData`` -- the fluid pipes, Mk1 and Mk2,
#: each in the ordinary and the ``NoIndicator`` variant a player gets when the flow indicator
#: is switched off. Listed rather than pattern-matched: ``Build_PipeHyper_C`` carries the
#: identical property and is a HYPERTUBE, which moves a player and no fluid at all, and
#: ``Build_PipelineSupport_C`` is a pole with a height and no geometry at all. See `_pipes`.
PIPE_CLASSES = (
    "Build_Pipeline_C",
    "Build_PipelineMK2_C",
    "Build_Pipeline_NoIndicator_C",
    "Build_PipelineMK2_NoIndicator_C",
)

_MANUFACTURER_HINTS = (
    "ConstructorMk1",
    "SmelterMk1",
    "FoundryMk1",
    "OilRefinery",
    "Packager",
    "ManufacturerMk1",
    "AssemblerMk1",
    "Blender",
    "HadronCollider",
    "Converter",
    "QuantumEncoder",
)
_EXTRACTOR_HINTS = (
    "MinerMk1",
    "MinerMk2",
    "MinerMk3",
    "OilPump",
    "WaterPump",
    "FrackingExtractor",
    "FrackingSmasher",
)
_GENERATOR_HINTS = (
    "GeneratorCoal",
    "GeneratorFuel",
    "GeneratorNuclear",
    "GeneratorBiomass",
    "GeneratorGeoThermal",
    "GeneratorIntegratedBiomass",
)
#: The pieces a belt run passes THROUGH: splitters, the smart and programmable splitters, and
#: mergers. Without them a map drawing belts alone shows a four-metre hole wherever a run is
#: split or joined.
#:
#: ``Build_ConveyorCeilingAttachment_C`` is NOT here despite the shared word: a ceiling mount
#: is a pole a belt hangs from, not a piece the items pass through.
_ATTACHMENT_HINTS = (
    "ConveyorAttachmentSplitter",
    "ConveyorAttachmentMerger",
)

#: The classes whose whole point is to hold items, keyed by the component they hold them in
#: (``StorageInventory``, always). Schema 15; see `_storage`.
#:
#: Listed, not matched on the word "Storage": every splitter and merger in the world owns a
#: component literally named ``StorageInventory``, holding the one to three items physically
#: inside the junction at the moment of the save. Those are items in TRANSIT, and counting
#: them would report the conveyor network twice, once as a route and once as a warehouse.
#:
#: Machine buffers are excluded and are not lost: an ``InputInventory`` / ``OutputInventory``
#: / ``FuelInventory`` is already on its own machine's record under ``buffers``, where it
#: means "this smelter is starved" rather than "the player owns this". So is the AWESOME
#: Shop's ``ShopInventory``, which is a catalogue, and the Space Elevator's intake, which
#: ``progression`` already reports against the phase.
STORAGE_CLASSES = (
    # The two the player means by "a container": 5 x 11 m either way, 24 and 48 slots.
    "Build_StorageContainerMk1_C",
    "Build_StorageContainerMk2_C",
    # The Personal Storage Box, the HUB's built-in container and the Blueprint Designer's.
    # All three hold stock the player put there, which is the only test that matters here.
    "Build_StoragePlayer_C",
    "Build_StorageIntegrated_C",
    "Build_StorageBlueprint_C",
    # The Dimensional Depot UPLOADER: the physical box that feeds the depot. Its contents are
    # what is waiting to be uploaded and are not the same number as ``depot``, which is the
    # FGCentralStorageSubsystem's central total -- 33 uploaders here against one subsystem.
    "Build_CentralStorage_C",
)

#: Owners whose ``StorageInventory`` counts as the player's stock but which are not in
#: STORAGE_CLASSES, matched on a word inside the instance name rather than by class.
#:
#: A Freight Wagon is a VEHICLE, not a buildable: it has no ``Build_`` class, no footprint in
#: the docs dump, and no save in the reference directory holds one, so its exact class name
#: cannot be read off anything here and the name match stands in for it.
_STORAGE_OWNER_HINTS = ("FreightWagon",)

#: The fluid half. A different record, not a different key: these hold a single fluid in an
#: ``mFluidBox`` float rather than a stack list, and the fluid's identity is not on the actor
#: at all -- it comes off the ``FGPipeNetwork`` that claims it, exactly as a pipe's does.
#:
#: ``mFluidBox`` is NOT a membership test, and that is why this is a list too: every pipe,
#: junction, pump and valve in the world carries one, holding the few m3 standing in the line.
#: A buffer is a class, not a property.
FLUID_BUFFER_CLASSES = (
    "Build_PipeStorageTank_C",  # Fluid Buffer, 400 m3
    "Build_IndustrialTank_C",  # Industrial Fluid Buffer, 2,400 m3
)

#: Classes that carry a factory building's own evidence -- a 300 s productivity monitor, or an
#: input/output/fuel buffer -- and for which this projection deliberately keeps no per-actor
#: record. **EDIT THIS LIST.** It is the whole reason the unfiled-class census below is silent,
#: so a class arriving that is not here is one nobody has looked at yet, which is exactly what
#: the census exists to say. Adding a name is a claim that its record is not wanted; the
#: alternative is a hint list above, which is a claim that it is.
#:
#: Measured, not guessed: across 18 saves spanning save versions 25 to 60, these six are the
#: ONLY classes reaching a drop point with that evidence on them. The other 68 unfiled
#: buildables -- every foundation, wall, belt, lift and catwalk -- carry none of it, and of the
#: 93 non-``Build_`` classes those saves hold, one does.
DISMISSED_FACTORY_CLASSES = frozenset(
    {
        # Moves fluid along a pipe the ``pipes`` layer already draws, and runs no recipe.
        "Build_PipelinePump_C",
        # A hypertube entrance. It moves the player, which is not production.
        "Build_PipeHyperStart_C",
        # The HUB. Its milestone intake is ``progression``'s business.
        "Build_TradingPost_C",
        # Its intake is already reported against the phase, by ``progression``.
        "Build_SpaceElevator_C",
        # The AWESOME Shop. Its inventory is a catalogue, not stock the player owns.
        "Build_ResourceSinkShop_C",
        # The Portable Miner, which the census found on its own first run: it really does
        # extract, carrying an ``mExtractResourceNode`` and an OutputInventory. It is dropped
        # anyway because it is not a buildable -- no ``Build_`` class, no FGBuildingDescriptor,
        # so nothing downstream can cost, size or draw one. Filing it under ``extractors``
        # would mean giving every consumer of that list a machine game data cannot describe.
        "BP_PortableMiner_C",
    }
)

#: How many class names the unfiled-class warning prints before it stops. One game update
#: cannot rename more than a handful of buildings, so a longer list is a parser that has
#: stopped recognising the format rather than a patch note.
UNFILED_CLASSES_SHOWN = 8

#: How far a span's curve is allowed to leave the straight line between its two control
#: points before the projection bothers to carry the tangents that bend it, in centimetres.
#:
#: One centimetre is the control points' OWN resolution -- they are rounded to whole
#: centimetres above -- so a curve that cannot depart its chord by a whole centimetre
#: describes something finer than the geometry it is drawn through records. It drops most
#: spans, which is what keeps schema 15 a 15% payload growth rather than a 54% one.
TANGENT_EPS_CM = 1.0

#: The peak of both cubic Hermite tangent basis functions on ``[0, 1]``: ``h10 = t^3-2t^2+t``
#: at ``t = 1/3`` and ``h11 = t^3-t^2`` at ``t = 2/3`` are each 4/27 in magnitude. See `_bulge`.
_HERMITE_PEAK = 4.0 / 27.0


def truthy(value) -> bool:
    """BoolProperty comes back as a uint8 where 16 is True. `v == 1` is wrong."""
    return bool(value)


def cls_of(type_path: str) -> str:
    return type_path.rsplit(".", 1)[-1] if type_path else ""


def ref_path(value) -> str | None:
    """instanceName of an ObjectReference, or None. Never use repr() on these."""
    p = getattr(value, "pathName", None)
    return str(p) if p else None


def ref_class(value) -> str | None:
    """Class name from an ObjectReference OR a bare asset-path string.

    Some structs store references as plain path strings rather than
    ObjectReference objects (e.g. the ``Item`` member of an inventory stack is
    ``[path, int]``), so both shapes must resolve.
    """
    p = ref_path(value)
    if p is None and isinstance(value, str) and value:
        p = value
    if not p:
        return None
    tail = p.rsplit(".", 1)[-1]
    return tail or None


def props(obj) -> dict:
    """properties is a list of [name, value] pairs; absent means empty."""
    out: dict = {}
    for entry in getattr(obj, "properties", None) or []:
        try:
            out[entry[0]] = entry[1]
        except (IndexError, TypeError):
            continue
    return out


def struct_fields(entry) -> dict:
    """Flatten one element of a StructProperty array into {field: value}.

    The parser emits each struct as ``[values, propertyTypes]``, where ``values`` is
    the list of ``[name, value]`` pairs and ``propertyTypes`` is a parallel list of
    ``[name, typeName, ...]``. Some nested structs arrive already flattened, so both
    shapes are accepted -- iterating the outer list blindly yields a list where a
    field name is expected and raises "unhashable type: 'list'".
    """
    if not isinstance(entry, list):
        return {}
    candidate = entry
    if len(entry) == 2 and all(isinstance(x, list) for x in entry):
        first = entry[0]
        if first and all(isinstance(p, list) and p and isinstance(p[0], str) for p in first):
            candidate = first
    out: dict = {}
    for pair in candidate:
        if isinstance(pair, list) and len(pair) >= 2 and isinstance(pair[0], str):
            out[pair[0]] = pair[1]
    return out


def iter_objects(save):
    """Yield (type_path, header, object) over every level.

    ComponentHeader lacks typePath, hence the getattr with a default -- attribute
    access would raise partway through a 44k-object walk.
    """
    for level in save.levels:
        headers = getattr(level, "actorAndComponentObjectHeaders", None) or []
        objects = getattr(level, "objects", None) or []
        for header, obj in zip(headers, objects):
            yield (getattr(header, "typePath", "") or ""), header, obj


def pos_of(header) -> list | None:
    p = getattr(header, "position", None)
    if not p:
        return None
    try:
        return [round(float(p[0]), 1), round(float(p[1]), 1), round(float(p[2]), 1)]
    except (TypeError, IndexError, ValueError):
        return None


def yaw_of(quat) -> float | None:
    """Top-down facing in degrees from a placement quaternion ``(x, y, z, w)``, or None.

    The convention, measured against tile-spaced foundation pairs rather than assumed:

    * Axis is world Z (up) and only Z. Every lightweight buildable has ``x == y == 0``
      exactly, and the ``Build_`` actors that do not are parts mounted on a wall -- flow
      indicators, ceiling attachments, wall poles -- never a machine. So one yaw is the whole
      rotation of everything a top-down or floor view draws.
    * Positive yaw turns +X towards +Y, in the same coordinates the projection's ``pos``
      reports, so it is directly comparable with ``atan2(dy, dx)`` between two positions.
    * Range ``(-180, 180]``, after one fold. A float32 quaternion, which is what an actor
      header carries, lands a half-turn on ``-179.999...``, so rounding alone would emit both
      ``-180.0`` and ``180.0`` for the same facing and a consumer bucketing yaws sees two.

    The general form is kept even though ``x == y == 0`` reduces it to ``2*atan2(z, w)``,
    because the wall-mounted actors do carry pitch and this is their yaw, not nonsense.

    A rotation that will not read comes back as None and never as 0.0 (schema 16): 0.0 is a
    MEASUREMENT meaning axis-aligned, which most of the world genuinely is, so null is the
    only way to say "facing unknown" distinguishably. ``extract`` counts what came back null
    into ``warnings``, so a save where the header decode is going wrong announces itself
    instead of looking like a tidy grid.
    """
    try:
        x, y, z, w = (float(v) for v in quat)
    except (TypeError, ValueError):
        return None
    deg = round(math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))), 2)
    return 180.0 if deg == -180.0 else deg


def header_info(path: str) -> dict:
    i = read_save_info(path)
    st = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "filename": os.path.basename(path),
        "session_name": i.sessionName,
        "save_identifier": i.saveIdentifier,  # stable per WORLD, groups saves
        "save_header_version": i.saveHeaderType,
        "save_version": i.saveVersion,
        "build_version": i.buildVersion,
        "play_duration_s": i.playDurationInSeconds,
        "save_datetime_ticks": i.saveDateTimeInTicks,
        "is_modded": truthy(getattr(i, "isModdedSave", False)),
        "is_creative": truthy(getattr(i, "isCreativeModeEnabled", False)),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
    }


#: What a run threw away, keyed by a sentence that reads with a count in front of it.
Drops = collections.Counter

#: How many distinct drop reasons ``warnings`` names before it stops naming them. A save
#: that is going wrong goes wrong in a handful of ways at once; a save that produces
#: twenty different reasons has a broken parser, and the first six say so just as well.
DROP_REASONS_SHOWN = 6

#: Ceiling on the per-chain stderr line below. One line per unreadable chain is fine for
#: the three a torn save produces and is a screenful for the 1,400 a version bump would.
CHAIN_NOTES_SHOWN = 5


def _drop_notes(drops: Drops) -> list[str]:
    """One ``warnings`` sentence per kind of record this extraction threw away.

    The projection is built by a dozen guards that ``continue`` past anything they cannot
    read, because a single undecodable spline must not cost the other 502 pipes. Silent, they
    would publish a SMALLER world and call it the world, so every guard counts what it drops
    and the tally is drained here.
    """
    notes = [f"{count} {reason}" for reason, count in drops.most_common(DROP_REASONS_SHOWN)]
    rest = len(drops) - DROP_REASONS_SHOWN
    if rest > 0:
        notes.append(f"and {rest} further kind(s) of unreadable record, not listed")
    return notes


def _unfiled_notes(unfiled: dict[str, str], factoryish) -> list[str]:
    """The unread-class census: buildings this run recognised as production and filed nowhere.

    The null-yaw census below says a placement was read wrong; this says a whole KIND of
    building was never read at all. A game update that renames or adds a manufacturer lands
    here, and unreported it makes the projection publish a smaller world -- ``machines`` short
    by a class, ``factory_health`` blind to it, and nothing anywhere saying so.

    ``factoryish`` is every instanceName the walk saw carrying a productivity monitor or an
    input/output/fuel buffer, which is the game's OWN mark of a factory building and is why
    this needs no list of 73 wall and foundation names to stay quiet. What it does need is
    ``DISMISSED_FACTORY_CLASSES``, the five that carry the mark and want no record.
    """
    census = collections.Counter(
        cls
        for instance, cls in unfiled.items()
        if instance in factoryish and cls not in DISMISSED_FACTORY_CLASSES
    )
    if not census:
        return []
    named = ", ".join(f"{n}x {cls}" for cls, n in census.most_common(UNFILED_CLASSES_SHOWN))
    rest = len(census) - UNFILED_CLASSES_SHOWN
    return [
        f"{len(census)} building class(es) carry a productivity monitor or a machine buffer "
        f"and this projection filed no record for any of them, so they are missing from "
        f"machines, extractors and generators: {named}"
        + (f", and {rest} more" if rest > 0 else "")
        + ". Each belongs in a hint list in extract.py, or in DISMISSED_FACTORY_CLASSES "
        "beside them"
    ]


def extract(path: str) -> dict:
    save = read_full_save(path)
    # Diagnostics, and only to stderr: stdout is the projection and has to stay parseable.
    # `projection._run_sidecar` folds this stream into the projection's own `warnings`.
    for offset, what in getattr(save, "warnings", None) or []:
        print(f"pioneersav: at body offset {offset}: {what}", file=sys.stderr)

    out: dict = {
        "schema_version": SCHEMA_VERSION,
        "header": header_info(path),
        "progression": {},
        "research": {"unclaimed_hard_drives": [], "ongoing": [], "unlocked_trees": []},
        "unlock_flags": {},
        "building_counts": {},
        "lightweight_counts": {},
        # Map-placed actors the save records as GONE. The only record of what has been
        # collected: nothing in a save says a power slug exists, only that one no longer does.
        "removed": {"cells": [], "instances": [], "counts": {}},
        "structures": {"classes": [], "instances": []},
        # Belt routing, as polylines. Schema 12; see `_belts`.
        "belts": {"classes": [], "segments": []},
        # Fluid pipe routing, as polylines. Schema 13; see `_pipes`.
        "pipes": {"classes": [], "networks": [], "segments": []},
        # The poles and the wires between them. Schema 17; see `_power`. Geometry ONLY: the
        # connectivity is ``graph["power"]``, and ``wires`` is that list's positional twin.
        "power": {"poles": {"classes": [], "instances": []}, "wires": []},
        "machines": [],
        "extractors": [],
        "generators": [],
        # The splitters and mergers a belt run passes through. Schema 13; see
        # `_ATTACHMENT_HINTS`. Their own list rather than a fourth kind of machine: they run
        # no recipe, draw no power and belong to the belt network that draws them.
        "attachments": [],
        # The containers and fluid buffers, and what is in each one. Schema 15; see `_storage`.
        # ``inventories["storage"]`` next door is the same stacks summed over the whole world,
        # which answers "have I got enough steel" and cannot answer "where is it".
        "storage": [],
        # The crates on the ground and what is in each one. Schema 18; see `_crates`. Its own
        # key rather than more ``storage`` rows: a container is infrastructure that stays where
        # it was put, and a crate is a situation that self-destructs the moment it is emptied.
        "crates": [],
        "pipe_networks": [],
        "depot": {},
        # Split by owner: lumping machine buffers in with carried stock overstates everything.
        # Fluids are raw litres here; the server scales them. A crate's contents get their own
        # bucket (schema 19) because they are recoverable stock rather than a machine buffer.
        # See `inventory_bucket`.
        "inventories": {"player": {}, "storage": {}, "machine": {}, "crate": {}},
        "node_state": {},
        # Char_Player_C carries the pawn's transform. BP_PlayerState_C sits at the
        # origin and is NOT a position -- reading it would put every player at (0,0).
        "players": [],
        # Connectivity, interned so the projection stays small: ~11.5k material edges
        # and ~1.3k power edges would be megabytes as repeated instanceNames.
        "graph": {"actors": [], "roles": [], "material": [], "power": []},
        "warnings": [],
    }
    #: Threaded into the builders that skip records rather than returned by them, so that a
    #: reader gets ONE list of what this save cost. Drained into ``warnings`` at the bottom.
    drops = Drops()
    #: instanceName -> class, for every actor the walk below files nowhere. Both drop points
    #: feed it; which of them are worth a warning is decided at the bottom, against evidence
    #: that only exists once every component has been seen. See the census there.
    unfiled: dict[str, str] = {}
    counts: dict[str, int] = {}
    n_objects = 0
    #: (chain actor world position, the actor) for every conveyor chain. Held rather than
    #: decoded here because the trailing bytes decode lazily and doing it in the walk would
    #: interleave a 0.3 s decode with the property pass for no gain -- see `_belts`.
    chain_actors: list[tuple] = []
    #: (class, instanceName, world position, mSplineData) per fluid pipe, and
    #: (mPipeNetworkID, fluid, [member paths]) per pipe network. Both held rather than
    #: resolved in the walk because a pipe's fluid comes off its NETWORK, and a network
    #: actor can be written after the pipes it owns -- see `_pipes`.
    pipe_actors: list[tuple] = []
    pipe_nets: list[tuple] = []
    #: (class, instanceName, pos, yaw, mFluidBox) per container and fluid buffer, and
    #: owner instanceName -> (totals, slotCount) for every ``StorageInventory`` in the world.
    #: Held because the walk cannot join them: a container's inventory is a COMPONENT, written
    #: after the actor that owns it, and a fluid buffer's fluid comes off its pipe NETWORK,
    #: which may be written after either.
    storage_actors: list[tuple] = []
    held: dict[str, tuple] = {}
    #: (class, instanceName, pos, yaw, mCrateType) per crate, and owner instanceName ->
    #: (totals, slotCount) for every component named ``Inventory``. Held for the containers'
    #: reason and one more: a crate's inventory component is spelled ``.inventory`` on some
    #: saves and ``.Inventory`` on others, so the join has to happen where the OWNER's class
    #: is known. See `_crates`.
    crate_actors: list[tuple] = []
    crate_held: dict[str, tuple] = {}
    #: (class, instanceName, pos, yaw) per pole, and shortName -> (endA, endB) for every actor
    #: that carries an ``mWireInstances``. Both held rather than emitted in the walk because a
    #: wire's two ENDS are the two power connection components that name it, and a component
    #: is written after -- and often long after -- the wire actor itself. See `_power`.
    pole_actors: list[tuple] = []
    wire_geom: dict[str, tuple] = {}
    #: Every ``Build_`` actor's world position, short name to (x, y, z). Held for one job and
    #: it is `_power`'s: a wire publishes its two endpoints in an order unrelated to the order
    #: its two connections were serialised, so pairing the drawn ends with the joined actors
    #: means measuring which end is nearer which actor. Positions the walk already computed.
    actor_at: dict[str, tuple] = {}

    # --- connectivity interning -------------------------------------------
    actor_ix: dict[str, int] = {}
    role_ix: dict[str, int] = {}
    uptime: dict[str, dict] = {}
    buffers: dict[str, dict] = {}
    #: owner instanceName -> {itemClass: count} slotted into its InventoryPotential.
    potential: dict[str, dict] = {}
    record_by_instance: dict[str, dict] = {}
    material_edges: list[list[int]] = []
    wire_ends: dict[str, list[tuple[str, str]]] = {}

    def actor_id(name: str) -> int:
        short = name.rsplit(".", 1)[-1]
        if short not in actor_ix:
            actor_ix[short] = len(actor_ix)
        return actor_ix[short]

    def role_id(name: str) -> int:
        if name not in role_ix:
            role_ix[name] = len(role_ix)
        return role_ix[name]

    for type_path, header, obj in iter_objects(save):
        n_objects += 1
        cls = cls_of(type_path)
        p = props(obj)
        instance = getattr(header, "instanceName", None) or getattr(obj, "instanceName", "")

        # Inventories live on COMPONENTS, which have no typePath at all, so this
        # must run before the empty-cls guard or every stack is silently skipped.
        if "mInventoryStacks" in p:
            bucket = inventory_bucket(str(instance))
            _accumulate_inventory(p["mInventoryStacks"], out["inventories"][bucket])
            # Input/OutputInventory belong to the OWNING machine and are what tell a
            # starved machine from a backed-up one. Keyed by owner, not by component.
            role = str(instance).rsplit(".", 1)[-1]
            if role in ("InputInventory", "OutputInventory", "FuelInventory"):
                # The OWNER's full instanceName, which is what the actor record uses.
                # rsplit(".", 2)[-2] would give the bare short name and never match.
                owner = str(instance).rpartition(".")[0]
                # A generator has no InputInventory -- its intake is FuelInventory.
                # Without this a starved coal plant shows no evidence either way.
                side = {"InputInventory": "in", "OutputInventory": "out"}.get(role, "fuel")
                totals: dict = {}
                _accumulate_inventory(p["mInventoryStacks"], totals)
                # Per ITEM, not just a total. "Is the output backed up" needs the item's
                # stack size, which only the docs know, so the class has to survive.
                buffers.setdefault(owner, {})[side] = {
                    "items": totals,
                    "slots": len(p["mInventoryStacks"] or []),
                }
            elif role == "StorageInventory":
                # Every one of them, including the splitters and mergers that also own a
                # component by this name: filtering here would mean knowing the owner's class,
                # which a component header does not carry. `_storage` looks up only the owners
                # it has an actor for, so the splitters go unclaimed.
                totals = {}
                _accumulate_inventory(p["mInventoryStacks"], totals)
                held[str(instance).rpartition(".")[0]] = (
                    totals,
                    len(p["mInventoryStacks"] or []),
                )
            elif role.lower() == "inventory":
                # A player pawn, a crashed drop pod and a crate all own a component by this
                # name, and a component header cannot say which; `_crates` looks up only the
                # owners it has a crate ACTOR for. Case-folded because the game spells it
                # ``.inventory`` on some save versions and ``.Inventory`` on others, and a
                # case-sensitive test silently empties half the crates in the world while
                # reporting them all as present.
                totals = {}
                _accumulate_inventory(p["mInventoryStacks"], totals)
                crate_held[str(instance).rpartition(".")[0]] = (
                    totals,
                    len(p["mInventoryStacks"] or []),
                )
            elif role == "InventoryPotential":
                # The overclock slot inventory: what is physically plugged into the building.
                # This is the ONLY record of a committed Power Shard and cannot be
                # reconstructed from the clock, because a shard raises the MAXIMUM potential
                # and the slider is then set anywhere below it.
                totals = {}
                _accumulate_inventory(p["mInventoryStacks"], totals)
                if totals:
                    potential[str(instance).rpartition(".")[0]] = totals

        # Productivity. The window is a fixed 300 s, so produce/window is a clean
        # fraction. ProduceDuration is ABSENT when zero -- UE omits defaults -- so a
        # missing value is a real zero, not missing data.
        if "mLastProductivityMeasurementDuration" in p:
            window = p.get("mLastProductivityMeasurementDuration") or 0.0
            produce = p.get("mLastProductivityMeasurementProduceDuration", 0.0) or 0.0
            cur_window = p.get("mCurrentProductivityMeasurementDuration", 0.0) or 0.0
            cur_produce = p.get("mCurrentProductivityMeasurementProduceDuration", 0.0) or 0.0
            uptime[str(instance)] = {
                "window_s": round(float(window), 2),
                "produce_s": round(float(produce), 2),
                "cur_window_s": round(float(cur_window), 2),
                "cur_produce_s": round(float(cur_produce), 2),
                "producing": truthy(p.get("mIsProducing", 0)),
            }

        # Factory connections live on COMPONENTS. instanceName is
        # "<...>.Build_X_C_123.Output1", so the owner is the second-to-last segment
        # and the connector role is the last -- the role is what orients the edge.
        target = p.get("mConnectedComponent")
        target_path = ref_path(target)
        if target_path and "." in instance:
            material_edges.append(
                [
                    actor_id(instance.rsplit(".", 2)[-2]),
                    actor_id(target_path.rsplit(".", 2)[-2]),
                    role_id(instance.rsplit(".", 1)[-1]),
                    role_id(target_path.rsplit(".", 1)[-1]),
                ]
            )
        for wire in p.get("mWires") or []:
            wire_path = ref_path(wire)
            if wire_path and "." in instance:
                wire_ends.setdefault(wire_path, []).append(
                    (instance.rsplit(".", 2)[-2], instance.rsplit(".", 1)[-1])
                )

        # A drawn wire's own geometry, keyed on the PROPERTY rather than on the class: what
        # makes an actor a wire here is that it carries the endpoints of one, so a modded or
        # future power line is read by this and would be skipped by a class list. See
        # `_wire_span`.
        #
        # NOT counted as a drop when it will not read, unlike every other guard in this file:
        # a wire whose geometry is missing keeps its edge in ``graph["power"]`` and its ROW in
        # ``power["wires"]``, holding null, and counting it would warn on every save older
        # than the property.
        if "mWireInstances" in p:
            span = _wire_span(p["mWireInstances"])
            if span is not None:
                wire_geom[str(instance).rsplit(".", 1)[-1]] = span

        if not cls:
            continue

        # ---- singleton managers -------------------------------------------
        if cls == "FGRecipeManager":
            out["progression"]["available_recipes"] = sorted(
                filter(None, (ref_class(r) for r in p.get("mAvailableRecipes") or []))
            )
            continue
        if cls == "BP_SchematicManager_C":
            out["progression"]["purchased_schematics"] = sorted(
                filter(None, (ref_class(r) for r in p.get("mPurchasedSchematics") or []))
            )
            out["progression"]["last_active_schematic"] = ref_class(p.get("mLastActiveSchematic"))
            continue
        if cls == "BP_GamePhaseManager_C":
            out["progression"]["game_phase"] = ref_class(p.get("mCurrentGamePhase")) or str(
                p.get("mCurrentGamePhase") or ""
            )
            out["progression"]["target_phase"] = ref_class(p.get("mTargetGamePhase")) or str(
                p.get("mTargetGamePhase") or ""
            )
            out["progression"]["phase_costs_remaining"] = _phase_costs(p.get("mGamePhaseCosts"))
            # THE live delivery record, and the only one. mGamePhaseCosts above is
            # marked DEPRECATED in FGGamePhaseManager.h and is provably frozen (see
            # _phase_costs). Absent means empty -- nothing delivered toward the target
            # phase yet -- which is a real answer, not missing data.
            out["progression"]["paid_off_target"] = _cost_amounts(
                p.get("mTargetGamePhasePaidOffCosts")
            )
            continue
        if cls == "BP_ResearchManager_C":
            out["research"]["unclaimed_hard_drives"] = _hard_drives(
                p.get("mUnclaimedHardDriveData")
            )
            out["research"]["last_used_hard_drive_id"] = p.get("mLastUsedHardDriveID")
            out["research"]["unlocked_trees"] = sorted(
                filter(None, (ref_class(r) for r in p.get("mUnlockedResearchTrees") or []))
            )
            # Absent means empty: UE omits empty SaveGame TArrays.
            out["research"]["ongoing"] = _ongoing(p.get("mSavedOngoingResearch"))
            continue
        if cls == "BP_UnlockSubsystem_C":
            for k in (
                "mIsMapUnlocked",
                "mIsBuildingOverclockUnlocked",
                # Written only once it becomes true -- UE omits a SaveGame property
                # still at its default -- so ABSENT means not researched. That is why a
                # save from before the research carries no such key at all.
                "mIsBuildingProductionBoostUnlocked",
                "mIsBuildingEfficiencyUnlocked",
                "mIsBlueprintsUnlocked",
                "mIsCustomizerUnlocked",
            ):
                if k in p:
                    out["unlock_flags"][k] = truthy(p[k])
            for k in ("mNumTotalInventorySlots", "mNumTotalArmEquipmentSlots"):
                if k in p:
                    out["unlock_flags"][k] = p[k]
            continue
        if cls == "FGCentralStorageSubsystem":
            out["depot"] = _stored_items(p.get("mStoredItems"))
            continue
        if cls == "FGLightweightBuildableSubsystem":
            # Holds Build_* classes that appear in NO actor header, so a
            # header-only census undercounts what is actually built.
            out["lightweight_counts"] = _lightweight(obj)
            out["structures"] = _structures(obj, drops)
            continue
        if cls == "FGPipeNetwork":
            fluid = ref_class(p.get("mFluidDescriptor"))
            if fluid:
                out["pipe_networks"].append({"instance": instance, "fluid": fluid})
            # Held for `_pipes` whether or not it named a fluid: an empty network still owns
            # its pipes, and "drawn, fluid unknown" beats "not drawn".
            pipe_nets.append(
                (
                    p.get("mPipeNetworkID"),
                    fluid,
                    [ref_path(m) for m in p.get("mFluidIntegrantScriptInterfaces") or []],
                )
            )
            continue
        # Four class names, not one: the three ``_RepSize*`` variants are the same actor
        # with a bigger replication budget and the identical record.
        if cls.startswith("FGConveyorChainActor"):
            chain_actors.append((getattr(header, "position", None), obj))
            continue

        if cls == "Char_Player_C":
            out["players"].append(
                {
                    "instance": instance,
                    "pos": pos_of(header),
                    # Present only while the player is holding it; useful as a hint
                    # that this pawn is the active one in a co-op save.
                    "has_build_gun": "mBuildGun" in p,
                }
            )
            continue

        # ---- resource nodes ------------------------------------------------
        if cls.startswith(("BP_ResourceNode", "BP_Fracking")):
            counts[cls] = counts.get(cls, 0) + 1
            left = p.get("mResourcesLeft")
            if left is not None and left != -1:
                out["node_state"][instance] = {"resources_left": left}
            continue

        # ---- crates ---------------------------------------------------------
        # ``continue``d past, unlike the pipes and containers below, because a crate is NOT a
        # ``Build_`` actor: it owes ``building_counts`` nothing and has no recipe, clock or
        # buffers.
        if cls in CRATE_CLASSES:
            crate_actors.append(
                (
                    cls,
                    instance,
                    pos_of(header),
                    yaw_of(getattr(header, "rotation", None)),
                    p.get("mCrateType"),
                )
            )
            continue

        if not cls.startswith("Build_"):
            unfiled[str(instance)] = cls
            continue
        counts[cls] = counts.get(cls, 0) + 1

        # Every buildable's place, for `_power`'s endpoint pairing and nothing else. It has to
        # be every one of them rather than only the poles, because a wire ends on a machine
        # nearly as often as on a pole.
        at = pos_of(header)
        if at is not None:
            actor_at[str(instance).rsplit(".", 1)[-1]] = tuple(at)

        # Poles, pipes and containers are HELD rather than `continue`d past: each is a Build_
        # actor and still owes ``building_counts`` its tally and `record` below its row.
        for_a_builder = (
            cls in POWER_POLE_CLASSES
            or cls in PIPE_CLASSES
            or cls in STORAGE_CLASSES
            or cls in FLUID_BUFFER_CLASSES
        )
        if cls in POWER_POLE_CLASSES:
            pole_actors.append(
                (cls, instance, pos_of(header), yaw_of(getattr(header, "rotation", None)))
            )

        if cls in PIPE_CLASSES:
            pipe_actors.append(
                (cls, instance, getattr(header, "position", None), p.get("mSplineData"))
            )

        if cls in STORAGE_CLASSES or cls in FLUID_BUFFER_CLASSES:
            storage_actors.append(
                (
                    cls,
                    instance,
                    pos_of(header),
                    yaw_of(getattr(header, "rotation", None)),
                    p.get("mFluidBox"),
                )
            )

        record = {
            "cls": cls,
            "instance": instance,
            "pos": pos_of(header),
            # The KEY is always emitted, unlike the property-derived fields below: an actor
            # header always carries a transform, so an absent yaw means the projection is old
            # rather than the building unrotated. Its VALUE is null where it would not read.
            "yaw": yaw_of(getattr(header, "rotation", None)),
        }
        if "mCurrentPotential" in p:
            record["clock"] = round(float(p["mCurrentPotential"]), 6)
        if "mPendingPotential" in p:
            record["pending_clock"] = round(float(p["mPendingPotential"]), 6)
        if "mIsProductionPaused" in p:
            record["paused"] = truthy(p["mIsProductionPaused"])
        # Somersloop runtime property names are UNVERIFIED -- neither appears in the
        # save nor in Docs.json. Record whatever is actually present.
        for key in ("mProductionBoost", "mCurrentProductionBoost", "mPendingProductionBoost"):
            if key in p:
                record["production_boost"] = p[key]
                record["production_boost_field"] = key

        # Uptime lives on the actor's OWN properties, so it is available now.
        live = uptime.get(str(instance))
        if live:
            record["uptime"] = live
        # Buffers do NOT: they are components, and components are visited after the
        # actor they belong to in the same pass, so `buffers` is still empty here.
        # Attached in a post-pass below.
        record_by_instance[str(instance)] = record

        if any(h in cls for h in _MANUFACTURER_HINTS):
            record["recipe"] = ref_class(p.get("mCurrentRecipe"))
            out["machines"].append(record)
        elif any(h in cls for h in _EXTRACTOR_HINTS):
            record["node"] = ref_path(p.get("mExtractableResource"))
            out["extractors"].append(record)
        elif any(h in cls for h in _GENERATOR_HINTS):
            record["fuel"] = ref_class(p.get("mCurrentFuelClass"))
            out["generators"].append(record)
        elif any(h in cls for h in _ATTACHMENT_HINTS):
            out["attachments"].append(record)
        elif not for_a_builder:
            # Built, tallied, and its record kept by nothing. Ordinary for a wall; the whole
            # point of the census for a class that turns out to run a recipe.
            unfiled[str(instance)] = cls

    # Buffers, now that every component has been seen.
    for owner, sides in buffers.items():
        record = record_by_instance.get(owner)
        if record is not None:
            record["buffers"] = sides

    # Slotted shards, same post-pass reason: InventoryPotential is a component.
    for owner, slotted in potential.items():
        record = record_by_instance.get(owner)
        if record is not None:
            record["potential_slots"] = slotted

    # The edges and the drawn spans come out of ONE pass over ``wire_ends``, which is what
    # makes ``power["wires"][i]`` the geometry of ``graph["power"][i]``: two passes would be
    # two chances to drop a different wire. See `_power`.
    power_edges, out["power"] = _power(
        wire_ends, wire_geom, pole_actors, actor_at, actor_id, actor_ix, drops
    )
    out["graph"] = {
        "actors": [name for name, _ in sorted(actor_ix.items(), key=lambda kv: kv[1])],
        "roles": [name for name, _ in sorted(role_ix.items(), key=lambda kv: kv[1])],
        "material": material_edges,
        "power": power_edges,
    }
    out["building_counts"] = dict(sorted(counts.items()))
    # ``actor_ix`` READ-ONLY in both, hence the dict rather than ``actor_id``: the graph's
    # actor list was snapshotted six lines up, so interning a new name here would mint an
    # index past the end of it. A piece with no connection at all gets -1 instead.
    out["belts"] = _belts(chain_actors, actor_ix, drops)
    out["pipes"] = _pipes(pipe_actors, pipe_nets, actor_ix, drops)
    out["storage"] = _storage(storage_actors, held, pipe_nets)
    out["crates"] = _crates(crate_actors, crate_held)
    out["removed"] = _removed(save)
    out["n_objects"] = n_objects
    out.setdefault("progression", {}).setdefault("available_recipes", [])
    out["progression"].setdefault("purchased_schematics", [])

    # Counted off the FINISHED payload rather than tallied inside `yaw_of`, so that what is
    # reported is exactly the number of null yaws a reader can go and find. Silent on a
    # healthy save; the point is that a header decode going wrong stops looking like a world
    # built on the grid. Every list carrying a placement is counted -- a kind left out would
    # make this a count of some of the world rather than of the world.
    unread = (
        sum(
            1
            for key in ("machines", "extractors", "generators", "attachments", "storage", "crates")
            for record in out[key]
            if record.get("yaw") is None
        )
        + sum(1 for row in out["structures"]["instances"] if len(row) > 4 and row[4] is None)
        + sum(1 for row in out["power"]["poles"]["instances"] if row[4] is None)
    )
    if unread:
        out["warnings"].append(
            f"{unread} placement(s) carry a rotation this parser could not read; "
            "their yaw is null rather than 0, which would have meant axis-aligned"
        )
    out["warnings"].extend(_unfiled_notes(unfiled, uptime.keys() | buffers.keys()))
    out["warnings"].extend(_drop_notes(drops))
    return out


def _removed(save) -> dict:
    """Map-placed actors the save records as GONE -- the only record of what was collected.

    Slugs, mushrooms, Mercer spheres, somersloops, shrines, crashed drop pods and world
    debris are placed by the map and never saved, so nothing in the save says a slug exists.
    What it says is which ones do *not* any more, and that negative record is the only way to
    answer "how many slugs have I picked up" or "which crash sites have I looted".

    `pioneersav` merges the three lists the format keeps into `destroyed_actors`; the fallback
    below reads the same three separately, as each level's `collectables1`/`collectables2` plus
    two save-level lists, and the two were verified equal set for set.

    Interned by cell, and the actor's path is reduced to its leaf: the full path repeats
    `Persistent_Level:PersistentLevel.` on every entry and says nothing.
    """
    refs = getattr(save, "destroyed_actors", None)
    if refs is None:
        # ref_path, not str(): an ObjectReference's __str__ renders the whole object as
        # "<ObjectReference: levelName=..., pathName=...>", so str() ends in ">" and every
        # leaf name comes out unique.
        pairs: list[tuple[str, str]] = []
        for level in getattr(save, "levels", None) or []:
            for which in ("collectables1", "collectables2"):
                for ref in getattr(level, which, None) or []:
                    pairs.append((str(getattr(ref, "levelName", "")), ref_path(ref) or ""))
        for which in ("dropPodObjectReferenceList", "extraObjectReferenceList"):
            for ref in getattr(save, which, None) or []:
                pairs.append((str(getattr(ref, "levelName", "")), ref_path(ref) or ""))
        refs = list(dict.fromkeys(pairs))

    cells: dict[str, int] = {}
    instances: list[list] = []
    counts: dict[str, int] = {}
    # Sorted, because the order is an artefact of which of the three lists was walked first
    # and means nothing; sorting keeps this field usable as a cache key.
    for cell, path in sorted(refs, key=lambda pair: (pair[0], pair[1])):
        leaf = path.rsplit(".", 1)[-1]
        if not leaf:
            continue
        ix = cells.setdefault(cell, len(cells))
        instances.append([ix, leaf])
        counts[_removed_class(leaf)] = counts.get(_removed_class(leaf), 0) + 1
    return {
        "cells": [c for c, _ in sorted(cells.items(), key=lambda kv: kv[1])],
        "instances": instances,
        "counts": dict(sorted(counts.items())),
    }


def _removed_class(leaf: str) -> str:
    """Class of a destroyed actor, from its instance name alone.

    There is no class path in these lists -- only the actor's name, and the game builds those
    three different ways: `BP_Crystal_mk3_C_2146` (class then index), `BP_Crystal2_228` (a
    numbered class variant then index) and `BP_MercerShrine_C_UAID_..._1397405905` (class, a
    world id, then an index). So the class is recovered by stripping from the right: the
    trailing index, then a `_UAID_<hex>` if present, then a trailing `_C`.

    Approximate: `BP_Crystal2_228` cannot be told from a class literally named `BP_Crystal2`,
    so the census groups by what the name shows. Callers wanting slugs should match a prefix
    (`BP_Crystal`), which is what `save/state.py` does.
    """
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    if len(parts) >= 2 and parts[-2] == "UAID":
        parts = parts[:-2]
    if parts and parts[-1] == "C":
        parts.pop()
    return "_".join(parts) or leaf


def _stored_items(raw) -> dict:
    """FGCentralStorageSubsystem.mStoredItems -> {itemClass: amount} (the Dimensional Depot)."""
    out: dict[str, float] = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        item = ref_class(fields.get("ItemClass")) or ref_class(fields.get("Item"))
        amount = fields.get("Amount", fields.get("NumItems", 0))
        if item is None and isinstance(entry, list) and len(entry) >= 2:
            item = ref_class(entry[0])
            if isinstance(entry[1], (int, float)):
                amount = entry[1]
        if item and isinstance(amount, (int, float)) and amount:
            out[item] = out.get(item, 0) + amount
    return out


def _phase_costs(raw) -> dict:
    """mGamePhaseCosts: remaining delivery amounts per phase. DEPRECATED AND FROZEN.

    FGGamePhaseManager.h calls both this array and the EGamePhase enum it is keyed by
    "DEPRECATED Only kept for save compatibility", and it is frozen in fact as well as in the
    header: measured across 29 saves of one world it is byte-identical either side of a
    completed Space Elevator phase, still claiming deliveries the player made 70 hours ago.
    It is emitted only so the server can show it beside the live record and say so.

    Only 4 phases are stored while the game has more, so later phases never appear.
    """
    out: dict = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        raw_phase = fields.get("gamePhase")
        # ByteProperty arrives as [enumTypeName, valueName]; take the value.
        if isinstance(raw_phase, list) and raw_phase:
            phase = str(raw_phase[-1])
        else:
            phase = ref_class(raw_phase) or str(raw_phase)
        out[phase] = _cost_amounts(fields.get("cost"))
    return out


def _cost_amounts(raw) -> dict:
    out: dict[str, float] = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        item = ref_class(fields.get("ItemClass"))
        if item:
            out[item] = fields.get("Amount", 0)
    return out


def _hard_drives(raw) -> list:
    """mUnclaimedHardDriveData -> the player's live 2-way choices.

    FHardDriveData = {HardDriveID:int32, PendingRewards:[schematic],
    PendingRewardsRerollsExecuted:int32}, per FGResearchManager.h. Rerolls left is
    1 - rerolls_executed (UFGResearchSettings::mNumRerollsPerHardDrive = 1, a
    config-driven default that a packaged ini could in principle override).
    """
    out: list = []
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        rewards = [ref_class(r) for r in fields.get("PendingRewards") or []]
        out.append(
            {
                "hard_drive_id": fields.get("HardDriveID"),
                "options": [r for r in rewards if r],
                "rerolls_executed": fields.get("PendingRewardsRerollsExecuted", 0),
            }
        )
    return out


def _ongoing(raw) -> list:
    """mSavedOngoingResearch. The float is seconds REMAINING, not a timestamp."""
    out: list = []
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        inner = struct_fields(fields.get("ResearchData")) if "ResearchData" in fields else fields
        out.append(
            {
                "schematic": ref_class(inner.get("Schematic")),
                "seconds_left": fields.get("ResearchCompleteTimestamp"),
                "fields_seen": sorted(fields),
            }
        )
    return out


def _placed(inst) -> bool:
    """Is this lightweight record a piece that exists, or a stale slot?

    A stale slot carries NO swatch and NO recipe; a real piece carries both, and across 31
    saves not one record carries exactly one, which makes this a clean test rather than a
    heuristic. Emitting a stale slot invents floor: `graph/structure.py` builds foundation
    slabs from these positions and `spatial/elevation.py` samples every one as ground height.

    NOT positional, and must not become so: the field indices differ between parsers, so
    asking "does any field name an asset" is the only test true of a real piece and false of a
    stale slot under either shape.
    """
    for field in inst if isinstance(inst, list) else ():
        if getattr(field, "pathName", None):
            return True
        if isinstance(field, str) and field:
            return True
    return False


def _lightweight(obj) -> dict:
    """Build_* classes held by FGLightweightBuildableSubsystem.

    These appear in NO actor header, so a header-only census undercounts what is built. The
    data lives in ``actorSpecificInfo`` and not in ``properties``, shaped as
    ``[count, [buildClassPath, [instance, ...]], ...]``, so the count comes from the instance
    list that follows each class path.
    """
    out: dict[str, int] = {}

    def walk(node) -> None:
        if not isinstance(node, list):
            return
        for i, child in enumerate(node):
            if isinstance(child, str) and "Build_" in child and child.endswith("_C"):
                cls = ref_class(child)
                nxt = node[i + 1] if i + 1 < len(node) else None
                # Stale slots are skipped, not counted -- see `_placed`.
                n = sum(1 for inst in nxt if _placed(inst)) if isinstance(nxt, list) else 1
                if cls:
                    out[cls] = out.get(cls, 0) + n
            else:
                walk(child)

    walk(getattr(obj, "actorSpecificInfo", None))
    return out


def _structures(obj, drops: Drops) -> dict:
    """Transforms of every lightweight buildable -- foundations, ramps, walls, catwalks.

    These carry the one signal power and belts both lack: what the player physically BUILT AS
    ONE THING, which is why factory slabs resolve far more finely here than either graph.

    ``actorSpecificInfo`` is a list of ``[buildClassPath, [instance, ...]]`` pairs, and each
    instance is ``[rotationQuaternion, position, ...]`` -- so unlike the class census above,
    the transform is the SECOND element, not derivable from the count.

    Rows are ``[classIndex, x, y, z, yaw]``, the yaw column since schema 12; most of a world's
    pieces sit at an angle that is not a multiple of 90, and without it a client draws an
    angled platform as a staircase. Interned and rounded to whole centimetres, because
    sub-centimetre precision cannot change whether two 8 m foundations touch.

    Slab geometry is NOT computed here. It lives behind a subprocess and a cache, so freezing
    the link distance in the sidecar would mean a 3-minute re-parse to tune a threshold.
    """
    classes: list[str] = []
    index: dict[str, int] = {}
    instances: list[list] = []

    for entry in getattr(obj, "actorSpecificInfo", None) or []:
        # NOT a drop: the blob is ``[version, [classPath, [instance, ...]], ...]``, so the
        # leading element is the record format's version word and is skipped on every save.
        if not isinstance(entry, list):
            continue
        if len(entry) != 2:
            drops["lightweight class block(s) skipped: not a [class, instances] pair"] += 1
            continue
        cls_path, items = entry
        cls = ref_class(cls_path) or str(cls_path).rsplit(".", 1)[-1]
        if not isinstance(items, list):
            drops[f"lightweight class block(s) skipped: {cls} lists no instances"] += 1
            continue
        ci = index.get(cls)
        if ci is None:
            ci = index[cls] = len(classes)
            classes.append(cls)
        for inst in items:
            if not (isinstance(inst, list) and len(inst) >= 2):
                drops["lightweight piece(s) dropped: no [rotation, position] to read"] += 1
                continue
            # NOT counted as a drop: a stale slot is a record of nothing, and emitting it
            # invents floor. See `_placed`, which is where that measurement lives.
            if not _placed(inst):
                continue
            pos = inst[1]
            try:
                instances.append([ci, int(pos[0]), int(pos[1]), int(pos[2]), yaw_of(inst[0])])
            except (TypeError, ValueError, IndexError):
                drops["lightweight piece(s) dropped: position would not read as numbers"] += 1
                continue

    return {"classes": classes, "instances": instances}


def _length(x: float, y: float, z: float) -> float:
    """Length of a 3-vector, in whatever unit its components are.

    Three scalars rather than a sequence, so that a 2-tuple cannot be passed where a spline
    tangent is meant: `test_geo_centroid` reserves the stdlib distance helper package-wide.
    """
    return math.sqrt(x * x + y * y + z * z)


def _bulge(p0: list, m0: list, p1: list, m1: list) -> float:
    """An UPPER BOUND, in centimetres, on how far a cubic Hermite span leaves its own chord.

    The whole of `_spans`' decision. A bound rather than a measurement, and it may only ever
    OVERSTATE the curve: overstating costs bytes, and understating would silently flatten a
    bend the save does record.

    A Hermite span is ``Q(t) = h00 p0 + h10 m0 + h01 p1 + h11 m1``. Split both tangents into
    the part along the chord ``v = p1 - p0`` and the part across it; the curve's distance from
    the chord SEGMENT is at most its sideways offset plus however far it runs off either end:

    * Sideways is ``h10 m0_perp + h11 m1_perp``, and both basis functions peak at 4/27, so it
      never exceeds ``(4/27)(|m0_perp| + |m1_perp|)``. A bound, not an equality: the two peak
      at different ``t`` (1/3 and 2/3) and partly cancel.
    * Along is the cubic ``u(t) = (s0+s1-2) t^3 + (3-2 s0-s1) t^2 + s0 t``, where ``s`` is a
      tangent's chord-relative length, and it is solved EXACTLY -- ``u`` runs 0 to 1 and any
      excursion outside that is a real overshoot past an endpoint. Exactly, because the crude
      bound reads 7% of the chord for the game's commonest tangent (half the chord) where the
      true overshoot is zero, which would cost the whole optimisation.

    Checked against a 512-point tessellation of every span in the reference save: never
    smaller than the sampled truth, and never more than 3.08x it.
    """
    vx, vy, vz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    n2 = vx * vx + vy * vy + vz * vz
    if n2 <= 0:
        # Coincident control points, which is the zero-length joint where a conveyor lift
        # meets its belt: there is no chord to be along, so all of both tangents is sideways.
        return _HERMITE_PEAK * (_length(*m0) + _length(*m1))
    s0 = (m0[0] * vx + m0[1] * vy + m0[2] * vz) / n2
    s1 = (m1[0] * vx + m1[1] * vy + m1[2] * vz) / n2
    across = _length(m0[0] - s0 * vx, m0[1] - s0 * vy, m0[2] - s0 * vz) + _length(
        m1[0] - s1 * vx, m1[1] - s1 * vy, m1[2] - s1 * vz
    )

    a, b, c = s0 + s1 - 2.0, 3.0 - 2.0 * s0 - s1, s0
    low, high = 0.0, 1.0
    if a:
        disc = 4.0 * b * b - 12.0 * a * c
        roots = (
            ((-2.0 * b + math.sqrt(disc)) / (6.0 * a), (-2.0 * b - math.sqrt(disc)) / (6.0 * a))
            if disc >= 0.0
            else ()
        )
    else:
        roots = (-c / (2.0 * b),) if b else ()
    for t in roots:
        if 0.0 < t < 1.0:
            u = ((a * t + b) * t + c) * t
            low, high = min(low, u), max(high, u)
    beyond = (max(0.0, -low) + max(0.0, high - 1.0)) * _length(vx, vy, vz)
    return _HERMITE_PEAK * across + beyond


def _spans(points: list, tangents: list) -> list:
    """The curve column for one route: ``[[...]]`` to append, or ``[]`` when it is all straight.

    Returned as a list to splat onto the row rather than as a value, because a route with no
    bend in it gets NO COLUMN AT ALL, which keeps a straight run's row byte-identical to the
    one schema 14 emitted.

    One entry per SPAN rather than per point, because a span is the unit a curve is drawn in:
    it needs the leave tangent of the point behind it and the arrive tangent of the point
    ahead, and nothing needs the arrive tangent of the first point or the leave tangent of the
    last -- which are also the only places the save stores a bare unit vector rather than a
    real tangent.

    A flat span stores ``0``, not its tangents. See `_bulge` for what "flat" is measured as.
    """
    spans: list = []
    curved = False
    for i in range(len(points) - 1):
        leave, arrive = tangents[i][1], tangents[i + 1][0]
        if _bulge(points[i], leave, points[i + 1], arrive) < TANGENT_EPS_CM:
            spans.append(0)
        else:
            spans.append(leave + arrive)
            curved = True
    return [spans] if curved else []


def _belts(chains: list, actor_ix: dict, drops: Drops) -> dict:
    """Every conveyor's route, as polylines. ``chains`` is ``[(actorPosition, actor), ...]``,
    and ``actor_ix`` is the connectivity graph's ``{shortName: index}``, read only.

    Belts are not lightweight buildables and their geometry is in no property: it lives in
    ``FGConveyorChainActor``'s trailing bytes, which ``pioneersav.trailers`` decodes as
    ``location, arrive tangent, leave tangent`` per point.

    Rows are ``[chainIndex, classIndex, [[x, y, z], ...], actorIndex]``, and
    ``[..., tangents]`` where the run bends -- see `_spans` for the fifth column. The layout
    is the one `_pipes` writes, column for column, because the two tables answer the same
    questions and a reader that has to remember which of them puts the actor where is a
    reader that will one day put it in the wrong place.

    * **chainIndex** groups segments into the run the game itself groups them into: one chain
      is one continuous flow of items. Dense and ordered, so a consumer wanting per-chain
      polylines concatenates the rows sharing an index and one wanting per-belt polylines
      draws each row.
    * **classIndex** interns the belt's class, which carries both the mark and whether the
      piece is a belt or a LIFT. A floor view needs exactly that distinction, because a lift
      is the connector between two Z bands.
    * The points are the spline's control points at the whole-centimetre precision
      ``_structures`` uses. There is nothing to thin: they are already the bends.
    * **actorIndex** points into ``graph["actors"]``. Schema 20, and the column that makes a
      drawn chain and a contracted run the same object -- see §6.15,
      ``docs/save-projection.md``, for the four surfaces that were naming the wrong thing
      without it. The chain names its pieces by INSTANCE, which is the very name the graph
      interns, so the join is the save's own identity and not a nearest match.

    Two measured facts about the source that this function is entirely about.

    1. **The spline is in the chain actor's frame**, so the actor's own position has to be
       added back -- the raw points sit a whole map away from the belts they belong to. Every
       chain on this disk carries an identity rotation, so there is no orientation to undo; a
       chain that ever carried one would need the points rotated before translating.
    2. **Segments are stored output-first.** Offsets grow towards the output and
       ``segments[-1]`` holds offset 0, the chain's input, so rows come out reversed and in
       TRAVEL ORDER. In file order consecutive segments of one chain do not meet, and every
       chain draws as a zigzag.

    A chain whose trailing bytes will not decode costs that chain and is counted into
    ``warnings`` rather than the whole projection. The first few also name their exception on
    stderr, because the TYPE of the failure is what says whether the format moved and a count
    cannot carry it.
    """
    classes: list[str] = []
    index: dict[str, int] = {}
    segments: list[list] = []
    chain_ix = 0
    chain_notes = 0

    for origin, obj in chains:
        try:
            info = obj.actorSpecificInfo
        except PARSE_ERROR as exc:
            drops["conveyor chain(s) dropped: the trailing bytes would not decode"] += 1
            # Capped: a version bump makes this every chain in the world, which is the case
            # where stderr matters most and exactly the case that scrolls the reason off.
            if chain_notes < CHAIN_NOTES_SHOWN:
                chain_notes += 1
                print(f"pioneersav: conveyor chain skipped: {exc}", file=sys.stderr)
            elif chain_notes == CHAIN_NOTES_SHOWN:
                chain_notes += 1
                print(
                    "pioneersav: further unreadable conveyor chains not listed; "
                    "the projection's warnings carry the total",
                    file=sys.stderr,
                )
            continue
        if not (isinstance(info, list) and len(info) >= 3 and isinstance(info[2], list)):
            drops["conveyor chain(s) dropped: no segment list in actorSpecificInfo"] += 1
            continue
        try:
            ox, oy, oz = (float(v) for v in origin)
        except (TypeError, ValueError):
            drops["conveyor chain(s) dropped: the actor position would not read"] += 1
            continue

        rows: list[list] = []
        for seg in reversed(info[2]):
            if not (isinstance(seg, list) and len(seg) >= 3):
                drops["belt segment(s) dropped: no [?, class, points] to read"] += 1
                continue
            instance = ref_path(seg[1]) or ""
            cls = _conveyor_class(instance)
            if not cls:
                drops["belt segment(s) dropped: the class is not a known conveyor"] += 1
                continue
            points = []
            tangents = []
            for point in seg[2] if isinstance(seg[2], list) else ():
                try:
                    at, arrive, leave = point[0], point[1], point[2]
                    # Rounded, not truncated as `_structures` does: that field's truncation is
                    # in the banked parity digests and cannot move, while rounding here is
                    # unbiased and exactly commutative with the whole-centimetre translation.
                    #
                    # All three are read and rounded BEFORE anything is appended, so that a
                    # point the decoder cannot make sense of costs its whole triple. Appending
                    # as they are computed leaves the two lists one apart when a tangent
                    # raises, and every span after the fault then bends around the wrong
                    # control point -- which still draws a curve, just not this one.
                    #
                    # The tangents get the rounding and NOT the translation: they are
                    # displacement vectors, so moving the chain's origin moves the points they
                    # hang off and leaves them alone.
                    at = [round(at[0] + ox), round(at[1] + oy), round(at[2] + oz)]
                    pair = ([round(v) for v in arrive], [round(v) for v in leave])
                    points.append(at)
                    tangents.append(pair)
                except (TypeError, ValueError, IndexError):
                    drops["belt point(s) dropped: location or tangent would not read"] += 1
                    continue
            # A single point is not a route.
            if len(points) < 2:
                drops["belt segment(s) dropped: fewer than 2 readable points"] += 1
                continue
            ci = index.get(cls)
            if ci is None:
                ci = index[cls] = len(classes)
                classes.append(cls)
            rows.append(
                [
                    chain_ix,
                    ci,
                    points,
                    actor_ix.get(instance.rsplit(".", 1)[-1], -1),
                    *_spans(points, tangents),
                ]
            )
        if rows:
            segments.extend(rows)
            chain_ix += 1

    return {"classes": classes, "segments": segments}


def _conveyor_class(path: str) -> str:
    """``...PersistentLevel.Build_ConveyorBeltMk3_C_1264`` -> ``Build_ConveyorBeltMk3_C``.

    A chain names its belts by instance, not by class, so the class is recovered from the
    name -- the same stripping ``_removed_class`` does, except that the trailing ``_C``
    stays, because these names are compared against class names that carry it.

    Safe because a conveyor, unlike a destroyed actor, has an actor header of its own: the
    stripped name was checked against the real ``typePath`` for every segment in the save
    folder and agreed on all of them, so the projection need not carry a second index of
    every belt actor in the world.
    """
    leaf = path.rsplit(".", 1)[-1]
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    return "_".join(parts) if len(parts) > 1 else ""


def _pipes(actors: list, networks: list, actor_ix: dict, drops: Drops) -> dict:
    """Every fluid pipe's route, as polylines, and the fluid each one carries.

    ``actors`` is ``[(class, instanceName, actorPosition, mSplineData), ...]``,
    ``networks`` is ``[(mPipeNetworkID, fluidClass, [memberPath, ...]), ...]``, and
    ``actor_ix`` is the connectivity graph's ``{shortName: index}``, read only.

    Unlike a belt, a pipe's spline is a PROPERTY: ``mSplineData``, an array of structs whose
    ``Location`` is one control point and whose ``ArriveTangent``/``LeaveTangent`` are the
    curve through them. The points are only the corners of an elbow, so dropping the tangents
    draws the polygon that cuts the corner the pipe was built to round; see `_spans`.

    Rows are ``[networkIndex, classIndex, [[x, y, z], ...], actorIndex]``, and
    ``[..., tangents]`` where the pipe bends:

    * **networkIndex** points into ``networks``, ``[{"id": ..., "fluid": ...}, ...]`` -- the
      game's own ``FGPipeNetwork`` grouping, and the reason this key can say WATER or CRUDE
      OIL rather than only "a pipe". ``-1`` for a pipe no network claims.
    * **classIndex** interns the build class: Mk1 and Mk2, each with a ``NoIndicator``
      variant, which is a pipe whose flow indicator the player switched off.
    * The points are whole centimetres, like ``_belts``, and are already only the corners.
    * **actorIndex** points into ``graph["actors"]``. Schema 14.

    The frame is the actor's, TRANSLATED AND NOT ROTATED: every ``Location`` is relative to
    the pipe's own actor position, and every pipeline actor on this disk carries an identity
    quaternion, so a pipe that ever carried a rotation would need its points rotated before
    translating. The same statement `_belts` makes about chains.

    Flow direction is NOT on a pipe and this file does not invent one. Its two connectors are
    named ``PipelineConnection0`` and ``PipelineConnection1`` rather than input and output,
    ``mFluidBox`` is a single float of contents, and the spline's order is the order the
    player dragged it. What the save does state is the coupling graph -- every fluid
    connection is a component carrying ``mConnectedComponent``, folded into
    ``graph["material"]``, with the component's own name typing the port at a machine
    (``PipeInputFactory``, ``PipeOutputFactory``, ``ConnectionAny0``) -- and ``actorIndex`` is
    the join from a segment row to it. Direction is inferred a layer up, in
    ``domain/world/flow.py``, where the guesswork can be labelled and refused.

    ``PipelineConnection0`` is ``points[0]`` and ``PipelineConnection1`` is ``points[-1]``,
    established by measuring claimed endpoints against their couplings.
    """
    fluid_of: dict[str, int] = {}
    nets: list[dict] = []
    for net_id, fluid, members in networks:
        ni = len(nets)
        nets.append({"id": net_id if isinstance(net_id, int) else None, "fluid": fluid})
        for member in members:
            if member:
                fluid_of[member] = ni

    classes: list[str] = []
    index: dict[str, int] = {}
    segments: list[list] = []

    for cls, instance, origin, spline in actors:
        if not isinstance(spline, list):
            drops["pipe(s) dropped: mSplineData is not a list of points"] += 1
            continue
        try:
            ox, oy, oz = (float(v) for v in origin)
        except (TypeError, ValueError):
            drops["pipe(s) dropped: the actor position would not read"] += 1
            continue
        points = []
        tangents = []
        for entry in spline:
            fields = struct_fields(entry)
            at = fields.get("Location")
            try:
                # Computed in full before either list grows, and only the points translated --
                # the same two rules `_belts` states, for the same two reasons.
                at = [round(at[0] + ox), round(at[1] + oy), round(at[2] + oz)]
                arrive = fields.get("ArriveTangent") or (0, 0, 0)
                leave = fields.get("LeaveTangent") or (0, 0, 0)
                pair = ([round(v) for v in arrive], [round(v) for v in leave])
                points.append(at)
                tangents.append(pair)
            except (TypeError, ValueError, IndexError):
                drops["pipe point(s) dropped: Location or tangent would not read"] += 1
                continue
        # A single point is not a route, the bar `_belts` sets.
        if len(points) < 2:
            drops["pipe(s) dropped: fewer than 2 readable spline points"] += 1
            continue
        ci = index.get(cls)
        if ci is None:
            ci = index[cls] = len(classes)
            classes.append(cls)
        segments.append(
            [
                fluid_of.get(str(instance), -1),
                ci,
                points,
                actor_ix.get(str(instance).rsplit(".", 1)[-1], -1),
                *_spans(points, tangents),
            ]
        )

    return {"classes": classes, "networks": nets, "segments": segments}


def _wire_span(raw) -> tuple[list, list] | None:
    """The two ends of one power wire, in WORLD centimetres, or None if it has no pair.

    ``mWireInstances`` is an array of ``FWireInstance``, each carrying a two-element
    ``Locations`` and a two-element ``CachedRelativeLocations``. The ABSOLUTE pair is what is
    taken, in the world's own unrotated frame, so nothing here has to know where a connector
    sits on the thing it is bolted to -- which is why schema 17 needs no table of per-class
    connector offsets. Verified against the actor's own ``mCachedLength``, which these numbers
    reproduce to float32 noise.

    THE FIRST PAIR ONLY. A line with two instances is a Power Tower span whose second instance
    is the PARALLEL CONDUCTOR: two strands 12 m apart between the same two towers. Taking the
    first takes one real strand rather than averaging two into a line neither occupies.

    Walked rather than indexed, because the decoded property nests the values beside their
    type descriptors. Document order is preserved, which is what makes "the first two" the
    first STRAND: on a four-location line the first two points are the two ENDS of strand one,
    not the two strands' starts.
    """
    found: list[list] = []

    def walk(node) -> None:
        if len(found) >= 2 or not isinstance(node, (list, tuple)):
            return
        if len(node) == 2 and node[0] == "Locations":
            point = node[1]
            if isinstance(point, (list, tuple)) and len(point) == 3:
                try:
                    found.append(
                        [round(float(point[0])), round(float(point[1])), round(float(point[2]))]
                    )
                except (TypeError, ValueError):
                    pass
                return
        for child in node:
            walk(child)

    walk(raw)
    return (found[0], found[1]) if len(found) == 2 else None


def _power(
    wire_ends: dict,
    wire_geom: dict,
    poles: list,
    actor_at: dict,
    actor_id,
    actor_ix: dict,
    drops: Drops,
) -> tuple[list, dict]:
    """The power network's GEOMETRY, and the edge list it is the twin of.

    Returns ``(power_edges, power)``, and returning both is the design: ``graph["power"]``
    has carried the power edges as interned actor-index pairs since schema 11, and schema 17
    adds where those edges are DRAWN. Two functions building two lists from the same dict
    would be two chances to skip a different wire and leave the reader joining lists that no
    longer line up. So there is one pass and one rule::

        len(power["wires"]) == len(graph["power"])
        power["wires"][i] is the span of graph["power"][i]

    always, on every save. ``wires[i]`` is ``[x0, y0, z0, x1, y1, z1]`` in world centimetres
    or **null** where that wire published no geometry -- a torn ``mWireInstances``, or a save
    older than the property, in which case every row is null and the invariant still holds.
    An empty ``wires`` list therefore means "no power edges at all", never "no geometry".

    The geometry is not the two actors' positions, because a wire is strung between
    CONNECTORS and a connector sits at a fixed offset on its owner -- 7 m above a Mk1 pole's
    origin, 2.1 m forward and 4.7 m to one side of a constructor's centre. Drawing
    pole-origin to machine-origin would put every wire through the middle of the machine it
    feeds, so `_wire_span` reads the endpoints the save publishes.

    The pair is ORDERED TO MATCH THE EDGE by measurement, because the save's own order agrees
    with the edge's about half the time: each end is assigned to the nearer of the two actors,
    in plan, which collapses the endpoint-to-origin offset to a per-class constant. Where the
    two are equidistant the save's order is kept, there being nothing to measure.

    Poles are the other half, a plain interned table in the shape ``structures`` uses:
    ``[classIndex, x, y, z, yaw, actorIndex]``, whole centimetres. The sixth column is the
    pole's index in ``graph["actors"]``, the join schema 14 gave a pipe, and is what lets a
    reader count a pole's wires off the edge list rather than off a second copy of the
    connectivity. ``-1`` for a pole no wire names.

    ``actor_id`` interns and ``actor_ix`` only reads. The wire pass MINTS actor indices; the
    pole pass must not, because a pole with no wire would be added to a list already
    snapshotted -- the hazard the note above the ``_pipes`` call in `extract` names.
    """
    edges: list[list[int]] = []
    wires: list[list | None] = []
    for path, ends in wire_ends.items():
        # Exactly two connections or it is not a wire: a half-built or orphaned line is
        # dropped rather than guessed at.
        if len(ends) != 2:
            continue
        a_owner, b_owner = ends[0][0], ends[1][0]
        edges.append([actor_id(a_owner), actor_id(b_owner)])
        wires.append(
            _wire_row(wire_geom.get(str(path).rsplit(".", 1)[-1]), a_owner, b_owner, actor_at)
        )

    classes: list[str] = []
    index: dict[str, int] = {}
    instances: list[list] = []
    for cls, instance, at, yaw in poles:
        if at is None:
            drops["pole(s) dropped: the actor position would not read"] += 1
            continue
        ci = index.get(cls)
        if ci is None:
            ci = index[cls] = len(classes)
            classes.append(cls)
        instances.append(
            [
                ci,
                round(at[0]),
                round(at[1]),
                round(at[2]),
                yaw,
                actor_ix.get(str(instance).rsplit(".", 1)[-1], -1),
            ]
        )

    return edges, {"poles": {"classes": classes, "instances": instances}, "wires": wires}


def _wire_row(span, a_owner: str, b_owner: str, actor_at: dict) -> list | None:
    """One wire's six numbers, with the ends put in the edge's own order. See `_power`.

    The plan distance decides, not the 3D one: the two candidates differ by hundreds of
    metres horizontally and by a few metres vertically, so height contributes nothing to the
    choice and a wire dropping to a machine below would only add noise to it.
    """
    if span is None:
        return None
    a, b = span
    at_a, at_b = actor_at.get(a_owner), actor_at.get(b_owner)
    if at_a is not None and at_b is not None:
        straight = _plan_gap(a, at_a) + _plan_gap(b, at_b)
        crossed = _plan_gap(b, at_a) + _plan_gap(a, at_b)
        if crossed < straight:
            a, b = b, a
    return [*a, *b]


def _plan_gap(point, at) -> float:
    """Horizontal distance between a wire endpoint and an actor's origin, centimetres."""
    return math.hypot(point[0] - at[0], point[1] - at[1])


def _storage(actors: list, held: dict, networks: list) -> list:
    """Every container and fluid buffer, where it stands, and what is inside it.

    ``actors`` is ``[(class, instanceName, pos, yaw, mFluidBox), ...]``, ``held`` is
    ``{ownerInstanceName: ({itemClass: count}, slotCount)}`` for every ``StorageInventory``
    component in the world, and ``networks`` is `_pipes`' ``[(id, fluid, [member, ...]), ...]``.

    ``inventories["storage"]`` sums these same stacks, which answers "have I got enough steel"
    and is the wrong shape for "where did I put it". Rows are one dict apiece rather than an
    interned table, there being few enough of them for the whole key to cost tens of kilobytes.

    Solids come from the owning actor's ``StorageInventory`` component, joined by instance
    name. Fluids are the other record entirely: a buffer keeps one ``mFluidBox`` float of
    CUBIC METRES and does not name its fluid, so the name comes off the ``FGPipeNetwork`` that
    claims the buffer -- the game's own answer, and the same join a pipe's ``fluid`` uses. A
    buffer no network claims keeps its level and gets a null fluid.

    Sorted by class then instance so the key is stable between runs and diffable between two
    saves of one world.
    """
    fluid_of: dict[str, str | None] = {}
    for _net_id, fluid, members in networks:
        for member in members:
            if member:
                fluid_of[str(member)] = fluid

    rows: list[dict] = []
    for cls, instance, pos, yaw, fluid_box in sorted(actors, key=lambda a: (a[0], str(a[1]))):
        row: dict = {"cls": cls, "instance": instance, "pos": pos, "yaw": yaw}
        if cls in FLUID_BUFFER_CLASSES:
            row["fluid"] = fluid_of.get(str(instance))
            # Cubic metres, and NOT the litres ``inventories`` reports: a fluid stack in an
            # inventory is stored 1000x and a fluid box is not. Checked against the capacities
            # the docs dump states for these two classes.
            try:
                row["stored_m3"] = round(float(fluid_box), 2)
            except (TypeError, ValueError):
                row["stored_m3"] = None
        else:
            totals, slots = held.get(str(instance), ({}, 0))
            # Biggest first, ties by class, so the order is total and a popup showing the top
            # few shows the useful few.
            row["items"] = [
                [item, amount]
                for item, amount in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
            ]
            # Straight off the component rather than looked up per class, because it is a fact
            # about this container and the docs dump spells it as two numbers to multiply.
            row["slots"] = slots
        rows.append(row)
    return rows


def crate_kind(raw) -> str:
    """``mCrateType`` as one of ``CRATE_KINDS``' words, defaulting to ``none``.

    The parser hands an EnumProperty back as ``[enumName, "EFGCrateType::CT_DeathCrate"]``, so
    the word wanted is the tail of the second element. Anything else -- an absent property, an
    unmet shape, a value the enum has grown since -- is ``none``, which is the enum's own name
    for "this crate does not say".
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return CRATE_KINDS["CT_None"]
    return CRATE_KINDS.get(str(raw[1]).rsplit("::", 1)[-1], CRATE_KINDS["CT_None"])


def _crates(actors: list, held: dict) -> list:
    """Every crate on the ground, what kind it is, where it is, and what is inside it.

    ``actors`` is ``[(class, instanceName, pos, yaw, mCrateType), ...]`` and ``held`` is
    ``{ownerInstanceName: ({itemClass: count}, slotCount)}`` for every component named
    ``Inventory``, in either case, anywhere in the world.

    A crate is not a buildable, not a machine, not a lightweight piece and not one of the
    classes ``storage`` joins, so nothing else in the projection can carry it. The same stacks
    are summed into ``inventories["crate"]``; see `inventory_bucket`.

    THE SAVE NAMES NO OWNER. ``AFGCrate`` has exactly one ``SaveGame`` property and it is
    ``mCrateType`` -- no player, no timestamp, no cause of death -- so in a single-player world
    every death crate is the player's by construction, and in a co-op world this projection
    cannot say whose it was. The kind is ``none`` for a crate predating the property, which is
    the game's own value and not a parse failure; see `CRATE_KINDS`.

    Contents are joined by owner instance name, the join `_storage` uses, with the same guard:
    a player pawn and the crashed drop pods also own a component named ``Inventory`` and are
    not in ``actors``, so they are never looked up.

    Sorted by kind then instance, so the key is diffable between two saves of one world and
    the death crates do not move around as dismantle crates come and go.
    """
    rows: list[dict] = []
    for cls, instance, pos, yaw, raw_type in actors:
        totals, slots = held.get(str(instance), ({}, 0))
        rows.append(
            {
                "cls": cls,
                "instance": instance,
                "pos": pos,
                "yaw": yaw,
                "kind": crate_kind(raw_type),
                "items": [
                    [item, amount]
                    for item, amount in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
                # Off the component, like a container's: a crate is sized to what was put in
                # it, so this is a fact about this crate rather than about its class.
                "slots": slots,
            }
        )
    rows.sort(key=lambda row: (row["kind"], str(row["instance"])))
    return rows


def owner_class(owner: str) -> str:
    """``Build_StorageContainerMk1_C_2147441119`` -> ``Build_StorageContainerMk1_C``.

    The instance id is a trailing ``_<digits>`` and nothing else is, so stripping one is
    exact. A name with no numeric tail comes back whole rather than losing its ``_C``.
    """
    head, sep, tail = owner.rpartition("_")
    return head if sep and tail.isdigit() else owner


def inventory_bucket(instance: str) -> str:
    """Which pile a stack belongs to, from the component's instanceName.

    An instanceName looks like
    ``...PersistentLevel.Build_ConstructorMk1_C_2147441119.InputInventory``, so it
    carries both the owning actor class and the inventory's ROLE.

    The distinction is not cosmetic: summing every stack in the world counts pipe and
    machine-buffer contents in litres, which would tell the player they can afford anything.

    **Storage is membership in STORAGE_CLASSES and NOT a substring of the owner's name.** The
    Personal Storage Box, the HUB's own container and the Blueprint Designer's carry no
    "Storage" word a name test would catch, and everything in them then buckets as a machine
    buffer -- material that exists and cannot be spent, which ``stock()`` never sees. The ROLE
    gate stays beside the class test, because a container owns other components too and only
    ``StorageInventory`` means stock.

    **Crate is schema 19.** A crate's contents live in a component named ``Inventory`` on an
    owner that is neither a player nor a storage class, so the schema-11 rule filed a dead
    pioneer's pockets with the smelter buffers; they are recoverable stock lying on the
    ground, which is neither bucket. The owner test is membership of CRATE_CLASSES, so the
    player pawn (caught by the player test above) and the crashed drop pods (no crate class)
    land where they always did.
    """
    owner = instance.rsplit(".", 2)[-2] if instance.count(".") >= 2 else instance
    role = instance.rsplit(".", 1)[-1]
    if "PlayerState" in owner or owner.startswith(("Char_", "BP_Player")):
        return "player"
    if role == "StorageInventory" and (
        owner_class(owner) in STORAGE_CLASSES or any(tag in owner for tag in _STORAGE_OWNER_HINTS)
    ):
        return "storage"
    if role.lower() == "inventory" and owner_class(owner) in CRATE_CLASSES:
        return "crate"
    return "machine"


def _accumulate_inventory(raw, totals: dict) -> None:
    """mInventoryStacks -> {itemClass: total}.

    The ``Item`` member is a bare ``[assetPath, int]`` pair, not a keyed struct.
    """
    for stack in raw if isinstance(raw, list) else []:
        fields = struct_fields(stack)
        item_raw = fields.get("Item")
        if isinstance(item_raw, list) and item_raw:
            item = ref_class(item_raw[0])
        else:
            item = ref_class(struct_fields(item_raw).get("ItemClass"))
        amount = fields.get("NumItems", 0)
        if item and isinstance(amount, (int, float)) and amount:
            totals[item] = totals.get(item, 0) + amount


def list_dir(root: str) -> dict:
    """Header-only scan of every .sav under ``root``, in ONE process.

    Header parsing is milliseconds but process startup is not, so scanning 63 saves
    with one subprocess each would cost seconds. Unsupported saves are bucketed with
    their reason rather than aborting the scan: of 63 real files, 35 are pre-1.0
    (saveHeaderType 1/8/9) and fail here.
    """
    saves: list[dict] = []
    unsupported: list[dict] = []
    for path in sorted(Path(root).rglob("*.sav")):
        try:
            saves.append(header_info(str(path)))
        except Exception as exc:
            st = path.stat()
            unsupported.append(
                {
                    "path": str(path),
                    "filename": path.name,
                    "reason": str(exc),
                    "mtime_ns": st.st_mtime_ns,
                    "size": st.st_size,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "saves": saves,
        "unsupported": unsupported,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(
            json.dumps(
                {"error": "usage: extract_save.py <path.sav> [--header-only] | --list <dir>"}
            )
        )
        return 2
    try:
        if argv[0] == "--list":
            if len(argv) < 2:
                json.dump({"error": "usage: --list <dir>"}, sys.stdout)
                return 2
            json.dump(list_dir(argv[1]), sys.stdout, separators=(",", ":"))
            return 0
        path = argv[0]
        if "--header-only" in argv:
            payload = {"schema_version": SCHEMA_VERSION, "header": header_info(path)}
        else:
            payload = extract(path)
    except PARSE_ERROR as exc:
        json.dump({"error": "parse_error", "detail": str(exc), "path": path}, sys.stdout)
        return 1
    except Exception as exc:  # unexpected: surface the type, keep stdout valid JSON
        traceback.print_exc(file=sys.stderr)
        json.dump({"error": type(exc).__name__, "detail": str(exc), "path": path}, sys.stdout)
        return 1
    json.dump(payload, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
