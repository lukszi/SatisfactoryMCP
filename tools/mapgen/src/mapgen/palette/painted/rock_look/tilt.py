"""The look's normal maps for the light, where the light carries the ground's detail normal.

There the painted layer leaves the maps' light out of its colour, and the light adds their
tilt, the slope that laid on the surface's normal (``light_tiles.with_detail``) gives the
mapped normal, to its normal tiles, so the relief turns with the live sun. The tilt is laid
where the painted layer lays the look, by the same weights, from the surface the light
captures, whatever layers a run draws. docs/map/painted.md section 30, "Rock textures".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.cache import Plane
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.palette.painted.rock_look.atlas import (
    KIND_ARCH,
    KIND_BARNACLE,
    KIND_CLIFF,
    KIND_DESERT,
    KIND_SHELL,
    RockLook,
)
from mapgen.palette.painted.rock_look.reference import LookPixels
from mapgen.palette.painted.rock_look.surface import read_texels, surface_normals
from mapgen.terrain.render_meshes import MESH_BARNACLE, MESH_ROCK, MESH_SHELL
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, U8Grid

__all__ = ["RockTilt", "TiltPlanes", "band_tilt", "tilt_of"]

_F = np.float32
_DESERT = FAMILIES.index("desert")


class RockTilt(NamedTuple):
    """What the light reads the tilt from: the look, the direct pass's family plane (None
    without it, as the painted layer then draws its rock without the look), and the share of
    the maps' light the painted style takes (``rock_look.shade``)."""

    look: RockLook
    family: Plane | None
    strength: float


class TiltPlanes(NamedTuple):
    """A piece's planes, halo included: the heights the light captures, the rock's seen
    weight and the overlay's lift (None without), the render-only meshes' weight, class and
    rock family (None without), and the band's origin: first row and column, and spacing."""

    z_m: FloatGrid
    rock: FloatGrid | None
    top: FloatGrid | None
    mesh_weight: FloatGrid | None
    mesh_class: U8Grid | None
    mesh_family: U8Grid | None
    origin: tuple[int, int, float]


def tilt_of(mapped: F32Grid, normal: F32Grid) -> F32Grid:
    """``(n, 2)`` east and south: what added to ``normal``'s slope, at its own up component,
    and renormalised gives ``mapped``; both ``(n, 3)`` unit normals."""
    scale = normal[:, 2] / np.maximum(mapped[:, 2], _F(1e-6))
    return (mapped[:, :2] * scale[:, None] - normal[:, :2]).astype(np.float32, copy=False)


def band_tilt(source: RockTilt, planes: TiltPlanes, family: U8Grid | None) -> F32Grid:
    """The piece's tilt, ``(rows, cols, 2)``: the rock (its family's kind, the arches over it
    by their lift) by its weight, the meshes over it by theirs, as ``surfaces`` lays the
    look; ``family`` is the direct pass's family on the piece."""
    shape = planes.z_m.shape
    lo, c0, spacing_m = planes.origin
    normal = surface_normals(planes.z_m, spacing_m)
    reader = _Reader(source.look, normal, lo, c0, spacing_m)
    rock = np.zeros((*shape, 2), np.float32)
    weight = np.zeros(shape, np.float32)
    if family is not None:
        for plane in (planes.rock, planes.top):
            if plane is not None:
                weight = np.maximum(weight, plane)
        pick = weight > 0.0
        rock[pick] = reader.at(pick, np.where(family == _DESERT, KIND_DESERT, KIND_CLIFF))
        if planes.top is not None:
            arch = pick & (planes.top > 0.0)
            lift = np.asarray(planes.top, np.float32)[arch][:, None]
            rock[arch] = rock[arch] * (_F(1.0) - lift) + reader.at(arch, KIND_ARCH) * lift
    out = rock * weight[..., None]
    mesh_w, cls = planes.mesh_weight, planes.mesh_class
    if mesh_w is not None and cls is not None and np.any(mesh_w > 0.0):
        mesh = np.zeros((*shape, 2), np.float32)
        on = mesh_w > 0.0
        if planes.mesh_family is not None:
            stone = on & (cls == MESH_ROCK)
            kinds = np.where(planes.mesh_family == _DESERT, KIND_DESERT, KIND_CLIFF)
            mesh[stone] = reader.at(stone, kinds)
        for which, kind in ((MESH_SHELL, KIND_SHELL), (MESH_BARNACLE, KIND_BARNACLE)):
            hit = on & (cls == which)
            mesh[hit] = reader.at(hit, kind)
        m = np.asarray(mesh_w, np.float32)[..., None]
        out = out * (_F(1.0) - m) + mesh * m
    return (out * _F(source.strength)).astype(np.float32, copy=False)


class _Reader:
    """The look's normal at picked pixels of a piece, as its tilt."""

    def __init__(self, look: RockLook, normal: F32Grid, lo: int, c0: int, spacing_m: float) -> None:
        self.look, self.normal = look, normal
        self.lo, self.c0, self.spacing_m = lo, c0, spacing_m

    def at(self, pick: BoolMask, kind: int | U8Grid) -> F32Grid:
        rows, cols = np.nonzero(pick)
        if not rows.size:
            return np.zeros((0, 2), np.float32)
        kinds = np.broadcast_to(np.asarray(kind, np.uint8), pick.shape)[rows, cols]
        surface = self.normal[rows, cols]
        px = LookPixels(
            u=np.asarray((self.c0 + cols + 0.5) * self.spacing_m, np.float32),
            v=np.asarray((self.lo + rows + 0.5) * self.spacing_m, np.float32),
            normal=surface,
            kind=kinds.astype(np.uint8),
            top=np.full(rows.size, -1, np.int32),
        )
        return tilt_of(read_texels(self.look, px).normal, surface)
