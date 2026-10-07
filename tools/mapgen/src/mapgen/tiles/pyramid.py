"""Cutting a drawn layer into its two tile pyramids, and the parallel cutter's self-check."""

from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import numpy as np

from mapgen.common import RENDERS_DIR_NAME
from mapgen.gamedata.frame import RENDER_2X_PX
from mapgen.lighting.stage import LIGHT_WORKER_BYTES, LIGHT_WORKER_CAP
from mapgen.tiles.cutter import CUT_WORKERS, Cutter, Source, Tree
from mapgen.tiles.recipes import RECIPE
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    install_pyramid,
)
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = [
    "ParallelCheck",
    "add_worker_flags",
    "check_parallel",
    "install_layer",
    "layer_dir",
    "pool_sizes",
    "queue_layer",
    "tree_megabytes",
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


def queue_layer(cutter: Cutter, source: Source, directory: Path, text: str) -> tuple[Tree, Tree]:
    """Queue ``tiles/``, then ``tiles@2x/`` cut from a downscale capped at ``RENDER_2X_PX``.

    Cut from the full sheet the @2x tree would gain a z6 of 512 px tiles weighing as much as
    the whole 1x pyramid. That downscale is the 1x level of the same size, resampled once.
    """
    tiles = cutter.tree(source, directory, TILES_DIR_NAME, PYRAMID_TILE_PX, text)
    dense = source.derive(min(source.px, RENDER_2X_PX))
    return tiles, cutter.tree(dense, directory, TILES_2X_DIR_NAME, PYRAMID_TILE_2X_PX, text)


def install_layer(
    sheet_rgb: np.ndarray,
    image_mod: ModuleType,
    out_dir: Path,
    layer: str,
    workers: int,
    recipe: int = RECIPE,
    renders_name: str = RENDERS_DIR_NAME,
) -> tuple[JsonObject, JsonObject, float]:
    """Cut one layer's two pyramids into place, and say what they wrote and how long it took.

    ``tiles/`` first, because that is what every client can read, then ``tiles@2x/``, which
    a client that cannot find it simply asks for the 1x instead. Each is renamed into place
    on its own, so a run that dies between them never leaves the page without a base map.
    ``workers`` encode; one cuts serially with ``install_pyramid``, the reference.
    """
    directory = layer_dir(out_dir, layer, renders_name)
    directory.mkdir(parents=True, exist_ok=True)
    text = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
    started = time.time()
    if workers <= 1:
        stats, dense = serial_layer(image_mod.fromarray(sheet_rgb), image_mod, directory, text)
        return stats, dense, time.time() - started
    with Cutter(image_mod, workers) as cutter:
        with cutter.publish(sheet_rgb) as source:
            trees = queue_layer(cutter, source, directory, text)
        stats, dense = (cutter.install(tree) for tree in trees)
    return stats, dense, time.time() - started


def serial_layer(
    sheet: Image, image_mod: ModuleType, directory: Path, text: str
) -> tuple[JsonObject, JsonObject]:
    """``queue_layer``'s two trees, one tile at a time in this process."""
    stats = install_pyramid(sheet, image_mod, directory, source=text)
    dense_px = min(sheet.width, RENDER_2X_PX)
    dense_sheet = (
        sheet if dense_px == sheet.width else sheet.resize((dense_px, dense_px), image_mod.LANCZOS)
    )
    dense = install_pyramid(
        dense_sheet,
        image_mod,
        directory,
        tile_px=PYRAMID_TILE_2X_PX,
        source=text,
        dir_name=TILES_2X_DIR_NAME,
    )
    return stats, dense


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
    sheet_rgb: np.ndarray, image_mod: ModuleType, scratch: Path, workers: int
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
    with Cutter(image_mod, workers) as cutter:
        # Wake every encoder before the clock starts: spawning interpreters that each import
        # numpy costs more than a small cut.
        list(cutter.encoders.map(int, range(cutter.workers)))
        started = time.time()
        with cutter.publish(sheet_rgb) as source:
            tree = cutter.tree(source, parallel_dir, TILES_DIR_NAME, PYRAMID_TILE_PX, "check")
        cutter.install(tree)
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
