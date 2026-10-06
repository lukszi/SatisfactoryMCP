"""The heightfield's planes: fill, landscape and cliff fused onto the 1 m grid, and encoded."""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.frame import FILL_RASTER_BOX_CM, GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.level.fill_raster import FILL_RASTER_PX, FILL_RASTER_SCALE_CM_PER_RAW
from mapgen.gamedata.level.landscape import drop_offsets
from mapgen.gamedata.meshes import DIRECT_SAMPLES_MIN
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The interface raster's own resolution, for the accuracy the fill layer inherits.
FILL_HORIZONTAL_M = 7500.0 / FILL_RASTER_PX


FILL_VERTICAL_M = FILL_RASTER_SCALE_CM_PER_RAW / 255.0 / 100.0


def fold_top_overlay(height_dm: np.ndarray, frame: dict, top: dict) -> tuple[np.ndarray, int]:
    """``height_dm`` max-folded with the overlay, and how many texels the overlay raised."""
    dx, dy = drop_offsets(frame)
    out = height_dm.copy()
    window = out[dy : dy + frame["height"], dx : dx + frame["width"]]
    over_dm = np.clip(np.round(np.nan_to_num(top["z_cm"], nan=-1e9) / 10.0), -32767, 32767)
    over_dm = np.where(np.isfinite(top["z_cm"]), over_dm, hf.NODATA).astype(np.int16)
    raise_ = (over_dm != hf.NODATA) & ((window == hf.NODATA) | (over_dm > window))
    window[raise_] = over_dm[raise_]
    return out, int(raise_.sum())


def fill_raster_indices() -> tuple[np.ndarray, np.ndarray]:
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


def compose_field(frame: dict, cliffs: dict, baseline_cm: np.ndarray, valid: np.ndarray) -> dict:
    """Fuse the layers into the output grid: fill, then landscape, then cliff over both.

    The fill is everywhere the interface raster says anything, so it goes down first and is
    the answer only where nothing better arrives. The landscape drops in index-aligned over
    its own frame. The cliff overlay then wins any texel where real geometry stands above
    the sculpted ground, and any texel the landscape left as a hole: a cave mouth's rock is
    still a measurement.
    """
    dx, dy = drop_offsets(frame)
    bi, bj = fill_raster_indices()

    z_m = np.where(valid, baseline_cm / 100.0, np.nan).astype(np.float32)[np.ix_(bj, bi)]
    prov = np.where(np.isnan(z_m), hf.PROV_NODATA, hf.PROV_FILL).astype(np.uint8)

    land_m = np.where(frame["good"], frame["z_cm"] / 100.0, np.nan).astype(np.float32)
    sub_prov = np.where(frame["good"], hf.PROV_LANDSCAPE, hf.PROV_NODATA).astype(np.uint8)

    cliff_m = (cliffs["z_cm"] / 100.0).astype(np.float32)
    take = np.isfinite(cliff_m) & (~np.isfinite(land_m) | (cliff_m > land_m))
    sub_z = np.where(take, cliff_m, land_m)
    # Two cliff values, one layer, equally accurate at 1 m: 5 says a source vertex landed in
    # this texel, 4 says the rasteriser reached it by interpolating the plane of a triangle
    # wider than the texel. The split exists for renders drawing finer than 1 m.
    direct = take & (cliffs["density"] >= DIRECT_SAMPLES_MIN)
    sub_prov = np.where(take, hf.PROV_CLIFF, sub_prov)
    sub_prov = np.where(direct, hf.PROV_CLIFF_DIRECT, sub_prov).astype(np.uint8)
    sub_density = np.where(take, np.minimum(cliffs["density"], 255), 0).astype(np.uint8)

    window = np.isfinite(sub_z)
    z_m[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_z[window]
    prov[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_prov[window]
    density = np.zeros((GRID_PX, GRID_PX), np.uint8)
    density[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_density[window]

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


def report_field(field: dict) -> None:
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


def encode_planes(field: dict, water: dict, frame: dict, top_dm: np.ndarray) -> dict[str, bytes]:
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
