"""Which material family each placed rock wears, and what a cliff family's top layer is.

A cliff mesh is placed with an override material whose parent chain ends in one of the
``Cliff_<Layer>`` instances: that instance names the ground texture laid over the rock's
up-facing faces, and the chain carries the cliff's ``Color Tint``. A desert rock mesh wears
an ``MI_DesertRock_*`` instance of ``MI_DesertRock``. docs/map/painted.md section 30 and
docs/map/calibration.md section 31 have the measurements.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias, TypedDict

import numpy as np

from mapgen.gamedata.install import open_package
from mapgen.gamedata.level.sweep import Sweep
from mapgen.gamedata.materials import (
    material_parent,
    scalar_parameters,
    texture_parameters,
    vector_parameters,
)
from mapgen.gamedata.placements import PLACEMENT_MESH, placement_material
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ScriptObjects

__all__ = [
    "ARCH_FAMILY_ROOTS",
    "BOULDER_FAMILY_ROOTS",
    "FALLOFF_PARAMETERS",
    "FAMILIES",
    "FAMILY_ROOTS",
    "FAMILY_SLOTS",
    "MAX_CHAIN",
    "SHELL_FAMILY_ROOTS",
    "TARGET_ONLY",
    "TINT_PARAMETER",
    "TOP_BOULDER_BIT",
    "TOP_TEXTURE_PARAMETERS",
    "FamilyResolver",
    "FamilySource",
    "TopOverride",
    "family_sources",
    "placement_families",
    "placement_top_rules",
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
#: The arch material instances that wear a family's colour, by leaf name: the desert arches'
#: ``MI_Arc_Desert_01`` (``MM_Arc_Emissive``, ``BaseColor`` 1.0, 0.41, 0.33) is terracotta
#: wherever it stands, where the arch masters' other instances are grey.
ARCH_FAMILY_ROOTS = {"MI_Arc_Desert_01": "desert"}
#: The shell materials that wear a family's colour: ``FT_SmallShell_Desert`` puts
#: ``MI_SmallShell_Desert`` (its own red albedo) on the Desert Canyons' small shells.
SHELL_FAMILY_ROOTS = {"MI_SmallShell_Desert": "desert"}
#: The foliage boulder materials that wear a family's colour: the desert boulders' foliage
#: types put these terracotta-tinted instances of the grey boulders' own on them.
BOULDER_FAMILY_ROOTS = {
    "MI_Boulder_02_Desert": "desert",
    "MI_Boulder_03_04_Desert": "desert",
    "MI_Boulder_Desert_06": "desert",
}
#: The top cache's family plane holds an arch's family code, or a boulder's with this bit.
TOP_BOULDER_BIT = 0x80
#: Families with no ``Color Tint`` or top layer to read: the palette's target colours them.
TARGET_ONLY = frozenset({"desert"})
ROOT_DIR = "/Game/FactoryGame/World/Environment/Rock/Cliff/Material/"
TINT_PARAMETER = "Color Tint"
#: The family's top-layer texture, the far one first: the map sees the rock from afar.
TOP_TEXTURE_PARAMETERS = ("Far Albedo", "Albedo")
MAX_CHAIN = 8
#: The top layer mask's numbers, which a cliff's own instance may set below its family root.
FALLOFF_PARAMETERS = ("Falloff_Power", "Falloff_Contrast")
#: A raster source id is ``family + FAMILY_SLOTS * rule``: the family code and the index of
#: the placement's top rule override (0 for none), one u8 when folded.
FAMILY_SLOTS = 16

#: An instance's own ``(Falloff_Power, Falloff_Contrast)`` below its family root; None where
#: it sets none, so the family's own number holds.
TopOverride: TypeAlias = tuple[float | None, float | None]


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
        roots: Mapping[str, str] = FAMILY_ROOTS,
    ) -> None:
        self.store, self.scripts, self.index, self.roots = store, scripts, index, roots
        self.by_mesh: dict[str, str | None] = {} if by_mesh is None else by_mesh
        self.by_material: dict[str, int] = {} if by_material is None else by_material
        self._overrides: dict[str, TopOverride] = {}

    def family_of(self, material: str | None) -> int:
        """The family code a material belongs to, by walking its parents to a family root."""
        if not material:
            return 0
        if material in self.by_material:
            return self.by_material[material]
        code, current = 0, material
        for _ in range(MAX_CHAIN):
            leaf = current.rsplit("/", 1)[-1]
            if leaf in self.roots:
                code = FAMILIES.index(self.roots[leaf])
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

    def top_override(self, material: str | None) -> TopOverride:
        """The nearest ``FALLOFF_PARAMETERS`` a material's chain sets below its family root."""
        if not material:
            return (None, None)
        if material in self._overrides:
            return self._overrides[material]
        found: dict[str, float] = {}
        current: str | None = material
        for _ in range(MAX_CHAIN):
            if current is None or current.rsplit("/", 1)[-1] in self.roots:
                break
            view = open_package(self.store, self.scripts, self.index, current)
            if view is None:
                break
            for name, value in scalar_parameters(view).items():
                if name in FALLOFF_PARAMETERS:
                    found.setdefault(name, value)
            current = material_parent(view)
        power, contrast = (found.get(name) for name in FALLOFF_PARAMETERS)
        self._overrides[material] = (power, contrast)
        return power, contrast


def worn_family(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    mesh: str,
    material: str | None,
    caches: tuple[dict[str, str | None], dict[str, int]],
    roots: Mapping[str, str] = FAMILY_ROOTS,
) -> int:
    """``FamilyResolver.worn_family`` by ``roots`` over ``caches``, ``(by mesh, by
    material)``, which the caller keeps across calls, one pair for each ``roots``."""
    resolver = FamilyResolver(store, scripts, index, *caches, roots=roots)
    return resolver.worn_family(mesh, material)


def placement_families(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    sweep: Sweep,
    roots: Mapping[str, str] = FAMILY_ROOTS,
    meshes_only: frozenset[int] | None = None,
) -> U8Grid:
    """One family code per row of ``sweep["placements"]``, by ``roots``; with
    ``meshes_only``, rows of other mesh ids keep 0 unread."""
    meshes, rows = sweep["meshes"], sweep["placements"]
    resolver = FamilyResolver(store, scripts, index, roots=roots)
    codes = np.zeros(len(rows), np.uint8)
    for i, row in enumerate(rows):
        mesh_id = int(row[PLACEMENT_MESH])
        if meshes_only is not None and mesh_id not in meshes_only:
            continue
        codes[i] = resolver.worn_family(meshes[mesh_id], placement_material(sweep, i))
    return codes


def placement_top_rules(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex, sweep: Sweep, codes: U8Grid
) -> tuple[U8Grid, list[TopOverride]]:
    """Per row of ``sweep["placements"]`` of a family ``codes`` gives (desert aside), the
    index into the returned overrides of its instance's own top mask numbers (0 for none,
    ``k`` for entry ``k - 1``). The most placed ``FAMILY_SLOTS - 1`` overrides are kept, in
    order of their numbers."""
    meshes, rows = sweep["meshes"], sweep["placements"]
    resolver = FamilyResolver(store, scripts, index)
    skip = {0, *(FAMILIES.index(family) for family in TARGET_ONLY)}
    found: list[TopOverride | None] = [None] * len(rows)
    for i, row in enumerate(rows):
        if int(codes[i]) in skip:
            continue
        material = placement_material(sweep, i)
        if material is None:
            material = resolver.mesh_material(meshes[int(row[PLACEMENT_MESH])])
        override = resolver.top_override(material)
        found[i] = None if override == (None, None) else override
    counts: dict[TopOverride, int] = {}
    for override in found:
        if override is not None:
            counts[override] = counts.get(override, 0) + 1
    kept = sorted(counts, key=lambda o: -counts[o])[: FAMILY_SLOTS - 1]
    table = sorted(kept, key=lambda o: tuple(-1.0 if v is None else v for v in o))
    slot = {override: k + 1 for k, override in enumerate(table)}
    variants = np.array([slot.get(o, 0) if o is not None else 0 for o in found], np.uint8)
    return variants, table


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
