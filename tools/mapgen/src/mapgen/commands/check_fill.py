"""Score the render's rebuilt lattice against held-out landscape, through the shipped code.

    uv run --extra gen python tools/check_map_fill.py

Every number comes from ``mapgen.terrain`` and ``mapgen.commands.renders`` as the render
calls them; only the baselines are emulated here. Four checks, defined in
docs/map/renders.md section 26:

* **fill**: the interface raster read at held-out dry landscape (east half, every third
  texel), against the 1 m terrain. Baseline: the nearest texel, which is what the field
  stores.
* **seam**: 60 dry landscape windows cut by a fake seam, landscape west and raster east.
  Baseline: the nearest raster with recipe 4's de-terracing.
* **holes**: the field's own interior hole shapes laid over known landscape. Baseline:
  nearest-neighbour fill.
* **sampler**: PCHIP on the rebuilt lattice over the six judge crops, at the 1 m vertices
  and at the render's 0.229 m pixels. Catmull-Rom beside it.

Reads the field and the game install; writes nothing unless ``--json`` names a file.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.common import ROOT, base_parser, require_gen
from mapgen.gamedata.frame import FILL_RASTER_BOX_CM, ORIGIN_X_CM, ORIGIN_Y_CM, RENDER_PX
from mapgen.gamedata.level.fill_raster import FILL_RASTER_PX, read_fill_raster
from mapgen.terrain.fill import (
    blend_seam,
    fill_from_raster,
    fill_holes,
    ground_lattice,
    nearest_fill,
    raster_positions,
    reconstruct_raster,
    terrain_lattice,
)
from mapgen.terrain.heightfield.field import FILL_HORIZONTAL_M, FILL_VERTICAL_M
from mapgen.terrain.sample import (
    AxisTaps,
    frame_coordinates,
    grid_position,
    sample_surface,
    taps_cubic,
    taps_linear,
    taps_pchip,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid
from satisfactory_mcp.core.gameassets.container import open_container
from satisfactory_mcp.domain.spatial import heightfield as hf

#: Crops as the NW field vertex (row, col), 256 m on a side: the smooth-render judge set.
CROPS = {
    "a_cliff": (768, 5632),
    "b_rolling": (1024, 6400),
    "c_coast": (4608, 768),
    "d_boulder_arch": (3840, 1792),
    "f_frame_dry": (576, 4992),
    "g_crater_hole": (4935, 2955),
}
CROP_M = 256
#: Field texels read around a crop, so its stencils never run off the lattice.
CROP_HALO = 4

#: The held-out split and thinning of the fill check.
FILL_TEST_FROM_COL = 3500
FILL_THIN = 3

#: The seam check's windows.
SEAM_WINDOW = 256
SEAM_COL = 128
SEAM_WINDOWS = 60
SEAM_SEED = 3

#: The hole check's placements.
HOLE_SEED = 7
HOLE_MARGIN = 24
HOLE_NEAR_GAP_M = 300
HOLE_MIN_TEXELS = 30

#: Recipe 4's de-terracing, emulated for the seam baseline only.
RECIPE4_SIGMA_M = FILL_HORIZONTAL_M
RECIPE4_CLAMP_M = FILL_VERTICAL_M

#: The kernels the sampler check compares, by the name the report gives them.
KERNELS: dict[str, Callable[[F64Grid, int], AxisTaps]] = {
    "pchip": taps_pchip,
    "catmull_rom": taps_cubic,
}


class SamplerScore(TypedDict):
    """One kernel on one crop: exact at the vertices, and how far it leaves its cells."""

    vertex_max_abs_m: float
    overshoot_max_m: float
    overshoot_p999_m: float


#: Why the checks refuse a field: its terrain plane is missing or off the field's vertices.
OFF_GRID = f"the field's {hf.TERRAIN_NAME} has no terrain_grid on the field's vertices"


def terrain_m(field: hf.Field) -> F32Grid:
    """``terrain.u16.z`` on the field grid in metres, NaN where it has no sample."""
    plane = np.asarray(field.plane(hf.TERRAIN_NAME))
    grid = field.terrain_grid
    if grid is None or grid["row_off"] is None or grid["col_off"] is None:
        raise ValueError(OFF_GRID)
    row_off, col_off = grid["row_off"], grid["col_off"]
    out = np.full(np.asarray(field.height_dm).shape, np.nan, np.float32)
    rows, cols = plane.shape
    z = (plane.astype(np.float32) - grid["zero"]) / grid["units_per_m"] + grid["offset_m"]
    window = (slice(row_off, row_off + rows), slice(col_off, col_off + cols))
    out[window] = np.where(plane > 0, z, np.nan)
    return out


def raster_grid(field: hf.Field) -> tuple[F64Grid, F64Grid]:
    """Fractional raster rows and columns of every field vertex."""
    x0, x1, y0, y1 = FILL_RASTER_BOX_CM
    px = FILL_RASTER_PX
    rows = raster_positions(field.height, field.y0_cm, field.spacing_cm, y0, y1, px)
    cols = raster_positions(field.width, field.x0_cm, field.spacing_cm, x0, x1, px)
    return rows, cols


def nearest_index(position: F64Grid) -> NDArray[np.integer]:
    return np.clip(np.round(position).astype(int), 0, FILL_RASTER_PX - 1)


def check_fill(field: hf.Field, land: F32Grid, raster_m: F64Grid, raster_valid: BoolMask,
               rebuilt_m: F32Grid) -> dict[str, object]:  # fmt: skip
    """Median absolute error of the fill reading on held-out dry landscape."""
    prov = np.asarray(field.provenance_plane)
    wet = np.asarray(field.water_quality_raster()) > 0
    top = np.asarray(field.plane(hf.TOP_NAME))
    raised = top > np.asarray(field.height_dm) + 5
    near_rock = ndimage.binary_dilation(np.isin(prov, hf.PROV_CLIFF_VALUES) | raised, iterations=8)
    near_wet = ndimage.binary_dilation(wet, iterations=16)
    rows, cols = raster_grid(field)
    whole = ndimage.binary_erosion(raster_valid, iterations=3)
    under = whole[np.ix_(nearest_index(rows), nearest_index(cols))]
    test = ~np.isnan(land) & ~near_rock & ~near_wet & under
    thin = np.zeros_like(test)
    thin[::FILL_THIN, ::FILL_THIN] = True
    test &= thin
    test[:, :FILL_TEST_FROM_COL] = False
    r, c = np.nonzero(test)
    truth = land[r, c].astype(np.float64)
    padded = nearest_fill(np.where(raster_valid, raster_m, 0.0), raster_valid)
    nearest = padded[nearest_index(rows[r]), nearest_index(cols[c])]
    return {
        "texels": len(r),
        "nearest_median_abs_m": round(float(np.median(np.abs(nearest - truth))), 3),
        "rebuilt_median_abs_m": round(float(np.median(np.abs(rebuilt_m[r, c] - truth))), 3),
        "rebuilt_median_bias_m": round(float(np.median(rebuilt_m[r, c] - truth)), 3),
    }


def recipe4_fill(nearest: NDArray[np.floating], fill: BoolMask) -> NDArray[np.floating]:
    """Recipe 4's de-terracing of a nearest-read fill, as ``deterraced_height`` did it."""
    weight = ndimage.gaussian_filter(fill.astype(np.float64), RECIPE4_SIGMA_M, mode="nearest")
    total = ndimage.gaussian_filter(np.where(fill, nearest, 0.0), RECIPE4_SIGMA_M, mode="nearest")
    moved = np.where(fill, np.clip(weight, 0, 1), 0) * (total / np.maximum(weight, 1e-6) - nearest)
    return nearest + np.clip(moved, -RECIPE4_CLAMP_M, RECIPE4_CLAMP_M)


def seam_jump(out: NDArray[np.floating], truth: F64Grid) -> float:
    step = np.abs(out[:, SEAM_COL] - out[:, SEAM_COL - 1])
    true_step = np.abs(truth[:, SEAM_COL] - truth[:, SEAM_COL - 1])
    return float(np.median(np.abs(step - true_step)))


def check_seam(field: hf.Field, land: F32Grid, raster_m: F64Grid, raster_valid: BoolMask,
               rebuilt_m: F32Grid) -> dict[str, object]:  # fmt: skip
    """The jump at a fake seam through dry landscape, before and after the band."""
    prov = np.asarray(field.provenance_plane)
    wet = np.asarray(field.water_quality_raster()) > 0
    bad = np.isnan(land) | ndimage.binary_dilation(
        np.isin(prov, hf.PROV_CLIFF_VALUES) | wet, iterations=4
    )
    rows, cols = raster_grid(field)
    grid = field.terrain_grid
    if grid is None or grid["row_off"] is None or grid["col_off"] is None:
        raise ValueError(OFF_GRID)
    row_off, col_off = grid["row_off"], grid["col_off"]
    rng = np.random.default_rng(SEAM_SEED)
    padded = nearest_fill(np.where(raster_valid, raster_m, 0.0), raster_valid)
    width = SEAM_WINDOW
    before: list[float] = []
    after: list[float] = []
    for _ in range(20000):
        r = int(rng.integers(row_off, row_off + grid["height"] - width))
        c = int(rng.integers(col_off, col_off + grid["width"] - width))
        if not (
            bad[r : r + width, c : c + width].mean() < 0.02
            and raster_valid[int(rows[r]), int(cols[c])]
        ):
            continue
        truth = land[r : r + width, c : c + width].astype(np.float64)
        west = np.zeros((width, width), bool)
        west[:, :SEAM_COL] = True
        nearest = padded[
            np.ix_(nearest_index(rows[r : r + width]), nearest_index(cols[c : c + width]))
        ]
        old = np.where(west, truth, recipe4_fill(nearest, ~west))
        window_rebuilt = rebuilt_m[r : r + width, c : c + width]
        blended, _band = blend_seam(window_rebuilt, np.nan_to_num(truth), west, ~west)
        new = np.where(west, truth, blended)
        before.append(seam_jump(old, truth))
        after.append(seam_jump(new, truth))
        if len(before) >= SEAM_WINDOWS:
            break
    return {
        "windows": len(before),
        "recipe4_median_jump_m": round(float(np.median(before)), 3),
        "rebuilt_median_jump_m": round(float(np.median(after)), 3),
    }


def _hole_shapes(field: hf.Field) -> list[BoolMask]:
    """The field's interior no-data holes as masks, off its edge and big enough, smallest first."""
    labels, _count = ndimage.label(np.asarray(field.provenance_plane) == hf.PROV_NODATA)
    edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
    shapes: list[BoolMask] = []
    for label, box in enumerate(ndimage.find_objects(labels), start=1):
        if box is None or label in edge:
            continue
        mask = labels[box] == label
        if mask.sum() >= HOLE_MIN_TEXELS:
            shapes.append(mask)
    shapes.sort(key=lambda mask: mask.sum())
    return shapes


def check_holes(field: hf.Field, land: F32Grid) -> dict[str, object]:
    """Median MAE of a hole fill over the field's own hole shapes on known landscape."""
    shapes = _hole_shapes(field)
    valid = ~np.isnan(land)
    near_gap = ndimage.distance_transform_edt(valid) < HOLE_NEAR_GAP_M
    rng = np.random.default_rng(HOLE_SEED)
    size = land.shape[0]
    margin = HOLE_MARGIN
    nearest_mae: list[float] = []
    filled_mae: list[float] = []
    fallbacks = 0
    for shape in shapes:
        reps = 6 if shape.sum() < 20000 else 2
        h, w = shape.shape
        done = tries = 0
        while done < reps and tries < 5000:
            tries += 1
            r = int(rng.integers(margin, size - h - margin))
            c = int(rng.integers(margin, size - w - margin))
            if not near_gap[r + h // 2, c + w // 2]:
                continue
            window = (slice(r - margin, r + h + margin), slice(c - margin, c + w + margin))
            if not valid[window].all():
                continue
            truth = land[window].astype(np.float64)
            known = np.ones(truth.shape, bool)
            known[margin : margin + h, margin : margin + w] = ~shape
            near = nearest_fill(np.where(known, truth, 0.0), known)
            filled, _holes, stats = fill_holes(np.where(known, truth, np.nan), known)
            fallbacks += stats.harmonic_fallback
            nearest_mae.append(float(np.mean(np.abs(near[~known] - truth[~known]))))
            filled_mae.append(float(np.mean(np.abs(filled[~known] - truth[~known]))))
            done += 1
    return {
        "shapes": len(shapes),
        "placements": len(filled_mae),
        "nearest_median_mae_m": round(float(np.median(nearest_mae)), 3),
        "rebuilt_median_mae_m": round(float(np.median(filled_mae)), 3),
        "harmonic_fallbacks": fallbacks,
    }


def cell_overshoot(lattice: F32Grid, row_positions: F64Grid, col_positions: F64Grid,
                   values: F32Grid) -> tuple[F32Grid, BoolMask]:  # fmt: skip
    """How far each sample lies outside its own 2x2 vertex cell, and where all four exist."""
    r = np.clip(np.floor(row_positions).astype(int), 0, lattice.shape[0] - 2)[:, None]
    c = np.clip(np.floor(col_positions).astype(int), 0, lattice.shape[1] - 2)[None, :]
    corners = np.stack([lattice[r, c], lattice[r, c + 1], lattice[r + 1, c], lattice[r + 1, c + 1]])
    over = np.maximum(values - corners.max(0), corners.min(0) - values).clip(0)
    return over, (corners != hf.NODATA).all(0)


def _score_kernel(lattice: F32Grid, kernel: Callable[[F64Grid, int], AxisTaps],
                  row_positions: F64Grid, col_positions: F64Grid) -> SamplerScore:  # fmt: skip
    """One kernel on one crop: at the 1 m vertices against the lattice, and at the pixels
    against each sample's own cell."""
    rows, cols = lattice.shape
    vertex = np.arange(CROP_HALO, CROP_HALO + CROP_M + 1, dtype=np.float64)
    linear = (taps_linear(row_positions, rows), taps_linear(col_positions, cols))
    vertex_linear = (taps_linear(vertex, rows), taps_linear(vertex, cols))
    taps = (kernel(row_positions, rows), kernel(col_positions, cols))
    values, missing = sample_surface(lattice, taps, linear, hf.NODATA)
    vertex_taps = (kernel(vertex, rows), kernel(vertex, cols))
    at_vertex, _vertex_missing = sample_surface(lattice, vertex_taps, vertex_linear, hf.NODATA)
    truth = lattice[CROP_HALO : CROP_HALO + CROP_M + 1, CROP_HALO : CROP_HALO + CROP_M + 1]
    known = truth != hf.NODATA
    over, whole = cell_overshoot(lattice, row_positions, col_positions, values)
    over = over[whole & ~missing] / hf.DM_PER_M
    return {
        "vertex_max_abs_m": round(
            float(np.abs(at_vertex[known] - truth[known]).max()) / hf.DM_PER_M, 6
        ),
        "overshoot_max_m": round(float(over.max()), 3) if over.size else 0.0,
        "overshoot_p999_m": round(float(np.percentile(over, 99.9)), 3) if over.size else 0.0,
    }


def check_sampler(ground_dm: F32Grid) -> dict[str, dict[str, SamplerScore]]:
    """Exactness at the vertices and overshoot at the pixels, PCHIP against Catmull-Rom."""
    x_cm, y_cm = frame_coordinates(RENDER_PX)
    out: dict[str, dict[str, SamplerScore]] = {}
    for name, (row, col) in CROPS.items():
        window = (
            slice(row - CROP_HALO, row + CROP_M + 1 + CROP_HALO),
            slice(col - CROP_HALO, col + CROP_M + 1 + CROP_HALO),
        )
        lattice = np.asarray(ground_dm[window], np.float32)
        x0 = ORIGIN_X_CM + (col - CROP_HALO) * 100
        y0 = ORIGIN_Y_CM + (row - CROP_HALO) * 100
        crop_cols = (x_cm >= x0 + CROP_HALO * 100) & (x_cm <= x0 + (CROP_HALO + CROP_M) * 100)
        crop_rows = (y_cm >= y0 + CROP_HALO * 100) & (y_cm <= y0 + (CROP_HALO + CROP_M) * 100)
        row_positions = grid_position(y_cm[crop_rows], y0, 100.0, lattice.shape[0])
        col_positions = grid_position(x_cm[crop_cols], x0, 100.0, lattice.shape[1])
        out[name] = {
            kernel_name: _score_kernel(lattice, kernel, row_positions, col_positions)
            for kernel_name, kernel in KERNELS.items()
        }
    return out


def main() -> int:
    parser = base_parser((__doc__ or "").partition("\n")[0])
    parser.add_argument("--field", type=Path, default=ROOT / "data" / "local" / hf.DIR_NAME)
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args()
    require_gen("ooz")
    field = hf.load_field(args.field)
    if field is None or not field.has_terrain:
        print(f"no field with {hf.TERRAIN_NAME} at {args.field}")
        return 4
    store = open_container(args.game)
    z_cm, raster_valid = read_fill_raster(store)
    raster_m = z_cm.astype(np.float64) / 100.0
    land = terrain_m(field)
    rows, cols = raster_grid(field)
    rebuilt_m, _whole = reconstruct_raster(raster_m, raster_valid, rows, cols)
    report: dict[str, object] = {"build": field.build}
    report["fill"] = check_fill(field, land, raster_m, raster_valid, rebuilt_m)
    print("fill", report["fill"], flush=True)
    report["seam"] = check_seam(field, land, raster_m, raster_valid, rebuilt_m)
    print("seam", report["seam"], flush=True)
    report["holes"] = check_holes(field, land)
    print("holes", report["holes"], flush=True)
    del rebuilt_m
    ground, _meta = ground_lattice(field, np.asarray(field.height_dm).astype(np.float32))
    ground, _meta = terrain_lattice(field, ground)
    _heights, ground, fill_meta = fill_from_raster(field, ground, raster_m, raster_valid)
    report["lattice"] = fill_meta
    sampler = check_sampler(ground)
    report["sampler"] = sampler
    for name, row in sampler.items():
        print("sampler", name, row, flush=True)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0
