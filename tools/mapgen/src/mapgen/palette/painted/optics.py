"""The water of the game-painted style: what is seen under each wet pixel.

Beer-Lambert over the bed (section 27), the class plane and each class's own optics (section
33), the seabed carpet (section 32), the crowns under the surface (section 36) and the
calibrated opaque water of an area (section 31), gated by the class it names.
docs/spatial-and-map.md sections 31 to 33, 36 and 37. The mix runs as a numba kernel unless
``mapgen.jit`` selects this numpy, its reference (docs/map/renders.md section 41).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias

import numpy as np
from scipy import ndimage

from mapgen.colour import srgb_to_linear
from mapgen.gamedata.vegetation.carpet import COVER_NAME, TOP_NAME
from mapgen.gamedata.water.bodies import OCEAN, WATER_CLASSES
from mapgen.jit import kernels_on
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
    UnderwaterScene,
    WaterBase,
    WaterClassStyle,
)
from mapgen.palette.painted.water_classes import class_shares
from mapgen.palette.water.shore import WET_MIX_MOST, optical_depth, wet_mix
from mapgen.palette.water.wet import EveryPixel, WetPixels, float32_planes
from mapgen.terrain.sample import ClassMix, class_taps
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "UNDERWATER_TERMS",
    "WATER_TABLE_COLUMNS",
    "base_water",
    "carpet_bed",
    "class_optics",
    "load_carpet",
    "mix_underwater",
    "opaque_share",
    "underwater",
    "water_table",
]

#: One row per water class: absorption, body, deep colour, deep tau, turbidity, bed tint.
WATER_TABLE_COLUMNS = (3, 3, 3, 1, 1, 3)

#: The band's water terms ``underwater`` reads: ``mix_underwater`` takes these alone onto the
#: wet pixels.
UNDERWATER_TERMS = frozenset({"depth_m", "ocean", "river", "river_below_m"})

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
    the ocean, a mouth blend mixes its classes' rows by ``class_shares``, class by class."""
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
    shares, table = class_shares(), np.asarray(rows, np.float32)
    mixed = shares[:, :1] * table[0]
    for k in range(1, len(table)):
        mixed = mixed + shares[:, k : k + 1] * table[k]
    return mixed


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
    under: FloatGrid, scene: UnderwaterScene, ground: PaintedSurface, sample: Sampler
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
    scene: UnderwaterScene,
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


def mix_underwater(
    lit: FloatGrid,
    g: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample: Sampler,
    sample_rock: Sampler,
    exposure: float | np.float32,
    crowns: CrownLayer | None = None,
) -> FloatGrid:
    """``lit`` with ``underwater`` mixed in by the water's cover, worked out on the wet pixels
    only: it is per pixel, so its bits there are the whole band's (``water.wet``). It reads
    the scene's heights, water and optics alone, and those are all that is taken."""
    cover = scene["water"]["cover"]
    wet = WetPixels(cover)
    if kernels_on() and float32_planes(lit, g, exposure):
        compiled = _mix_compiled(lit, g, (scene, ground), (sample, sample_rock, exposure), crowns,
                                 wet)  # fmt: skip
        if compiled is not None:
            return compiled
    if wet.whole:
        under = underwater(g, scene, ground, sample, sample_rock, exposure, crowns)
        return wet_mix(lit, under, cover[..., None])
    optics = scene.get("water_optics")
    taken: UnderwaterScene = {
        "z_m": wet.take(scene["z_m"]),
        "water": wet.take_planes(scene["water"], UNDERWATER_TERMS),
        "water_optics": None if optics is None else wet.take_planes(optics),
    }
    under = underwater(
        wet.take(g),
        taken,
        ground,
        lambda plane: wet.take(sample(plane)),
        lambda plane: wet.take(sample_rock(plane)),
        exposure,
        None if crowns is None else wet.take_planes(crowns),
    )
    return wet.mix(lit, wet.take(lit), under, wet.take(cover)[..., None])


#: A kernel's planes: arrays of the pixels it works on, and colours.
_Terms: TypeAlias = tuple[FloatGrid, ...]


def _mix_compiled(
    lit: FloatGrid,
    g: FloatGrid,
    surface: tuple[PaintedScene, PaintedSurface],
    reads: tuple[Sampler, Sampler, float | np.float32],
    crowns: CrownLayer | None,
    wet: WetPixels,
) -> FloatGrid | None:
    """``mix_underwater`` by the kernel; every ``exp`` of ``underwater`` and ``carpet_bed`` is
    worked out here, by numpy and as they work it out, on the pixels the mix reads. None, for
    the numpy painter, where a plane they read is not float32."""
    from mapgen.palette.painted import kernels

    scene, ground = surface
    sample, sample_rock, exposure = reads
    pixels = EveryPixel(wet.shape) if wet.whole else wet
    water = pixels.take_planes(scene["water"], UNDERWATER_TERMS)
    optics = scene.get("water_optics")
    in_place = ("tint", "body", "deep")
    taken = None if optics is None else pixels.take_planes(
        optics, [key for key in optics if key not in in_place])  # fmt: skip
    w = taken or ground.water
    depth = optical_depth(water, ground.palette["shore"].get("river"))[..., None]
    floor = w["inland_floor"] * (1.0 - water["ocean"])[..., None]
    if "turbidity" in w:
        floor = np.maximum(floor, w["turbidity"])
    wet_terms = (np.exp(-w["k"] * depth), (1.0 - floor)[:, 0],
                 np.exp(-depth / w["deep_tau_m"])[:, 0])  # fmt: skip
    heights = (scene, water["depth_m"])  # the heights are read only where they are needed
    carpet = _carpet_terms(pixels, heights, ground, sample)
    sunk = _sunk_terms(pixels, heights, crowns, w["k"])
    murky = (sample_rock, w.get("opaque_tau_m"))
    opaque = _opaque_terms(pixels, (depth, water, taken), ground, murky)
    band = _band_terms(g, scene, crowns, ground.water if optics is None else optics)
    knobs = (exposure, w["bed"], w["sky"])
    if not float32_planes(*band, *wet_terms, *carpet, *sunk, *opaque, *knobs):
        return None
    flags = (len(carpet[1]) > 0, len(sunk[1]) > 0, len(opaque[1]) > 0, wet.whole)
    out = kernels.underwater(lit.reshape(-1, 3), pixels.index, band, wet_terms,
                             (carpet, sunk, opaque), np.hstack(knobs, dtype=np.float32), flags,
                             WET_MIX_MOST)  # fmt: skip
    return out.reshape(lit.shape)


def _band_terms(
    g: FloatGrid, scene: PaintedScene, crowns: CrownLayer | None, optics: WaterBase | ClassOptics
) -> _Terms:
    """What the kernel reads of the band in place, laid flat: ``g``, the cover, the optics'
    tint, body and deep colour (a colour for every pixel where they are one), the crowns'."""
    size = g.shape[0] * g.shape[1]

    def flat(plane: FloatGrid) -> FloatGrid:
        if plane.ndim < 2:
            return np.broadcast_to(plane, (size, *plane.shape))
        return plane.reshape(size, *plane.shape[2:])

    tint = optics["tint"] if "turbidity" in optics else np.ones(3, np.float32)
    crown = np.zeros((0, 3), np.float32) if crowns is None else flat(crowns["colour"])
    colours = (flat(tint), flat(optics["body"]), flat(optics["deep"]), crown)
    return flat(g), flat(scene["water"]["cover"]), *colours


def _carpet_terms(
    pixels: WetPixels,
    heights: tuple[PaintedScene, FloatGrid],
    ground: PaintedSurface,
    sample: Sampler,
) -> _Terms:
    """``carpet_bed``'s ``exp``, cover, colour and the ocean's body and sky; no pixels
    without the carpet."""
    style, carpet, water = ground.palette.get("carpet"), ground.carpet, ground.water
    if style is None or carpet is None:
        return (np.zeros((0, 3), np.float32), np.zeros(0, np.float32),
                *(np.zeros(3, np.float32) for _ in range(3)))  # fmt: skip
    scene, depth = heights
    z_m = pixels.take(scene["z_m"])
    top = pixels.take(sample(carpet[1]))
    above = np.clip(z_m + depth - top, 0.0, None) * np.float32(style["depth_scale"])
    cover = pixels.take(sample(carpet[0])) / np.float32(255.0) * np.float32(style["strength"])
    cover = np.where(depth > 0.0, np.clip(cover, 0.0, 1.0), 0.0)
    transmit = np.exp(-water["k"] * above[..., None])
    return transmit, cover, srgb_to_linear(style["colour"]), water["body"], water["sky"]


def _sunk_terms(
    pixels: WetPixels, heights: tuple[PaintedScene, FloatGrid], crowns: CrownLayer | None,
    k: FloatGrid,
) -> _Terms:  # fmt: skip
    """The sunk crowns' ``exp`` and share; no pixels where none is sunk."""
    sunk = None if crowns is None else pixels.take(crowns["alpha"]) * pixels.take(crowns["sunk"])
    if crowns is None or sunk is None or not sunk.any():
        return np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
    scene, depth = heights
    z_m = pixels.take(scene["z_m"])
    above = np.maximum(z_m + depth - pixels.take(crowns["top_m"]), 0.0)[..., None]
    return np.exp(-k * above), sunk


def _opaque_terms(
    pixels: WetPixels,
    water: tuple[FloatGrid, BandWater, ClassOptics | None],
    ground: PaintedSurface,
    reads: tuple[Sampler, FloatGrid | np.float32 | None],
) -> _Terms:
    """The opaque water's ``exp``, and per class with a share its sampled weight times that
    share and its colour; no classes without any."""
    depth, band_water, optics = water
    sample_rock, tau = reads
    shares: list[FloatGrid] = []
    colours: list[FloatGrid] = []
    murk = np.zeros(0, np.float32)
    if ground.opaque_water:
        assert tau is not None, "the opaque water comes with its depth scale"
        murk = np.exp(-depth / tau)[:, 0]
        classified = ground.water_class is not None
        for weight, colour, cid in ground.opaque_water:
            share = opaque_share(cid, optics, band_water, classified)
            if share is not None:
                shares.append(pixels.take(sample_rock(weight)) * share)
                colours.append(colour)
    if not shares:
        return murk, np.zeros((0, 0), np.float32), np.zeros((0, 3), np.float32)
    return murk, np.stack(shares), np.stack(colours)
