"""The ``rocks`` command (``heightmap --rocks``): the collision pack beside the field."""

from __future__ import annotations

import json
import time
from pathlib import Path

from mapgen.gamedata.mesh import MeshBounds
from mapgen.gamedata.rocks import rock_pack
from mapgen.gamedata.sweep import sweep_levels
from satisfactory_mcp.core.gameassets.container import open_container
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "write_rocks",
]


def write_rocks(args, build_pin: str, build_raw) -> int:
    """``--rocks``: add the collision pack to the field at ``--field``, beside its planes."""
    from satisfactory_mcp.domain.spatial import rocks

    field_dir: Path = args.field
    field = hf.load_field(field_dir, cache=False)
    if field is None:
        print(f"no terrain field at {field_dir}; run this tool without --rocks first.")
        return 4
    if field.build != build_pin:
        print(f"the field at {field_dir} is from {field.build}, not the installed {build_pin}.")
        return 3
    targets = [field_dir / rocks.DATA_NAME, field_dir / rocks.META_NAME]
    if any(t.exists() for t in targets) and not args.force:
        print(f"{field_dir} already has a collision pack. Pass --force to replace it.")
        return 3
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    started = time.time()
    store = open_container(args.game)
    scripts = ScriptObjects(paks, oodle_decompress)
    index = AssetIndex(store)
    classes = ClassFacts(store, index)
    print("sweeping the world for rock placements and cave floors")
    sweep = sweep_levels(store, scripts, classes, MeshBounds(store, scripts, index), not args.quiet)
    payload = rock_pack(store, scripts, index, classes, sweep, build_pin, build_raw)
    for name, blob in payload.items():
        tmp = field_dir / f"{name}.tmp"
        tmp.write_bytes(blob)
        tmp.replace(field_dir / name)
    counts = json.loads(payload[rocks.META_NAME])["counts"]
    for name, value in counts.items():
        print(f"  {name:>26}: {value}")
    total = sum(len(b) for b in payload.values())
    print(f"wrote {field_dir}: {total / 1e6:.1f} MB in {time.time() - started:.0f}s")
    return 0
