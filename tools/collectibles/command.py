"""Generate data/world_collectibles.json from the game's own map assets plus the saves.

The command behind ``tools/gen_world_collectibles.py``: read the map, read the saves of one
session, merge, write, summarise.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from mapgen.common import ROOT, base_parser, require_gen
from satisfactory_mcp.core.gameassets.container import CONTAINER, paks_dir
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import installed_build_from_exe
from tools.collectibles.build import build
from tools.collectibles.catalog import EXCLUDED, NUCLEAR_HOG
from tools.collectibles.hazards import CreatureCatalog, HazardWorld, Radioactivity, build_hazards
from tools.collectibles.map_read import MapWorld, read_map, read_other_levels
from tools.collectibles.report import print_summary
from tools.collectibles.saves import SaveFacts, find_saves, read_save_facts

DEFAULT_SAVES = Path.home() / "AppData/Local/FactoryGame/Saved/SaveGames"
DEFAULT_OUT = ROOT / "data" / "world_collectibles.json"


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "saves",
        nargs="?",
        default=DEFAULT_SAVES,
        type=Path,
        help="directory of .sav files; a per-account subdirectory is searched too",
    )
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help="destination JSON")
    args = parser.parse_args()

    pyooz_version = require_gen("ooz")["pyooz"]

    paks = paks_dir(args.game)
    if not (paks / f"{CONTAINER}.utoc").exists():
        print(f"no {CONTAINER}.utoc under {paks}")
        return 1
    print(f"reading the map from {paks} with pyooz {pyooz_version}")
    scripts = ScriptObjects(paks, oodle_decompress)
    print(
        f"  global.utoc ScriptObjects: {scripts.object_count} objects in "
        f"{scripts.package_count} script packages, {scripts.chunk_bytes / 1e6:.1f} MB"
    )
    store = IoStore(paks, CONTAINER, oodle_decompress)
    print(
        f"  .utoc v{store.version} flags {hex(store.flags)}, {store.entry_count} entries, "
        f"{store.block_count} blocks, {store.block_size // 1024} KiB blocks, "
        f"methods {store.methods}"
    )
    world, hazards, other_levels = read_world(store, scripts)

    loaded = load_session_saves(args.saves, set(world.class_counts))
    if loaded is None:
        return 1
    files_found, readable_saves, session_saves = loaded

    rows, meta = build(
        world,
        hazards,
        session_saves,
        store=store,
        scripts=scripts,
        game_build=installed_build_from_exe(args.game),
        pyooz_version=pyooz_version,
        readable_saves=readable_saves,
        files_found=files_found,
        other_levels=other_levels,
    )
    args.out.write_text(json.dumps({"_meta": meta, "collectibles": rows}, indent=1), "utf-8")
    print_summary(meta, rows, args.out)
    return 0


def read_world(store: IoStore, scripts: ScriptObjects) -> tuple[MapWorld, HazardWorld, list[dict]]:
    """The map's placements, its hazard sources resolved, and the other levels' census."""
    world = read_map(store, scripts)
    print(
        f"  {world.packages_read} packages, {world.actor_count} map-placed actors in "
        f"{len(world.class_counts)} classes "
        f"({sum(world.game_class_counts.values())} of them blueprint-classed, which is what "
        f"a /Game/ walk would see), {len(world.placements)} collectibles, "
        f"{len(world.hazards)} hazard actors, {store.blocks_read} blocks -> "
        f"{store.bytes_out / 1e6:.0f} MB, {world.seconds:.1f}s"
    )
    for reason, count in world.read_problems.most_common(5):
        print(f"  WARNING: {count} x {reason}")
    stale = [cls for cls in EXCLUDED if not world.class_counts.get(cls)]
    if stale:
        print(f"  WARNING: EXCLUDED names {len(stale)} class(es) the map does not place: {stale}")

    index = AssetIndex(store)
    classes = ClassFacts(store, index)
    creatures = CreatureCatalog(store, index, classes)
    resources = {h.label for h in world.hazards if h.kind == "resource" and h.label}
    decay = Radioactivity(classes, index, resources)
    hazards = build_hazards(world.hazards, creatures, decay)
    if NUCLEAR_HOG not in hazards.species:
        print(
            f"  WARNING: no {NUCLEAR_HOG} among the hostile species, so the second radiation "
            f"reason can never fire. Species found: {sorted(hazards.species)}"
        )
    print(
        f"  hazards: {hazards.hostile_placements} hostile placements over "
        f"{len(hazards.species)} species ({hazards.passive_placements} passive ones dropped "
        f"by mIsPassiveCreature), {hazards.spore_flowers} spore flowers, "
        f"{hazards.gas_fields} gas-field actors, {hazards.uranium_sources} radioactive "
        f"sources over {hazards.resource_classes_checked} resource classes checked "
        f"({list(hazards.radioactive_classes)})"
    )
    for cls, declared in hazards.class_declared_radius_cm.items():
        print(
            f"  declared radius: {cls:24} {declared['placements']:>5} placements  "
            f"{declared['distinct_radii']} distinct {declared['radius_cm']}"
        )
    other_levels = read_other_levels(store, scripts)
    stray = [e for e in other_levels if e.get("actors_of_an_emitted_class")]
    print(
        f"  {len(other_levels)} other .umap in the container "
        f"({sum(e.get('actors', 0) for e in other_levels)} actors); "
        f"{len(stray)} of them place an actor of an emitted class"
    )
    if stray:
        print(f"  WARNING: rows are missing -- another level places collectibles: {stray}")
    return world, hazards, other_levels


def load_session_saves(
    saves_dir: Path, map_classes: set[str]
) -> tuple[int, list[SaveFacts], list[SaveFacts]] | None:
    """``(files found, every readable save, the largest session's saves)``, or None.

    The session filter runs before the newest save is chosen, and the newest is chosen by
    the clock it recorded rather than by mtime: an autosave rewritten in place has a fresh
    mtime and an old clock.
    """
    paths = find_saves(saves_dir)
    if not paths:
        print(f"no .sav files under {saves_dir}")
        return None
    print(f"\nreading {len(paths)} save(s) from {saves_dir} for state")
    readable_saves = [
        got
        for got in (read_save_facts(path, map_classes, keep_all_classes=False) for path in paths)
        if got is not None
    ]
    if not readable_saves:
        print("no readable saves")
        return None

    sessions = collections.Counter(f.session for f in readable_saves)
    session_saves = readable_saves
    if len(sessions) > 1:
        keep = sessions.most_common(1)[0][0]
        print(f"  {len(sessions)} sessions present: {dict(sessions)}")
        print(f"  the union of two worlds is not a world; keeping {keep!r} only")
        session_saves = [f for f in readable_saves if f.session == keep]

    newest = max(session_saves, key=lambda f: (f.ticks, f.play_seconds))
    print(f"  newest by save clock: {newest.name} ({newest.when.date()}); re-reading it in full")
    again = read_save_facts(newest.path, map_classes, keep_all_classes=True)
    if again is None:
        print(f"  {newest.name} became unreadable on the second pass")
        return None
    session_saves[session_saves.index(newest)] = again
    return len(paths), readable_saves, session_saves
