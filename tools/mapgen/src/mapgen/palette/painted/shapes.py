"""What the painted style reads: its palette, a band's scene, the paint store, field and ground."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple, NotRequired, Protocol, TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import Plane, TitanPlanes
from mapgen.gamedata.level.lighting import AtmosphereVolume, LevelLighting
from mapgen.gamedata.vegetation.crown_sprites import CrownsBlock, MaterialColour
from mapgen.palette.scene import BandGrid, BandScene, BandTaps, UnderwaterWater, WaterTerms
from mapgen.palette.schema import (
    CalibrationArea,
    CalibrationStyle,
    CarpetStyle,
    DerivedLayer,
    PaintedPalette,
    RockPatchesStyle,
    RockTopStyle,
    TitanTreesStyle,
    WaterClassStyle,
)
from mapgen.terrain.crown_stamp import CrownSet, LitCrowns
from mapgen.terrain.ground_detail.reference import GroundDetail
from satisfactory_mcp.core.arrays import F16Grid, F32Grid, FloatGrid, I16Grid, U8Grid

__all__ = [
    "AlbedoTable",
    "BandTaps",
    "BandWater",
    "BiomeGrid",
    "CalibrationArea",
    "CalibrationStyle",
    "Carpet",
    "CarpetStyle",
    "ClassOptics",
    "ColourPlanes",
    "CrownLayer",
    "CrownOp",
    "DerivedLayer",
    "FieldPlanes",
    "FloatGrid",
    "OpaqueWater",
    "PaintFile",
    "PaintGrid",
    "PaintMeta",
    "PaintPlane",
    "PaintedPalette",
    "PaintedScene",
    "PaintedSurface",
    "Ramp",
    "RockFamilyEntry",
    "RockPatchesStyle",
    "RockTopStyle",
    "Sampler",
    "TitanTreesStyle",
    "UnderwaterScene",
    "WaterBase",
    "WaterClassStyle",
    "WetOptics",
]

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
    """One rock family of the store: its tint and, where it has one, its top layer's colour
    and texture."""

    material: str
    tint: list[float] | None
    top_texture: str | None
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
    crowns: NotRequired[CrownsBlock]
    generator: NotRequired[str]
    generator_version: NotRequired[int]
    cl: NotRequired[int]
    digest: NotRequired[str]
    texture_means_linear: NotRequired[dict[str, list[float]]]
    material_vectors: NotRequired[dict[str, list[float]]]
    lighting: NotRequired[LevelLighting | None]
    atmosphere_volumes: NotRequired[list[AtmosphereVolume]]
    mesh_materials: NotRequired[dict[str, MaterialColour]]


class BiomeGrid(TypedDict):
    """The map-area raster: an index per texel of a square, and the asset each index is."""

    width: int
    area: U8Grid
    assets_by_index: list[str | None]


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

#: A band's water terms by name, one plane each (``palette.water``'s).
BandWater: TypeAlias = WaterTerms
#: A plane of the paint store or the rock grid, sampled onto a band's pixels.
Sampler: TypeAlias = Callable[[NDArray[np.generic]], F32Grid]
#: A colour as one value per channel, or as one coarse plane per channel the band samples.
ColourPlanes: TypeAlias = F32Grid | list[F32Grid]
#: A crown colour transfer (seven numbers or seven planes) and the chroma its gate opens over.
CrownOp: TypeAlias = tuple[ColourPlanes, tuple[float, float]]
#: The dry-land height ramp: its low and high metres and the CDF between.
Ramp: TypeAlias = tuple[float, float, F32Grid]


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


class CrownLayer(TypedDict):
    """A band's crowns lit and ready to lay over the pixel: alpha, colour, top, sunk share."""

    alpha: FloatGrid
    colour: FloatGrid
    top_m: FloatGrid
    sunk: FloatGrid


class PaintedScene(BandScene):
    """One band as the painted style draws it (``render.draw.painting``): the shared scene, then
    its crowns, sun term, rock and mesh weights, the arches' and boulders' lift over the
    cliffs, water optics, and the ground's detail under the bake."""

    crowns: LitCrowns | None
    titan_crowns: NotRequired[LitCrowns | None]
    ndl: FloatGrid
    ndl_flat: np.float32
    rock_weight: FloatGrid
    top_weight: NotRequired[FloatGrid | None]
    mesh_weight: FloatGrid | None
    mesh_class: U8Grid | None
    mesh_family: U8Grid | None
    water_optics: ClassOptics | None
    grid: BandGrid
    unlit: NotRequired[bool]
    detail: NotRequired[GroundDetail | None]


class WetOptics(TypedDict):
    """``ClassOptics`` at the wet pixels, less the colours the kernel reads of the band in
    place (tint, body and deep)."""

    k: F32Grid
    sky: F32Grid
    deep_tau_m: F32Grid | np.float32
    bed: np.float32
    inland_floor: np.float32
    opaque_tau_m: np.float32
    turbidity: F32Grid
    share: NotRequired[dict[int, F32Grid]]


class UnderwaterScene(NamedTuple):
    """What the colour under the water reads of a band (``optics.underwater``): its heights,
    water and optics, so the wet pixels' own planes can stand in for the band's."""

    z_m: FloatGrid
    water: UnderwaterWater
    water_optics: ClassOptics | None


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

    palette: PaintedPalette
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
    family_top_rgb: dict[int, ColourPlanes]
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
