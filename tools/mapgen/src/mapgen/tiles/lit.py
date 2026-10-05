"""A render drawn unlit: the surface it hands the lighting stage, and how each layer installs.

Each layer keeps ``tiles/`` and ``tiles@2x/`` lit by the default sun, so a page without
WebGL and every older reader still draw a lit map, and adds ``unlit/``, the colour the page
relights live. docs/spatial-and-map.md section 28.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np

from mapgen.lighting.model import DIRECT_SCALE, apply_terms
from mapgen.lighting.stage import LIGHT_DIR_NAME, Surface, bake_light, default_terms, discard
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.palette.lightparams import shader_light
from mapgen.tiles.pyramid import install_layer, layer_dir
from satisfactory_mcp.core.gameassets.pyramid import install_pyramid

__all__ = ["LIGHT_CACHE_DIR_NAME", "UNLIT_DIR_NAME", "UnlitRun", "relight_in_place"]

UNLIT_DIR_NAME = "unlit"
LIGHT_CACHE_DIR_NAME = "light.cache"
RELIGHT_ROWS = 512


def relight_in_place(sheet: np.ndarray, surface: Surface, params: dict) -> None:
    """Light an unlit sheet by the default sun, a band of rows at a time."""
    terms = default_terms(surface)
    for top in range(0, sheet.shape[0], RELIGHT_ROWS):
        rows = slice(top, top + RELIGHT_ROWS)
        svf = terms[rows, :, 0].astype(np.float32) / 255.0
        direct = terms[rows, :, 1].astype(np.float32) / DIRECT_SCALE
        land = surface.land[rows].astype(np.float32) / 255.0
        sheet[rows] = apply_terms(sheet[rows], svf, direct, land, params)
    del terms


class UnlitRun:
    """One ``--unlit`` run: the surface the first layer captures, the bake, the installs."""

    def __init__(self, cache_root: Path, size: int, occluder=None, slabs=None) -> None:
        self.surface = Surface(cache_root / LIGHT_CACHE_DIR_NAME, size)
        self.occluder, self.slabs = occluder, slabs
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
        """``install_layer``'s contract, plus ``unlit/``; the first call bakes the light."""
        if self.meta is None:
            print("baking the lighting pyramid", flush=True)
            self.meta = bake_light(self.surface, out_dir / name, workers, self.occluder, self.slabs)
            done = self.meta["tiles"]
            print(
                f"  light: {done['count']} tiles over z0..z{done['max_z']} "
                f"({done['bytes'] / 1e6:.1f} MB) in {self.meta['render']['seconds']}s"
            )
        directory = layer_dir(out_dir, layer, name)
        directory.mkdir(parents=True, exist_ok=True)
        started = time.time()
        source = f"tools/gen_map_renders.py, {layer} unlit, Lanczos"
        self.unlit[layer] = install_pyramid(
            image_mod.fromarray(sheet), image_mod, directory, source=source, workers=workers,
            dir_name=UNLIT_DIR_NAME,
        )  # fmt: skip
        relight_in_place(sheet, self.surface, shader_light(layer))
        stats, dense, _cut = install_layer(sheet, image_mod, out_dir, layer, workers, recipe, name)
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
        self.surface.close()
        discard(self.surface)
        shutil.rmtree(self.surface.directory, ignore_errors=True)
