"""The ``rocks`` command (``heightmap --rocks``): the collision pack beside the field."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from mapgen.gamedata.install import missing_container, open_game
from mapgen.gamedata.level.sweep import sweep_levels
from mapgen.gamedata.meshes import MeshBounds
from mapgen.gamedata.rocks.collision_pack import encode_rock_pack
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue, require_object
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "write_rocks",
]


def write_rocks(args: argparse.Namespace, build_pin: str, build_raw: JsonObject) -> int:
    """``--rocks``: add the collision pack to the field at ``--field``, beside its planes."""
    from satisfactory_mcp.domain.spatial.heightfield import collision_pack as rocks

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
    if (missing := missing_container(args.game)) is not None:
        print(missing)
        return 1
    started = time.time()
    reader = open_game(args.game)
    print("sweeping the world for rock placements and cave floors")
    bounds = MeshBounds(reader.store, reader.scripts, reader.index)
    sweep = sweep_levels(reader.store, reader.scripts, reader.classes, bounds, not args.quiet)
    payload = encode_rock_pack(reader, sweep, build_pin, build_raw)
    for name, blob in payload.items():
        tmp = field_dir / f"{name}.tmp"
        tmp.write_bytes(blob)
        tmp.replace(field_dir / name)
    meta: JsonValue = json.loads(payload[rocks.META_NAME])
    for name, value in require_object(require_object(meta)["counts"]).items():
        print(f"  {name:>26}: {value}")
    total = sum(len(b) for b in payload.values())
    print(f"wrote {field_dir}: {total / 1e6:.1f} MB in {time.time() - started:.0f}s")
    return 0
