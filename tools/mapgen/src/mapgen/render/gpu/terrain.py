"""The terrain layer's piece on the GPU, from its ground to its bytes, a thread a pixel.

The piece's planes go up once, the hillshade, the ramp, the shore's water composite and the
void run on the device, and only the kept pixels come back, as the bytes ``compose`` would cut
from the CPU's colour. The water's transmission, an ``exp``, is worked out by numpy first,
as the numba painter has it. Imported only when ``mapgen.jit.gpu_on()``.
docs/map/renders.md section 43.
"""

from __future__ import annotations

from typing import NamedTuple

import cupy as cp
import numpy as np

from mapgen.lighting.hillshade import (
    FLAT_SHADE,
    SHADE_FLOOR,
    SHADE_RANGE,
    SUN_ALTITUDE_DEG,
    SUN_AZIMUTH_DEG,
    WATER_SHADE_FLOOR,
    WATER_SHADE_RANGE,
)
from mapgen.lighting.sun import sun_vector
from mapgen.palette.scene import WaterTerms
from mapgen.palette.schema import ShoreOptics
from mapgen.palette.styles import (
    PIT_EDGE_RGB,
    PIT_RGB,
    RAMP_STOPS,
    SEA_RGB,
    TERRAIN_SHORE,
    VOID_EDGE_RGB,
    VOID_RIM_RGB,
    WATER_DEEP,
    WATER_SHALLOW,
)
from mapgen.palette.water.shore import WET_MIX_MOST, optical_depth
from mapgen.render.gpu.device import DeviceBand, kernel, on_device, row_grid
from mapgen.render.ground.void import DrawnVoid
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, U8Grid

__all__ = ["TerrainPiece", "shade_plane", "terrain_bytes"]

_SOURCE = "terrain.cu"

#: How ``with_sea``, nothing, or ``with_void`` lays the void over the colour.
_WITH_SEA, _NO_VOID, _WITH_VOID = 0, 1, 2

#: The water planes the composite reads, in ``terrain.cu``'s order.
_WATER = ("cover", "depth", "banks", "above_m", "edge", "depth_m", "below_m", "ocean")


class TerrainPiece(NamedTuple):
    """What the terrain style reads of a piece: its heights, lit by the hillshade over
    ``spacing_m`` or, None, flat; the borrowed shading; the water; where it has no data and
    its void (``open_sea``: the run draws the open sea); the ramp's ends; the kept pixels."""

    z_m: FloatGrid
    spacing_m: float | None
    borrow: FloatGrid
    water: WaterTerms
    missing: BoolMask
    void: DrawnVoid | None
    open_sea: bool
    ramp: tuple[float, float]
    kept: tuple[slice, slice]


def terrain_bytes(piece: TerrainPiece) -> U8Grid | None:
    """The kept pixels of ``terrain_colours``, ``_void`` and the byte, as ``compose`` cuts
    them; None for the CPU to draw them where a plane is not float32 or the device has no
    memory for the piece."""
    planes = (piece.z_m, piece.borrow, *_water(piece.water).values(), *(piece.void or ()))
    if not all(plane.dtype == np.float32 for plane in planes):
        return None
    return on_device(lambda: _draw(piece))


def _water(water: WaterTerms) -> dict[str, FloatGrid]:
    """The planes the composite reads; one the style leaves unread stands in as the cover,
    as ``shore._composite_compiled`` hands it."""
    planes = {name: water.get(name, water["cover"]) for name in _WATER}
    planes["banks"] = water.get("banks", water["ocean"])
    return planes


def shade_plane(band: DeviceBand, z_m: F32Grid, spacing_m: float) -> cp.ndarray[np.float32]:
    """``hillshade(z_m, spacing_m)`` on the device, kept in ``band`` as ``"shade"``."""
    rows, cols = z_m.shape
    out = cp.empty((rows, cols), np.float32)
    sun = cp.asarray(np.array(sun_vector(SUN_AZIMUTH_DEG, SUN_ALTITUDE_DEG), np.float32))
    head = (band.upload("z_m", z_m), np.int32(rows), np.int32(cols))
    steps = (np.float32(2.0 * spacing_m), np.float32(spacing_m))
    tail = (sun, np.float32(SHADE_RANGE), np.float32(SHADE_FLOOR), out)
    kernel(_SOURCE, "sun_dot")(*row_grid(rows, cols), (*head, *steps, *tail))
    band.keep("shade", out)
    return out


def _draw(piece: TerrainPiece) -> U8Grid:
    band = DeviceBand()
    rows, cols = piece.z_m.shape
    if piece.spacing_m is None:
        shade = cp.full((rows, cols), FLAT_SHADE, np.float32)
    else:
        shade = shade_plane(band, piece.z_m, piece.spacing_m)
    optics: ShoreOptics = TERRAIN_SHORE
    water = _water(piece.water)
    depth = optical_depth(piece.water, optics.get("river"), optics.get("inland"))
    transmit = np.exp(-depth / np.float32(optics["clarity_m"]))
    every = np.count_nonzero(water["cover"]) > WET_MIX_MOST * water["cover"].size
    on_water = tuple(band.upload(name, plane) for name, plane in water.items())
    on_void = (on_water[0],) * 4
    if piece.void is not None:
        on_void = tuple(band.upload(f"void.{k}", p) for k, p in piece.void._asdict().items())
    mode = _WITH_VOID if piece.void is not None else _NO_VOID if piece.open_sea else _WITH_SEA
    r0, r1, _ = piece.kept[0].indices(rows)
    c0, c1, _ = piece.kept[1].indices(cols)
    out = cp.empty((r1 - r0, c1 - c0, 3), np.uint8)
    planes = (band.upload("z_m", piece.z_m), shade, band.upload("borrow", piece.borrow))
    planes += (*on_water, band.upload("transmit", transmit), *on_void)
    style = (cp.asarray(RAMP_STOPS), np.int32(len(RAMP_STOPS)), cp.asarray(_knobs(piece.ramp)))
    style += (cp.asarray(_void_colours()), np.int32(every), np.int32(mode))
    place = (np.int32(cols), np.int32(r0), np.int32(c0), np.int32(r1 - r0), np.int32(c1 - c0))
    missing = band.upload("missing", piece.missing.astype(np.uint8))
    args = (*planes, missing, *style, *place, out)
    kernel(_SOURCE, "terrain")(*row_grid(r1 - r0, c1 - c0), args)
    return out.get()


def _knobs(ramp: tuple[float, float]) -> F32Grid:
    """The ramp's low end and span as ``terrain_colours`` divides by it, then ``Shore`` of
    ``terrain.cu``: ``shore._composite_compiled``'s knobs and colours for the terrain's
    optics."""
    lo, hi = ramp
    optics: ShoreOptics = TERRAIN_SHORE
    band, foam = optics.get("wet_band") or {}, optics.get("foam") or {}
    band_m, strength = band.get("m") or 0.0, foam.get("strength") or 0.0
    tint = band["tint"] if band_m else (1.0, 1.0, 1.0)
    froth = (foam["max_depth_m"], foam["width_m"]) if strength else (1.0, 1.0)
    white = np.float32(255.0) * np.float32(foam.get("white", 1.0))
    shore = [band_m, optics["edge_alpha"], optics["wet_darken"]]
    shore += [WATER_SHADE_FLOOR, WATER_SHADE_RANGE, optics.get("stroke", 0.0), strength]
    shore += [*froth, white, *WATER_SHALLOW, *WATER_DEEP, *tint]
    return np.array([lo, max(hi - lo, 1e-6), *shore], np.float32)


def _void_colours() -> F32Grid:
    """``with_void``'s colours in ``terrain.cu``'s order: the void's edge, the pit's edge,
    the sea, the pit and the rim."""
    return np.concatenate([VOID_EDGE_RGB, PIT_EDGE_RGB, SEA_RGB, PIT_RGB, VOID_RIM_RGB])
