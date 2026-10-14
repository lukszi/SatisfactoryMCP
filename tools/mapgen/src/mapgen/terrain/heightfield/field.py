"""The heightfield's planes: fill, landscape and cliff fused onto the 1 m grid, and encoded."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.frame import FILL_RASTER_BOX_CM, GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.level.fill_raster import FILL_RASTER_PX, FILL_RASTER_SCALE_CM_PER_RAW
from mapgen.gamedata.level.landscape import drop_offsets
from mapgen.gamedata.meshes import DIRECT_SAMPLES_MIN
from satisfactory_mcp.core.arrays import BoolMask, I16Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

if TYPE_CHECKING:
    from mapgen.gamedata.level.landscape import LandscapeFrame
    from mapgen.gamedata.rocks.cliffs import CliffRaster, TopOverlay
    from mapgen.gamedata.water.channel import WaterSurface

__all__ = [
    "BEYOND_FRAME_BELOW_FILL_M",
    "FILL_HORIZONTAL_M",
    "FILL_VERTICAL_M",
    "FieldLayers",
    "compose_field",
    "encode_planes",
    "fill_raster_indices",
    "fold_top_overlay",
    "report_field",
]

#: The interface raster's own resolution, for the accuracy the fill layer inherits.
FILL_HORIZONTAL_M = (FILL_RASTER_BOX_CM[1] - FILL_RASTER_BOX_CM[0]) / 100.0 / FILL_RASTER_PX
FILL_VERTICAL_M = FILL_RASTER_SCALE_CM_PER_RAW / 255.0 / 100.0
#: Past the landscape's frame a rock replaces the fill where its top stands no more than this
#: under it: over the sea the fill holds the water's surface, and the rock deeper down is
#: the seabed (docs/map/heightfield.md section 22, "Rocks past the frame").
BEYOND_FRAME_BELOW_FILL_M = 2.0


class FieldLayers(TypedDict):
    """``compose_field``: the fused planes, where the landscape dropped in, and the tallies."""

    height_dm: I16Grid
    prov: U8Grid
    density: U8Grid
    drop: tuple[int, int]
    coverage: dict[str, float]
    cliff_texels: int
    cliff_direct_fraction: float
    density_p50: int
    z_range_m: list[float]
    quantisation_max_m: float
    quantisation_rms_m: float


def fold_top_overlay(height_dm: I16Grid, top: TopOverlay) -> tuple[I16Grid, int]:
    """``height_dm`` max-folded with the overlay, rasterised on the output grid, and how many
    texels the overlay raised."""
    over_dm = np.clip(np.round(np.nan_to_num(top["z_cm"], nan=-1e9) / 10.0), -32767, 32767)
    over_dm = np.where(np.isfinite(top["z_cm"]), over_dm, hf.NODATA).astype(np.int16)
    raise_ = (over_dm != hf.NODATA) & ((height_dm == hf.NODATA) | (over_dm > height_dm))
    return np.where(raise_, over_dm, height_dm).astype(np.int16), int(raise_.sum())


def fill_raster_indices() -> tuple[NDArray[np.integer], NDArray[np.integer]]:
    """Which baseline texel each output column and row falls in. Nearest, never blended.

    The fill is 3.66 m data read at 1 m, so an interpolation would draw a smooth surface out
    of a raster that has none and hide the coarseness the provenance byte declares.
    """
    x0, x1, y0, y1 = FILL_RASTER_BOX_CM
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    bi = np.clip(
        ((columns - x0) / (x1 - x0) * FILL_RASTER_PX - 0.5).round().astype(int),
        0,
        FILL_RASTER_PX - 1,
    )
    bj = np.clip(
        ((rows - y0) / (y1 - y0) * FILL_RASTER_PX - 0.5).round().astype(int), 0, FILL_RASTER_PX - 1
    )
    return bi, bj


def compose_field(
    frame: LandscapeFrame, cliffs: CliffRaster, baseline_cm: NDArray[np.floating], valid: BoolMask
) -> FieldLayers:
    """Fuse the layers into the output grid: fill, then landscape, then cliff over both.

    The fill is everywhere the interface raster says anything, so it goes down first and is
    the answer only where nothing better arrives. The landscape drops in index-aligned over
    its own frame. The cliff overlay, rasterised on the whole output grid, then wins any
    texel in the frame where real geometry stands above the sculpted ground or the
    landscape left a hole (a cave mouth's rock is still a measurement), and any texel past
    the frame where it stands within ``BEYOND_FRAME_BELOW_FILL_M`` of the fill or above it.
    """
    dx, dy = drop_offsets(frame)
    bi, bj = fill_raster_indices()

    z_m = np.where(valid, baseline_cm / 100.0, np.nan).astype(np.float32)[np.ix_(bj, bi)]
    prov = np.where(np.isnan(z_m), hf.PROV_NODATA, hf.PROV_FILL).astype(np.uint8)

    window = (slice(dy, dy + frame["height"]), slice(dx, dx + frame["width"]))
    framed = np.zeros((GRID_PX, GRID_PX), bool)
    framed[window] = True
    land_m = np.full((GRID_PX, GRID_PX), np.nan, np.float32)
    land_m[window] = np.where(frame["good"], frame["z_cm"] / 100.0, np.nan)
    landed = np.isfinite(land_m)

    cliff_m = (cliffs["z_cm"] / 100.0).astype(np.float32)
    # A NaN on either side compares False: no rock, or no fill to agree with, keeps the fill.
    beyond = ~framed & (cliff_m >= z_m - BEYOND_FRAME_BELOW_FILL_M)
    within = framed & np.isfinite(cliff_m) & (~landed | (cliff_m > land_m))
    take = within | beyond
    z_m[landed] = land_m[landed]
    prov[landed] = hf.PROV_LANDSCAPE
    # Two cliff values, one layer, equally accurate at 1 m: 5 says a source vertex landed in
    # this texel, 4 says the rasteriser reached it by interpolating the plane of a triangle
    # wider than the texel. The split exists for renders drawing finer than 1 m.
    direct = take & (cliffs["density"] >= DIRECT_SAMPLES_MIN)
    z_m[take] = cliff_m[take]
    prov[take] = hf.PROV_CLIFF
    prov[direct] = hf.PROV_CLIFF_DIRECT
    density = np.where(take, np.minimum(cliffs["density"], 255), 0).astype(np.uint8)

    known = np.isfinite(z_m)
    height_dm = np.where(known, np.clip(np.round(z_m * 10.0), -32767, 32767), hf.NODATA)
    error = np.abs(height_dm.astype(np.float32) / 10.0 - z_m)[known]
    cliff = np.isin(prov, hf.PROV_CLIFF_VALUES)
    return {
        "height_dm": height_dm.astype(np.int16),
        "prov": prov,
        "density": density,
        "drop": (dx, dy),
        "coverage": {
            hf.PROV_NAMES[value]: float((prov == value).mean())
            for value in (
                hf.PROV_NODATA,
                hf.PROV_LANDSCAPE,
                hf.PROV_FILL,
                hf.PROV_CLIFF,
                hf.PROV_CLIFF_DIRECT,
            )
        },
        "cliff_texels": int(cliff.sum()),
        "cliff_direct_fraction": float((prov == hf.PROV_CLIFF_DIRECT).sum() / max(cliff.sum(), 1)),
        "density_p50": int(np.median(density[cliff])) if cliff.any() else 0,
        "z_range_m": [float(np.nanmin(z_m)), float(np.nanmax(z_m))],
        "quantisation_max_m": float(error.max()),
        "quantisation_rms_m": float(np.sqrt((error**2).mean())),
    }


def report_field(field: FieldLayers) -> None:
    """The composed field's coverage, range and cliff province, one progress line each."""
    for name, fraction in field["coverage"].items():
        print(f"  {name:>10}: {fraction * 100:6.2f}% of the box")
    print(
        f"  z range {field['z_range_m'][0]:.1f} .. {field['z_range_m'][1]:.1f} m; "
        f"int16-decimetre quantisation RMS {field['quantisation_rms_m']:.4f} m"
    )
    print(
        f"  cliff province {field['cliff_texels']} texels, "
        f"{field['cliff_direct_fraction'] * 100:.1f}% with a source vertex in them "
        f"(median {field['density_p50']} samples per cliff texel)"
    )


def encode_planes(
    field: FieldLayers, water: WaterSurface, frame: LandscapeFrame, top_dm: I16Grid
) -> dict[str, bytes]:
    """Every plane of the field, encoded with the shipped codec, keyed by file name."""
    water_dm = np.where(
        np.isfinite(water["level_m"]),
        np.clip(np.round(np.nan_to_num(water["level_m"]) * hf.DM_PER_M), -32767, 32767),
        hf.NODATA,
    ).astype(np.int16)
    return {
        hf.HEIGHT_NAME: hf.encode_i16(field["height_dm"]),
        hf.PROV_NAME: hf.encode_u8(field["prov"]),
        hf.WATER_NAME: hf.encode_i16(water_dm),
        hf.WATER_QUALITY_NAME: hf.encode_u8(water["quality"]),
        hf.DENSITY_NAME: hf.encode_u8(field["density"]),
        hf.TERRAIN_NAME: hf.encode_u16(frame["raw"]),
        hf.TOP_NAME: hf.encode_i16(top_dm),
    }
