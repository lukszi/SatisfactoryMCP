"""Each paint layer's calibrated target, measured where it is drawn: the median colour of its
pure texels, global or inside an area entry's areas, and the target taken back to ground colour
with the altitude lift those texels stand at. docs/map/calibration.md section 31, "The
transfer".
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import TypeAlias

import numpy as np

from mapgen.palette.painted.calibration import (
    display_to_ground,
    median_lab,
    split_weight,
    with_derived,
)
from mapgen.palette.painted.rock_grid import ROCK_GRID_M
from mapgen.palette.painted.shapes import (
    CalibrationArea,
    FloatGrid,
    PaintedPalette,
    PaintPlane,
    Ramp,
)
from mapgen.palette.painted.transfer import LayerOp, layer_op, layer_transfer
from mapgen.palette.styles import ramp_position
from satisfactory_mcp.core.arrays import BoolMask, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["EVERY_4TH", "DrawnGround", "calibrate_layers", "fine_share"]

#: Every fourth texel each way: where the medians of the paint are measured.
EVERY_4TH = (slice(None, None, 4), slice(None, None, 4))

#: A layer's colour transfer to measure: its key, layer, display target and where it counts.
LayerJob: TypeAlias = tuple[str, str, str, BoolMask]
#: Where the ground is drawn, on the ``EVERY_4TH`` texels: its height in metres (NaN off the
#: field), the texels no rock, arch or boulder stands on, and the style's height ramp.
DrawnGround: TypeAlias = tuple[FloatGrid, BoolMask, Ramp]
#: An area entry's weight on the rock grid, by its area keys.
AreaWeight: TypeAlias = Callable[[Collection[str]], FloatGrid]


def fine_share(coarse: FloatGrid, rows: int, cols: int) -> U8Grid:
    """A coarse 0..1 weight on the 1 m grid as a 0..255 share, nearest."""
    r = np.minimum(np.arange(rows) // ROCK_GRID_M, coarse.shape[0] - 1)
    c = np.minimum(np.arange(cols) // ROCK_GRID_M, coarse.shape[1] - 1)
    return np.round(coarse[np.ix_(r, c)] * 255.0).astype(np.uint8)


def calibrate_layers(
    albedo: FloatGrid,
    weights: Mapping[str, PaintPlane],
    palette: PaintedPalette,
    area_weight: AreaWeight,
    drawn: DrawnGround | None,
) -> tuple[FloatGrid, JsonObject]:
    """Each layer's median colour moved onto its target, measured on whatever albedo came,
    and what was measured.

    An area entry's layer target takes that layer's weight inside its areas; the global target
    takes the rest, and each one's source median is measured on its own side. Given the
    ``drawn`` ground, the median is taken over the pure texels it shows (all of them where
    those are too few), and the target is taken back to ground colour at their median ramp
    position, the altitude lift they are drawn with; without it, over all pure texels at the
    ramp's middle.
    """
    cal = with_derived(palette["calibration"])
    flat = albedo[EVERY_4TH]
    total = np.zeros(flat.shape[:2], np.float32)
    for w in weights.values():
        total += w[EVERY_4TH]
    split = dict(weights)
    jobs = _area_layer_jobs(cal.get("areas", []), weights, split, area_weight)
    scoped: dict[str, BoolMask] = {}
    for _key, name, _hex, inside in jobs:
        scoped[name] = scoped.get(name, np.zeros_like(inside)) | inside
    for name, hex_colour in cal["layers"].items():
        if name in weights:
            outside = ~scoped[name] if name in scoped else np.ones(total.shape, bool)
            jobs.append((name, name, hex_colour, outside))
    ops: dict[str, LayerOp] = {}
    record: JsonObject = {}
    for key, name, hex_colour, where in jobs:
        pure = (weights[name][EVERY_4TH] >= cal["pure_share"] * np.maximum(total, 1.0)) & where
        if pure.sum() < cal["min_texels"]:
            continue
        shown = pure if drawn is None else pure & drawn[1]
        if shown.sum() >= cal["min_texels"]:
            pure = shown
        at = _ramp_at(drawn, pure, palette)
        ops[key] = layer_op(median_lab(flat[pure]), display_to_ground(palette, hex_colour, at))
        record[key] = {"texels": int(pure.sum()), "dL": round(ops[key][0], 4), "ramp_at": at}
    return (layer_transfer(albedo, split, ops) if ops else albedo), record


def _ramp_at(drawn: DrawnGround | None, pure: BoolMask, palette: PaintedPalette) -> float:
    """The median ramp position of the ``pure`` texels; 0.5 without the drawn ground or any
    of them on the field."""
    if drawn is None:
        return 0.5
    z_m, _shown, (lo, hi, cdf) = drawn
    found = z_m[pure]
    found = found[np.isfinite(found)]
    if not found.size:
        return 0.5
    position = ramp_position(found, lo, hi, cdf, palette["ramp_equalised"])
    return round(float(np.median(position)), 4)


def _area_layer_jobs(
    entries: Sequence[CalibrationArea],
    weights: Mapping[str, PaintPlane],
    split: dict[str, PaintPlane],
    area_weight: AreaWeight,
) -> list[LayerJob]:
    """Each area entry's layer targets, with the layer's weight in ``split`` cut into the
    share inside its areas (``layer@entry``) and the rest."""
    jobs: list[LayerJob] = []
    for i, entry in enumerate(entries):
        layers = {k: v for k, v in entry.get("layers", {}).items() if k in weights}
        if not layers:
            continue
        shape = next(iter(weights.values())).shape
        share = fine_share(area_weight(entry["areas"]), shape[0], shape[1])
        inside = share[EVERY_4TH] >= 128
        for name, hex_colour in layers.items():
            key = f"{name}@{i}"
            split[key], split[name] = split_weight(split[name], share)
            jobs.append((key, name, hex_colour, inside))
    return jobs
