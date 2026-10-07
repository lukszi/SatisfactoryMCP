"""A drawn layer's tile trees, the cut's pool flags, and the parallel cutter's self-check."""

from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from mapgen.common import RENDERS_DIR_NAME
from mapgen.gamedata.frame import RENDER_2X_PX
from mapgen.lighting.stage import LIGHT_WORKER_BYTES, LIGHT_WORKER_CAP
from mapgen.tiles.cutter import CUT_WORKERS, TileStream, TreeSpec
from mapgen.tiles.imaging import TileImaging
from mapgen.tiles.recipes import RECIPE
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    install_pyramid,
)
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "ParallelCheck",
    "add_worker_flags",
    "check_parallel",
    "layer_dir",
    "lit_trees",
    "pool_sizes",
    "tree_megabytes",
    "tree_text",
]


def add_worker_flags(parser: argparse.ArgumentParser) -> None:
    """``--light-workers`` and ``--cut-workers``, and ``--workers``, the default for both."""
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="processes for --light-workers and --cut-workers, where either is not given",
    )
    parser.add_argument(
        "--light-workers",
        type=int,
        default=None,
        help=(
            f"processes baking the light (default: one a core, at most {LIGHT_WORKER_CAP}, and "
            f"no more than the free memory holds at {LIGHT_WORKER_BYTES / 1e9:.1f} GB each)"
        ),
    )
    parser.add_argument(
        "--cut-workers",
        type=int,
        default=None,
        help=(
            f"processes encoding tiles (default {CUT_WORKERS}, fewer when memory is short; "
            "1 cuts serially). The tiles are the same bytes either way"
        ),
    )


def pool_sizes(args: argparse.Namespace) -> tuple[int | None, int]:
    """The light bake's processes and the cut's encoders: each flag, else ``--workers``.

    None for the bake leaves it to count them from the cores and free memory when it starts.
    """
    light = args.workers if args.light_workers is None else args.light_workers
    cut = args.workers if args.cut_workers is None else args.cut_workers
    return None if light is None else max(1, light), max(1, CUT_WORKERS if cut is None else cut)


def layer_dir(out_dir: Path, layer: str, renders_name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / renders_name / layer


def tree_megabytes(tree: JsonObject) -> float:
    """The megabytes a tile tree's install record says it wrote."""
    size = tree.get("bytes")
    return size / 1e6 if isinstance(size, int | float) else 0.0


def tree_text(tree: JsonObject, noun: str) -> str:
    """``N <noun> over z0..zM (S MB)`` for a tile tree an install recorded."""
    return f"{tree['count']} {noun} over z0..z{tree['max_z']} ({tree_megabytes(tree):.1f} MB)"


def lit_trees(layer: str, recipe: int = RECIPE) -> tuple[TreeSpec, tuple[TreeSpec, int]]:
    """A drawn layer's ``tiles/``, and ``tiles@2x/`` cut from the sheet downscaled to at most
    ``RENDER_2X_PX``.

    Cut from the full sheet the @2x tree would gain a z6 of 512 px tiles weighing as much as
    the whole 1x pyramid. That downscale is the 1x level of the same size, resampled once.
    """
    text = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
    dense = TreeSpec(TILES_2X_DIR_NAME, PYRAMID_TILE_2X_PX, text)
    return TreeSpec(TILES_DIR_NAME, PYRAMID_TILE_PX, text), (dense, RENDER_2X_PX)


@dataclass(frozen=True)
class ParallelCheck:
    """One pyramid cut serially and in parallel, and whether every tile's bytes agreed.

    ``differing_tiles`` names the first eight that did not.
    """

    levels: list[int]
    tiles: int
    seconds_serial: float
    seconds_parallel: float
    workers: int
    byte_identical: bool
    differing_tiles: list[str]

    @property
    def speedup(self) -> float:
        return round(self.seconds_serial / max(self.seconds_parallel, 1e-9), 2)

    def record(self) -> JsonObject:
        """The check as the render sidecar keeps it."""
        return {
            "levels": list[JsonValue](self.levels),
            "tiles": self.tiles,
            "seconds_serial": self.seconds_serial,
            "seconds_parallel": self.seconds_parallel,
            "speedup": self.speedup,
            "workers": self.workers,
            "byte_identical": self.byte_identical,
            "differing_tiles": list[JsonValue](self.differing_tiles),
            "method": (
                "the whole pyramid cut both ways into two scratch directories, SHA-256 of every "
                "tile compared name by name: Pillow's resize of the whole sheet and one process "
                "against the strip resampling and the encode pool"
            ),
        }


def check_parallel(
    sheet_rgb: U8Grid, image_mod: TileImaging, scratch: Path, workers: int
) -> ParallelCheck:
    """Cut one pyramid twice -- serially and in parallel -- and compare every tile's SHA-256.

    The parallel cutter's claim is **identical bytes** rather than equivalence, which a hash
    settles. On demand rather than every run: it costs two extra cuts of the sheet, and it
    guards against a change to the cutter, not against a flaky machine.
    """
    serial_dir, parallel_dir = scratch / "serial", scratch / "parallel"
    serial_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    install_pyramid(image_mod.fromarray(sheet_rgb), image_mod, serial_dir)
    seconds_serial = round(time.time() - started, 2)
    serial = _tile_digests(serial_dir)
    parallel_dir.mkdir(parents=True, exist_ok=True)
    px = sheet_rgb.shape[0]
    with TileStream(image_mod, workers) as stream:
        stream.warm()
        started = time.time()
        sheet = stream.sheet(parallel_dir, px, [TreeSpec(TILES_DIR_NAME, PYRAMID_TILE_PX, "check")])
        for top in range(0, px, PYRAMID_TILE_PX):
            stream.put(sheet, sheet_rgb[top : top + PYRAMID_TILE_PX])
        stream.install(sheet)
    seconds_parallel = round(time.time() - started, 2)
    parallel = _tile_digests(parallel_dir)
    shutil.rmtree(scratch, ignore_errors=True)
    return ParallelCheck(
        levels=sorted({int(name.split("/")[1]) for name in serial}),
        tiles=len(serial),
        seconds_serial=seconds_serial,
        seconds_parallel=seconds_parallel,
        workers=workers,
        byte_identical=serial == parallel,
        differing_tiles=sorted(name for name in serial if serial[name] != parallel.get(name))[:8],
    )


def _tile_digests(root: Path) -> dict[str, str]:
    """Every PNG under ``root`` by its relative path, and the SHA-256 of its bytes."""
    return {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.png"))
    }
