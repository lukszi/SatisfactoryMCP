"""A render drawn unlit: the surface it hands the lighting stage, and how each layer installs.

Each layer keeps ``tiles/`` and ``tiles@2x/`` lit by the default sun, so a page without
WebGL and every older reader still draw a lit map, and adds ``unlit/``, the colour the page
relights live. The stage's ``light.cache/`` is scratch for one run. docs/spatial-and-map.md
section 29.
"""

from __future__ import annotations

import argparse
import shutil
import time
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, cast

import numpy as np

from mapgen.cache import held_open
from mapgen.common import Refusal
from mapgen.gamedata.ground.paint_store import CROWN_NAME
from mapgen.lighting.model import DIRECT_SCALE, apply_terms
from mapgen.lighting.occluders import CrownGrid, sheet_crowns
from mapgen.lighting.stage import (
    LIGHT_DIR_NAME,
    Surface,
    bake_light,
    default_terms,
    discard,
    occluder_planes,
)
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.palette.lightparams import shader_light
from mapgen.palette.painted.albedo import load_paint_meta, paint_plane
from mapgen.palette.painted.ground import PaintedGround
from mapgen.palette.painted.shapes import PaintPlane
from mapgen.palette.styles import LAYER_STYLES
from mapgen.tiles.cutter import Cutter, TileImaging
from mapgen.tiles.pyramid import install_layer, layer_dir, queue_layer
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX, install_pyramid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "LIGHT_CACHE_DIR_NAME",
    "SCRATCH_IN_USE",
    "UNLIT_DIR_NAME",
    "CrownTops",
    "LightingRun",
    "Occluder",
    "add_light_flags",
    "claim_scratch",
    "crown_layers",
    "crown_occluder",
    "crown_tops",
    "light_run",
    "relight_in_place",
]

UNLIT_DIR_NAME = "unlit"
LIGHT_CACHE_DIR_NAME = "light.cache"
RELIGHT_ROWS = 512

#: Exit code of a run whose light scratch a render still running holds open.
SCRATCH_IN_USE = 11

#: The crowns the light bake casts: their tops in metres and the share of a pixel covered.
Occluder = tuple[np.ndarray, np.ndarray]


class CrownTops(NamedTuple):
    """The paint store's crown-top plane, decimetres on its 1 m grid, and where it lies."""

    top_dm: PaintPlane
    grid: CrownGrid


def add_light_flags(parser: argparse.ArgumentParser) -> None:
    """``--light`` (the default) or ``--no-light``; ``--unlit``, the old opt-in, is ``--light``."""
    parser.add_argument(
        "--light",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "on by default: draw colour unlit beside a default-sun copy, and bake the lighting "
            "pyramid. --no-light draws the hillshade into the colour and bakes no light"
        ),
    )
    parser.add_argument("--unlit", dest="light", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=None,
        help=(
            f"where the light's {LIGHT_CACHE_DIR_NAME}/ lives while the run lasts (default: "
            "--cache-dir, else beside the renders). Nothing reads it after the run, which "
            "deletes it; at full size it takes about 20 GB, so a fast local disk helps"
        ),
    )


def claim_scratch(args: argparse.Namespace, renders: Path) -> Path | None:
    """Where the run's ``light.cache/`` goes, emptied of a run that died; None without light.

    A render still running keeps its heights mapped, and Windows refuses to rename a mapped
    file, so that scratch is refused rather than emptied under it.
    """
    if not args.light:
        return None
    root: Path = args.scratch_dir or args.cache_dir or renders
    directory = root / LIGHT_CACHE_DIR_NAME
    surface = directory / "z.npy"
    if surface.is_file() and held_open([surface]):
        raise Refusal(
            SCRATCH_IN_USE,
            f"{directory} is the light scratch of a render still running. Wait for it to "
            "finish, or pass --scratch-dir with another directory.",
        )
    shutil.rmtree(directory, ignore_errors=True)
    return root


def relight_in_place(sheet: U8Grid, surface: Surface, params: JsonObject) -> None:
    """Light an unlit sheet by the default sun, a band of rows at a time.

    A style that draws the crowns (``params["crowns"]``) takes the direct term with their
    shadows; every other style the ground's alone.
    """
    terms = default_terms(surface)
    which = 2 if params.get("crowns") else 1
    for top in range(0, sheet.shape[0], RELIGHT_ROWS):
        rows = slice(top, top + RELIGHT_ROWS)
        svf = terms[rows, :, 0].astype(np.float32) / 255.0
        direct = terms[rows, :, which].astype(np.float32) / DIRECT_SCALE
        land = surface.land[rows].astype(np.float32) / 255.0
        sheet[rows] = apply_terms(sheet[rows], svf, direct, land, params)
    del terms


def crown_layers() -> list[str]:
    """The layers that draw the tree crowns, so read the crown horizons."""
    return [layer for layer in LAYER_STYLES if shader_light(layer).get("crowns")]


def crown_tops(paint_dir: Path, painted: PaintedGround | None) -> CrownTops | None:
    """The crown tops the light casts whatever layers a run draws: the painted ground's when
    it is drawn, else the paint store's; None without a store or its crown plane."""
    if painted is not None:
        meta, plane = painted.meta, painted.crown
    else:
        meta = load_paint_meta(paint_dir)
        if meta is None or CROWN_NAME not in meta["files"]:
            return None
        plane = paint_plane(paint_dir, meta, CROWN_NAME)
    return None if plane is None else CrownTops(plane, meta["grid"])


def crown_occluder(crowns: CrownTops | None, scratch_root: Path, size: int) -> Occluder | None:
    """The crown tops and cover on the sheet, written where the bake reads its occluder.

    None without them. The stage reads these files in place: there is no second copy.
    """
    if crowns is None:
        return None
    top, cover = occluder_planes(scratch_root / LIGHT_CACHE_DIR_NAME, size)
    return sheet_crowns(crowns.top_dm, crowns.grid, size, top, cover), cover


class LightingRun:
    """The default ``--light`` run: the surface the first layer captures, which every layer
    captures alike, the light bake, and each layer's ``unlit/`` and relit installs.

    ``light_workers`` bake the light, None counting them from the cores and free memory;
    ``install``'s own ``workers`` encode the tiles.
    """

    def __init__(
        self,
        scratch_root: Path,
        size: int,
        occluder: Occluder | None = None,
        slabs: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
        light_workers: int | None = None,
    ) -> None:
        self.surface = Surface(scratch_root / LIGHT_CACHE_DIR_NAME, size)
        self.occluder, self.slabs = occluder, slabs
        self.light_workers = light_workers
        self.captured = False
        self.meta: JsonObject | None = None
        self.unlit: dict[str, JsonObject] = {}

    def surface_for(self) -> Surface | None:
        """The surface to capture into: only the first layer draws it, any layer the same."""
        if self.captured:
            return None
        self.captured = True
        return self.surface

    def install(
        self,
        sheet: U8Grid,
        image_mod: TileImaging,
        out_dir: Path,
        layer: str,
        workers: int,
        recipe: int,
        renders_name: str,
    ) -> tuple[JsonObject, JsonObject, float]:
        """``install_layer``'s contract, plus ``unlit/``; the first call bakes the light.

        Above one worker the unlit tree encodes while the sheet is relit, from its own copy.
        """
        if self.meta is None:
            self.meta = self._bake(out_dir / renders_name)
        if workers <= 1:
            return self._install_serially(sheet, image_mod, out_dir, layer, recipe, renders_name)
        directory = layer_dir(out_dir, layer, renders_name)
        directory.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with Cutter(image_mod, workers) as cutter:
            with cutter.publish(sheet) as unlit:
                text = f"tools/gen_map_renders.py, {layer} unlit, Lanczos"
                first = cutter.tree(unlit, directory, UNLIT_DIR_NAME, PYRAMID_TILE_PX, text)
            relight_in_place(sheet, self.surface, shader_light(layer))
            with cutter.publish(sheet) as lit:
                text = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
                tiles, dense = queue_layer(cutter, lit, directory, text)
            self.unlit[layer] = cutter.install(first)
            stats, dense_stats = cutter.install(tiles), cutter.install(dense)
        return stats, dense_stats, time.time() - started

    def _bake(self, renders: Path) -> JsonObject:
        print("baking the lighting pyramid", flush=True)
        meta = bake_light(
            self.surface, renders, self.light_workers, self.occluder, self.slabs,
            occluder_layers=crown_layers(),
        )  # fmt: skip
        done, render = cast(JsonObject, meta["tiles"]), cast(JsonObject, meta["render"])
        print(
            f"  light: {done['count']} tiles over z0..z{done['max_z']} "
            f"({cast(int, done['bytes']) / 1e6:.1f} MB) in {render['seconds']}s "
            f"on {render['workers']} workers"
        )
        return meta

    def _install_serially(
        self,
        sheet: U8Grid,
        image_mod: TileImaging,
        out_dir: Path,
        layer: str,
        recipe: int,
        renders_name: str,
    ) -> tuple[JsonObject, JsonObject, float]:
        directory = layer_dir(out_dir, layer, renders_name)
        directory.mkdir(parents=True, exist_ok=True)
        started = time.time()
        source = f"tools/gen_map_renders.py, {layer} unlit, Lanczos"
        self.unlit[layer] = install_pyramid(
            image_mod.fromarray(sheet), image_mod, directory, source=source,
            dir_name=UNLIT_DIR_NAME,
        )  # fmt: skip
        relight_in_place(sheet, self.surface, shader_light(layer))
        stats, dense, _cut = install_layer(
            sheet, image_mod, out_dir, layer, 1, recipe, renders_name
        )
        return stats, dense, time.time() - started

    def decorate(self, sidecar: JsonObject, layer: str) -> None:
        """Name the lighting pyramid and the shader's style fields in a layer's sidecar."""
        meta = sidecar["_meta"]
        if not isinstance(meta, dict):
            return
        meta["light"] = {
            "dir": f"../{LIGHT_DIR_NAME}",
            "unlit_dir": UNLIT_DIR_NAME,
            "unlit_tiles": self.unlit.get(layer),
            "params": shader_light(layer),
            "baked_sun": list(DEFAULT_SUN),
            "role": (
                "tiles/ and tiles@2x/ are lit by baked_sun; unlit/ is the colour the page "
                "relights with the lighting pyramid in dir, for any sun"
            ),
        }
        provenance = meta.get("provenance")
        if isinstance(provenance, dict) and self.meta is not None:
            provenance["light"] = self.meta["light"]

    def close(self) -> None:
        # The occluder is a memory map in the light cache; Windows will not delete it while open.
        self.occluder = None
        self.surface.close()
        discard(self.surface)
        shutil.rmtree(self.surface.directory, ignore_errors=True)


@contextmanager
def light_run(
    root: Path | None, size: int, crowns: CrownTops | None, workers: int | None = None
) -> Generator[LightingRun | None, None, None]:
    """The run's light stage in ``root``, crowns first, its scratch deleted however the run
    ends, the crowns' and the surface's failures included; or None without the light."""
    if root is None:
        yield None
        return
    run = None
    try:
        run = LightingRun(root, size, crown_occluder(crowns, root, size), light_workers=workers)
        yield run
    except BaseException as exc:
        # The failed frames hold the scratch's memory maps, which Windows will not delete.
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        if run is not None:
            run.close()
        else:
            shutil.rmtree(root / LIGHT_CACHE_DIR_NAME, ignore_errors=True)
