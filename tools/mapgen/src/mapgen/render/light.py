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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from mapgen.cache import held_open
from mapgen.lighting.model import DIRECT_SCALE, apply_terms
from mapgen.lighting.occluders import sheet_crowns
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
from mapgen.palette.styles import LAYER_STYLES
from mapgen.tiles.cutter import Cutter
from mapgen.tiles.pyramid import install_layer, layer_dir, queue_layer
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX, install_pyramid

__all__ = [
    "LIGHT_CACHE_DIR_NAME",
    "UNLIT_DIR_NAME",
    "LightingRun",
    "add_light_flags",
    "claim_scratch",
    "crown_layers",
    "crown_occluder",
    "light_run",
    "relight_in_place",
]

UNLIT_DIR_NAME = "unlit"
LIGHT_CACHE_DIR_NAME = "light.cache"
RELIGHT_ROWS = 512


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
    root = args.scratch_dir or args.cache_dir or renders
    directory = root / LIGHT_CACHE_DIR_NAME
    surface = directory / "z.npy"
    if surface.is_file() and held_open([surface]):
        raise SystemExit(
            f"{directory} is the light scratch of a render still running. Wait for it to "
            "finish, or pass --scratch-dir with another directory."
        )
    shutil.rmtree(directory, ignore_errors=True)
    return root


def relight_in_place(sheet: np.ndarray, surface: Surface, params: dict) -> None:
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


def crown_occluder(painted, cache_root: Path, size: int):
    """The painted ground's crown tops and cover, written where the bake reads its occluder.

    None without them. The stage reads these files in place: there is no second copy.
    """
    crown = getattr(painted, "crown", None)
    if crown is None:
        return None
    top, cover = occluder_planes(cache_root / LIGHT_CACHE_DIR_NAME, size)
    return sheet_crowns(crown, painted.meta["grid"], size, top, cover), cover


class LightingRun:
    """The default ``--light`` run: the surface the first layer captures, the light bake,
    and each layer's ``unlit/`` and relit installs.

    ``light_workers`` bake the light, None counting them from the cores and free memory;
    ``install``'s own ``workers`` encode the tiles.
    """

    def __init__(self, cache_root: Path, size: int, occluder=None, slabs=None,
                 light_workers: int | None = None) -> None:  # fmt: skip
        self.surface = Surface(cache_root / LIGHT_CACHE_DIR_NAME, size)
        self.occluder, self.slabs = occluder, slabs
        self.light_workers = light_workers
        self.captured = False
        self.meta: dict | None = None
        self.unlit: dict[str, dict] = {}

    def surface_for(self) -> Surface | None:
        """The surface to capture into: only the first layer draws it, all draw the same."""
        if self.captured:
            return None
        self.captured = True
        return self.surface

    def install(self, sheet, image_mod, out_dir: Path, layer: str, workers: int, recipe: int,
                name: str) -> tuple[dict, dict, float]:  # fmt: skip
        """``install_layer``'s contract, plus ``unlit/``; the first call bakes the light.

        Above one worker the unlit tree encodes while the sheet is relit, from its own copy.
        """
        if self.meta is None:
            print("baking the lighting pyramid", flush=True)
            self.meta = bake_light(
                self.surface, out_dir / name, self.light_workers, self.occluder, self.slabs,
                occluder_layers=crown_layers(),
            )  # fmt: skip
            done, render = self.meta["tiles"], self.meta["render"]
            print(
                f"  light: {done['count']} tiles over z0..z{done['max_z']} "
                f"({done['bytes'] / 1e6:.1f} MB) in {render['seconds']}s "
                f"on {render['workers']} workers"
            )
        if workers <= 1:
            return self._install_serially(sheet, image_mod, out_dir, layer, recipe, name)
        directory = layer_dir(out_dir, layer, name)
        directory.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with Cutter(image_mod, workers) as cutter:
            with cutter.publish(sheet) as unlit:
                text = f"tools/gen_map_renders.py, {layer} unlit, Lanczos"
                first = cutter.tree(unlit, directory, UNLIT_DIR_NAME, PYRAMID_TILE_PX, text)
            relight_in_place(sheet, self.surface, shader_light(layer))
            with cutter.publish(sheet) as lit:
                text = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
                trees = queue_layer(cutter, lit, directory, text)
            self.unlit[layer] = cutter.install(first)
            stats, dense = (cutter.install(tree) for tree in trees)
        return stats, dense, time.time() - started

    def _install_serially(self, sheet, image_mod, out_dir: Path, layer: str, recipe: int,
                          name: str) -> tuple[dict, dict, float]:  # fmt: skip
        directory = layer_dir(out_dir, layer, name)
        directory.mkdir(parents=True, exist_ok=True)
        started = time.time()
        source = f"tools/gen_map_renders.py, {layer} unlit, Lanczos"
        self.unlit[layer] = install_pyramid(
            image_mod.fromarray(sheet), image_mod, directory, source=source,
            dir_name=UNLIT_DIR_NAME,
        )  # fmt: skip
        relight_in_place(sheet, self.surface, shader_light(layer))
        stats, dense, _cut = install_layer(sheet, image_mod, out_dir, layer, 1, recipe, name)
        return stats, dense, time.time() - started

    def decorate(self, sidecar: dict, layer: str) -> None:
        """Name the lighting pyramid and the shader's style fields in a layer's sidecar."""
        meta = sidecar["_meta"]
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
        if isinstance(meta.get("provenance"), dict) and self.meta is not None:
            meta["provenance"]["light"] = self.meta["light"]

    def close(self) -> None:
        # The occluder is a memory map in the light cache; Windows will not delete it while open.
        self.occluder = None
        self.surface.close()
        discard(self.surface)
        shutil.rmtree(self.surface.directory, ignore_errors=True)


@contextmanager
def light_run(root: Path | None, size: int, painted,
              workers: int | None = None) -> Iterator[LightingRun | None]:  # fmt: skip
    """The run's light stage in ``root``, crowns first, closed however the run ends; or None."""
    run = None
    if root is not None:
        run = LightingRun(root, size, crown_occluder(painted, root, size), light_workers=workers)
    try:
        yield run
    finally:
        if run is not None:
            run.close()
