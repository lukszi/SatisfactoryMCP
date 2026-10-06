"""The ``caves`` command (``heightmap --caves``): the cave masks beside the field."""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from mapgen.gamedata.caves import (
    CAVE_BUFFER_CELLS,
    CAVE_CELL_CM,
    CAVE_MARKER_DIRS,
    CAVE_MASK_PX,
    build_caves,
    sweep_caves,
)
from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM
from satisfactory_mcp.core.gameassets.container import open_container
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import install_directory, sha256_hex
from satisfactory_mcp.core.gameassets.versions import CAVES_VERSION
from satisfactory_mcp.domain.spatial import caves
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "write_caves",
]


def write_caves(args, build_pin: str, build_raw) -> int:
    """``--caves``: sweep the world for the two cave signals and write ``--caves-dir``."""
    out_dir: Path = args.caves_dir
    if out_dir.exists() and not args.force:
        print(f"{out_dir} already exists. Pass --force to replace it.")
        return 3
    field = hf.load_field(args.field, cache=False)
    if field is None:
        print(
            f"no terrain field at {args.field}: the cave markers are kept only where they "
            "stand under the ground, which needs one. Run this tool without --caves first."
        )
        return 4
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    started = time.time()
    store = open_container(args.game)
    scripts = ScriptObjects(paks, oodle_decompress)
    classes = ClassFacts(store, AssetIndex(store))
    print("sweeping the world for cave sound volumes and cave decoration")
    found = sweep_caves(store, scripts, classes, not args.quiet)
    arrays, counts = build_caves(found, field._height_dm)
    seconds = round(time.time() - started, 1)
    for name, value in counts.items():
        print(f"  {name:>22}: {value}")
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    meta = {
        "description": (
            "Where caves are under this world's single-valued terrain field: a safety flag, "
            "not a floor. Cut from the reader's own install by "
            "tools/gen_world_heightmap.py --caves; never committed."
        ),
        "generator": "tools/gen_world_heightmap.py --caves",
        "caves_version": CAVES_VERSION,
        "transcribed": datetime.now(UTC).strftime("%Y-%m-%d"),
        "sources": {"game": {"game_version_pinned": build_pin, "build_raw": build_raw}},
        "field": {"directory": field.directory.name, "build": field.build},
        "grid": {
            "width": CAVE_MASK_PX,
            "height": CAVE_MASK_PX,
            "cell_cm": CAVE_CELL_CM,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "georeference": "cell (row, col) covers x0 + col*cell .. x0 + (col+1)*cell; floored",
        },
        "mask_bits": {
            str(caves.BIT_MARKERS): (
                f"cave decoration under {CAVE_MARKER_DIRS} more than "
                f"{caves.INSIDE_DEPTH_M:g} m below the ground, buffered by "
                f"{CAVE_BUFFER_CELLS} cells"
            ),
            str(caves.BIT_HULL): "every cell the plan of a cave sound volume's convex hull touches",
        },
        "hulls": (
            "planes (n, 4) as nx, ny, nz, d in cm, outward: inside where n.p + d <= 0; "
            "starts (hulls + 1) slices planes per hull; boxes (hulls, 6) min and max xyz"
        ),
        "counts": counts,
        "seconds": seconds,
        "digest": sha256_hex(buffer.getvalue()),
    }
    written = install_directory(
        out_dir,
        {
            caves.DATA_NAME: buffer.getvalue(),
            caves.META_NAME: json.dumps(meta, indent=1).encode("utf-8"),
        },
    )
    total = sum(written.values())
    print(f"wrote {out_dir}  {total} B  ({total / 1e3:.0f} kB) in {seconds:.0f}s")
    return 0
