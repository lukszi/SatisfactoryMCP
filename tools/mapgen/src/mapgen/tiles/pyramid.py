"""Cutting a drawn layer into its two tile pyramids, and the parallel cutter's self-check.

Moved verbatim from ``tools/gen_map_renders.py``.
"""

from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from mapgen.common import RENDERS_DIR_NAME
from mapgen.gamedata.frame import RENDER_2X_PX
from mapgen.tiles.recipes import RECIPE
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    cut_square,
    cut_square_parallel,
    install_pyramid,
)

__all__ = [
    "CHECK_PARALLEL_Z",
    "DEFAULT_WORKERS",
    "WORKER_CAP",
    "check_parallel",
    "install_layer",
    "layer_dir",
]

#: How many processes deflate tiles when ``--workers`` is not given. One per core, capped
#: because past a point the cores wait on the disk rather than on zlib, and each interpreter
#: pays to import numpy before it writes a PNG.
WORKER_CAP = 16
DEFAULT_WORKERS = min(os.cpu_count() or 1, WORKER_CAP)

#: Which level ``--check-parallel`` cuts twice. z5 is 1,024 tiles, so the timing means
#: something, and every supported sheet size has it.
CHECK_PARALLEL_Z = 5

# --------------------------------------------------------------------------------------
# Installing a layer.
# --------------------------------------------------------------------------------------


def layer_dir(out_dir: Path, layer: str, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / layer


def install_layer(
    sheet_rgb,
    image_mod,
    out_dir: Path,
    layer: str,
    workers: int,
    recipe: int = RECIPE,
    name: str = RENDERS_DIR_NAME,
) -> tuple[dict, dict, float]:
    """Cut one layer's two pyramids into place, and say what they wrote and how long it took.

    ``tiles/`` first, because that is what every client can read, then ``tiles@2x/``, which
    a client that cannot find it simply asks for the 1x instead. Each is renamed into place
    on its own, so a run that dies between them never leaves the page without a base map.

    The @2x tree is cut from a **downscale** of the sheet, capped at ``RENDER_2X_PX``: cut
    from the full sheet it would gain a z6 of 512 px tiles weighing as much as the whole 1x
    pyramid, for pixels a hi-DPI client already gets by asking for ``z + 1`` at 1x.
    """
    directory = layer_dir(out_dir, layer, name)
    directory.mkdir(parents=True, exist_ok=True)
    sheet = image_mod.fromarray(sheet_rgb)
    source = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
    started = time.time()
    stats = install_pyramid(sheet, image_mod, directory, source=source, workers=workers)
    dense_px = min(sheet.width, RENDER_2X_PX)
    dense_sheet = (
        sheet if dense_px == sheet.width else sheet.resize((dense_px, dense_px), image_mod.LANCZOS)
    )
    dense = install_pyramid(
        dense_sheet,
        image_mod,
        directory,
        tile_px=PYRAMID_TILE_2X_PX,
        source=source,
        workers=workers,
        dir_name=TILES_2X_DIR_NAME,
    )
    return stats, dense, time.time() - started


def check_parallel(sheet_rgb, image_mod, scratch: Path, workers: int) -> dict:
    """Cut one level twice -- serially and in parallel -- and compare every tile's SHA-256.

    The parallel cutter's claim is **identical bytes** rather than equivalence, which a hash
    settles. On demand rather than every run: it costs one extra cut of one level and it
    guards against a change to the cutter, not against a flaky machine.
    """
    from hashlib import sha256

    sheet = image_mod.fromarray(sheet_rgb)
    level = sheet.resize((PYRAMID_TILE_PX << CHECK_PARALLEL_Z,) * 2, image_mod.LANCZOS)
    digests = {}
    timings = {}
    for name, jobs in (("serial", 1), ("parallel", workers)):
        dest = scratch / name
        dest.mkdir(parents=True, exist_ok=True)
        if jobs > 1:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                # Wake every worker before the clock starts: spawning interpreters that each
                # import numpy costs more than the cutting being measured.
                list(pool.map(int, range(jobs)))
                started = time.time()
                cut_square_parallel(level, dest, CHECK_PARALLEL_Z, PYRAMID_TILE_PX, pool)
                timings[name] = round(time.time() - started, 2)
        else:
            started = time.time()
            cut_square(level, dest, CHECK_PARALLEL_Z, 0, 0, PYRAMID_TILE_PX)
            timings[name] = round(time.time() - started, 2)
        digests[name] = {
            str(path.relative_to(dest)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest()
            for path in sorted(dest.rglob("*.png"))
        }
    same = digests["serial"] == digests["parallel"]
    shutil.rmtree(scratch, ignore_errors=True)
    return {
        "level": CHECK_PARALLEL_Z,
        "tiles": len(digests["serial"]),
        "seconds_serial": timings["serial"],
        "seconds_parallel": timings["parallel"],
        "speedup": round(timings["serial"] / max(timings["parallel"], 1e-9), 2),
        "workers": workers,
        "byte_identical": same,
        "differing_tiles": sorted(
            name
            for name in digests["serial"]
            if digests["serial"][name] != digests["parallel"].get(name)
        )[:8],
        "method": (
            "the same level cut both ways into two scratch directories, SHA-256 of every "
            "tile compared name by name. The parallel path resamples nothing -- it is handed "
            "the level already resized -- so this is an identity, not a tolerance."
        ),
    }
