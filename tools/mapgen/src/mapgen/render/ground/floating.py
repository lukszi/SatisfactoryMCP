"""What floats over a piece of a band, for the light: arches and rock overhangs.

An arch floats wherever it covers a pixel; a rock where the underside of its top layer clears
both the surface under it and the ground by ``OVERHANG_CLEAR_M``. The light takes, there, the
surface drawn without what floats (the ``solid``), and the floating geometry's underside and
the drawn top (``lighting/spans/slabs.py``). docs/map/light-and-crowns.md section 29.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NamedTuple, Protocol

import numpy as np

from mapgen.cache import DirectPlanes, MeshPlanes, TopPlanes
from mapgen.lighting.spans.slabs import SlabPlanes
from mapgen.palette.water.shore import composite_meshes
from mapgen.render.ground.lift import blend_regimes, composite_top
from mapgen.terrain.overhangs import OVERHANG_CLEAR_M
from mapgen.terrain.sample import Taps
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid

__all__ = ["FieldPiece", "FloatSources", "band_slabs", "piece_slabs"]

#: ``render.ground.lift.rock_kept`` with the piece's water and sea bound: ``(z_rock_cm, missing,
#: linear)`` to the share of its coverage a rock keeps, None without the open sea.
RockKept = Callable[[F32Grid, BoolMask, Taps], F32Grid | None]


class FieldPiece(NamedTuple):
    """A piece's ground before the rocks lift it: the field's heights and where it knows
    nothing, and the lattice the rocks stand on (None without them)."""

    z_m: FloatGrid
    missing: BoolMask
    base_m: FloatGrid | None


class FloatSources(NamedTuple):
    """What a piece's solid surface is composed from: the rasters, the piece's rows and columns
    of them (``cut``) and of its output (``kept``), its linear taps, and ``rock_kept``."""

    direct: DirectPlanes | None
    overlay: TopPlanes | None
    meshes: MeshPlanes | None
    cut: tuple[slice, slice]
    kept: tuple[slice, slice]
    linear: Taps
    keep_rock: RockKept


def _arches(overlay: TopPlanes | None, cut: tuple[slice, slice]) -> BoolMask | None:
    if overlay is None or overlay.arch_coverage is None:
        return None
    covered: BoolMask = np.asarray(overlay.arch_coverage[cut], np.uint8) > 0
    return covered


def _overhangs(direct: DirectPlanes | None, cut: tuple[slice, slice],
               base_m: FloatGrid | None) -> BoolMask | None:  # fmt: skip
    """Where a rock's underside clears its floor and the ground."""
    if direct is None or direct.under is None or direct.floor is None or base_m is None:
        return None
    under = np.asarray(direct.under[cut], np.float32) / np.float32(100.0)
    floor = np.asarray(direct.floor[cut], np.float32) / np.float32(100.0)
    below = np.fmax(floor, base_m)
    covered: BoolMask = np.asarray(direct.coverage[cut], np.uint8) > 0
    floats: BoolMask = covered & np.isfinite(under) & (under > below + np.float32(OVERHANG_CLEAR_M))
    return floats


def _solid(sources: FloatSources, field: FieldPiece, rocks: BoolMask | None,
           level_m: FloatGrid) -> FloatGrid:  # fmt: skip
    """The piece drawn as the light's seabed rule draws it, with the floating rocks set down
    on their floor and only the boulders of the top raster."""
    cut, z, missing = sources.cut, field.z_m, field.missing
    direct = sources.direct
    if direct is not None and field.base_m is not None:
        rock_z = np.asarray(direct.z[cut], np.float32)
        cover = np.asarray(direct.coverage[cut])
        if rocks is not None and direct.floor is not None:
            floor = np.asarray(direct.floor[cut], np.float32)
            rock_z = np.where(rocks, np.nan_to_num(floor), rock_z).astype(np.float32)
            cover = np.where(rocks & ~np.isfinite(floor), 0, cover).astype(cover.dtype)
        keep = sources.keep_rock(rock_z, missing, sources.linear)
        z = blend_regimes(field.base_m, missing, (rock_z, cover),
                          direct.subsamples, keep)[0]  # fmt: skip
    top = sources.overlay
    if top is not None:
        boulders, cover_b = top.z, top.coverage
        if top.solid_z is not None and top.solid_coverage is not None:
            boulders, cover_b = top.solid_z, top.solid_coverage
        z = composite_top(z, np.asarray(boulders[cut], np.float32), np.asarray(cover_b[cut]),
                          top.subsamples)  # fmt: skip
    if sources.meshes is not None:
        meshes = sources.meshes
        z = composite_meshes(z, np.asarray(meshes.z_cm[cut], np.float32),
                             np.asarray(meshes.cls[cut], np.uint8), level_m, composite_top,
                             seabed=True)[0]  # fmt: skip
    return z


def piece_slabs(sources: FloatSources, field: FieldPiece, drawn: FloatGrid,
                level_m: FloatGrid) -> SlabPlanes | None:  # fmt: skip
    """What floats over the piece's output pixels, or None where nothing does there.

    ``drawn`` is the surface the light captures; the solid is it wherever nothing floats.
    """
    arches = _arches(sources.overlay, sources.cut)
    rocks = _overhangs(sources.direct, sources.cut, field.base_m)
    if rocks is not None and arches is not None:
        rocks &= ~arches
    parts = [mask for mask in (arches, rocks) if mask is not None]
    if not parts:
        return None
    floating = np.logical_or.reduce(parts)
    kept = sources.kept
    if not floating[kept].any():
        return None
    solid = np.where(floating, _solid(sources, field, rocks, level_m), drawn)
    nan = np.float32(np.nan)
    lo = np.full(drawn.shape, nan, np.float32)
    if rocks is not None and sources.direct is not None and sources.direct.under is not None:
        under = np.asarray(sources.direct.under[sources.cut], np.float32) / np.float32(100.0)
        lo = np.where(rocks, under, lo)
    top = sources.overlay
    if arches is not None and top is not None and top.arch_under is not None:
        under = np.asarray(top.arch_under[sources.cut], np.float32) / np.float32(100.0)
        lo = np.where(arches, under, lo)
    hi = np.where(floating, drawn, nan)
    lo = np.fmin(lo, hi)
    return SlabPlanes(*(np.asarray(p[kept], np.float32) for p in (solid, lo, hi)))


def band_slabs(lights: Sequence[_HasSlabs]) -> SlabPlanes | None:
    """A band's pieces' slabs in column order, the solid surface their heights where a piece
    has none; None where no piece has any."""
    if all(light.slabs is None for light in lights):
        return None
    parts: list[SlabPlanes] = []
    for light in lights:
        if light.slabs is not None:
            parts.append(light.slabs)
            continue
        nan = np.full(light.z_m.shape, np.nan, np.float32)
        parts.append(SlabPlanes(np.asarray(light.z_m, np.float32), nan, nan))
    return SlabPlanes(*(np.concatenate(planes, axis=1) for planes in zip(*parts, strict=True)))


class _HasSlabs(Protocol):
    """``surface.LightPlanes`` as ``band_slabs`` reads it."""

    @property
    def z_m(self) -> F32Grid: ...

    @property
    def slabs(self) -> SlabPlanes | None: ...
