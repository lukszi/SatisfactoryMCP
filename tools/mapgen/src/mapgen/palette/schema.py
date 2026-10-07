"""The palette files' shapes, one TypedDict per file and block, and the check at load that a
file has exactly them (``checked``): a missing key, a stray one or an unknown rock family is
refused with its path in the file, never met mid-render.
"""

from __future__ import annotations

import typing
from collections.abc import Mapping
from typing import Literal, NotRequired, TypedDict, TypeVar, cast

from mapgen.gamedata.rocks.families import FAMILIES
from satisfactory_mcp.core.jsontypes import is_object_dict, is_object_list

__all__ = [
    "CalibrationArea",
    "CalibrationStyle",
    "CarpetStyle",
    "CrownStyle",
    "DerivedLayer",
    "FallsStyle",
    "FoamStyle",
    "InlandShoreStyle",
    "PaintedPalette",
    "PaintedWaterStyle",
    "Palette",
    "PaletteError",
    "ReliefPalette",
    "ReliefRockStyle",
    "ReliefShadeStyle",
    "ReliefWaterStyle",
    "RiverShoreStyle",
    "RockPatchesStyle",
    "RockTopStyle",
    "SatellitePalette",
    "ShoreOptics",
    "ShoreStyle",
    "TerrainPalette",
    "TitanTreesStyle",
    "ToneStyle",
    "WaterClassStyle",
    "WaterClassesStyle",
    "WetBandStyle",
    "checked",
]

#: Any one of the palette shapes below.
Palette = TypeVar("Palette", bound=Mapping[str, object])

#: The value types a palette holds as themselves, a bool never counting as a number.
_EXACT: dict[object, str] = {int: "a whole number", str: "a string", bool: "true or false"}


class PaletteError(ValueError):
    """A palette file that does not have its style's shape; the message names the place."""


# ------------------------------------------------------------------------- shared blocks


class RiverShoreStyle(TypedDict):
    """``shore.river``: a river reads at least ``min_depth_m`` deep once ``bank_m`` in."""

    min_depth_m: float
    bank_m: float


class InlandShoreStyle(TypedDict):
    """``shore.inland``: inland field water is covered fully once ``edge_m`` deep and reads at
    least ``min_depth_m`` deep."""

    min_depth_m: float
    edge_m: float


class WetBandStyle(TypedDict):
    """``shore.wet_band``: ground within ``m`` of the waterline, multiplied towards ``tint``."""

    m: float
    tint: list[float]


class FoamStyle(TypedDict):
    """``shore.foam``: a faint line over water shallower than ``max_depth_m``."""

    strength: float
    max_depth_m: float
    white: float
    width_m: float


class ShoreStyle(TypedDict, total=False):
    """``shore`` as any style may hold it: the line's stroke, the river, inland water, the wet
    band and foam."""

    stroke: float
    river: RiverShoreStyle
    inland: InlandShoreStyle
    wet_band: WetBandStyle
    foam: FoamStyle


class ShoreOptics(ShoreStyle):
    """The shore of a style that draws water over its ground: clarity, line and wet ground."""

    clarity_m: float
    edge_alpha: float
    wet_darken: float


class FallsStyle(TypedDict):
    """``falls``: the foam streak at a lip, the ring and mist where it lands."""

    about: str
    foam: list[int]
    mist_rgb: list[int]
    streak: float
    streak_end: float
    pool: float
    core: float
    ring_width: float
    mist: float
    mist_reach: float
    edge_m: float
    spread: list[float]
    pool_radius: list[float]
    top: float
    above_m: float
    strands: float
    pool_below: float


# -------------------------------------------------------------------- terrain, satellite


class TerrainPalette(TypedDict):
    """``terrain-hypsometric.json``."""

    id: str
    about: str
    ramp_lo_pct: float
    ramp_hi_pct: float
    ramp_stops: list[list[float]]
    water_shallow: list[int]
    water_deep: list[int]
    shore: ShoreOptics


class SatellitePalette(TypedDict):
    """``satellite-biome.json``."""

    id: str
    about: str
    biome_colours: dict[str, list[int]]
    no_mans_land: list[int]
    rock: list[int]
    rock_lo_deg: float
    rock_hi_deg: float
    high: list[int]
    high_lo_m: float
    high_hi_m: float
    high_lift: float
    water_shallow: list[int]
    water_deep: list[int]
    noise_seed: int
    noise_octaves: list[list[float]]
    shore: ShoreOptics
    falls: FallsStyle


# ------------------------------------------------------------------------------- relief


class ReliefRockStyle(TypedDict):
    """``rock``: the slope band rock takes over in, and its lightness step, chroma and hue."""

    lo_deg: float
    hi_deg: float
    max: float
    dl: float
    c: float
    h: float


class ReliefShadeStyle(TypedDict):
    """``shade``: the suns ``(azimuth, altitude, weight)`` and how their light moves colour."""

    suns: list[list[float]]
    mode: Literal["add", "scale"]
    k: float
    lo: float
    hi: float
    dechroma: float
    cool: list[float]
    warm: list[float]
    l_max: float


class ReliefWaterStyle(TypedDict):
    """``water``: the tint from shallow to deep by depth, the light it takes and its stroke."""

    shallow_lch: list[float]
    deep_lch: list[float]
    tau_m: float
    level_only: float
    blur_m: float
    lit: float
    stroke_lch: list[float]
    stroke: float


class ReliefPalette(TypedDict):
    """``relief-muted.json`` and ``relief-night.json``."""

    id: str
    about: str
    ramp_lo_pct: float
    ramp_hi_pct: float
    ramp_equalised: float
    ramp_lch: list[list[float]]
    biome_blend_texels: float
    biome_tints: dict[str, list[float]]
    biome_fallback: list[float]
    rock: ReliefRockStyle
    shade: ReliefShadeStyle
    borrow_ink_damp: float
    water: ReliefWaterStyle
    shore: ShoreOptics


# ------------------------------------------------------------------------------ painted


class CrownStyle(TypedDict):
    """``crowns``: whether and how the painted style draws each tree's crown."""

    draw: bool
    canopy_kept: float
    opacity: float
    darkening: float
    chroma: float
    waterline_m: float
    dome_gain: float
    shade_clamp: list[float]
    hidden_below_m: float


class PaintedWaterStyle(TypedDict):
    """The painted style's ``water``: the Beer-Lambert model and the ocean's own rows."""

    model: str
    k_per_m: list[float]
    body: list[float]
    surface_r: float
    sky: list[float]
    deep: list[float]
    deep_tau_m: float
    bed_wet: float
    inland_floor: float
    opaque_tau_m: float


class WaterClassStyle(TypedDict):
    """One inland water class's optics, in place of the ocean's."""

    k_per_m: list[float]
    body: list[float]
    deep: list[float]
    deep_tau_m: float
    turbidity: float
    bed_tint: list[float]


class _About(TypedDict):
    about: str


class WaterClassesStyle(_About, total=False):
    """``water_classes``: a class left out draws as the ocean."""

    river: WaterClassStyle
    lake: WaterClassStyle
    lake_blue: WaterClassStyle
    swamp: WaterClassStyle
    cave: WaterClassStyle
    sulfur: WaterClassStyle
    hot_spring: WaterClassStyle
    translucent: WaterClassStyle


class RockPatchesStyle(TypedDict):
    """``rock_top.patches``: the noise that breaks a family's top layer into patches."""

    seed: int
    octaves_m: list[list[float]]
    level: float
    soft: float
    flat: list[float]
    flat_gain: float


class RockTopStyle(TypedDict):
    """``rock_top``: the up-facing ramp a family's top layer takes, and its patches."""

    up: list[float]
    patches: NotRequired[RockPatchesStyle]


class TitanTreesStyle(TypedDict):
    """``titan_trees``: the Titan trees' opacity and their leaf and trunk colours."""

    opacity: float
    leaves: list[int]
    trunk: list[int]


class CarpetStyle(TypedDict):
    """``carpet``: the seabed carpet's colour and how its rosettes spread into patches."""

    colour: list[int]
    strength: float
    depth_scale: float
    blur_m: float
    gain: float


class ToneStyle(TypedDict):
    """``tone``: the painted style's exposure shoulder."""

    gain: float
    knee: float
    white: float


#: ``calibration.derived``: a layer's target made from another's (``from``, a keyword).
DerivedLayer = TypedDict(
    "DerivedLayer", {"from": str, "lightness": float, "chroma": float, "hue_deg": float}
)


class _Areas(TypedDict):
    areas: list[str]


class CalibrationArea(_Areas, total=False):
    """One ``calibration.areas`` entry: targets that hold inside the named areas only."""

    layers: dict[str, str]
    rock: str
    meshes: dict[str, str]
    canopy: str
    water: str
    water_class: str


class CalibrationStyle(TypedDict):
    """``calibration``: the display targets per layer, family, top, mesh, crown and area."""

    about: str
    pure_share: float
    min_texels: int
    area_blur_m: float
    layers: dict[str, str]
    derived: dict[str, DerivedLayer]
    canopy: str
    rock: str
    rock_keeps_exposure: bool
    families: dict[str, str]
    tops: dict[str, str]
    meshes: dict[str, str]
    crowns: dict[str, str]
    species: dict[str, str]
    areas: list[CalibrationArea]


class PaintedPalette(TypedDict):
    """``satellite-painted.json``."""

    id: str
    about: str
    albedo_darkening: float
    wet_sand_lightness_of_sand: float
    pigment: float
    canopy_gain: float
    canopy_dark: float
    crowns: CrownStyle
    have_blur_m: float
    fallback_blur_m: float
    seam_blend_m: float
    seam_jump: list[float]
    biome_tint_strength: float
    biome_tint_blur_m: float
    biome_tint_ab: dict[str, list[float]]
    rock_tint_blur_m: float
    rock_tint_lightness: float
    rock_tint_chroma: float
    rock_lightness_add: float
    mesh_colours: dict[str, list[int]]
    chroma_gain: float
    altitude_lift: float
    ramp_lo_pct: float
    ramp_hi_pct: float
    ramp_equalised: float
    ambient: float
    sky: list[float]
    sun: list[float]
    exposure: float
    borrow_ink_damp: float
    shore: ShoreStyle
    water: PaintedWaterStyle
    water_classes: WaterClassesStyle
    falls: FallsStyle
    ground: str
    rock_top: RockTopStyle
    titan_trees: TitanTreesStyle
    carpet: NotRequired[CarpetStyle]
    tone: ToneStyle
    calibration: CalibrationStyle


# --------------------------------------------------------------------------- the check


def checked(shape: type[Palette], raw: object, style: str) -> Palette:
    """``raw``, a palette file as read, once it has exactly ``shape``'s keys and value types.

    A calibration target naming a rock family the game does not have is refused here too: the
    painter would otherwise die on it mid-render.
    """
    _fits(shape, raw, style)
    calibration = _as_dict(raw, style).get("calibration")
    if calibration is not None:
        for block in ("families", "tops"):
            names = _as_dict(_as_dict(calibration, style)[block], style)
            unknown = sorted(set(names) - set(FAMILIES))
            if unknown:
                raise PaletteError(f"{style}: calibration.{block} names no family {unknown}")
    return cast(Palette, raw)


def _fits(hint: object, value: object, where: str) -> None:
    """Refuse ``value`` unless it has the type ``hint`` names, nested blocks and all."""
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if isinstance(hint, type) and typing.is_typeddict(hint):
        _fits_block(hint, value, where)
    elif origin is list:
        for index, item in enumerate(_as_list(value, where)):
            _fits(args[0], item, f"{where}[{index}]")
    elif origin is dict:
        for key, item in _as_dict(value, where).items():
            _fits(args[1], item, f"{where}.{key}")
    elif origin is Literal:
        if value not in args:
            raise PaletteError(f"{where}: {value!r} is not one of {list(args)}")
    elif hint is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise PaletteError(f"{where}: {value!r} is not a number")
    elif hint in _EXACT:
        if type(value) is not hint:
            raise PaletteError(f"{where}: {value!r} is not {_EXACT[hint]}")
    else:
        raise TypeError(f"{where}: no check for {hint!r}")


def _fits_block(shape: type, value: object, where: str) -> None:
    block = _as_dict(value, where)
    hints = typing.get_type_hints(shape)
    required: frozenset[str] = shape.__dict__["__required_keys__"]
    wrong = {"missing": required - block.keys(), "not read": block.keys() - hints.keys()}
    if any(wrong.values()):
        found = "; ".join(f"{label} {sorted(keys)}" for label, keys in wrong.items() if keys)
        raise PaletteError(f"{where}: {found}")
    for key, item in block.items():
        _fits(hints[key], item, f"{where}.{key}")


def _as_list(value: object, where: str) -> list[object]:
    if not is_object_list(value):
        raise PaletteError(f"{where}: {value!r} is not a list")
    return value


def _as_dict(value: object, where: str) -> dict[str, object]:
    if not is_object_dict(value):
        raise PaletteError(f"{where}: {value!r} is not an object")
    return value
