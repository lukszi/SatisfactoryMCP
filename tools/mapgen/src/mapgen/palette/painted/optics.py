"""The water of the game-painted style: what is seen under each wet pixel.

Beer-Lambert over the bed (section 27), the class plane and each class's own optics (section
33), the seabed carpet (section 32), the crowns under the surface (section 36) and the
calibrated opaque water of an area (section 31), gated by the class it names.
docs/spatial-and-map.md sections 31 to 33, 36 and 37.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.colour import srgb_to_linear
from mapgen.gamedata.vegetation.carpet import COVER_NAME, TOP_NAME
from mapgen.gamedata.water.bodies import OCEAN, WATER_CLASSES
from mapgen.palette.painted.albedo import paint_plane
from mapgen.palette.painted.shapes import (
    BandTaps,
    BandWater,
    Carpet,
    ClassOptics,
    CrownLayer,
    FloatGrid,
    PaintedPalette,
    PaintedScene,
    PaintedSurface,
    PaintMeta,
    Sampler,
    WaterBase,
    WaterClassStyle,
)
from mapgen.palette.painted.water_classes import class_shares
from mapgen.palette.water.shore import optical_depth
from mapgen.terrain.sample import ClassMix, class_taps
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "WATER_TABLE_COLUMNS",
    "base_water",
    "carpet_bed",
    "class_optics",
    "load_carpet",
    "opaque_share",
    "underwater",
    "water_table",
]

#: One row per water class: absorption, body, deep colour, deep tau, turbidity, bed tint.
WATER_TABLE_COLUMNS = (3, 3, 3, 1, 1, 3)

_RIVER = WATER_CLASSES.index("river")


def base_water(palette: PaintedPalette) -> WaterBase:
    """The palette's ocean optics, linear: what every wet pixel without a class takes."""
    water = palette["water"]
    return {
        "k": np.asarray(water["k_per_m"], np.float32),
        "body": srgb_to_linear(water["body"]),
        "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
        "deep": srgb_to_linear(water["deep"]),
        "deep_tau_m": np.float32(water["deep_tau_m"]),
        "bed": np.float32(water["bed_wet"]),
        "inland_floor": np.float32(water.get("inland_floor", 0.0)),
        "opaque_tau_m": np.float32(water["opaque_tau_m"]),
    }


def water_table(palette: PaintedPalette) -> FloatGrid:
    """A row of linear optics per class plane value; a class the palette leaves out draws as
    the ocean, a mouth blend mixes its classes' rows by ``class_shares``."""
    water = palette["water"]
    ocean: WaterClassStyle = {
        "k_per_m": water["k_per_m"],
        "body": water["body"],
        "deep": water["deep"],
        "deep_tau_m": water["deep_tau_m"],
        "turbidity": 0.0,
        "bed_tint": [1.0, 1.0, 1.0],
    }
    classes = palette.get("water_classes", {})
    rows: list[list[float]] = []
    for name in WATER_CLASSES:
        entry = classes.get(name, ocean)
        if isinstance(entry, str):
            raise TypeError(f"the palette's water_classes.{name} is text, not optics")
        rows.append(
            [
                *entry["k_per_m"],
                *srgb_to_linear(entry["body"]),
                *srgb_to_linear(entry["deep"]),
                entry["deep_tau_m"],
                entry["turbidity"],
                *entry["bed_tint"],
            ]
        )
    return class_shares() @ np.asarray(rows, np.float32)


def class_optics(
    plane: U8Grid,
    rows: FloatGrid,
    base: WaterBase,
    taps: BandTaps,
    river: FloatGrid | None = None,
    shares: Sequence[int] = (),
) -> ClassOptics | None:
    """Per-pixel optics for a band with inland water; None draws every pixel as the ocean.

    ``river`` is the ribbon's share of each pixel's water, drawn with the river row.
    ``shares`` are class ids whose share of each pixel's water comes back under ``share``.
    """
    mix = ClassMix(class_taps(plane, taps), OCEAN)
    ribbon = river if river is not None and bool(np.any(river > 0)) else None
    if mix.classes() <= {0, OCEAN} and ribbon is None:
        return None
    mixed = mix.of(rows)
    if ribbon is not None:
        mixed += ribbon[..., None] * (rows[_RIVER] - mixed)
    parts = np.split(mixed, np.cumsum(WATER_TABLE_COLUMNS)[:-1], axis=-1)
    k, body, deep, tau, turbidity, tint = parts
    optics: ClassOptics = {**base, "k": k, "body": body, "deep": deep, "deep_tau_m": tau,
                           "turbidity": turbidity, "tint": tint}  # fmt: skip
    if shares:
        got = mix.of(class_shares()[:, list(shares)])
        if ribbon is not None:
            got *= (1.0 - ribbon)[..., None]
        optics["share"] = {cid: got[..., i] for i, cid in enumerate(shares)}
    return optics


def opaque_share(
    cid: int, optics: ClassOptics | None, water: BandWater, classified: bool
) -> FloatGrid | None:
    """How much of each pixel's water is class ``cid``; None where the band has none.

    Without a class plane, inland water (off the ocean's reach) stands in for every class.
    """
    if optics is not None and "share" in optics:
        return optics["share"][cid]
    if not classified:
        return 1.0 - water["ocean"]
    return None


def load_carpet(paint_dir: Path, meta: PaintMeta, palette: PaintedPalette) -> Carpet | None:
    """The seabed carpet's cover (u8) and top (metres, float16), or ``None`` without it.

    The rosettes are spread into patches, ``1 - exp(-gain * blurred share)``. No-data tops take
    the highest top within the blur, so the sampler never blends a sentinel into a patch edge.
    """
    style = palette.get("carpet")
    if style is None or COVER_NAME not in meta["files"] or not style.get("strength"):
        return None
    share = paint_plane(paint_dir, meta, COVER_NAME).astype(np.float32) / np.float32(255.0)
    spread = ndimage.gaussian_filter(share, style["blur_m"])
    cover = np.round((1.0 - np.exp(-style["gain"] * spread)) * 255).astype(np.uint8)
    top = paint_plane(paint_dir, meta, TOP_NAME)
    reach = 2 * int(np.ceil(2 * style["blur_m"])) + 1
    near = ndimage.grey_dilation(top, size=(reach, reach))
    missing = top == hf.NODATA
    top_m = np.where(missing, near, top).astype(np.float32) / np.float32(hf.DM_PER_M)
    top_m[missing & (near == hf.NODATA)] = np.float32(-1000.0)
    return Carpet(cover, top_m.astype(np.float16))


def carpet_bed(
    under: FloatGrid, scene: PaintedScene, ground: PaintedSurface, sample: Sampler
) -> FloatGrid:
    """``under`` with the seabed carpet seen through the water above its own top."""
    style, carpet, water = ground.palette.get("carpet"), ground.carpet, ground.water
    if style is None or carpet is None:
        return under
    depth = scene["water"]["depth_m"]
    top = sample(carpet[1])
    above = np.clip(scene["z_m"] + depth - top, 0.0, None) * np.float32(style["depth_scale"])
    cover = sample(carpet[0]) / np.float32(255.0) * np.float32(style["strength"])
    cover = np.where(depth > 0.0, np.clip(cover, 0.0, 1.0), 0.0)[..., None]
    transmit = np.exp(-water["k"] * above[..., None])
    rgb = srgb_to_linear(style["colour"])
    seen = rgb * transmit + water["body"] * (1.0 - transmit) + water["sky"]
    return under * (1.0 - cover) + seen * cover


def underwater(
    g: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample: Sampler,
    sample_rock: Sampler,
    exposure: float | np.float32,
    crowns: CrownLayer | None = None,
) -> FloatGrid:
    """The colour under the water surface: the bed through the class's optics, the carpet,
    the ``crowns`` under the surface, the open-sea term, and the opaque area water where its
    class is."""
    water = scene["water"]
    optics = scene.get("water_optics")
    w = optics or ground.water
    depth = optical_depth(water, ground.palette["shore"].get("river"))[..., None]
    floor = w["inland_floor"] * (1.0 - water["ocean"])[..., None]
    tint = w["tint"] if "turbidity" in w else np.float32(1.0)
    if "turbidity" in w:
        floor = np.maximum(floor, w["turbidity"])

    def through(colour: FloatGrid, metres: FloatGrid) -> FloatGrid:
        transmit = np.exp(-w["k"] * metres) * (1.0 - floor)
        return colour * w["bed"] * tint * transmit + w["body"] * (1.0 - transmit) + w["sky"]

    under = through(g * exposure, depth)
    if ground.carpet is not None:
        under = carpet_bed(under, scene, ground, sample)
    if crowns is not None:
        sunk = (crowns["alpha"] * crowns["sunk"])[..., None]
        if sunk.any():
            above = np.maximum(scene["z_m"] + water["depth_m"] - crowns["top_m"], 0.0)[..., None]
            under = under * (1.0 - sunk) + through(crowns["colour"], above) * sunk
    open_sea = 1.0 - np.exp(-depth / w["deep_tau_m"])
    under = under * (1.0 - open_sea) + w["deep"] * open_sea
    if ground.opaque_water:
        murk = 1.0 - np.exp(-depth / w["opaque_tau_m"])
        classified = ground.water_class is not None
        for weight, colour, cid in ground.opaque_water:
            share = opaque_share(cid, optics, water, classified)
            if share is None:
                continue
            s = (sample_rock(weight) * share)[..., None] * murk
            under = under * (1.0 - s) + colour * s
    return under
