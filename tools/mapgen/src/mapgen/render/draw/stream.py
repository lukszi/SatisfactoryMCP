"""A pass's bands cut into every layer's tile trees as they settle, so no sheet is kept whole.

With the light, each band goes to its layer's ``unlit/`` at once and waits for the default
sun's terms of its rows (``render/draw/light.py``); then it is relit on the cutter's lanes and
goes to ``tiles/`` and ``tiles@2x/``. Without it, the band is the lit colour and goes there
at once. A band near an arch waits for the next band's first rows, so the arches' FXAA reads
past its edges (``render/draw/archaa.py``). docs/map/renders.md section 42.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import NamedTuple, TypeAlias

import numpy as np

from mapgen.cache import Plane
from mapgen.palette.lightparams import shader_light
from mapgen.render.draw.archaa import FXAA_HALO, arch_fxaa
from mapgen.render.draw.light import UNLIT_DIR_NAME, LightingRun, relight_rows
from mapgen.tiles.cutter import Sheet, TileStream, TreeSpec
from mapgen.tiles.pyramid import layer_dir, lit_trees
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["LayerTrees", "RenderOut", "RenderStream"]

#: A band of a layer waiting for its turn: its first row and its rows.
_Band: TypeAlias = tuple[int, U8Grid]
#: What a sheet's lane does to a band before it takes it.
Transform: TypeAlias = Callable[[U8Grid], U8Grid]


class LayerTrees(NamedTuple):
    """What a layer's cut installed: ``tiles/``, ``tiles@2x/``, ``unlit/`` (None without the
    light), and the seconds it waited for them after the draw."""

    tiles: JsonObject
    dense: JsonObject
    unlit: JsonObject | None
    seconds: float


class RenderOut(NamedTuple):
    """Where a run's layers go: ``out_dir/renders_name/<layer>/``."""

    out_dir: Path
    renders_name: str


class _Lane(NamedTuple):
    """A layer's sheet a band goes to: its first, or with the light its relit one."""

    layer: str
    relit: bool


class RenderStream:
    """Every layer of a pass cut as its bands settle: its trees staged now, under
    ``out_dir/renders_name/<layer>/``, and each band handed to them by ``put``.

    ``arches`` is the arches' coverage on the sheet; a band near one is antialiased there.
    """

    def __init__(
        self,
        cutter: TileStream,
        layers: tuple[str, ...],
        out: RenderOut,
        size: int,
        recipe: int,
        light: LightingRun | None,
        arches: Plane | None = None,
    ) -> None:
        out_dir, renders_name = out
        self.cutter, self.light, self.size, self.arches = cutter, light, size, arches
        self.lit: dict[str, Sheet] = {}
        self.unlit: dict[str, Sheet] = {}
        self.first: dict[str, deque[_Band]] = {}
        self.waiting: dict[str, deque[_Band]] = {}
        self.tails: dict[_Lane, U8Grid | None] = {}
        for layer in layers:
            directory = layer_dir(out_dir, layer, renders_name)
            directory.mkdir(parents=True, exist_ok=True)
            tiles, dense = lit_trees(layer, recipe)
            self.lit[layer] = cutter.sheet(directory, size, [tiles], dense)
            self.first[layer] = deque()
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
            self.first[layer].append((top, rgb))
            self._release_first(layer)
            if self.light is not None:
                self.waiting[layer].append((top, rgb))
        if self.light is not None:
            self.light.drawn(stop)
            self._release()

    def _cover(self, top: int, stop: int) -> tuple[int, int, U8Grid] | None:
        """The arches' coverage over rows ``top`` to ``stop`` and ``FXAA_HALO`` either side,
        cut to the sheet: ``(first row, last row, coverage)``; None where no arch is."""
        if self.arches is None:
            return None
        r0, r1 = max(top - FXAA_HALO, 0), min(stop + FXAA_HALO, self.size)
        cover = np.asarray(self.arches[r0:r1], np.uint8)
        return (r0, r1, cover) if cover.any() else None

    def _halo_ready(self, queue: deque[_Band], ahead: int) -> bool:
        """Whether the band at the head of ``queue`` may go: not near an arch, or the band
        after it is in, or it is the sheet's last; and ``ahead`` holds its rows past it."""
        top, rgb = queue[0]
        stop = top + rgb.shape[0]
        if self._cover(top, stop) is None:
            return True
        end = min(stop + FXAA_HALO, self.size)
        return (len(queue) > 1 or stop >= self.size) and ahead >= end

    def _pop_with_halo(self, lane: _Lane, queue: deque[_Band]) -> _Halo | None:
        """The head of ``queue``, popped: what its arches are antialiased with (its neighbours'
        rows and the arches' coverage), or None for a band no arch is near. Its tail is kept."""
        top, rgb = queue.popleft()
        stop = top + rgb.shape[0]
        before, self.tails[lane] = self.tails.get(lane), rgb[-FXAA_HALO:]
        found = self._cover(top, stop)
        if found is None:
            return None
        r0, r1, cover = found
        tail = queue[0][1][: r1 - stop] if queue else rgb[:0]
        head = before[before.shape[0] - (top - r0) :] if before is not None else rgb[:0]
        return _Halo(head, tail, cover)

    def _release_first(self, layer: str) -> None:
        """Every band of ``layer`` ready for its first sheet, in order: ``unlit/`` with the
        light, else the lit trees."""
        queue = self.first[layer]
        sheet = self.unlit[layer] if self.light is not None else self.lit[layer]
        while queue and self._halo_ready(queue, self.size):
            rgb = queue[0][1]
            halo = self._pop_with_halo(_Lane(layer, relit=False), queue)
            self.cutter.put(sheet, rgb, None if halo is None else partial(_with_halo, halo=halo))

    def _release(self) -> None:
        """Every waiting band whose rows' terms are in, relit on its lit sheet's lane."""
        if self.light is None:
            return
        ready = self.light.collect()
        for layer, waiting in self.waiting.items():
            params = shader_light(layer)
            while waiting and waiting[0][0] + waiting[0][1].shape[0] <= ready:
                if not self._halo_ready(waiting, ready):
                    break
                top, rgb = waiting[0]
                halo = self._pop_with_halo(_Lane(layer, relit=True), waiting)
                rows = (top - _rows(halo, "head"), top + rgb.shape[0] + _rows(halo, "tail"))
                terms, land = _terms(self.light, *rows)
                relight: Transform = partial(relight_rows, terms=terms, land=land, params=params)
                if halo is not None:
                    relight = partial(_relit_with_halo, halo=halo, relight=relight)
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


class _Halo(NamedTuple):
    """What a band's arches are antialiased with: the rows of the band before it and after
    it the filter reads, and the arches' coverage over all of them."""

    head: U8Grid
    tail: U8Grid
    cover: U8Grid


def _rows(halo: _Halo | None, side: str) -> int:
    if halo is None:
        return 0
    return int((halo.head if side == "head" else halo.tail).shape[0])


def _with_halo(rgb: U8Grid, halo: _Halo) -> U8Grid:
    """A band antialiased on its arches with its neighbours' rows."""
    whole = np.concatenate([halo.head, rgb, halo.tail])
    core = slice(halo.head.shape[0], halo.head.shape[0] + rgb.shape[0])
    return arch_fxaa(whole, halo.cover, core)


def _relit_with_halo(rgb: U8Grid, halo: _Halo, relight: Transform) -> U8Grid:
    """A band and its neighbours' rows relit (``relight`` holds the terms of all of them),
    then antialiased on its arches."""
    lit = relight(np.concatenate([halo.head, rgb, halo.tail]))
    core = slice(halo.head.shape[0], halo.head.shape[0] + rgb.shape[0])
    return arch_fxaa(lit, halo.cover, core)


def _terms(light: LightingRun, r0: int, r1: int) -> tuple[U8Grid, U8Grid]:
    """The default sun's terms and land weight of rows ``r0`` to ``r1``, across block rows."""
    block = light.block
    parts = [light.terms(a, min((a // block + 1) * block, r1)) for a in _starts(r0, r1, block)]
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def _starts(r0: int, r1: int, block: int) -> list[int]:
    """The first row of each block row's share of ``r0`` to ``r1``."""
    return [r0, *range((r0 // block + 1) * block, r1, block)]
