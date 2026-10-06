"""What the painted style reads: its palette, a band's scene, the paint store, field and ground."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple, NotRequired, Protocol, Required, TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import Plane, TitanPlanes
from mapgen.palette.scene import BandGrid
from mapgen.palette.styles import CrownStyle, PaintedWaterStyle, ShoreStyle, ToneStyle
from mapgen.terrain.crown_stamp import CrownSet
from satisfactory_mcp.core.arrays import F16Grid, F32Grid, F64Grid, I16Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "AlbedoTable",
    "AreaTarget",
    "BandTaps",
    "BandWater",
    "BiomeGrid",
    "CalibrationTargets",
    "Carpet",
    "CarpetStyle",
    "ClassOptics",
    "ColourPlanes",
    "CrownOp",
    "CrownTerms",
    "DerivedTarget",
    "FieldPlanes",
    "FloatGrid",
    "LitCrowns",
    "OpaqueWater",
    "PaintFile",
    "PaintGrid",
    "PaintMeta",
    "PaintPlane",
    "PaintedScene",
    "PaintedStyle",
    "PaintedSurface",
    "Ramp",
    "RockFamilyEntry",
    "RockPatches",
    "RockTop",
    "Sampler",
    "TitanTreesStyle",
    "WaterBase",
    "WaterClassStyle",
]

# -- the palette file ----------------------------------------------------------------------

DerivedTarget = TypedDict(
    "DerivedTarget",
    {"from": Required[str], "lightness": float, "chroma": float, "hue_deg": float},
    total=False,
)


class AreaTarget(TypedDict, total=False):
    """One ``calibration.areas`` entry: the areas it scopes, and its targets inside them."""

    areas: Required[list[str]]
    layers: dict[str, str]
    rock: str
    canopy: str
    meshes: dict[str, str]
    water: str
    water_class: str


class CalibrationTargets(TypedDict, total=False):
    """``calibration``: display sRGB targets by layer, family, top, mesh, crown and area."""

    about: str
    pure_share: Required[float]
    min_texels: Required[int]
    area_blur_m: Required[float]
    layers: Required[dict[str, str]]
    derived: dict[str, DerivedTarget]
    canopy: str
    rock: str
    rock_keeps_exposure: bool
    families: dict[str, str]
    tops: dict[str, str]
    meshes: dict[str, str]
    crowns: dict[str, str]
    species: dict[str, str]
    areas: list[AreaTarget]


class WaterClassStyle(TypedDict):
    """One inland water class's optics, as the ocean's ``water`` row spells them."""

    k_per_m: list[float]
    body: list[float]
    deep: list[float]
    deep_tau_m: float
    turbidity: float
    bed_tint: list[float]


class RockPatches(TypedDict):
    """``rock_top.patches``: the noise that breaks a family's top layer into patches."""

    seed: int
    octaves_m: list[list[float]]
    level: float
    soft: float
    flat: list[float]
    flat_gain: float


class RockTop(TypedDict):
    """``rock_top``: the up-facing ramp a family's top layer takes, and its patches."""

    up: list[float]
    patches: NotRequired[RockPatches]


class TitanTreesStyle(TypedDict, total=False):
    """``titan_trees``: their opacity over the finished pixel, and their two colours."""

    opacity: float
    leaves: list[float]
    trunk: list[float]


class CarpetStyle(TypedDict):
    """``carpet``: the seabed carpet's colour, strength, spread and depth scale."""

    colour: list[float]
    strength: float
    depth_scale: float
    blur_m: float
    gain: float


class PaintedStyle(TypedDict):
    """``palettes/satellite-painted.json``: every number the painted style draws with."""

    id: str
    about: str
    ground: NotRequired[str]
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
    mesh_colours: dict[str, list[float]]
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
    water_classes: NotRequired[dict[str, WaterClassStyle | str]]
    falls: NotRequired[JsonObject]
    rock_top: RockTop
    titan_trees: NotRequired[TitanTreesStyle]
    carpet: NotRequired[CarpetStyle]
    tone: ToneStyle
    calibration: CalibrationTargets


# -- the paint store -----------------------------------------------------------------------

#: A plane of the paint store as decoded: 8-bit weights and colours, or 16-bit heights in dm.
PaintPlane: TypeAlias = NDArray[np.uint8 | np.int16]


class PaintFile(TypedDict):
    """One plane of the paint store: its shape and kind, and the paint layer it weighs."""

    shape: list[int]
    kind: NotRequired[str]
    layer: NotRequired[str]


class AlbedoTable(TypedDict):
    """``albedo_linear``: linear colour per paint layer, overlay, the rock and the canopy."""

    layers: dict[str, list[float]]
    layers_bake_fit: NotRequired[dict[str, list[float]]]
    overlays: dict[str, list[float]]
    rock: list[float]
    canopy: list[float]


class RockFamilyEntry(TypedDict, total=False):
    """One rock family of the store: its tint and, where it has one, its top layer's colour."""

    tint: list[float] | None
    top: list[float] | None


class PaintGrid(TypedDict):
    """The paint store's 1 m grid: its size in texels and its corner."""

    width: int
    height: int
    x0_cm: float
    y0_cm: float
    spacing_cm: float


class PaintMeta(TypedDict):
    """The paint store's ``meta.json``, the parts the painted style reads."""

    grid: PaintGrid
    files: dict[str, PaintFile]
    albedo_linear: AlbedoTable
    components: list[list[int]]
    component_px: int
    rock_families: NotRequired[dict[str, RockFamilyEntry]]
    crowns: NotRequired[JsonObject]
    generator: NotRequired[str]
    generator_version: NotRequired[int]
    cl: NotRequired[int]
    digest: NotRequired[str]


class BiomeGrid(TypedDict):
    """The map-area raster: an index per texel of a square, and the asset each index is."""

    width: int
    area: U8Grid
    assets_by_index: NotRequired[list[str]]


class FieldPlanes(Protocol):
    """The parts of a heightfield the painted style reads."""

    @property
    def height(self) -> int: ...

    @property
    def width(self) -> int: ...

    @property
    def height_dm(self) -> I16Grid | None: ...

    def water_raster(self) -> I16Grid | None: ...

    def water_quality_raster(self) -> U8Grid | None: ...


# -- a band --------------------------------------------------------------------------------

#: A float plane as the painters compute it: float32 at run time, but numpy's stubs type a
#: python float operand's promotion as float64, so a signature names the kind, not the width.
FloatGrid: TypeAlias = NDArray[np.floating]
#: A band's sampling taps, rows then columns: each the texel indices and their weights.
BandTaps: TypeAlias = tuple[
    tuple[NDArray[np.generic], NDArray[np.generic]],
    tuple[NDArray[np.generic], NDArray[np.generic]],
]
#: A band's water terms by name, one plane each (``palette.water``'s).
BandWater: TypeAlias = dict[str, F32Grid]
#: The crowns stamped over a band by name, one plane each (``render.compose.domed_crowns``).
CrownTerms: TypeAlias = dict[str, F32Grid]
#: A plane of the paint store or the rock grid, sampled onto a band's pixels.
Sampler: TypeAlias = Callable[[NDArray[np.generic]], F32Grid]
#: A colour as one value per channel, or as one coarse plane per channel the band samples.
ColourPlanes: TypeAlias = F32Grid | list[F32Grid]
#: A crown colour transfer (seven numbers or seven planes) and the chroma its gate opens over.
CrownOp: TypeAlias = tuple[ColourPlanes, tuple[float, float]]
#: The dry-land height ramp: its low and high metres and the CDF between.
Ramp: TypeAlias = tuple[float, float, F64Grid]


class WaterBase(TypedDict):
    """The ocean's optics, linear: what every wet pixel without a class of its own takes."""

    k: F32Grid
    body: F32Grid
    sky: F32Grid
    deep: F32Grid
    deep_tau_m: F32Grid | np.float32
    bed: np.float32
    inland_floor: np.float32
    opaque_tau_m: np.float32


class ClassOptics(WaterBase):
    """A band's per-pixel optics by water class, and the class shares asked for."""

    turbidity: F32Grid
    tint: F32Grid
    share: NotRequired[dict[int, F32Grid]]


class LitCrowns(TypedDict):
    """A band's crowns lit and ready to lay over the pixel: alpha, colour, top, sunk share."""

    alpha: FloatGrid
    colour: FloatGrid
    top_m: FloatGrid
    sunk: FloatGrid


class PaintedScene(TypedDict):
    """One band as the painted style draws it (``render.compose``)."""

    z_m: F32Grid
    borrow: F32Grid
    ramp_lo: float
    ramp_hi: float
    water: BandWater
    crowns: CrownTerms | None
    ndl: F32Grid
    ndl_flat: np.float32
    rock_weight: F32Grid
    mesh_weight: F32Grid | None
    mesh_class: U8Grid | None
    mesh_family: U8Grid | None
    water_optics: ClassOptics | None
    grid: BandGrid


# -- the ground ----------------------------------------------------------------------------


class OpaqueWater(NamedTuple):
    """An area's calibrated opaque water: its weight on the rock grid, colour and class."""

    weight: F32Grid
    colour: F32Grid
    water_class: int


class Carpet(NamedTuple):
    """The seabed carpet on the 1 m grid: its cover share and its top in metres."""

    cover: U8Grid
    top_m: F16Grid


class PaintedSurface(Protocol):
    """What the painted painters read of the ground (``ground.PaintedGround``)."""

    palette: PaintedStyle
    albedo: list[F16Grid]
    canopy: PaintPlane
    canopy_rgb: ColourPlanes
    crown: PaintPlane | None
    crowns: CrownSet | None
    crown_ops: list[CrownOp]
    rock: list[F32Grid]
    rock_family: Plane | None
    family_rock: dict[int, list[F32Grid]]
    family_tint: F32Grid
    family_top: F32Grid
    family_has_top: F32Grid
    mesh_rgb: dict[int, ColourPlanes]
    seabed_coral: F32Grid
    titan: TitanPlanes | None
    titan_rgb: dict[int, F32Grid]
    ramp: Ramp
    water: WaterBase
    water_class: U8Grid | None
    opaque_water: list[OpaqueWater]
    carpet: Carpet | None
