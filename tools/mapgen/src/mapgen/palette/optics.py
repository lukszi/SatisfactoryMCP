"""The water of the game-painted style: what is seen under each wet pixel.

Beer-Lambert over the bed (section 27), each water class's own optics (section 33), the
seabed carpet (section 32), the crowns under the surface (section 36) and the calibrated
opaque water of an area (section 31), gated by the class it names. docs/spatial-and-map.md
sections 31 to 33, 36 and 37.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.carpet import COVER_NAME, TOP_NAME
from mapgen.gamedata.waterbodies import CLASSES, OCEAN, WATER_BODIES_NAME, class_shares
from mapgen.palette.colour import srgb_to_linear
from mapgen.palette.shore import optical_depth
from mapgen.terrain.sample import ClassMix, class_taps
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "WATER_TABLE_COLUMNS",
    "carpet_bed",
    "class_optics",
    "load_carpet",
    "load_water_bodies",
    "opaque_share",
    "paint_plane",
    "underwater",
    "water_table",
]

#: One row per water class: absorption, body, deep colour, deep tau, turbidity, bed tint.
WATER_TABLE_COLUMNS = (3, 3, 3, 1, 1, 3)

_RIVER = CLASSES.index("river")


def paint_plane(paint_dir: Path, meta: dict, name: str) -> np.ndarray:
    """One plane of the paint store, decoded to its recorded shape."""
    entry = meta["files"][name]
    shape = entry["shape"]
    flat_width = shape[1] * (shape[2] if len(shape) > 2 else 1)
    decode = hf.decode_i16 if entry.get("kind") == "i16" else hf.decode_u8
    grid = decode((paint_dir / name).read_bytes(), shape[0], flat_width)
    return grid.reshape(shape)


def load_water_bodies(paint_dir: Path, meta: dict) -> dict | None:
    """The store's water actors and hot-spring terraces; None for a store that predates them."""
    if WATER_BODIES_NAME not in meta.get("files", {}):
        return None
    return json.loads((paint_dir / WATER_BODIES_NAME).read_text(encoding="utf-8"))


def water_table(palette: dict) -> np.ndarray:
    """A row of linear optics per class plane value; a class the palette leaves out draws as
    the ocean, a mouth blend mixes its classes' rows by ``class_shares``."""
    water = palette["water"]
    ocean = {
        "k_per_m": water["k_per_m"],
        "body": water["body"],
        "deep": water["deep"],
        "deep_tau_m": water["deep_tau_m"],
        "turbidity": 0.0,
        "bed_tint": [1.0, 1.0, 1.0],
    }
    rows = []
    for name in CLASSES:
        entry = palette.get("water_classes", {}).get(name, ocean)
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


def class_optics(plane, rows, base: dict, taps, river=None, shares=()) -> dict | None:
    """Per-pixel optics for a band with inland water; None draws every pixel as the ocean.

    ``river`` is the ribbon's share of each pixel's water, drawn with the river row.
    ``shares`` are class ids whose share of each pixel's water comes back under ``share``.
    """
    mix = ClassMix(class_taps(plane, taps), OCEAN)
    ribbon = river is not None and bool(np.any(river > 0))
    if mix.classes() <= {0, OCEAN} and not ribbon:
        return None
    mixed = mix.of(rows)
    if ribbon:
        mixed += river[..., None] * (rows[_RIVER] - mixed)
    parts = np.split(mixed, np.cumsum(WATER_TABLE_COLUMNS)[:-1], axis=-1)
    k, body, deep, tau, turbidity, tint = parts
    optics = {**base, "k": k, "body": body, "deep": deep, "deep_tau_m": tau,
              "turbidity": turbidity, "tint": tint}  # fmt: skip
    if shares:
        got = mix.of(class_shares()[:, list(shares)])
        if ribbon:
            got *= (1.0 - river)[..., None]
        optics["share"] = {cid: got[..., i] for i, cid in enumerate(shares)}
    return optics


def opaque_share(cid: int, optics: dict | None, water: dict, classified: bool):
    """How much of each pixel's water is class ``cid``; None where the band has none.

    Without a class plane, inland water (off the ocean's reach) stands in for every class.
    """
    if optics is not None and "share" in optics:
        return optics["share"][cid]
    if not classified:
        return 1.0 - water["ocean"]
    return None


def load_carpet(paint_dir: Path, meta: dict, palette: dict):
    """The seabed carpet's cover (u8) and top (metres, float16), or ``None`` without it.

    The rosettes are spread into patches, ``1 - exp(-gain * blurred share)``. No-data tops take
    the highest top within the blur, so the sampler never blends a sentinel into a patch edge.
    """
    style = palette.get("carpet", {})
    if COVER_NAME not in meta["files"] or not style.get("strength"):
        return None
    share = paint_plane(paint_dir, meta, COVER_NAME).astype(np.float32) / np.float32(255.0)
    spread = ndimage.gaussian_filter(share, style["blur_m"])
    cover = np.round((1.0 - np.exp(-style["gain"] * spread)) * 255).astype(np.uint8)
    top = paint_plane(paint_dir, meta, TOP_NAME)
    reach = 2 * int(np.ceil(2 * style["blur_m"])) + 1
    near = ndimage.grey_dilation(top, size=reach)
    missing = top == hf.NODATA
    top = np.where(missing, near, top).astype(np.float32) / np.float32(hf.DM_PER_M)
    top[missing & (near == hf.NODATA)] = np.float32(-1000.0)
    return cover, top.astype(np.float16)


def carpet_bed(under, scene, ground, sample):
    """``under`` with the seabed carpet seen through the water above its own top."""
    p, w = ground.palette["carpet"], ground.water
    depth = scene["water"]["depth_m"]
    top = sample(ground.carpet[1])
    above = np.clip(scene["z_m"] + depth - top, 0.0, None) * np.float32(p["depth_scale"])
    cover = sample(ground.carpet[0]) / np.float32(255.0) * np.float32(p["strength"])
    cover = np.where(depth > 0.0, np.clip(cover, 0.0, 1.0), 0.0)[..., None]
    transmit = np.exp(-w["k"] * above[..., None])
    rgb = srgb_to_linear(p["colour"])
    seen = rgb * transmit + w["body"] * (1.0 - transmit) + w["sky"]
    return under * (1.0 - cover) + seen * cover


def underwater(g, scene: dict, ground, sample, sample_rock, exposure, trees=None):
    """The colour under the water surface: the bed through the class's optics, the carpet,
    the crowns under the surface (``trees``, ``crown_layer``'s), the open-sea term, and the
    opaque area water where its class is."""
    p = ground.palette
    water = scene["water"]
    optics = scene.get("water_optics")
    w = optics or ground.water
    depth = optical_depth(water, p["shore"].get("river"))[..., None]
    floor = w["inland_floor"] * (1.0 - water["ocean"])[..., None]
    tint = w["tint"] if "turbidity" in w else np.float32(1.0)
    if "turbidity" in w:
        floor = np.maximum(floor, w["turbidity"])

    def through(colour, metres):
        transmit = np.exp(-w["k"] * metres) * (1.0 - floor)
        return colour * w["bed"] * tint * transmit + w["body"] * (1.0 - transmit) + w["sky"]

    under = through(g * exposure, depth)
    if ground.carpet is not None:
        under = carpet_bed(under, scene, ground, sample)
    sunk = None if trees is None else (trees["alpha"] * trees["sunk"])[..., None]
    if sunk is not None and sunk.any():
        above = np.maximum(scene["z_m"] + water["depth_m"] - trees["top_m"], 0.0)[..., None]
        under = under * (1.0 - sunk) + through(trees["colour"], above) * sunk
    open_sea = 1.0 - np.exp(-depth / w["deep_tau_m"])
    under = under * (1.0 - open_sea) + w["deep"] * open_sea
    if ground.opaque_water:
        murk = 1.0 - np.exp(-depth / w["opaque_tau_m"])
        classified = getattr(ground, "water_class", None) is not None
        for weight, colour, cid in ground.opaque_water:
            share = opaque_share(cid, optics, water, classified)
            if share is None:
                continue
            s = (sample_rock(weight) * share)[..., None] * murk
            under = under * (1.0 - s) + colour * s
    return under
