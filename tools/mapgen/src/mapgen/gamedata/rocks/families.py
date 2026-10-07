"""Which material family each placed rock wears, and what a cliff family's top layer is.

A cliff mesh is placed with an override material whose parent chain ends in one of the
``Cliff_<Layer>`` instances: that instance names the ground texture laid over the rock's
up-facing faces, and the chain carries the cliff's ``Color Tint``. A desert rock mesh wears
an ``MI_DesertRock_*`` instance of ``MI_DesertRock``. docs/map/painted.md section 30 and
docs/map/calibration.md section 31 have the measurements.
"""

from __future__ import annotations

from typing import TypedDict

import numpy as np

from mapgen.gamedata.install import open_package
from mapgen.gamedata.level.sweep import Sweep
from mapgen.gamedata.materials import material_parent, texture_parameters, vector_parameters
from mapgen.gamedata.placements import PLACEMENT_MESH, placement_material
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ScriptObjects

__all__ = [
    "FAMILIES",
    "FAMILY_ROOTS",
    "MAX_CHAIN",
    "TARGET_ONLY",
    "TINT_PARAMETER",
    "TOP_TEXTURE_PARAMETERS",
    "FamilyResolver",
    "FamilySource",
    "family_sources",
    "placement_families",
    "worn_family",
]

#: Family codes, as the direct raster's family plane stores them. 0 is "no family".
FAMILIES = (
    "none",
    "cliff",
    "forest",
    "grass",
    "redgrass",
    "sand",
    "wetsand",
    "redjungle",
    "desert",
)

#: The material instances that root a family, by leaf name.
FAMILY_ROOTS = {
    "Cliff": "cliff",
    "Cliff_Forest": "forest",
    "Cliff_Grass": "grass",
    "Cliff_RedGrass": "redgrass",
    "Cliff_Sand": "sand",
    "Cliff_WetSand": "wetsand",
    "Cliff_RedJungle": "redjungle",
    "MI_DesertRock": "desert",
}
#: Families with no ``Color Tint`` or top layer to read: the palette's target colours them.
TARGET_ONLY = frozenset({"desert"})
ROOT_DIR = "/Game/FactoryGame/World/Environment/Rock/Cliff/Material/"
TINT_PARAMETER = "Color Tint"
#: The family's top-layer texture, the far one first: the map sees the rock from afar.
TOP_TEXTURE_PARAMETERS = ("Far Albedo", "Albedo")
MAX_CHAIN = 8


class FamilySource(TypedDict):
    """Where a cliff family's colours come from: its root material, tint and top texture."""

    material: str
    tint: tuple[float, float, float] | None
    top_texture: str | None


class FamilyResolver:
    """The family a mesh or material wears, each package read once per resolver.

    ``by_mesh`` (a mesh's own first material) and ``by_material`` (a material's family
    code) are the caches; a caller may pass its own to keep them across resolvers.
    """

    def __init__(
        self,
        store: IoStore,
        scripts: ScriptObjects,
        index: AssetIndex,
        by_mesh: dict[str, str | None] | None = None,
        by_material: dict[str, int] | None = None,
    ) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self.by_mesh: dict[str, str | None] = {} if by_mesh is None else by_mesh
        self.by_material: dict[str, int] = {} if by_material is None else by_material

    def family_of(self, material: str | None) -> int:
        """The family code a material belongs to, by walking its parents to a family root."""
        if not material:
            return 0
        if material in self.by_material:
            return self.by_material[material]
        code, current = 0, material
        for _ in range(MAX_CHAIN):
            leaf = current.rsplit("/", 1)[-1]
            if leaf in FAMILY_ROOTS:
                code = FAMILIES.index(FAMILY_ROOTS[leaf])
                break
            view = open_package(self.store, self.scripts, self.index, current)
            parent = material_parent(view) if view is not None else None
            if not parent:
                break
            current = parent
        self.by_material[material] = code
        return code

    def mesh_material(self, mesh: str) -> str | None:
        """A mesh's own first material, which a placement without an override wears."""
        if mesh not in self.by_mesh:
            view = open_package(self.store, self.scripts, self.index, mesh)
            imported = view.pkg.imported_packages if view is not None else []
            self.by_mesh[mesh] = next(
                (p for p in imported if "/Material" in p and "PhysicalMaterial" not in p), None
            )
        return self.by_mesh[mesh]

    def worn_family(self, mesh: str, material: str | None) -> int:
        """The family a mesh wears: its placement's override ``material``, else its own."""
        return self.family_of(material if material is not None else self.mesh_material(mesh))


def worn_family(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    mesh: str,
    material: str | None,
    caches: tuple[dict[str, str | None], dict[str, int]],
) -> int:
    """``FamilyResolver.worn_family`` over ``caches``, ``(by mesh, by material)``, which the
    caller keeps across calls."""
    return FamilyResolver(store, scripts, index, *caches).worn_family(mesh, material)


def placement_families(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex, sweep: Sweep
) -> U8Grid:
    """One family code per row of ``sweep["placements"]``."""
    meshes, rows = sweep["meshes"], sweep["placements"]
    resolver = FamilyResolver(store, scripts, index)
    codes = np.zeros(len(rows), np.uint8)
    for i, row in enumerate(rows):
        mesh = meshes[int(row[PLACEMENT_MESH])]
        codes[i] = resolver.worn_family(mesh, placement_material(sweep, i))
    return codes


def family_sources(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex
) -> dict[str, FamilySource]:
    """Per family: its ``Color Tint`` (the nearest one up the chain) and top-layer texture."""
    out: dict[str, FamilySource] = {}
    for leaf, family in FAMILY_ROOTS.items():
        if family in TARGET_ONLY:
            continue
        tint: tuple[float, float, float] | None = None
        top: str | None = None
        current = ROOT_DIR + leaf
        for _ in range(MAX_CHAIN):
            view = open_package(store, scripts, index, current)
            if view is None:
                break
            tint = tint or _rgb(vector_parameters(view).get(TINT_PARAMETER))
            if current.endswith("/" + leaf):
                textures = {name: path for name, path in texture_parameters(view).items() if path}
                top = next((textures[p] for p in TOP_TEXTURE_PARAMETERS if p in textures), None)
            parent = material_parent(view)
            if not parent or tint is not None:
                break
            current = parent
        out[family] = {"material": ROOT_DIR + leaf, "tint": tint, "top_texture": top}
    return out


def _rgb(vector: tuple[float, float, float, float] | None) -> tuple[float, float, float] | None:
    return None if vector is None else (vector[0], vector[1], vector[2])
