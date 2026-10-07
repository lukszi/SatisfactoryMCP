"""A render drawn unlit: the surface it hands the lighting stage, the bake that follows the
draw a block row at a time, and the default sun's terms that relight each layer's bands.

Each layer keeps ``tiles/`` and ``tiles@2x/`` lit by the default sun, so a page without
WebGL and every older reader still draw a lit map, and adds ``unlit/``, the colour the page
relights live. The stage's ``light.cache/`` is scratch for one run; the finished bake is
kept beside the raster caches for a run that draws the same surface (``kept_light``).
docs/map/light-and-crowns.md section 29 and docs/map/renders.md section 42.
"""

from __future__ import annotations

import argparse
import shutil
import traceback
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, TypeAlias

import numpy as np

from mapgen.cache import TitanPlanes, held_open
from mapgen.common import Refusal
from mapgen.gamedata.ground.paint_store import CROWN_NAME
from mapgen.lighting.bake import LightBake, block_rows
from mapgen.lighting.model import DIRECT_SCALE, apply_terms
from mapgen.lighting.occluders import CrownGrid, sheet_crowns
from mapgen.lighting.stage import (
    LIGHT_DIR_NAME,
    TERM_CROWNED_DIRECT,
    TERM_CROWNED_SKY,
    TERM_DIRECT,
    TERM_SKY,
    Surface,
    caster_digests,
    discard,
    light_key,
    occluder_planes,
)
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.palette.lightparams import shader_light
from mapgen.palette.painted.albedo import load_paint_meta, paint_plane
from mapgen.palette.painted.ground import PaintedGround
from mapgen.palette.painted.shapes import PaintPlane
from mapgen.palette.painted.trees import sample_titan
from mapgen.palette.styles import LAYER_STYLES
from mapgen.render.draw.kept_light import KEPT_LIGHT_DIR_NAME, KeptBake, KeptLight
from satisfactory_mcp.core.arrays import F32Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject, as_float, require_object
from satisfactory_mcp.core.mapprogress import encode_stage

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
    "relight_rows",
    "scratch_root",
    "titan_crowns",
]

UNLIT_DIR_NAME = "unlit"
LIGHT_CACHE_DIR_NAME = "light.cache"
#: Rows relit at a time: each row is lit on its own, so only the memory it takes changes.
RELIGHT_ROWS = 64
#: Sheet rows of the Titan raster laid into the crown planes at a time.
TITAN_ROWS = 256

#: Exit code of a run whose light scratch a render still running holds open.
SCRATCH_IN_USE = 11

#: The crowns the light bake casts: their tops in metres and the share of a pixel covered.
Occluder: TypeAlias = tuple[F32Grid, U8Grid]

#: Where a block row's terms are read from: the kept bake's, or this run's bake.
KEPT, BAKED = "kept", "baked"


class CrownTops(NamedTuple):
    """The paint store's crown-top plane, decimetres on its 1 m grid, and where it lies; and
    the Titan trees' raster where the painted layer draws them."""

    top_dm: PaintPlane
    grid: CrownGrid
    titan: TitanPlanes | None = None


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


def scratch_root(args: argparse.Namespace, renders: Path) -> Path:
    """Where a run's scratch goes: ``--scratch-dir``, else ``--cache-dir``, else ``renders``."""
    root: Path = args.scratch_dir or args.cache_dir or renders
    return root


def claim_scratch(args: argparse.Namespace, renders: Path) -> Path | None:
    """Where the run's ``light.cache/`` goes, emptied of a run that died; None without light.

    A render still running keeps its heights mapped, and Windows refuses to rename a mapped
    file, so that scratch is refused rather than emptied under it.
    """
    if not args.light:
        return None
    root = scratch_root(args, renders)
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


def relight_rows(rgb: U8Grid, terms: U8Grid, land: U8Grid, params: JsonObject) -> U8Grid:
    """Unlit rows lit by the default sun, from the bake's terms and the land weight of the
    same rows.

    A style that draws the crowns (``params["crowns"]``) takes the terms with their shadows
    and the canopy's own light (``stage.TERMS``); every other style the ground's alone.
    """
    crowned = (TERM_CROWNED_DIRECT, TERM_CROWNED_SKY)
    which, sky = crowned if params.get("crowns") else (TERM_DIRECT, TERM_SKY)
    out = np.empty_like(rgb)
    for top in range(0, rgb.shape[0], RELIGHT_ROWS):
        rows = slice(top, top + RELIGHT_ROWS)
        svf = terms[rows, :, sky].astype(np.float32) / 255.0
        direct = terms[rows, :, which].astype(np.float32) / DIRECT_SCALE
        dry = land[rows].astype(np.float32) / 255.0
        out[rows] = apply_terms(rgb[rows], svf, direct, dry, params)
    return out


def crown_layers() -> list[str]:
    """The layers that draw the tree crowns, so read the crown horizons."""
    return [layer for layer in LAYER_STYLES if shader_light(layer).get("crowns")]


def crown_tops(paint_dir: Path, painted: PaintedGround | None) -> CrownTops | None:
    """The crown tops the light casts whatever layers a run draws: the painted ground's when
    it is drawn, with its Titan trees, else the paint store's; None without a store or its
    crown plane."""
    titan = None
    if painted is not None:
        meta, plane, titan = painted.meta, painted.crown, painted.titan
    else:
        meta = load_paint_meta(paint_dir)
        if meta is None or CROWN_NAME not in meta["files"]:
            return None
        plane = paint_plane(paint_dir, meta, CROWN_NAME)
    return None if plane is None else CrownTops(plane, meta["grid"], titan)


def crown_occluder(crowns: CrownTops | None, scratch_root: Path, size: int) -> Occluder | None:
    """The crown tops and cover on the sheet, written where the bake reads its occluder.

    None without them. The stage reads these files in place: there is no second copy.
    """
    if crowns is None:
        return None
    top, cover = occluder_planes(scratch_root / LIGHT_CACHE_DIR_NAME, size)
    sheet_crowns(crowns.top_dm, crowns.grid, size, top, cover)
    if crowns.titan is not None:
        titan_crowns(crowns.titan, top, cover)
    return top, cover


def titan_crowns(titan: TitanPlanes, top: F32Grid, cover: U8Grid) -> None:
    """The Titan trees laid into the crown planes, a band of rows at a time: their top where
    it stands higher, and the larger cover. They cast and take the canopy's light as crowns."""
    rows, cols = top.shape
    for start in range(0, rows, TITAN_ROWS):
        stop = min(start + TITAN_ROWS, rows)
        found = sample_titan(titan, (start, stop, 0, cols))
        if found is None:
            continue
        z_m, share, _cls = found
        seen = share >= np.float32(0.5 / 255.0)
        band = np.asarray(top[start:stop])
        top[start:stop] = np.where(seen & ~(band >= z_m), z_m, band)
        byte = np.round(np.clip(share, 0.0, 1.0) * 255.0)
        cover[start:stop] = np.where(seen, np.maximum(cover[start:stop], byte), cover[start:stop])


class LightingRun:
    """The default ``--light`` run: the surface the draw's one pass captures, the bake that
    follows it a block row at a time, and the terms each band is relit by.

    ``light_workers`` bake the light, None counting them from the cores and free memory.
    With a ``cache_root`` the bake is kept under it. A kept bake that differs at most in the
    surface is read while the bands drawn so far are the ones it was baked from, and is
    installed if they all are; the first band drawn otherwise starts this run's bake.
    """

    def __init__(
        self,
        scratch_root: Path,
        size: int,
        occluder: Occluder | None = None,
        light_workers: int | None = None,
        cache_root: Path | None = None,
    ) -> None:
        self.surface = Surface(scratch_root / LIGHT_CACHE_DIR_NAME, size)
        self.occluder = occluder
        # Hashed now, while the planes sheet_crowns just wrote are still in memory.
        self.casts = caster_digests(occluder)
        self.light_workers = light_workers
        self.kept = None if cache_root is None else KeptLight(cache_root / KEPT_LIGHT_DIR_NAME)
        self.meta: JsonObject | None = None
        self.renders: Path | None = None
        self.bake: LightBake | None = None
        self.reuse: KeptBake | None = None
        self.matched = self.matched_rows = 0
        self.block, self.reads = block_rows(size)
        self.served: list[str | None] = [None] * len(self.reads)
        self._terms: dict[str, U8Grid] = {}

    def begin(self, renders: Path) -> None:
        """Before the draw: read a kept bake that may be this surface's, else start baking."""
        self.renders = renders
        if self.kept is not None:
            self.reuse = self.kept.candidate(light_key(self.surface, self.casts, crown_layers()))
        if self.reuse is None:
            self._start_bake()

    def _start_bake(self) -> LightBake:
        if self.bake is None:
            if self.renders is None:
                raise RuntimeError("a light run bakes into the renders it began with")
            print("baking the lighting pyramid as the bands come in", flush=True)
            self.bake = LightBake(
                self.surface,
                self.renders,
                self.light_workers,
                self.occluder,
                occluder_layers=crown_layers(),
            )
        return self.bake

    def drawn(self, rows: int) -> None:
        """The surface holds its first ``rows`` rows: each block row whose reads they cover is
        read from the kept bake while it matches, else queued on this run's bake."""
        if self.reuse is not None:
            self._match_kept()
        size = self.surface.size
        for row, reads in enumerate(self.reads):
            if reads > rows and rows < size:
                break
            if self.reuse is not None:
                if self.served[row] is None and self.matched_rows >= reads:
                    self.served[row] = KEPT
            else:
                self._start_bake().queue(row)

    def _match_kept(self) -> None:
        """The kept bake's bands this run has drawn the same, in row order; the first drawn
        otherwise ends the reading."""
        kept = self.reuse
        if kept is None:
            return
        drawn = dict(self.surface.puts())
        places = {place for place, _ in kept.puts}
        same = set(drawn) <= places
        while same and self.matched < len(kept.puts):
            place, digest = kept.puts[self.matched]
            if place not in drawn:
                return
            full = (place.c0, place.c1) == (0, self.surface.size)
            same = drawn[place] == digest and full and place.row == self.matched_rows
            if same:
                self.matched, self.matched_rows = self.matched + 1, place.row + place.rows
        if not same:
            self.reuse = None

    def collect(self) -> int:
        """Collect the block rows the bake has finished, and return the rows whose terms are
        in, from the top: a run of block rows read or baked."""
        for row in range(len(self.reads)):
            if self.served[row] is None and self.bake is not None and row in self.bake.rows:
                future = self.bake.rows[row]
                if future.done():
                    future.result()
                    self.served[row] = BAKED
            if self.served[row] is None:
                return row * self.block
        return self.surface.size

    def terms(self, r0: int, r1: int) -> tuple[U8Grid, U8Grid]:
        """The default sun's terms of rows ``[r0, r1)``, and their land weight, as copies."""
        source = self.served[r0 // self.block]
        if source is None or (r1 - 1) // self.block != r0 // self.block:
            raise ValueError(f"rows {r0}:{r1} are not in one block row whose terms are in")
        if source not in self._terms:
            path = self.kept.terms if source == KEPT and self.kept else self.surface.terms
            self._terms[source] = np.load(path, mmap_mode="r")
        return np.array(self._terms[source][r0:r1]), np.array(self.surface.land[r0:r1])

    def finish(self, release: Callable[[], None]) -> None:
        """After the draw: the kept bake installed when every band matched it, else this
        run's bake finished and kept. ``release`` runs each time more rows' terms are in."""
        renders = self.renders
        if renders is None:
            raise RuntimeError("a light run finishes the renders it began with")
        key = light_key(self.surface, self.casts, crown_layers())
        kept = self.kept.matching(key) if self.reuse is not None and self.kept else None
        if kept is not None and self.kept is not None:
            print("the lighting pyramid: kept from a run that drew this surface", flush=True)
            release()
            self._terms.clear()
            self.meta = self.kept.install(kept, renders)
            self.surface.terms = self.kept.terms
            print(encode_stage("light", 1.0), flush=True)
            return
        self.reuse = None
        bake = self._start_bake()
        self.meta = bake.finish(key, on_row=lambda _row: release())
        # Nothing may hold the terms mapped while the keep moves them.
        bake.close()
        self._terms.clear()
        if self.kept is not None:
            puts = self.surface.puts()
            self.surface.terms = self.kept.keep(self.meta, renders, self.surface.terms, puts)
        done, render = require_object(self.meta["tiles"]), require_object(self.meta["render"])
        print(
            f"  light: {done['count']} tiles over z0..z{done['max_z']} "
            f"({as_float(done['bytes']) / 1e6:.1f} MB) in {render['seconds']}s "
            f"on {render['workers']} workers"
        )

    def decorate(self, sidecar: JsonObject, layer: str, unlit: JsonObject | None) -> None:
        """Name the lighting pyramid, ``unlit/`` and the shader's style fields in a layer's
        sidecar."""
        meta = sidecar["_meta"]
        if not isinstance(meta, dict):
            return
        meta["light"] = {
            "dir": f"../{LIGHT_DIR_NAME}",
            "unlit_dir": UNLIT_DIR_NAME,
            "unlit_tiles": unlit,
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
        # The light processes and every map of the scratch go first: Windows will not delete
        # a mapped file.
        if self.bake is not None:
            self.bake.close()
        self.bake = None
        self._terms.clear()
        self.occluder = None
        self.surface.close()
        discard(self.surface)
        shutil.rmtree(self.surface.directory, ignore_errors=True)


@contextmanager
def light_run(
    root: Path | None,
    size: int,
    crowns: CrownTops | None,
    workers: int | None = None,
    cache_root: Path | None = None,
) -> Generator[LightingRun | None, None, None]:
    """The run's light stage in ``root``, crowns first, its scratch deleted however the run
    ends, the crowns' and the surface's failures included; or None without the light. The
    bake is kept under ``cache_root``, where one of the same surface is reused."""
    if root is None:
        yield None
        return
    run = None
    try:
        # No local holds the occluder: its memory maps must go with the run's.
        run = LightingRun(
            root,
            size,
            crown_occluder(crowns, root, size),
            light_workers=workers,
            cache_root=cache_root,
        )
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
