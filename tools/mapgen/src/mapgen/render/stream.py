"""A pass's bands cut into every layer's tile trees as they settle, so no sheet is kept whole.

With the light, each band goes to its layer's ``unlit/`` at once and waits for the default
sun's terms of its rows (``render/light.py``); then it is relit on the cutter's lanes and
goes to ``tiles/`` and ``tiles@2x/``. Without it, the band is the lit colour and goes there
at once. docs/map/renders.md section 42.
"""

from __future__ import annotations

import time
from collections import deque
from functools import partial
from pathlib import Path
from typing import NamedTuple

from mapgen.palette.lightparams import shader_light
from mapgen.render.light import UNLIT_DIR_NAME, LightingRun, relight_rows
from mapgen.tiles.cutter import Sheet, TileStream, TreeSpec
from mapgen.tiles.pyramid import layer_dir, lit_trees
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["LayerTrees", "RenderStream"]


class LayerTrees(NamedTuple):
    """What a layer's cut installed: ``tiles/``, ``tiles@2x/``, ``unlit/`` (None without the
    light), and the seconds it waited for them after the draw."""

    tiles: JsonObject
    dense: JsonObject
    unlit: JsonObject | None
    seconds: float


class RenderStream:
    """Every layer of a pass cut as its bands settle: its trees staged now, under
    ``out_dir/renders_name/<layer>/``, and each band handed to them by ``put``."""

    def __init__(
        self,
        cutter: TileStream,
        layers: tuple[str, ...],
        out: tuple[Path, str],
        size: int,
        recipe: int,
        light: LightingRun | None,
    ) -> None:
        out_dir, renders_name = out
        self.cutter, self.light = cutter, light
        self.lit: dict[str, Sheet] = {}
        self.unlit: dict[str, Sheet] = {}
        self.waiting: dict[str, deque[tuple[int, U8Grid]]] = {}
        for layer in layers:
            directory = layer_dir(out_dir, layer, renders_name)
            directory.mkdir(parents=True, exist_ok=True)
            tiles, dense = lit_trees(layer, recipe)
            self.lit[layer] = cutter.sheet(directory, size, [tiles], dense)
            if light is not None:
                text = f"tools/gen_map_renders.py, {layer} unlit, Lanczos"
                unlit = TreeSpec(UNLIT_DIR_NAME, PYRAMID_TILE_PX, text)
                self.unlit[layer] = cutter.sheet(directory, size, [unlit])
                self.waiting[layer] = deque()
        if light is not None:
            light.begin(out_dir / renders_name)

    def put(self, top: int, bands: dict[str, U8Grid]) -> None:
        """The band from row ``top`` of every layer, settled: the draw's ``BandSink``."""
        stop = top
        for layer, rgb in bands.items():
            stop = top + rgb.shape[0]
            if self.light is None:
                self.cutter.put(self.lit[layer], rgb)
                continue
            self.cutter.put(self.unlit[layer], rgb)
            self.waiting[layer].append((top, rgb))
        if self.light is not None:
            self.light.drawn(stop)
            self._release()

    def _release(self) -> None:
        """Every waiting band whose rows' terms are in, relit on its lit sheet's lane."""
        if self.light is None:
            return
        ready = self.light.ready()
        for layer, waiting in self.waiting.items():
            params = shader_light(layer)
            while waiting and waiting[0][0] + waiting[0][1].shape[0] <= ready:
                top, rgb = waiting.popleft()
                terms, land = self.light.terms(top, top + rgb.shape[0])
                relight = partial(relight_rows, terms=terms, land=land, params=params)
                self.cutter.put(self.lit[layer], rgb, relight)

    def finish(self) -> None:
        """After the draw: the light finished, and every band it held relit and handed on."""
        if self.light is not None:
            self.light.finish(self._release)
            left = [layer for layer, waiting in self.waiting.items() if waiting]
            if left:
                raise RuntimeError(f"bands of {left} never had their light")

    def install(self, layer: str) -> LayerTrees:
        """Wait for ``layer``'s trees, then rename each into place: ``unlit/``, ``tiles/``,
        ``tiles@2x/``."""
        started = time.time()
        unlit = self.cutter.install(self.unlit[layer])[0] if self.light is not None else None
        tiles, dense = self.cutter.install(self.lit[layer])
        return LayerTrees(tiles, dense, unlit, time.time() - started)
